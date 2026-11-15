"""Version 1 machine-only WebSocket protocol; every state change commits before send."""

import asyncio
import json
import os
import re
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from devops_server import ci_service as ci
from devops_server.ci_models import CIAttempt, CIJob, CIWorker
from devops_server.schemas import Input

router = APIRouter()


class Distribution(Input):
    id: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=100)
    version: str = Field(default="", max_length=100)


class Libc(Input):
    name: Literal["glibc", "musl", "unknown"] = "unknown"
    version: str = Field(default="", max_length=100)


class Capabilities(Input):
    os: Literal["linux", "macos", "windows"]
    arch: Literal["arm64", "x64"]
    executors: list[Literal["native"]] = Field(min_length=1, max_length=1)
    labels: list[str] = Field(default_factory=list, max_length=20)
    version: str = Field(min_length=1, max_length=40)
    runtime: int = Field(default=1, ge=1, le=2)
    os_version: str = Field(default="", max_length=100)
    kernel_version: str = Field(default="", max_length=200)
    os_edition: str = Field(default="", max_length=200)
    distribution: Distribution = Field(default_factory=Distribution)
    libc: Libc = Field(default_factory=Libc)


class Hello(Input):
    v: Literal[1]
    type: Literal["hello"]
    capabilities: Capabilities
    registration_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_attempt: int | None = Field(default=None, ge=1)
    abandoned_attempt: int | None = Field(default=None, ge=1)


class Message(Input):
    v: Literal[1]
    type: Literal["heartbeat", "job.accept", "job.started", "job.log", "job.step", "job.complete"]
    id: str = Field(min_length=1, max_length=80)
    attempt_id: int | None = Field(default=None, ge=1)
    seq: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=16000)
    index: int | None = Field(default=None, ge=0, le=49)
    state: (
        Literal["running", "success", "failure", "cancelled", "timed_out", "lost", "skipped"] | None
    ) = None
    exit_code: int | None = None
    error: str = Field(default="", max_length=500)


def connect(engine, raw, hello):
    with Session(engine, expire_on_commit=False) as db:
        ci.transition_lock(db)
        hashed = ci.digest(raw)
        worker = db.scalar(
            select(CIWorker)
            .where(
                or_(
                    CIWorker.token_digest == hashed,
                    CIWorker.registration_digest == hashed,
                )
            )
            .with_for_update()
        )
        if not worker or not worker.enabled:
            raise HTTPException(401, "worker 身份无效")
        if worker.registration_digest == hashed:
            if (
                not hello.registration_digest
                or not worker.registration_expires_at
                or worker.registration_expires_at <= ci.now()
            ):
                raise HTTPException(401, "注册令牌已失效")
            worker.token_digest = hello.registration_digest
            worker.registration_digest = None
            worker.registration_expires_at = None
        elif hello.registration_digest:
            raise HTTPException(400, "已注册 worker 不接受注册参数")
        worker.generation += 1
        worker.last_seen_at = ci.now()
        worker.capabilities = hello.capabilities.model_dump()
        attempt = ci.worker_attempt(db, worker.id)
        if attempt and (
            hello.abandoned_attempt == attempt.id
            or (attempt.state != "assigned" and hello.active_attempt != attempt.id)
        ):
            ci.finish(db, attempt, "lost", "worker 重启后未恢复执行，需人工重跑")
        db.commit()
        return worker.id, worker.generation


def transact(engine, settings, worker_id, generation, reported, message=None):
    with Session(engine, expire_on_commit=False) as db:
        ci.transition_lock(db)
        worker = db.scalar(select(CIWorker).where(CIWorker.id == worker_id).with_for_update())
        if not worker or not worker.enabled or worker.generation != generation:
            raise HTTPException(401, "worker 身份或连接已失效")
        active = ci.worker_attempt(db, worker.id)
        if active:
            run = db.get(ci.CIRun, db.get(CIJob, active.job_id).run_id)
            repo = db.get(ci.Repository, run.repository_id)
            if not ci.allowed(db, repo, run.actor_id) or worker.pool not in ci.repository_pools(
                db, repo
            ):
                active.cancel_requested = True
        replies = []
        if message:
            worker.last_seen_at = ci.now()
            if message.type == "heartbeat":
                reported = message.attempt_id
            if message.attempt_id:
                attempt = db.scalar(
                    select(CIAttempt)
                    .where(
                        CIAttempt.id == message.attempt_id,
                        CIAttempt.worker_id == worker.id,
                    )
                    .with_for_update()
                )
                if not attempt:
                    raise HTTPException(403, "执行不属于当前 worker")
                if attempt.state not in ci.ACTIVE:
                    replies.append(
                        {"type": "job.finished", "attempt_id": attempt.id, "state": attempt.state}
                    )
                elif attempt.lease_until <= ci.now() or attempt.deadline <= ci.now():
                    state = "timed_out" if attempt.deadline <= ci.now() else "lost"
                    ci.finish(db, attempt, state, "执行租约或时限已过期")
                    replies.append(
                        {"type": "job.finished", "attempt_id": attempt.id, "state": state}
                    )
                else:
                    job = db.get(CIJob, attempt.job_id)
                    attempt.lease_until = min(
                        ci.now() + timedelta(seconds=ci.LEASE_SECONDS), attempt.deadline
                    )
                    if message.type == "job.accept":
                        reported = attempt.id
                        if attempt.state == "assigned" and not attempt.cancel_requested:
                            attempt.state = job.state = "starting"
                            ci.project(db, job)
                    elif message.type == "job.started":
                        if attempt.state not in ("starting", "running"):
                            raise HTTPException(409, "执行尚未获准启动")
                        attempt.state = job.state = "running"
                        ci.project(db, job)
                    elif message.type == "job.log":
                        if (
                            message.seq is None
                            or message.text is None
                            or attempt.state == "assigned"
                        ):
                            raise HTTPException(422, "日志消息无效")
                        payload = message.text.encode()
                        if len(payload) > 32 * 1024 or (
                            message.seq > attempt.log_seq
                            and attempt.log_bytes + len(payload) > ci.LOG_LIMIT
                        ):
                            raise HTTPException(413, "日志超过容量限制")
                        if message.seq == attempt.log_seq + 1:
                            directory = ci.data_directory(settings, attempt.id)
                            temporary = directory / "log.tmp"
                            with temporary.open("wb") as stream:
                                stream.write(payload)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(temporary, directory / f"log-{message.seq}")
                            ci.sync_directory(directory)
                            attempt.log_seq += 1
                            attempt.log_bytes += len(payload)
                        elif message.seq > attempt.log_seq:
                            raise HTTPException(409, "日志序号不连续")
                        replies.append(
                            {"type": "log.ack", "attempt_id": attempt.id, "seq": attempt.log_seq}
                        )
                    elif message.type == "job.step":
                        if (
                            message.index is None
                            or message.index >= len(job.spec["steps"])
                            or message.state
                            not in (
                                "running",
                                "success",
                                "failure",
                                "cancelled",
                                "timed_out",
                                "skipped",
                            )
                        ):
                            raise HTTPException(422, "步骤消息无效")
                        steps = list(attempt.steps)
                        if message.index > len(steps):
                            raise HTTPException(409, "步骤顺序无效")
                        result = {
                            "name": job.spec["steps"][message.index]["name"],
                            "state": message.state,
                            "exit_code": message.exit_code,
                        }
                        if message.index == len(steps):
                            steps.append(result)
                        else:
                            if (
                                steps[message.index]["state"] != "running"
                                and steps[message.index] != result
                            ):
                                raise HTTPException(409, "步骤已结束")
                            steps[message.index] = result
                        attempt.steps = steps
                    elif message.type == "job.complete":
                        if (
                            message.state not in ci.TERMINAL
                            or message.state == "skipped"
                            or message.seq != attempt.log_seq
                        ):
                            raise HTTPException(409, "请先确认全部日志")
                        if message.state == "success" and (
                            len(attempt.steps) != len(job.spec["steps"])
                            or any(
                                s["state"] not in ("success", "skipped")
                                and not (
                                    s["state"] == "failure"
                                    and job.spec["steps"][i].get("continue_on_error", False)
                                )
                                for i, s in enumerate(attempt.steps)
                            )
                        ):
                            raise HTTPException(409, "尚有未成功的步骤")
                        ci.finish(
                            db,
                            attempt,
                            "cancelled" if attempt.cancel_requested else message.state,
                            message.error,
                        )
                        replies.append(
                            {
                                "type": "job.finished",
                                "attempt_id": attempt.id,
                                "state": attempt.state,
                            }
                        )
            replies.append(
                {"type": "ack", "reply_to": message.id, "lease_seconds": ci.LEASE_SECONDS}
            )
        attempt = ci.worker_attempt(db, worker.id)
        if attempt and attempt.grant_id:
            from devops_server.models import GitOAuthGrant

            grant = db.get(GitOAuthGrant, attempt.grant_id)
            run = db.get(ci.CIRun, db.get(CIJob, attempt.job_id).run_id)
            try:
                ci.authorize_task(db, grant, run.repository_id)
            except HTTPException:
                attempt.cancel_requested = True
                grant.revoked_at = ci.now()
        if attempt and attempt.cancel_requested:
            if attempt.state == "assigned":
                ci.finish(db, attempt, "cancelled")
                replies.append(
                    {"type": "job.finished", "attempt_id": attempt.id, "state": "cancelled"}
                )
            else:
                replies.append({"type": "job.cancel", "attempt_id": attempt.id})
        elif attempt and attempt.state == "assigned":
            replies.append({"type": "job.offer", "attempt_id": attempt.id})
        elif attempt and attempt.state == "starting":
            replies.append(ci.start_payload(db, settings, attempt))
        elif (
            not attempt
            and reported is None
            and worker.last_seen_at > ci.now() - timedelta(seconds=30)
        ):
            attempt = ci.assign(db, worker)
            if attempt:
                replies.append({"type": "job.offer", "attempt_id": attempt.id})
        if worker.last_seen_at < ci.now() - timedelta(seconds=45):
            raise HTTPException(408, "worker 心跳超时")
        db.commit()
        return reported, replies


async def receive(ws):
    value = await ws.receive_text()
    if len(value.encode()) > 64 * 1024:
        raise ValueError("message too large")
    return json.loads(value)


@router.websocket("/api/ci/worker")
async def worker_socket(ws: WebSocket):
    value = ws.headers.get("authorization", "")
    if ws.headers.get("origin") or not re.fullmatch(r"Bearer [A-Za-z0-9_-]{64}", value):
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        hello = Hello.model_validate(await asyncio.wait_for(receive(ws), timeout=10))
        engine, settings = ws.app.state.engine, ws.app.state.settings
        worker_id, generation = await run_in_threadpool(connect, engine, value[7:], hello)
        await ws.send_json(
            {
                "v": 1,
                "type": "welcome",
                "worker_id": worker_id,
                "generation": generation,
                "lease_seconds": ci.LEASE_SECONDS,
            }
        )
        reported = hello.active_attempt
        while True:
            message = None
            try:
                message = Message.model_validate(await asyncio.wait_for(receive(ws), timeout=2))
            except TimeoutError:
                pass
            reported, replies = await run_in_threadpool(
                transact, engine, settings, worker_id, generation, reported, message
            )
            for reply in replies:
                await ws.send_json({"v": 1, "id": secrets.token_hex(12), **reply})
    except WebSocketDisconnect:
        pass
    except HTTPException, ValidationError, ValueError, TimeoutError:
        await ws.close(code=4400, reason="连接或消息无效，请检查身份、协议和执行状态")
