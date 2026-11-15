"""Execute official Distribution GC after an administrator confirms a manual window.

This process has storage access, but no Docker socket, SSH key or public HTTP endpoint.
It never stops writes: operators must keep all writers idle until the run finishes.
"""

import fcntl
import logging
import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from devops_server import registry_gc_service as service
from devops_server.config import get_connections
from devops_server.config_store import load_settings
from devops_server.database import build_engine
from devops_server.registry_client import RegistryClient
from devops_server.registry_gc_health import HEARTBEAT
from devops_server.registry_models import RegistryGCRun, RegistryNamespace

ROOT = Path("/var/lib/registry")
CONFIG = Path("/etc/distribution/config.yml")
BINARY = "/usr/local/bin/registry"
LOG = logging.getLogger("devops.registry_gc")
SUMMARY = re.compile(r"^(\d+) blobs marked, (\d+) blobs and (\d+) manifests eligible for deletion$")


class GCError(Exception):
    pass


def command(args, tick, lock_fd, consume, *, timeout=21600):
    """Stream bounded output without recording private repository names or credentials."""
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        pass_fds=(lock_fd,),
        env={"PATH": os.defpath, "OTEL_TRACES_EXPORTER": "none"},
    )
    started, last_tick, pending = time.monotonic(), 0, b""
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                current = time.monotonic()
                if current - started > timeout:
                    raise GCError("操作超时")
                if current - last_tick >= 5:
                    tick()
                    last_tick = current
                for key, _ in selector.select(timeout=1):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        consume(line.decode("utf-8", errors="replace"))
                    if len(pending) > 65536:
                        raise GCError("执行输出格式异常")
                if not selector.get_map() and process.poll() is None:
                    time.sleep(0.1)
            if pending:
                consume(pending.decode("utf-8", errors="replace"))
            if process.wait() != 0:
                raise GCError("执行失败")
    finally:
        # Retain the lock until the child has exited, even on SIGTERM or DB failure.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def allocated_bytes(root, tick, lock_fd):
    size = []
    command(["du", "-sk", str(root / "data")], tick, lock_fd, size.append)
    try:
        return int(size[-1].split()[0]) * 1024
    except IndexError, ValueError:
        raise GCError("无法读取存储占用") from None


def collect(config, tick, lock_fd, *, dry_run):
    counts = {}

    def consume(line):
        if match := SUMMARY.fullmatch(line.strip()):
            counts.update(
                zip(
                    ("marked_blobs", "eligible_blobs", "eligible_manifests"),
                    map(int, match.groups()),
                    strict=True,
                )
            )

    args = [BINARY, "garbage-collect", "--delete-untagged"]
    if dry_run:
        args.append("--dry-run")
    command([*args, str(config)], tick, lock_fd, consume)
    if not counts:
        raise GCError("回收结果无法确认")
    return counts


def pulse(engine):
    with Session(engine) as db, db.begin():
        service.heartbeat(db)
    HEARTBEAT.touch()


def purge_deleted_namespaces(engine, tick):
    """Remove tag roots through the official API, only during manual GC.

    No blob is deleted here. Distribution GC retains shared blobs and traverses the
    remaining live image graphs. Revisit tombstones on each explicit GC so uploads
    already in flight at deletion cannot permanently retain tagged content.
    """
    with Session(engine) as db:
        namespaces = list(
            db.execute(
                select(RegistryNamespace.id, RegistryNamespace.name)
                .where(
                    RegistryNamespace.deleted_at.is_not(None),
                )
                .order_by(RegistryNamespace.id)
            )
        )
    if not namespaces:
        return [], 0
    upstream = RegistryClient(load_settings(engine).registry_internal_url, heartbeat=tick)
    images = upstream.pages("/v2/_catalog", "repositories")
    removed = 0
    for _, namespace in namespaces:
        for name in images:
            if not name.startswith(namespace + "/"):
                continue
            try:
                tags = upstream.tags(name)
            except HTTPException as error:
                if error.status_code == 404:
                    continue
                raise
            for tag in tags:
                try:
                    upstream.delete_tag(name, tag)
                    removed += 1
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
    # Rescan the catalog too: an in-flight upload could have created a new image.
    deleted_names = {name for _, name in namespaces}
    for name in upstream.pages("/v2/_catalog", "repositories"):
        if name.split("/", 1)[0] not in deleted_names:
            continue
        try:
            remaining = upstream.tags(name)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            remaining = []
        if remaining:
            raise GCError("已删除 namespace 出现新的镜像写入")
    return [namespace_id for namespace_id, _ in namespaces], removed


def run_one(engine, root=ROOT, config=CONFIG):
    # The same lock is held by deployment. Never replace or unlink its inode.
    with (root / ".gc.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with Session(engine) as db, db.begin():
            run_id = service.claim(db)
        if run_id is None:
            return False

        last_pulse = 0

        def tick():
            nonlocal last_pulse
            if time.monotonic() - last_pulse >= 5:
                pulse(engine)
                last_pulse = time.monotonic()

        collecting = False
        retiring = False
        try:
            before = allocated_bytes(root, tick, lock.fileno())
            with Session(engine) as db, db.begin():
                run = db.get(RegistryGCRun, run_id)
                if not service.actor_valid(db, run):
                    raise GCError("发起权限已失效")
                retiring = bool(
                    db.scalar(
                        select(RegistryNamespace.id)
                        .where(RegistryNamespace.deleted_at.is_not(None))
                        .limit(1)
                    )
                )
                run.before_bytes = before
                if retiring:
                    run.phase = "namespaces"
            namespace_ids, tags = purge_deleted_namespaces(engine, tick)
            cleanup = {"deleted_namespaces": len(namespace_ids), "deleted_tags": tags}
            with Session(engine) as db, db.begin():
                run = db.get(RegistryGCRun, run_id)
                run.phase, run.summary = "preview", cleanup
            preview = collect(config, tick, lock.fileno(), dry_run=True)
            with Session(engine) as db, db.begin():
                run = db.get(RegistryGCRun, run_id)
                if not service.actor_valid(db, run):
                    raise GCError("发起权限已失效")
                run.summary, run.phase = {**preview, **cleanup}, "collect"
            collecting = True
            result = collect(config, tick, lock.fileno(), dry_run=False)
            after = allocated_bytes(root, tick, lock.fileno())
            with Session(engine) as db, db.begin():
                run = db.get(RegistryGCRun, run_id)
                run.after_bytes, run.summary = after, {**result, **cleanup}
                if namespace_ids:
                    db.execute(
                        update(RegistryNamespace)
                        .where(
                            RegistryNamespace.id.in_(namespace_ids),
                            RegistryNamespace.deleted_at.is_not(None),
                        )
                        .values(purged_at=service.now())
                    )
                service.finish(db, run)
        except GCError, OSError, HTTPException:
            with Session(engine) as db, db.begin():
                service.finish(
                    db,
                    db.get(RegistryGCRun, run_id),
                    "回收失败，可能已回收部分数据。请检查存储和镜像引用完整性，再确认维护窗口重试。"
                    if collecting
                    else (
                        "已删除 namespace 的清理或预检失败，部分标签可能已清理，未执行正式回收。"
                        "请确认停止所有写入，检查 Registry 后重新发起 GC。"
                    )
                    if retiring
                    else "预检或授权检查失败，未执行正式回收。请检查存储、镜像引用和管理员会话。",
                )
        return True


def main():
    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    engine = build_engine(get_connections())
    try:
        settings = load_settings(engine)
        if not settings.registry_enabled or not ROOT.is_dir() or not CONFIG.is_file():
            raise SystemExit("Registry GC storage or configuration is unavailable")
        (ROOT / "data").mkdir(exist_ok=True)
        while True:
            try:
                pulse(engine)
                run_one(engine)
            except SQLAlchemyError, OSError:
                # A failed DB write leaves the job interrupted; the next claim records failure.
                LOG.warning("Registry GC storage unavailable; no automatic GC retry")
            time.sleep(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
