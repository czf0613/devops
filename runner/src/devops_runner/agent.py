"""Durable outbox and reconnecting WebSocket worker; project code only runs in an executor."""

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from devops_runner.environment import capabilities
from devops_runner.executor import NativeExecutor
from devops_runner.network import artifact_client, network_proxy
from devops_runner.workflow import WorkflowSteps


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("server 必须是无路径和凭据的 HTTPS origin")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("仅本机测试允许 HTTP")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def websocket_url(server):
    return server.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/ci/worker"


class Execution:
    def __init__(self, agent, payload):
        self.agent, self.payload = agent, payload
        self.id = payload["attempt_id"]
        self.root = agent.root / f"attempt-{self.id}"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cancelled = asyncio.Event()
        self.executor = NativeExecutor(self.root, self.log, self.cancelled)
        self.event_counter = 0
        self.log_seq = 0
        self.log_bytes = 0
        self.last_ack = time.monotonic()
        self.state = "running"
        self.task = None
        self.started = False

    def request_cancel(self):
        if self.cancelled.is_set():
            return
        self.cancelled.set()
        if self.started and self.task and not self.task.done():
            self.task.cancel()

    def emit(self, kind, **values):
        self.event_counter += 1
        message = {
            "v": 1,
            "id": f"{self.id}-{self.event_counter}",
            "type": kind,
            "attempt_id": self.id,
            **values,
        }
        save(self.root / "outbox" / f"{self.event_counter:08d}.json", message)

    async def log(self, value):
        # The checkout credential must never leave the worker as log content.
        value = (
            value.replace(self.payload.get("token", ""), "[redacted]")
            if self.payload.get("token")
            else value
        )
        for offset in range(0, len(value), 4000):
            chunk = value[offset : offset + 4000]
            size = len(chunk.encode())
            if self.log_bytes + size > self.payload.get("log_limit", 10 * 1024 * 1024):
                raise RuntimeError("构建日志超过 10 MiB 上限")
            self.log_seq += 1
            self.log_bytes += size
            self.emit("job.log", seq=self.log_seq, text=chunk)

    async def watchdog(self):
        deadline = time.monotonic() + self.payload["spec"]["timeout_minutes"] * 60
        while self.state == "running":
            await asyncio.sleep(1)
            if (
                time.monotonic() > deadline
                or time.monotonic() - self.last_ack > self.payload.get("lease_seconds", 90) - 5
            ):
                self.state = "timed_out" if time.monotonic() > deadline else "lost"
                self.request_cancel()
                return

    async def run(self):
        self.started = True
        watcher = asyncio.create_task(self.watchdog())
        result, error = "success", ""
        try:
            self.emit("job.started")
            await self.executor.prepare()
            github = self.payload["spec"].get("runtime", 1) >= 2
            if not github:
                await self.log(f"Checkout {self.payload['sha']}\n")
                await self.executor.checkout(
                    self.agent.server + self.payload["source_path"],
                    self.payload["source_ref"],
                    self.payload["sha"],
                    self.payload["token"],
                )
            headers = {"Authorization": "Bearer " + self.payload["token"]}
            async with artifact_client(
                self.agent.server,
                headers=headers,
                timeout=120,
                follow_redirects=False,
            ) as client:
                for artifact in [] if github else self.payload.get("downloads", []):
                    path = (
                        self.executor.workspace / "artifacts" / artifact["job"] / artifact["name"]
                    )
                    if not path.resolve().is_relative_to(self.executor.workspace.resolve()):
                        raise RuntimeError("依赖产物路径无效")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    checksum = hashlib.sha256()
                    async with client.stream(
                        "GET", self.agent.server + f"/api/ci/artifacts/{artifact['id']}"
                    ) as response:
                        response.raise_for_status()
                        with path.open("wb") as stream:
                            async for chunk in response.aiter_bytes():
                                checksum.update(chunk)
                                stream.write(chunk)
                    if checksum.hexdigest() != artifact["sha256"]:
                        raise RuntimeError("依赖产物校验失败")
            workflow = WorkflowSteps(self) if github else None
            for index, step in enumerate(self.payload["spec"]["steps"]):
                if self.cancelled.is_set():
                    raise asyncio.CancelledError
                condition = step.get("condition", "success()")
                if (
                    condition == "cancelled()"
                    or (condition == "success()" and result != "success")
                    or (condition == "failure()" and result == "success")
                ):
                    self.emit("job.step", index=index, state="skipped")
                    continue
                self.emit("job.step", index=index, state="running")
                await self.log(f"\n--- {step['name']} ---\n")
                code = (
                    await workflow.run(step, index)
                    if workflow
                    else await self.executor.execute(step, index, self.payload["spec"]["env"])
                )
                status = "success" if code == 0 else "failure"
                if self.cancelled.is_set():
                    status = "cancelled" if self.state == "running" else self.state
                self.emit(
                    "job.step",
                    index=index,
                    state=status if status != "lost" else "failure",
                    exit_code=code,
                )
                if code or self.cancelled.is_set():
                    if not step.get("continue_on_error", False) or self.cancelled.is_set():
                        result = status
                    if self.cancelled.is_set():
                        break
            if result == "success":
                async with artifact_client(
                    self.agent.server,
                    headers=headers,
                    timeout=120,
                    follow_redirects=False,
                ) as client:
                    for name, path in self.executor.collect(self.payload["spec"]["artifacts"]):
                        if path.stat().st_size > self.payload["artifact_limit"]:
                            raise RuntimeError("构建产物超过容量限制")

                        async def chunks(source=path):
                            with source.open("rb") as stream:
                                while chunk := await asyncio.to_thread(stream.read, 1024 * 1024):
                                    yield chunk

                        for retry in range(3):
                            try:
                                response = await client.put(
                                    self.agent.server + f"/api/ci/artifacts/{self.id}",
                                    params={"name": name},
                                    content=chunks(),
                                )
                                response.raise_for_status()
                                break
                            except httpx.TransportError:
                                if retry == 2:
                                    raise
                                await asyncio.sleep(1)
        except asyncio.CancelledError:
            result = "cancelled"
        except (
            OSError,
            RuntimeError,
            ValueError,
            httpx.HTTPError,
            subprocess.SubprocessError,
        ) as exc:
            # Do not print requests or exception text that could include credentials.
            result, error = (
                "failure",
                f"执行失败（{type(exc).__name__}），请检查工具链、网络或资源限制",
            )
            if self.log_bytes + len(error.encode()) + 1 <= self.payload["log_limit"]:
                await self.log(error + "\n")
        finally:
            if self.state in ("timed_out", "lost"):
                result = self.state
            elif self.cancelled.is_set():
                result = "cancelled"
            self.state = result
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            try:
                await self.executor.cleanup()
            except OSError:
                result, error = "failure", "工作目录清理失败，请检查构建机"
                self.agent.stopping.set()
            self.emit("job.complete", state=result, seq=self.log_seq, error=error)
            save(self.root / "finished.json", {"attempt_id": self.id, "state": result})


class Agent:
    def __init__(self, config, config_path):
        self.config, self.config_path = config, config_path
        self.server = origin(config["server"])
        self.root = Path(config["work_dir"]).resolve()
        self.active = None
        self.stopping = asyncio.Event()
        self.offered = None
        self.abandoned = None
        self.replay = None

    async def recover(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in sorted(self.root.glob("attempt-*")):
            if not path.is_dir() or path.is_symlink():
                raise RuntimeError("worker 执行目录无效")
            attempt_id = int(path.name.removeprefix("attempt-"))
            if (path / "finished.json").exists():
                self.replay = path
            else:
                # The POSIX pipe supervisor / Windows Job Object stops orphaned builds.
                await asyncio.sleep(4)
                await asyncio.to_thread(shutil.rmtree, path)
                self.abandoned = attempt_id

    def active_id(self):
        if self.active:
            return self.active.id
        if self.replay:
            return int(self.replay.name.removeprefix("attempt-"))
        return None

    def outbox(self):
        root = self.active.root if self.active else self.replay
        return sorted((root / "outbox").glob("*.json")) if root else []

    async def run(self):
        await self.recover()
        delay = 1
        while not self.stopping.is_set() or self.active:
            try:
                async with connect(
                    websocket_url(self.server),
                    additional_headers={"Authorization": "Bearer " + self.config["token"]},
                    open_timeout=10,
                    ping_interval=15,
                    max_size=1024 * 1024,
                    proxy=network_proxy(self.server),
                ) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "v": 1,
                                "type": "hello",
                                "capabilities": capabilities(self.config.get("labels", [])),
                                "active_attempt": self.active_id(),
                                "abandoned_attempt": self.abandoned,
                            }
                        )
                    )
                    welcome = json.loads(await ws.recv())
                    if welcome["type"] != "welcome":
                        raise RuntimeError("协议不兼容")
                    self.abandoned = None
                    print("worker 已连接", flush=True)
                    delay = 1
                    sent, last_heartbeat = None, 0
                    while not self.stopping.is_set() or self.active:
                        if self.stopping.is_set() and self.active:
                            self.active.request_cancel()
                        if time.monotonic() - last_heartbeat > 10:
                            await ws.send(
                                json.dumps(
                                    {
                                        "v": 1,
                                        "id": secrets.token_hex(12),
                                        "type": "heartbeat",
                                        "attempt_id": self.active_id(),
                                    }
                                )
                            )
                            last_heartbeat = time.monotonic()
                        pending = self.outbox()
                        if pending and sent is None:
                            sent = pending[0]
                            await ws.send(sent.read_text())
                        try:
                            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.25))
                        except TimeoutError:
                            continue
                        kind = message["type"]
                        if kind == "ack":
                            if self.active:
                                self.active.last_ack = time.monotonic()
                            if sent and json.loads(sent.read_text())["id"] == message["reply_to"]:
                                sent.unlink(missing_ok=True)
                                sent = None
                        elif kind == "job.offer":
                            if not self.active_id() and not self.stopping.is_set():
                                self.offered = message["attempt_id"]
                                await ws.send(
                                    json.dumps(
                                        {
                                            "v": 1,
                                            "id": secrets.token_hex(12),
                                            "type": "job.accept",
                                            "attempt_id": self.offered,
                                        }
                                    )
                                )
                        elif kind == "job.start":
                            if not self.active_id() and self.offered == message["attempt_id"]:
                                self.active = Execution(self, message)
                                self.active.task = asyncio.create_task(self.active.run())
                            elif self.active_id() != message["attempt_id"]:
                                raise RuntimeError("server 派发了冲突任务")
                        elif (
                            kind == "job.cancel"
                            and self.active
                            and self.active.id == message["attempt_id"]
                        ):
                            self.active.request_cancel()
                        elif kind == "job.finished" and self.active_id() == message["attempt_id"]:
                            if self.active:
                                if not self.active.task.done():
                                    self.active.request_cancel()
                                await self.active.task
                                root = self.active.root
                                self.active = None
                            else:
                                root, self.replay = self.replay, None
                            await asyncio.to_thread(shutil.rmtree, root)
                            sent, self.offered, last_heartbeat = None, None, 0
                            print(f"执行 #{message['attempt_id']}：{message['state']}", flush=True)
            except OSError, ConnectionClosed, TimeoutError, InvalidStatus:
                if not self.stopping.is_set():
                    print("连接中断，保留执行记录并重连", flush=True)
                    await asyncio.sleep(delay)
                    delay = min(15, delay * 2)
            if self.stopping.is_set() and self.active:
                self.active.request_cancel()
                await self.active.task
                # Retain the final journal for a future reconnect; shutdown is bounded.
                break


async def register(config_path, server, registration, work_dir, labels):
    server = origin(server)
    if config_path.exists():
        raise RuntimeError("配置文件已存在；请使用新配置路径注册")
    config = {
        "server": server,
        "token": secrets.token_urlsafe(48),
        "work_dir": str(work_dir.resolve()),
        "labels": labels,
    }
    # Persist the permanent credential before consuming the one-time registration.
    save(config_path, config)
    try:
        async with connect(
            websocket_url(server),
            additional_headers={"Authorization": "Bearer " + registration},
            open_timeout=10,
            proxy=network_proxy(server),
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "v": 1,
                        "type": "hello",
                        "capabilities": capabilities(labels),
                        "registration_digest": hashlib.sha256(config["token"].encode()).hexdigest(),
                    }
                )
            )
            response = json.loads(await ws.recv())
            config["worker_id"] = response["worker_id"]
            save(config_path, config)
    except BaseException:
        # If the response was lost, this credential may already be registered.
        print("注册响应未确认；保留配置，可尝试 run 或重新签发注册令牌", flush=True)
        raise
    print(f"worker #{config['worker_id']} 已注册，凭据已保存", flush=True)
