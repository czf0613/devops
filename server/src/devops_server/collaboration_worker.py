"""Durable collaboration worker. PostgreSQL owns tasks; ref markers recover merges."""

import argparse
import logging
import signal
import time
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from devops_server import collaboration_git as git
from devops_server import collaboration_service as service
from devops_server.collaboration_health import HEARTBEAT, healthy
from devops_server.collaboration_models import (
    CollaborationJob,
    ContentReference,
    DiscussionAttachment,
    ItemEvent,
    MergeIntent,
    Notification,
    PullRequest,
    Subscription,
    WorkItem,
)
from devops_server.collaboration_pulls import enqueue, merge_checks, refresh, review_summary
from devops_server.collaboration_pushes import index_push, ingest
from devops_server.collaboration_schemas import MergePolicy
from devops_server.config import get_connections
from devops_server.config_store import load_settings
from devops_server.database import build_engine
from devops_server.mail import MailDeliveryError, SMTPMailer
from devops_server.models import GitOAuthGrant, Repository, User, UserSession
from devops_server.repositories import membership, repository_access

LOG = logging.getLogger("devops.collaboration")


def actor_valid(db, intent):
    user = db.get(User, intent.actor_id, populate_existing=True)
    if intent.oauth_grant_id:
        grant = db.get(GitOAuthGrant, intent.oauth_grant_id, populate_existing=True)
        return bool(
            user
            and user.status == "active"
            and grant
            and grant.user_id == user.id
            and grant.client_id == "devops-cli"
            and grant.revoked_at is None
            and grant.expires_at > service.now()
            and "api:write" in grant.scope.split()
        )
    session = db.get(UserSession, intent.session_id, populate_existing=True)
    return bool(
        user
        and session
        and session.user_id == user.id
        and user.status == "active"
        and session.revoked_at is None
        and session.expires_at > service.now()
        and session.password_version == user.password_version
    )


def authorize_merge(db, repo, item, pull, intent):
    if not actor_valid(db, intent) or repository_access(db, repo, intent.actor_id) != "write":
        raise git.GitFailure("合并授权已失效，请重新登录或确认仓库权限", status=403)
    if item.state != "open" or item.deleted_at is not None or repo.deleted_at is not None:
        raise git.GitFailure("PR 已关闭或仓库不可用", status=409)
    if (pull.base_sha, pull.head_sha) != (intent.base_sha, intent.head_sha):
        raise git.GitFailure("PR 分支已更新，请重新确认合并", status=409)
    if intent.method not in MergePolicy.model_validate(repo.merge_policy).methods:
        raise git.GitFailure("合并方式已被禁用", status=409)
    reasons = review_summary(db, repo, item, pull)["reasons"]
    if reasons:
        raise git.GitFailure("；".join(reasons), status=409)
    if intent.delete_branch and (
        repo.protect_push or (repo.protect_main and pull.head_ref == "main")
    ):
        raise git.GitFailure("分支保护不允许删除源分支", status=409)


def merge_records(db, intent_id):
    intent = db.get(MergeIntent, intent_id)
    if not intent:
        raise git.GitFailure("合并任务不存在", status=404)
    item = db.get(WorkItem, intent.item_id)
    repo = db.scalar(
        select(Repository)
        .where(Repository.id == item.repository_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    db.refresh(intent)
    db.refresh(item)
    pull = db.scalar(
        select(PullRequest)
        .where(PullRequest.item_id == item.id)
        .execution_options(populate_existing=True)
    )
    return repo, item, pull, intent


def finalize_merge(engine, settings, intent_id):
    # Graph lock precedes repository locks, including related issues in other repositories.
    with Session(engine, expire_on_commit=False) as db:
        service.graph_lock(db)
        intent = db.get(MergeIntent, intent_id)
        item = db.get(WorkItem, intent.item_id)
        repo = db.get(Repository, item.repository_id)
        pull = db.scalar(select(PullRequest).where(PullRequest.item_id == item.id))
        if intent.state == "complete":
            return
        if intent.state not in {"prepared", "applied"}:
            raise git.GitFailure("合并记录尚未完成引用更新")
        source = git.repository_directory(settings.git_repositories_root, repo.id)
        if git.merge_marker(source, intent.id) != intent.result_sha:
            raise git.GitFailure("合并引用无法确认，已停止恢复")
        targets = set()
        if pull.base_ref == "main":
            targets.update(
                db.scalars(
                    select(ContentReference.target_item_id).where(
                        ContentReference.source_item_id == item.id,
                        ContentReference.source_comment_id.is_(None),
                        ContentReference.kind == "closing",
                        ContentReference.active.is_(True),
                        ContentReference.target_item_id.is_not(None),
                    )
                )
            )
            enqueue(
                db,
                f"merge-commits:{intent.id}",
                "index-push",
                {
                    "repository_id": repo.id,
                    "actor_id": intent.actor_id,
                    "old": intent.base_sha,
                    "new": intent.head_sha,
                    "ref": "refs/heads/main",
                    "offset": 0,
                    "skip_ci": True,
                },
            )
        enqueue(
            db,
            f"merge-ci:{intent.id}",
            "ci-push",
            {
                "repository_id": repo.id,
                "actor_id": intent.actor_id,
                "old": intent.base_sha,
                "new": intent.result_sha,
                "ref": f"refs/heads/{pull.base_ref}",
                "event_id": f"merge-ci:{intent.id}",
            },
        )
        target_items = (
            list(db.scalars(select(WorkItem).where(WorkItem.id.in_(targets)))) if targets else []
        )
        for rid in sorted({repo.id} | {target.repository_id for target in target_items}):
            db.scalar(
                select(Repository)
                .where(Repository.id == rid)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        db.refresh(intent)
        db.refresh(item)
        if intent.state == "complete":
            return
        pull.merged_sha, pull.merged_by, pull.merge_method = (
            intent.result_sha,
            intent.actor_id,
            intent.method,
        )
        item.state, item.closed_at = "merged", service.now()
        item.version += 1
        intent.state = "complete"
        service.event(
            db, item, intent.actor_id, "merged", {"sha": intent.result_sha, "method": intent.method}
        )
        for target in target_items:
            db.refresh(target)
            target_repo = db.get(Repository, target.repository_id)
            if (
                target.kind == "issue"
                and target.deleted_at is None
                and target.state == "open"
                and not target_repo.archived
                and target_repo.deleted_at is None
                and service.triage(
                    target_repo, membership(db, target_repo.id, intent.actor_id), intent.actor_id
                )
            ):
                target.state, target.close_reason, target.closed_at = (
                    "closed",
                    "completed",
                    service.now(),
                )
                target.version += 1
                service.event(
                    db, target, intent.actor_id, "closed_by_pull", {"related_item_id": item.id}
                )
        if (
            intent.delete_branch
            and not repo.protect_push
            and not (repo.protect_main and pull.head_ref == "main")
        ):
            # A source push after the merge must survive; old-OID delete will reject it.
            code, _ = git.run(
                source,
                "update-ref",
                "-d",
                f"refs/heads/{pull.head_ref}",
                intent.head_sha,
                accepted=(0, 1, 128),
            )
            service.event(
                db, item, intent.actor_id, "branch_deleted" if code == 0 else "branch_kept"
            )
        db.commit()


def perform_merge(engine, settings, intent_id):
    # Phase 1 persists the prepared object ID before any branch can move.
    with Session(engine, expire_on_commit=False) as db:
        repo, item, pull, intent = merge_records(db, intent_id)
        if intent.state in {"complete", "failed"}:
            return {"state": intent.state}
        source = git.repository_directory(settings.git_repositories_root, repo.id)
        marker = git.merge_marker(source, intent.id)
        if marker:
            if marker != intent.result_sha:
                raise git.GitFailure("合并恢复记录不一致，已停止")
            intent.state = "applied"
        elif intent.state == "pending":
            if intent.oauth_grant_id and not settings.git_oauth_enabled:
                raise git.GitFailure("CLI 授权已停用", status=403)
            authorize_merge(db, repo, item, pull, intent)
            if (
                git.branch_sha(source, pull.base_ref) != intent.base_sha
                or git.branch_sha(source, pull.head_ref) != intent.head_sha
            ):
                raise git.GitFailure("分支已更新，请重新确认合并", status=409)
            intent.result_sha = git.prepare_merge(
                settings.git_repositories_root,
                repo.id,
                intent.base_sha,
                intent.head_sha,
                intent.method,
                intent.message,
            )
            intent.state = "prepared"
        db.commit()
    # Phase 2 rechecks current policy, account/session and both refs under the repository lock.
    with Session(engine, expire_on_commit=False) as db:
        repo, item, pull, intent = merge_records(db, intent_id)
        if intent.state == "prepared":
            source = git.repository_directory(settings.git_repositories_root, repo.id)
            marker = git.merge_marker(source, intent.id)
            if marker is None:
                if intent.oauth_grant_id and not settings.git_oauth_enabled:
                    raise git.GitFailure("CLI 授权已停用", status=403)
                authorize_merge(db, repo, item, pull, intent)
                git.apply_merge(
                    source,
                    intent.id,
                    pull.base_ref,
                    pull.head_ref,
                    intent.base_sha,
                    intent.head_sha,
                    intent.result_sha,
                )
            elif marker != intent.result_sha:
                raise git.GitFailure("合并恢复记录不一致，已停止")
            intent.state = "applied"
            db.commit()
    finalize_merge(engine, settings, intent_id)
    return {"state": "complete"}


def claim(engine):
    with Session(engine, expire_on_commit=False) as db:
        current = service.now()
        job = db.scalar(
            select(CollaborationJob)
            .where(
                or_(
                    and_(
                        CollaborationJob.status == "queued",
                        CollaborationJob.available_at <= current,
                    ),
                    and_(
                        CollaborationJob.status == "running",
                        CollaborationJob.lease_until <= current,
                    ),
                )
            )
            .order_by(CollaborationJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status, job.attempts = "running", job.attempts + 1
        job.lease_until = current + timedelta(minutes=3)
        db.commit()
        return {"id": job.id, "kind": job.kind, "payload": job.payload, "attempt": job.attempts}


def run_one(engine, settings):
    work = claim(engine)
    if work is None:
        return False
    failure = None
    result = {}
    try:
        if work["kind"] == "merge-check":
            payload = work["payload"]
            result = git.check_merge(
                settings.git_repositories_root,
                payload["repository_id"],
                payload["base"],
                payload["head"],
                payload["method"],
            )
        elif work["kind"] == "index-push":
            result = index_push(engine, settings, work["payload"])
        elif work["kind"] == "ci-push":
            from devops_server.ci_service import push_event

            push_event(engine, settings, work["payload"])
            result = {"processed": True}
        elif work["kind"] == "merge":
            result = perform_merge(engine, settings, work["payload"]["intent_id"])
        else:
            raise git.GitFailure("未知后台任务")
    except git.GitFailure as error:
        failure = error
        result = {"status": "error", "error": error.message}
    except HTTPException, SQLAlchemyError, OSError, ValueError:
        failure = git.GitFailure("后台服务暂时不可用，任务将重试")
        result = {"status": "error", "error": failure.message}
    with Session(engine) as db:
        job = db.scalar(
            select(CollaborationJob)
            .where(CollaborationJob.id == work["id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.attempts != work["attempt"] or job.status != "running":
            return True
        job.result, job.lease_until = result, None
        if failure:
            retry = failure.status >= 500 and job.attempts < 3
            job.status = "queued" if retry else "failed"
            job.available_at = service.now() + timedelta(seconds=15 * job.attempts)
            if work["kind"] == "merge" and not retry:
                intent = db.get(MergeIntent, work["payload"]["intent_id"])
                item = db.get(WorkItem, intent.item_id)
                source = git.repository_directory(
                    settings.git_repositories_root, item.repository_id
                )
                # A SQL failure after update-ref must remain recoverable, never report unmerged.
                if git.merge_marker(source, intent.id) == intent.result_sha and intent.result_sha:
                    job.status = "queued"
                    job.available_at = service.now() + timedelta(seconds=60)
                else:
                    intent.state, intent.error = "failed", failure.message
        else:
            job.status = "complete"
        db.commit()
    return True


def reconcile(engine, settings, after_id=0):
    # Bounded pages recover missed hooks and invalidate both head and base changes.
    with Session(engine, expire_on_commit=False) as db:
        ids = list(
            db.scalars(
                select(WorkItem.id)
                .where(
                    WorkItem.kind == "pull",
                    WorkItem.state == "open",
                    WorkItem.deleted_at.is_(None),
                    WorkItem.id > after_id,
                )
                .order_by(WorkItem.id)
                .limit(50)
            )
        )
    deadline = time.monotonic() + 10
    processed = after_id
    for item_id in ids:
        if time.monotonic() >= deadline:
            return processed
        processed = item_id
        try:
            with Session(engine) as db:
                item = db.get(WorkItem, item_id)
                repo = db.scalar(
                    select(Repository)
                    .where(Repository.id == item.repository_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if repo.deleted_at is not None:
                    continue
                db.refresh(item)
                pull = db.scalar(select(PullRequest).where(PullRequest.item_id == item.id))
                if item.state != "open" or pull is None:
                    continue
                missing = refresh(db, settings.git_repositories_root, repo, item, pull)
                if not any(missing.values()):
                    merge_checks(db, repo, pull)
                    from devops_server.ci_service import pull_event

                    pull_event(db, settings, repo, item, pull)
                db.commit()
        except git.GitFailure, SQLAlchemyError, HTTPException:
            LOG.warning("PR synchronization will retry for item %s", item_id)
    return ids[-1] if len(ids) == 50 else 0


def send_notification(engine, settings, mailer):
    with Session(engine) as db:
        record = db.scalar(
            select(Notification)
            .where(Notification.email_state == "pending")
            .order_by(Notification.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not record:
            return False
        item = db.get(WorkItem, record.item_id)
        user = db.get(User, record.user_id)
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.item_id == record.item_id, Subscription.user_id == user.id
            )
        )
        if (
            not user
            or user.status != "active"
            or not service.visible(db, item, user.id)
            or not subscription
            or not subscription.watching
            or not subscription.email
        ):
            record.email_state = "cancelled"
        else:
            event = db.get(ItemEvent, record.event_id)
            url = settings.allowed_origins[0].rstrip("/") + service.item_url(db, item)
            if event.data.get("comment_id"):
                url += f"#issuecomment-{event.data['comment_id']}"
            try:
                mailer.send_notification(user.email, f"#{item.number} {item.title}", url)
                record.email_state = "sent"
            except MailDeliveryError:
                # In-app notification is durable even when email is unavailable.
                record.email_state = "failed"
        db.commit()
        return True


def main():
    parser = argparse.ArgumentParser(description="DevOps collaboration worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        raise SystemExit(0 if healthy() else 1)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    engine = build_engine(get_connections())
    settings = load_settings(engine)
    mailer = SMTPMailer(settings)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_cleanup = last_reconcile = 0
    after_id = receipt_after = 0
    try:
        while not stopping:
            try:
                if time.monotonic() - last_cleanup >= 3600:
                    with Session(engine) as db:
                        db.execute(
                            delete(DiscussionAttachment).where(
                                DiscussionAttachment.item_id.is_(None),
                                DiscussionAttachment.created_at < service.now() - timedelta(days=1),
                            )
                        )
                        db.commit()
                    last_cleanup = time.monotonic()
                if time.monotonic() - last_reconcile >= 10:
                    from devops_server.ci_service import sweep

                    sweep(engine, settings)
                    receipt_after = ingest(engine, settings, receipt_after)
                    after_id = reconcile(engine, settings, after_id)
                    last_reconcile = time.monotonic()
                worked = run_one(engine, settings)
                mailed = send_notification(engine, settings, mailer)
                HEARTBEAT.touch()
            except SQLAlchemyError, OSError:
                LOG.warning("Collaboration storage unavailable; retrying")
                worked = mailed = False
            if args.once:
                return
            if not worked and not mailed:
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
