"""Transactional CI scheduling, permissions and immutable workflow snapshots."""

import fnmatch
import hashlib
import os
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, aliased

from devops_server import collaboration_git as git
from devops_server.ci_environment import matches as environment_matches
from devops_server.ci_github import WorkflowError
from devops_server.ci_models import CIArtifact, CIAttempt, CIJob, CIRepository, CIRun, CIWorker
from devops_server.ci_schema import parse_workflow
from devops_server.collaboration_models import PullCheck
from devops_server.models import GitOAuthGrant, GitOAuthToken, Repository, User
from devops_server.repositories import repository_access

ACTIVE = ("assigned", "starting", "running")
TERMINAL = ("success", "failure", "cancelled", "timed_out", "lost", "skipped")
LEASE_SECONDS = 90
LOG_LIMIT = 10 * 1024 * 1024
ARTIFACT_LIMIT = 256 * 1024 * 1024
ARTIFACT_TOTAL_LIMIT = 1024 * 1024 * 1024
RETENTION_DAYS = 30


def now():
    return datetime.now(UTC)


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def transition_lock(db):
    # Serialize the small scheduling transactions across server replicas and the sweeper.
    # Build execution and data transfer never hold this lock.
    db.execute(text("SELECT pg_advisory_xact_lock(1685026419, 17225)"))


def data_directory(settings, attempt_id):
    root = Path(settings.git_repositories_root) / ".ci-data"
    path = root / str(attempt_id)
    if root.is_symlink() or path.is_symlink():
        raise HTTPException(503, "CI 存储不可用")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def allowed(db, repo, actor_id):
    config = db.get(CIRepository, repo.id)
    user = db.get(User, actor_id)
    return bool(
        (config is None or (config.enabled and config.authorized_by == repo.owner_id))
        and not repo.archived
        and repo.deleted_at is None
        and user
        and user.status == "active"
        and repository_access(db, repo, actor_id) == "write"
    )


def repository_pools(db, repo):
    config = db.get(CIRepository, repo.id)
    return config.pools if config else ["default"]


def workflow_paths(settings, repository_id, sha):
    source = git.repository_directory(settings.git_repositories_root, repository_id)
    entries = git.run(source, "ls-tree", "-rz", "--name-only", sha, "--", ".github/workflows/")[
        1
    ].decode("utf-8")
    paths = sorted(
        p for p in entries.split("\0") if p.count("/") == 2 and p.endswith((".yml", ".yaml"))
    )
    if len(paths) > 30:
        raise WorkflowError("每个提交最多支持 30 个工作流")
    if not paths and git.text_git(source, "ls-tree", sha, "--", ".devops/ci.yml"):
        paths = [".devops/ci.yml"]
    return paths


def read_workflow(settings, repository_id, sha, path=None, *, inputs=None):
    source = git.repository_directory(settings.git_repositories_root, repository_id)
    if path is None:
        paths = workflow_paths(settings, repository_id, sha)
        if not paths:
            return None
        if len(paths) != 1:
            raise WorkflowError("请选择要运行的工作流")
        path = paths[0]
    if not (
        path == ".devops/ci.yml"
        or (
            path.startswith(".github/workflows/")
            and path.count("/") == 2
            and path.endswith((".yml", ".yaml"))
            and ".." not in path.split("/")
        )
    ):
        raise WorkflowError("工作流路径无效")
    entry = git.text_git(source, "ls-tree", sha, "--", path)
    if not entry:
        return None
    if not entry.startswith(("100644 blob ", "100755 blob ")):
        raise ValueError("CI 配置必须是普通文件")
    raw = git.run(source, "cat-file", "blob", f"{sha}:{path}", limit=128 * 1024)[1]
    result = parse_workflow(
        raw.decode("utf-8"), github=path.startswith(".github/"), inputs=inputs
    ).model_dump()
    return {**result, "path": path}


def event_matches(workflow, event, ref):
    if event not in workflow["on"]:
        return False
    filters = workflow.get("triggers", {}).get(
        "workflow_dispatch" if event == "manual" else event, {}
    )
    includes = filters.get("branches", workflow.get("branches", []))
    excludes = filters.get("branches-ignore", [])
    included = not includes
    for pattern in includes:
        negative = pattern.startswith("!")
        if fnmatch.fnmatchcase(ref, pattern[1:] if negative else pattern):
            included = not negative
    return included and not any(fnmatch.fnmatchcase(ref, p) for p in excludes)


def create_run(
    db,
    settings,
    repo,
    actor_id,
    event,
    ref,
    sha,
    event_key,
    *,
    item_id=None,
    rerun=None,
    workflow_path=None,
    inputs=None,
    filter_ref=None,
):
    # Caller owns the repository lock, shared with membership/lifecycle operations.
    if not allowed(db, repo, actor_id):
        if event == "manual":
            raise HTTPException(403, "CI 未启用、仓库授权已变更或缺少写权限")
        return None
    if existing := db.scalar(select(CIRun).where(CIRun.event_key == event_key)):
        return existing
    error = ""
    try:
        workflow = (
            rerun.workflow
            if rerun
            else read_workflow(settings, repo.id, sha, workflow_path, inputs=inputs)
        )
        if workflow is None:
            if event == "manual":
                raise HTTPException(422, "提交中没有所选 .github/workflows 工作流")
            return None
        if not rerun and not event_matches(workflow, event, filter_ref or ref):
            if event == "manual":
                raise HTTPException(422, "此流水线未启用所选分支的手动触发")
            return None
        pools = set(repository_pools(db, repo))
        if any(j["environment"]["pool"] not in pools for j in workflow["jobs"].values()):
            raise ValueError("流水线引用了未授权的 worker 池")
    except WorkflowError as exc:
        workflow, error = {"path": workflow_path or ""}, str(exc)
    except ValueError, TypeError, git.GitFailure:
        # Parser errors can contain the user's configuration. Never echo them.
        workflow, error = {"path": workflow_path or ""}, "CI 配置无效，请检查格式、依赖和资源池设置"
    run = CIRun(
        repository_id=repo.id,
        actor_id=actor_id,
        event_key=event_key,
        event=event,
        ref=ref,
        sha=sha,
        item_id=item_id,
        rerun_of=rerun.id if rerun else None,
        workflow=workflow,
        state="failure" if error else "queued",
        error=error,
    )
    db.add(run)
    db.flush()
    source = git.repository_directory(settings.git_repositories_root, repo.id)
    git.run(source, "update-ref", f"refs/devops/ci/{run.id}", sha)
    for name, spec in workflow.get("jobs", {}).items():
        job = CIJob(run_id=run.id, name=name, spec=spec)
        db.add(job)
        db.flush()
        if item_id:
            check_name = (
                f"ci/{workflow.get('path', '').removeprefix('.github/workflows/')}/{name}"
                if workflow.get("format") == "github"
                else f"ci/{name}"
            )
            record = db.scalar(
                select(PullCheck).where(
                    PullCheck.item_id == item_id,
                    PullCheck.head_sha == sha,
                    PullCheck.name == check_name,
                )
            )
            if not record:
                record = PullCheck(
                    item_id=item_id, head_sha=sha, name=check_name, status="queued", source="ci"
                )
                db.add(record)
            record.ci_job_id, record.status, record.source = job.id, "queued", "ci"
            record.details = f"CI #{run.id}"
            owner_name = quote(db.get(User, repo.owner_id).username, safe="")
            record.external_url = (
                f"/repositories/{owner_name}/{quote(repo.name, safe='')}/ci/{run.id}"
            )
    return run


def create_event_runs(db, settings, repo, actor_id, event, ref, sha, event_key, **kwargs):
    if not allowed(db, repo, actor_id):
        return []
    try:
        paths = workflow_paths(settings, repo.id, sha)
    except ValueError, git.GitFailure:
        paths = [None]  # Record a visible configuration failure, instead of losing the event.
    result = []
    for path in paths:
        run = create_run(
            db,
            settings,
            repo,
            actor_id,
            event,
            ref,
            sha,
            event_key + (":" + digest(path)[:20] if path else ""),
            workflow_path=path,
            **kwargs,
        )
        if run:
            result.append(run)
    return result


def project(db, job):
    run = db.get(CIRun, job.run_id)
    status = {
        "starting": "running",
        "assigned": "queued",
        "timed_out": "failure",
        "lost": "failure",
        "skipped": "failure",
    }.get(job.state, job.state)
    for check in db.scalars(
        select(PullCheck).where(PullCheck.ci_job_id == job.id, PullCheck.source == "ci")
    ):
        check.status = status
        check.details = f"CI #{run.id} · {job.state}"
    db.flush()
    states = list(db.scalars(select(CIJob.state).where(CIJob.run_id == run.id)))
    if all(s in TERMINAL for s in states):
        run.state = (
            "success"
            if all(s == "success" for s in states)
            else (
                "cancelled"
                if "cancelled" in states
                and not any(s in ("failure", "timed_out", "lost") for s in states)
                else "failure"
            )
        )
    elif any(s in ("starting", "running") for s in states):
        run.state = "running"
    else:
        run.state = "queued"


def finish(db, attempt, state, error=""):
    if attempt.state not in ACTIVE:
        return
    started = attempt.state != "assigned"
    attempt.state, attempt.finished_at, attempt.error = state, now(), error[:500]
    # Cancellation, lease expiry or a worker crash may interrupt the final step update.
    attempt.steps = [
        {**step, "state": state} if step["state"] == "running" else step for step in attempt.steps
    ]
    if attempt.grant_id:
        db.get(GitOAuthGrant, attempt.grant_id).revoked_at = now()
    job = db.get(CIJob, attempt.job_id)
    job.state = state
    project(db, job)
    if (
        started
        and state in ("failure", "timed_out", "lost")
        and job.spec.get("matrix_group")
        and job.spec.get("fail_fast")
    ):
        for sibling in db.scalars(
            select(CIJob).where(CIJob.run_id == job.run_id, CIJob.id != job.id)
        ):
            if sibling.spec.get("matrix_group") != job.spec["matrix_group"]:
                continue
            if sibling.state == "queued":
                sibling.state = "cancelled"
                project(db, sibling)
            elif sibling.state in ACTIVE:
                active = db.scalar(
                    select(CIAttempt).where(
                        CIAttempt.job_id == sibling.id, CIAttempt.state.in_(ACTIVE)
                    )
                )
                if active:
                    active.cancel_requested = True


def settle(db):
    """Unstarted offers may return to queue; started work never auto-retries."""
    transition_lock(db)
    current = now()
    for attempt in db.scalars(
        select(CIAttempt)
        .where(CIAttempt.state.in_(ACTIVE))
        .order_by(CIAttempt.lease_until, CIAttempt.id)
        .limit(200)
        .with_for_update(skip_locked=True)
    ):
        job = db.get(CIJob, attempt.job_id)
        run = db.get(CIRun, job.run_id)
        repo = db.get(Repository, run.repository_id)
        worker = db.get(CIWorker, attempt.worker_id)
        if (
            not allowed(db, repo, run.actor_id)
            or not worker.enabled
            or worker.pool not in repository_pools(db, repo)
        ):
            attempt.cancel_requested = True
            if attempt.grant_id:
                db.get(GitOAuthGrant, attempt.grant_id).revoked_at = current
        if attempt.lease_until <= current or attempt.deadline <= current:
            was_assigned = attempt.state == "assigned"
            finish(
                db,
                attempt,
                "timed_out" if attempt.deadline <= current else "lost",
                "执行租约或时限已到期",
            )
            if was_assigned and not attempt.cancel_requested:
                job.state = "queued"
                project(db, job)
    for job in db.scalars(
        select(CIJob)
        .where(CIJob.state == "queued")
        .order_by(CIJob.id)
        .execution_options(yield_per=200)
        .with_for_update(skip_locked=True)
    ):
        run = db.get(CIRun, job.run_id)
        repo = db.get(Repository, run.repository_id)
        if not allowed(db, repo, run.actor_id) or job.spec["environment"][
            "pool"
        ] not in repository_pools(db, repo):
            job.state = "cancelled"
            project(db, job)
            continue
        dependencies = list(
            db.scalars(
                select(CIJob).where(CIJob.run_id == job.run_id, CIJob.name.in_(job.spec["needs"]))
            )
        )
        if any(j.state in TERMINAL and j.state != "success" for j in dependencies):
            job.state = "skipped"
            project(db, job)


def match(worker, spec):
    env, caps = spec["environment"], worker.capabilities
    return (
        env["pool"] == worker.pool
        and env["executor"] in caps.get("executors", [])
        and (env["os"] is None or env["os"] == caps.get("os"))
        and (env["arch"] is None or env["arch"] == caps.get("arch"))
        and set(env["labels"]) <= set(caps.get("labels", []))
        and environment_matches(caps, env.get("constraints", []))
        and caps.get("runtime", 1) >= spec.get("runtime", 1)
    )


def worker_attempt(db, worker_id):
    return db.scalar(
        select(CIAttempt)
        .where(CIAttempt.worker_id == worker_id, CIAttempt.state.in_(ACTIVE))
        .with_for_update()
    )


def assign(db, worker):
    if not worker.enabled or worker.draining or worker_attempt(db, worker.id):
        return None
    environment = CIJob.spec["environment"]
    dependency = aliased(CIJob)
    blocked = (
        select(dependency.id)
        .where(
            dependency.run_id == CIJob.run_id,
            CIJob.spec["needs"].op("?")(dependency.name),
            dependency.state != "success",
        )
        .exists()
    )
    for job in db.scalars(
        select(CIJob)
        .where(
            CIJob.state == "queued",
            ~blocked,
            environment["pool"].astext == worker.pool,
            or_(
                environment["os"].astext.is_(None),
                environment["os"].astext == worker.capabilities.get("os"),
            ),
            environment["executor"].astext.in_(worker.capabilities.get("executors", [])),
            or_(
                environment["arch"].astext.is_(None),
                environment["arch"].astext == worker.capabilities.get("arch"),
            ),
            environment["labels"].contained_by(worker.capabilities.get("labels", [])),
        )
        .order_by(CIJob.id)
        .execution_options(yield_per=200)
        .with_for_update(skip_locked=True)
    ):
        if not match(worker, job.spec):
            continue
        run = db.get(CIRun, job.run_id)
        repo = db.get(Repository, run.repository_id)
        if not allowed(db, repo, run.actor_id) or worker.pool not in repository_pools(db, repo):
            continue
        dependencies = list(
            db.scalars(
                select(CIJob).where(CIJob.run_id == job.run_id, CIJob.name.in_(job.spec["needs"]))
            )
        )
        if any(j.state != "success" for j in dependencies):
            continue
        number = (
            db.scalar(select(func.max(CIAttempt.number)).where(CIAttempt.job_id == job.id)) or 0
        ) + 1
        current = now()
        attempt = CIAttempt(
            job_id=job.id,
            worker_id=worker.id,
            number=number,
            state="assigned",
            lease_until=current + timedelta(seconds=LEASE_SECONDS),
            deadline=current + timedelta(minutes=job.spec["timeout_minutes"]),
        )
        db.add(attempt)
        job.state = "assigned"
        db.flush()
        project(db, job)
        return attempt
    return None


def authorize_task(db, grant, repository_id, *, write=False):
    if grant.client_id != "devops-ci":
        return
    attempt = db.scalar(select(CIAttempt).where(CIAttempt.grant_id == grant.id))
    if not attempt:
        raise HTTPException(403, "CI 授权无效")
    job = db.get(CIJob, attempt.job_id)
    run = db.get(CIRun, job.run_id)
    repo = db.get(Repository, run.repository_id)
    worker = db.get(CIWorker, attempt.worker_id)
    if (
        write
        or run.repository_id != repository_id
        or attempt.state not in ("starting", "running")
        or attempt.cancel_requested
        or attempt.lease_until <= now()
        or attempt.deadline <= now()
        or not worker.enabled
        or not allowed(db, repo, run.actor_id)
        or worker.pool not in repository_pools(db, repo)
    ):
        raise HTTPException(403, "CI 执行授权已结束或权限已变更")


def task_bearer(db, raw):
    token = db.scalar(select(GitOAuthToken).where(GitOAuthToken.access_token == raw))
    grant = db.get(GitOAuthGrant, token.grant_id) if token else None
    if (
        not grant
        or grant.client_id != "devops-ci"
        or grant.revoked_at
        or grant.expires_at <= now()
        or token.expires_at <= now()
    ):
        raise HTTPException(401, "CI 授权无效")
    attempt = db.scalar(select(CIAttempt).where(CIAttempt.grant_id == grant.id))
    if not attempt:
        raise HTTPException(401, "CI 授权无效")
    run = db.get(CIRun, db.get(CIJob, attempt.job_id).run_id)
    authorize_task(db, grant, run.repository_id)
    return attempt, run


def start_payload(db, settings, attempt):
    job = db.get(CIJob, attempt.job_id)
    run = db.get(CIRun, job.run_id)
    repo = db.get(Repository, run.repository_id)
    if not attempt.grant_id:
        grant = GitOAuthGrant(
            client_id="devops-ci",
            user_id=run.actor_id,
            scope="git:read",
            expires_at=attempt.deadline,
        )
        db.add(grant)
        db.flush()
        db.add(
            GitOAuthToken(
                grant_id=grant.id,
                access_token=secrets.token_urlsafe(48),
                refresh_token=secrets.token_urlsafe(48),
                expires_at=attempt.deadline,
            )
        )
        attempt.grant_id = grant.id
        db.flush()
    token = db.scalar(select(GitOAuthToken).where(GitOAuthToken.grant_id == attempt.grant_id))
    downloads = []
    for dependency in db.scalars(
        select(CIJob).where(CIJob.run_id == run.id, CIJob.name.in_(job.spec["download_artifacts"]))
    ):
        previous = db.scalar(
            select(CIAttempt)
            .where(CIAttempt.job_id == dependency.id, CIAttempt.state == "success")
            .order_by(CIAttempt.id.desc())
        )
        if previous:
            for artifact in db.scalars(
                select(CIArtifact).where(CIArtifact.attempt_id == previous.id)
            ):
                downloads.append(
                    {
                        "id": artifact.id,
                        "name": artifact.name,
                        "job": dependency.name,
                        "sha256": artifact.sha256,
                    }
                )
    return {
        "type": "job.start",
        "attempt_id": attempt.id,
        "job_id": job.id,
        "run_id": run.id,
        "sha": run.sha,
        "source_ref": f"refs/devops/ci/{run.id}",
        "source_path": (
            f"/git/{quote(db.get(User, repo.owner_id).username, safe='')}/"
            f"{quote(repo.name, safe='')}.git"
        ),
        "token": token.access_token,
        "spec": job.spec,
        "context": {
            "github": {
                "sha": run.sha,
                "ref": f"refs/heads/{run.ref}",
                "ref_name": run.ref,
                "repository": f"{db.get(User, repo.owner_id).username}/{repo.name}",
                "event_name": "workflow_dispatch" if run.event == "manual" else run.event,
                "run_id": str(run.id),
                "workflow": run.workflow.get("name", "CI"),
                "server_url": settings.allowed_origins[0],
            },
            "inputs": run.workflow.get("inputs", {}),
            "matrix": job.spec.get("matrix", {}),
        },
        "downloads": downloads,
        "deadline": attempt.deadline.isoformat(),
        "lease_seconds": LEASE_SECONDS,
        "log_seq": attempt.log_seq,
        "log_limit": LOG_LIMIT,
        "artifact_limit": ARTIFACT_LIMIT,
    }


def push_event(engine, settings, payload):
    if payload.get("offset", 0) or payload["new"] == "0" * 40:
        return
    with Session(engine) as db:
        repo = db.scalar(
            select(Repository).where(Repository.id == payload["repository_id"]).with_for_update()
        )
        if not repo:
            return
        create_event_runs(
            db,
            settings,
            repo,
            payload["actor_id"],
            "push",
            payload["ref"].removeprefix("refs/heads/"),
            payload["new"],
            payload.get("event_id")
            or f"push:{repo.id}:{payload['ref']}:{payload['old']}:{payload['new']}",
        )
        db.commit()


def pull_event(db, settings, repo, item, pull):
    if item.state == "open" and not pull.draft:
        create_event_runs(
            db,
            settings,
            repo,
            item.author_id,
            "pull_request",
            pull.head_ref,
            pull.head_sha,
            f"pr:{item.id}:{pull.head_sha}",
            item_id=item.id,
            filter_ref=pull.base_ref,
        )


def sweep(engine, settings):
    with Session(engine) as db:
        settle(db)
        db.commit()
    # Expired files are bounded per pass; immutable metadata remains auditable.
    with Session(engine) as db:
        for attempt in db.scalars(
            select(CIAttempt)
            .where(
                CIAttempt.finished_at < now() - timedelta(days=RETENTION_DAYS),
                CIAttempt.data_purged_at.is_(None),
            )
            .order_by(CIAttempt.id)
            .limit(200)
            .with_for_update(skip_locked=True)
        ):
            shutil.rmtree(data_directory(settings, attempt.id))
            attempt.data_purged_at = now()
        db.commit()


def run_out(db, run, *, detail=False):
    result = {
        "id": run.id,
        "repository_id": run.repository_id,
        "event": run.event,
        "ref": run.ref,
        "sha": run.sha,
        "state": run.state,
        "error": run.error,
        "created_at": run.created_at,
        "name": run.workflow.get("name", "CI"),
        "workflow_path": run.workflow.get("path", ".devops/ci.yml"),
        "rerun_of": run.rerun_of,
        "item_id": run.item_id,
    }
    if detail:
        result["jobs"] = []
        for job in db.scalars(select(CIJob).where(CIJob.run_id == run.id).order_by(CIJob.id)):
            attempts = []
            for attempt in db.scalars(
                select(CIAttempt).where(CIAttempt.job_id == job.id).order_by(CIAttempt.id)
            ):
                worker = db.get(CIWorker, attempt.worker_id)
                attempts.append(
                    {
                        "id": attempt.id,
                        "number": attempt.number,
                        "worker": worker.name,
                        "state": attempt.state,
                        "cancel_requested": attempt.cancel_requested,
                        "steps": attempt.steps,
                        "error": attempt.error,
                        "created_at": attempt.created_at,
                        "finished_at": attempt.finished_at,
                        "artifacts": [
                            {
                                "id": a.id,
                                "name": a.name,
                                "size": a.size,
                                "sha256": a.sha256,
                                "expires_at": a.expires_at,
                            }
                            for a in db.scalars(
                                select(CIArtifact).where(CIArtifact.attempt_id == attempt.id)
                            )
                        ],
                    }
                )
            result["jobs"].append(
                {
                    "id": job.id,
                    "name": job.name,
                    "display_name": job.spec.get("display_name") or job.name,
                    "state": job.state,
                    "environment": job.spec["environment"],
                    "needs": job.spec["needs"],
                    "attempts": attempts,
                }
            )
    return result
