"""Browser CI APIs and explicitly scoped, streaming artifact transport."""

import hashlib
import os
import re
import secrets
import tempfile
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devops_server import ci_service as ci
from devops_server import collaboration_git as git
from devops_server.admin_verification import verify_admin_operation
from devops_server.ci_models import CIArtifact, CIAttempt, CIJob, CIRepository, CIRun, CIWorker
from devops_server.ci_schema import Identifier
from devops_server.dependencies import Administrator, Database, RepositoryAuthenticated
from devops_server.repositories import repository_access, require_owner, visible_repository
from devops_server.schemas import AdminVerification, Input
from devops_server.services import audit

router = APIRouter(tags=["CI"])


def worker_out(worker):
    return {
        "id": worker.id,
        "name": worker.name,
        "pool": worker.pool,
        "enabled": worker.enabled,
        "draining": worker.draining,
        "registered": bool(worker.token_digest),
        "online": bool(
            worker.last_seen_at and worker.last_seen_at > ci.now() - timedelta(seconds=45)
        ),
        "last_seen_at": worker.last_seen_at,
        "capabilities": worker.capabilities,
    }


class WorkerCreate(AdminVerification):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f]+$")
    pool: Identifier = "default"


class WorkerUpdate(AdminVerification):
    action: Literal["drain", "resume", "revoke", "register"]


@router.get("/api/admin/ci/workers")
def workers(actor: Administrator, db: Database):
    return {
        "items": [
            worker_out(w)
            for w in db.scalars(select(CIWorker).order_by(CIWorker.id.desc()).limit(500))
        ]
    }


@router.post("/api/admin/ci/workers", status_code=201)
def create_worker(body: WorkerCreate, request: Request, actor: Administrator, db: Database):
    if db.scalar(select(CIWorker.id).where(CIWorker.name == body.name)):
        raise HTTPException(409, "worker 名称已存在")
    verify_admin_operation(db, request, actor, body.code)
    raw = secrets.token_urlsafe(48)
    worker = CIWorker(
        name=body.name,
        pool=body.pool,
        registration_digest=ci.digest(raw),
        registration_expires_at=ci.now() + timedelta(minutes=15),
    )
    db.add(worker)
    db.flush()
    audit(
        db,
        request,
        "ci.worker_created",
        actor_id=actor.user.id,
        target_type="ci_worker",
        target_id=worker.id,
    )
    return {
        **worker_out(worker),
        "registration_token": raw,
        "registration_expires_at": worker.registration_expires_at,
    }


@router.post("/api/admin/ci/workers/{worker_id}")
def update_worker(
    worker_id: int, body: WorkerUpdate, request: Request, actor: Administrator, db: Database
):
    ci.transition_lock(db)
    worker = db.scalar(select(CIWorker).where(CIWorker.id == worker_id).with_for_update())
    if not worker:
        raise HTTPException(404, "worker 不存在")
    verify_admin_operation(db, request, actor, body.code)
    raw = None
    if body.action == "drain":
        worker.draining = True
    elif body.action == "resume":
        if not worker.enabled:
            raise HTTPException(409, "已撤销的 worker 需要重新注册")
        worker.draining = False
    else:
        worker.generation += 1
        worker.token_digest = worker.registration_digest = None
        worker.enabled = body.action == "register"
        worker.last_seen_at = None
        worker.draining = False
        for attempt in db.scalars(
            select(CIAttempt)
            .where(CIAttempt.worker_id == worker.id, CIAttempt.state.in_(ci.ACTIVE))
            .with_for_update()
        ):
            ci.finish(db, attempt, "lost", "worker 身份已撤销，原执行结果不再接受")
        if body.action == "register":
            raw = secrets.token_urlsafe(48)
            worker.registration_digest = ci.digest(raw)
            worker.registration_expires_at = ci.now() + timedelta(minutes=15)
    audit(
        db,
        request,
        f"ci.worker_{body.action}",
        actor_id=actor.user.id,
        target_type="ci_worker",
        target_id=worker.id,
    )
    return {**worker_out(worker), "registration_token": raw}


class RepositoryConfig(Input):
    enabled: bool
    pools: list[Identifier] = Field(default_factory=list, max_length=30)


@router.get("/api/repositories/{repository_id}/ci")
def configuration(repository_id: int, actor: RepositoryAuthenticated, db: Database):
    repo, _ = visible_repository(db, repository_id, actor.user.id)
    config = db.get(CIRepository, repository_id)
    owner = repo.owner_id == actor.user.id
    pools = (
        sorted(
            {
                "default",
                *db.scalars(select(CIWorker.pool).where(CIWorker.enabled.is_(True))),
                *(config.pools if config else []),
            }
        )
        if owner
        else []
    )
    return {
        "enabled": config is None or bool(config.enabled and config.authorized_by == repo.owner_id),
        "pools": ci.repository_pools(db, repo),
        "available_pools": pools,
        "can_configure": owner,
        "can_run": repository_access(db, repo, actor.user.id) == "write",
    }


@router.put("/api/repositories/{repository_id}/ci")
def configure(
    repository_id: int,
    body: RepositoryConfig,
    request: Request,
    actor: RepositoryAuthenticated,
    db: Database,
):
    repo, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    require_owner(repo, actor.user.id)
    if repo.archived:
        raise HTTPException(409, "仓库已归档")
    existing = {"default", *db.scalars(select(CIWorker.pool).where(CIWorker.enabled.is_(True)))}
    if body.enabled and (not body.pools or not set(body.pools) <= existing):
        raise HTTPException(422, "请选择已有的 worker 池")
    config = db.get(CIRepository, repository_id)
    if not config:
        config = CIRepository(repository_id=repository_id, authorized_by=actor.user.id)
        db.add(config)
    config.enabled, config.pools, config.authorized_by = (
        body.enabled,
        sorted(set(body.pools)),
        actor.user.id,
    )
    audit(
        db,
        request,
        "ci.repository_configured",
        actor_id=actor.user.id,
        target_type="repository",
        target_id=repo.id,
        details=body.model_dump(),
    )
    return {"ok": True}


@router.get("/api/repositories/{repository_id}/ci/runs")
def runs(
    repository_id: int,
    actor: RepositoryAuthenticated,
    db: Database,
    page: int = Query(1, ge=1),
    workflow: str = Query("", max_length=300),
    state: str = Query("", max_length=20),
):
    visible_repository(db, repository_id, actor.user.id)
    query = select(CIRun).where(CIRun.repository_id == repository_id)
    if workflow:
        query = query.where(
            func.coalesce(CIRun.workflow["path"].astext, ".devops/ci.yml") == workflow
        )
    if state:
        query = query.where(CIRun.state == state)
    return {
        "items": [
            ci.run_out(db, r)
            for r in db.scalars(query.order_by(CIRun.id.desc()).offset((page - 1) * 20).limit(20))
        ],
        "page": page,
        "page_size": 20,
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


class RunInput(Input):
    ref: str = Field(default="main", min_length=1, max_length=1024)
    workflow: str | None = Field(default=None, max_length=300)
    inputs: dict = Field(default_factory=dict, max_length=10)


@router.get("/api/repositories/{repository_id}/ci/workflows")
def workflows(
    repository_id: int,
    request: Request,
    actor: RepositoryAuthenticated,
    db: Database,
    ref: str = Query("main", min_length=1, max_length=1024),
):
    visible_repository(db, repository_id, actor.user.id)
    settings = request.app.state.settings
    try:
        sha = git.branch_sha(
            git.repository_directory(settings.git_repositories_root, repository_id), ref
        )
        items = []
        for path in ci.workflow_paths(settings, repository_id, sha):
            try:
                workflow = ci.read_workflow(settings, repository_id, sha, path)
                items.append(
                    {
                        "path": path,
                        "name": workflow["name"],
                        "manual": "manual" in workflow["on"],
                        "inputs": workflow.get("triggers", {})
                        .get("workflow_dispatch", {})
                        .get("inputs", {}),
                        "error": "",
                    }
                )
            except ci.WorkflowError as exc:
                items.append(
                    {
                        "path": path,
                        "name": path.rsplit("/", 1)[-1],
                        "manual": False,
                        "inputs": {},
                        "error": str(exc),
                    }
                )
            except ValueError, TypeError, git.GitFailure:
                items.append(
                    {
                        "path": path,
                        "name": path.rsplit("/", 1)[-1],
                        "manual": False,
                        "inputs": {},
                        "error": "工作流配置无效",
                    }
                )
        return {"items": items, "ref": ref, "sha": sha}
    except git.GitFailure as exc:
        raise git.api_failure(exc) from None


@router.get("/api/repositories/{repository_id}/ci/checks/{sha}")
def commit_checks(repository_id: int, sha: str, actor: RepositoryAuthenticated, db: Database):
    visible_repository(db, repository_id, actor.user.id)
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise HTTPException(422, "提交 SHA 无效")
    identity = func.coalesce(CIRun.workflow["path"].astext, ".devops/ci.yml")
    latest = list(
        db.scalars(
            select(CIRun)
            .where(CIRun.repository_id == repository_id, CIRun.sha == sha)
            .distinct(identity)
            .order_by(identity, CIRun.id.desc())
        )
    )
    states = [run.state for run in latest]
    state = (
        "none"
        if not states
        else (
            "failure"
            if any(s in ("failure", "cancelled", "lost", "timed_out") for s in states)
            else "running"
            if any(s not in ci.TERMINAL for s in states)
            else "success"
        )
    )
    return {"sha": sha, "state": state, "items": [ci.run_out(db, run) for run in latest]}


@router.post("/api/repositories/{repository_id}/ci/runs", status_code=201)
def trigger(
    repository_id: int,
    body: RunInput,
    request: Request,
    actor: RepositoryAuthenticated,
    db: Database,
):
    repo, _ = visible_repository(db, repository_id, actor.user.id, lock=True)
    if repository_access(db, repo, actor.user.id) != "write":
        raise HTTPException(403, "运行 CI 需要仓库写权限")
    try:
        sha = git.branch_sha(
            git.repository_directory(
                request.app.state.settings.git_repositories_root, repository_id
            ),
            body.ref,
        )
        run = ci.create_run(
            db,
            request.app.state.settings,
            repo,
            actor.user.id,
            "manual",
            body.ref,
            sha,
            "manual:" + secrets.token_hex(16),
            workflow_path=body.workflow,
            inputs=body.inputs,
        )
    except git.GitFailure as exc:
        raise git.api_failure(exc) from None
    audit(
        db,
        request,
        "ci.run_created",
        actor_id=actor.user.id,
        target_type="ci_run",
        target_id=run.id,
    )
    return ci.run_out(db, run, detail=True)


def readable_run(db, repository_id, run_id, actor, *, write=False):
    repo, _ = visible_repository(db, repository_id, actor.user.id, lock=write)
    if write and repository_access(db, repo, actor.user.id) != "write":
        raise HTTPException(403, "需要仓库写权限")
    run = db.get(CIRun, run_id)
    if not run or run.repository_id != repository_id:
        raise HTTPException(404, "CI 运行不存在")
    return repo, run


@router.get("/api/repositories/{repository_id}/ci/runs/{run_id}")
def run_detail(repository_id: int, run_id: int, actor: RepositoryAuthenticated, db: Database):
    _, run = readable_run(db, repository_id, run_id, actor)
    return ci.run_out(db, run, detail=True)


@router.post("/api/repositories/{repository_id}/ci/runs/{run_id}/cancel")
def cancel(
    repository_id: int, run_id: int, request: Request, actor: RepositoryAuthenticated, db: Database
):
    _, run = readable_run(db, repository_id, run_id, actor, write=True)
    ci.transition_lock(db)
    for job in db.scalars(select(CIJob).where(CIJob.run_id == run.id).with_for_update()):
        if job.state == "queued":
            job.state = "cancelled"
            ci.project(db, job)
        elif job.state in ci.ACTIVE:
            attempt = db.scalar(
                select(CIAttempt)
                .where(CIAttempt.job_id == job.id, CIAttempt.state.in_(ci.ACTIVE))
                .with_for_update()
            )
            if attempt:
                attempt.cancel_requested = True
    audit(
        db,
        request,
        "ci.cancel_requested",
        actor_id=actor.user.id,
        target_type="ci_run",
        target_id=run.id,
    )
    return {"ok": True}


@router.post("/api/repositories/{repository_id}/ci/runs/{run_id}/rerun", status_code=201)
def rerun(
    repository_id: int, run_id: int, request: Request, actor: RepositoryAuthenticated, db: Database
):
    repo, previous = readable_run(db, repository_id, run_id, actor, write=True)
    if previous.state not in ci.TERMINAL:
        raise HTTPException(409, "请等待原运行结束")
    run = ci.create_run(
        db,
        request.app.state.settings,
        repo,
        actor.user.id,
        "manual",
        previous.ref,
        previous.sha,
        "rerun:" + secrets.token_hex(16),
        item_id=previous.item_id,
        rerun=previous,
    )
    audit(
        db,
        request,
        "ci.rerun",
        actor_id=actor.user.id,
        target_type="ci_run",
        target_id=run.id,
        details={"rerun_of": previous.id},
    )
    return ci.run_out(db, run, detail=True)


def readable_attempt(db, repository_id, attempt_id, actor):
    visible_repository(db, repository_id, actor.user.id)
    attempt = db.get(CIAttempt, attempt_id)
    run = db.get(CIRun, db.get(CIJob, attempt.job_id).run_id) if attempt else None
    if not run or run.repository_id != repository_id:
        raise HTTPException(404, "CI 执行不存在")
    return attempt


@router.get("/api/repositories/{repository_id}/ci/attempts/{attempt_id}/logs")
def logs(
    repository_id: int,
    attempt_id: int,
    request: Request,
    actor: RepositoryAuthenticated,
    db: Database,
    after: int = Query(0, ge=0),
):
    attempt = readable_attempt(db, repository_id, attempt_id, actor)
    if attempt.finished_at and attempt.finished_at < ci.now() - timedelta(days=ci.RETENTION_DAYS):
        raise HTTPException(410, "日志已超过保留期限")
    chunks = []
    end = min(after + 32, attempt.log_seq)
    for seq in range(after + 1, end + 1):
        path = ci.data_directory(request.app.state.settings, attempt_id) / f"log-{seq}"
        chunks.append(
            {"seq": seq, "text": path.read_text() if path.is_file() else "[日志块缺失]\n"}
        )
    return {
        "chunks": chunks,
        "next": end,
        "complete": attempt.state in ci.TERMINAL and end >= attempt.log_seq,
    }


@router.get("/api/repositories/{repository_id}/ci/artifacts/{artifact_id}")
def download(
    repository_id: int,
    artifact_id: int,
    request: Request,
    actor: RepositoryAuthenticated,
    db: Database,
):
    artifact = db.get(CIArtifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "产物不存在")
    readable_attempt(db, repository_id, artifact.attempt_id, actor)
    return artifact_response(request, artifact)


def artifact_response(request, artifact):
    path = (
        ci.data_directory(request.app.state.settings, artifact.attempt_id)
        / f"artifact-{artifact.id}"
    )
    if artifact.expires_at <= ci.now() or not path.is_file() or path.is_symlink():
        raise HTTPException(410, "产物已过期或不可用")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=artifact.name.split("/")[-1],
        headers={"X-Content-Type-Options": "nosniff"},
    )


def machine_request(request):
    return request.method == "PUT" and bool(
        re.fullmatch(r"/api/ci/artifacts/[0-9]+", request.url.path)
    )


def raw_bearer(request):
    value = request.headers.get("authorization", "")
    if not re.fullmatch(r"Bearer [A-Za-z0-9_-]{64}", value):
        raise HTTPException(401, "CI 授权无效")
    return value[7:]


@router.put("/api/ci/artifacts/{attempt_id}")
async def upload(
    attempt_id: int, request: Request, name: str = Query(min_length=1, max_length=300)
):
    if (
        "\\" in name
        or name.startswith("/")
        or ":" in name
        or ".." in name.split("/")
        or any(ord(c) < 32 for c in name)
    ):
        raise HTTPException(422, "产物名称无效")
    raw = raw_bearer(request)
    with Session(request.app.state.engine) as db:
        attempt, _ = ci.task_bearer(db, raw)
        if attempt.id != attempt_id:
            raise HTTPException(403, "只能上传当前执行的产物")
    directory = ci.data_directory(request.app.state.settings, attempt_id)
    fd, temporary = tempfile.mkstemp(prefix="upload-", dir=directory)
    try:
        size, checksum = 0, hashlib.sha256()
        with os.fdopen(fd, "wb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > ci.ARTIFACT_LIMIT:
                    raise HTTPException(413, "单个产物不能超过 256 MiB")
                checksum.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        with Session(request.app.state.engine) as db:
            db.scalar(select(CIAttempt).where(CIAttempt.id == attempt_id).with_for_update())
            ci.task_bearer(db, raw)
            artifact = db.scalar(
                select(CIArtifact).where(
                    CIArtifact.attempt_id == attempt_id, CIArtifact.name == name
                )
            )
            if artifact:
                if artifact.sha256 != checksum.hexdigest() or artifact.size != size:
                    raise HTTPException(409, "同名产物内容不同")
            else:
                total = db.scalar(
                    select(func.coalesce(func.sum(CIArtifact.size), 0)).where(
                        CIArtifact.attempt_id == attempt_id
                    )
                )
                count = db.scalar(
                    select(func.count())
                    .select_from(CIArtifact)
                    .where(CIArtifact.attempt_id == attempt_id)
                )
                if total + size > ci.ARTIFACT_TOTAL_LIMIT or count >= 100:
                    raise HTTPException(413, "单次执行产物超过 1 GiB 或 100 个")
                artifact = CIArtifact(
                    attempt_id=attempt_id,
                    name=name,
                    size=size,
                    sha256=checksum.hexdigest(),
                    expires_at=ci.now() + timedelta(days=ci.RETENTION_DAYS),
                )
                db.add(artifact)
                db.flush()
            os.replace(temporary, directory / f"artifact-{artifact.id}")
            ci.sync_directory(directory)
            db.commit()
            return {"id": artifact.id, "sha256": artifact.sha256}
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@router.get("/api/ci/artifacts/{artifact_id}")
def task_download(artifact_id: int, request: Request, db: Database):
    attempt, run = ci.task_bearer(db, raw_bearer(request))
    artifact = db.get(CIArtifact, artifact_id)
    source_attempt = db.get(CIAttempt, artifact.attempt_id) if artifact else None
    source_job = db.get(CIJob, source_attempt.job_id) if source_attempt else None
    job = db.get(CIJob, attempt.job_id)
    if (
        not source_job
        or source_job.run_id != run.id
        or source_job.name not in job.spec["download_artifacts"]
        or source_attempt.state != "success"
    ):
        raise HTTPException(404, "产物不在当前任务的依赖范围")
    return artifact_response(request, artifact)
