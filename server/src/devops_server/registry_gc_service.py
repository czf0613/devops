"""Manual GC queue shared by the admin API and the restricted storage worker."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from devops_server.dependencies import role_codes
from devops_server.models import AuditEvent, User, UserSession
from devops_server.registry_models import RegistryGCRun, RegistryGCWorker

ACTIVE = ("queued", "running")
START_WINDOW = timedelta(seconds=60)


def now():
    return datetime.now(UTC)


def worker_available(worker):
    return bool(
        worker and worker.last_seen_at and now() - worker.last_seen_at < timedelta(seconds=30)
    )


def heartbeat(db):
    worker = db.get(RegistryGCWorker, 1)
    worker.last_seen_at = now()


def actor_valid(db, run):
    user, session = db.get(User, run.actor_id), db.get(UserSession, run.session_id)
    return bool(
        user
        and user.status == "active"
        and session
        and session.user_id == user.id
        and session.revoked_at is None
        and session.expires_at > now()
        and session.password_version == user.password_version
        and "admin" in role_codes(db, user.id)
    )


def finish(db, run, error=None):
    run.state = "failed" if error else "succeeded"
    run.phase = "finished"
    run.error = error
    run.finished_at = now()
    db.add(
        AuditEvent(
            actor_user_id=run.actor_id,
            action="registry.gc_failed" if error else "registry.gc_completed",
            target_type="registry_gc",
            target_id=run.id,
            result="failure" if error else "success",
            details={
                "summary": run.summary,
                "error": error,
                "before_bytes": run.before_bytes,
                "after_bytes": run.after_bytes,
            },
        )
    )


def claim(db):
    # The caller owns the filesystem lock: any 'running' record is interrupted, never retried.
    run = db.scalar(select(RegistryGCRun).where(RegistryGCRun.state.in_(ACTIVE)).with_for_update())
    if not run:
        return None
    if run.state == "running":
        finish(db, run, "上次回收被中断，可能已回收部分数据。请重新确认维护窗口后发起新任务。")
        return None
    if now() - run.created_at > START_WINDOW:
        finish(db, run, "任务未在确认后 60 秒内启动，已取消。请重新确认维护窗口。")
        return None
    if not actor_valid(db, run):
        finish(db, run, "发起人的管理员权限或登录会话已失效，未执行回收。")
        return None
    run.state, run.phase, run.started_at = "running", "preview", now()
    return run.id


def run_out(run):
    return {
        key: getattr(run, key)
        for key in (
            "id",
            "actor_id",
            "state",
            "phase",
            "created_at",
            "started_at",
            "finished_at",
            "before_bytes",
            "after_bytes",
            "summary",
            "error",
        )
    }
