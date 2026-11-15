"""Bounded push receipt ingestion and commit reference indexing, outside Git receive."""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from devops_server import collaboration_git as git
from devops_server import collaboration_references as refs
from devops_server import collaboration_service as service
from devops_server.collaboration_models import ContentReference, WorkItem
from devops_server.collaboration_pulls import enqueue
from devops_server.models import Repository, User
from devops_server.repositories import membership, repository_access

ZERO = "0" * 40


def ingest(engine, settings, after_id=0):
    """Commit the durable job before removing a receipt; repeat ingestion is harmless."""
    with Session(engine) as db:
        ids = list(
            db.scalars(
                select(Repository.id)
                .where(
                    Repository.deleted_at.is_(None),
                    Repository.id > after_id,
                )
                .order_by(Repository.id)
                .limit(50)
            )
        )
        for rid in ids:
            directory = (
                git.repository_directory(settings.git_repositories_root, rid) / "devops-events"
            )
            if not directory.is_dir() or directory.is_symlink():
                continue
            count = 0
            for receipt in directory.glob("push.*.ready"):
                if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 2048:
                    continue
                try:
                    actor, update = receipt.read_text().splitlines()
                    old, new, ref = update.split(" ", 2)
                    if (
                        not actor.isdecimal()
                        or len(actor) > 18
                        or int(actor) <= 0
                        or not git.SHA.fullmatch(old)
                        or not git.SHA.fullmatch(new)
                        or not ref.startswith("refs/heads/")
                        or len(ref) > 1035
                    ):
                        continue
                except ValueError, UnicodeError:
                    continue
                enqueue(
                    db,
                    f"push:{rid}:{receipt.name}",
                    "index-push",
                    {
                        "event_id": f"push:{rid}:{receipt.name}",
                        "repository_id": rid,
                        "actor_id": int(actor),
                        "old": old,
                        "new": new,
                        "ref": ref,
                        "offset": 0,
                    },
                )
                db.commit()
                receipt.unlink(missing_ok=True)
                count += 1
                if count >= 50:
                    break
        return ids[-1] if len(ids) == 50 else 0


def index_push(engine, settings, payload):
    from devops_server.ci_service import push_event

    if not payload.get("skip_ci"):
        push_event(engine, settings, payload)
    if payload["new"] == ZERO:
        return {"indexed": 0}
    rid, actor_id = payload["repository_id"], payload["actor_id"]
    offset = payload.get("offset", 0)
    with git.isolated(settings.git_repositories_root, rid) as (scratch, _):
        revisions = [payload["new"]]
        if payload["old"] != ZERO:
            revisions.append("^" + payload["old"])
        commits = git.text_git(
            scratch,
            "rev-list",
            "--topo-order",
            "--max-count=6",
            f"--skip={offset}",
            *revisions,
            "--",
        ).splitlines()
        for sha in commits[:5]:
            message = git.text_git(scratch, "show", "-s", "--format=%B", sha)
            with Session(engine) as db:
                service.graph_lock(db)
                repo = db.get(Repository, rid)
                user = db.get(User, actor_id)
                if (
                    not repo
                    or not user
                    or user.status != "active"
                    or not repository_access(db, repo, actor_id)
                ):
                    continue
                targets = refs.resolve(
                    db,
                    repo,
                    message,
                    actor_id,
                    settings.git_repositories_root,
                    settings.allowed_origins,
                )
                for target_rid in sorted({rid} | {t["repository_id"] for t in targets}):
                    db.scalar(
                        select(Repository)
                        .where(Repository.id == target_rid)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                if not repository_access(db, repo, actor_id):
                    continue
                for target in targets:
                    target_repo = db.get(Repository, target["repository_id"])
                    if not repository_access(db, target_repo, actor_id):
                        continue
                    key = f"commit:{rid}:{sha}"
                    existing = db.scalar(
                        select(ContentReference).where(
                            ContentReference.source_key == key,
                            ContentReference.target_key == target["key"],
                        )
                    )
                    if not existing:
                        existing = ContentReference(
                            source_key=key,
                            source_sha=sha,
                            source_repository_id=rid,
                            target_key=target["key"],
                            target_repository_id=target_repo.id,
                            target_item_id=target["item_id"],
                            target_sha=target["sha"],
                            target_user_id=target["user_id"],
                            kind="mention",
                        )
                        db.add(existing)
                    if (
                        target["closing"]
                        and existing.kind != "closing"
                        and target.get("item_kind") == "issue"
                        and payload["ref"] == "refs/heads/main"
                        and not target_repo.archived
                        and service.triage(
                            target_repo, membership(db, target_repo.id, actor_id), actor_id
                        )
                    ):
                        existing.kind = "closing"
                        item = db.get(WorkItem, target["item_id"], populate_existing=True)
                        if item.state == "open" and item.deleted_at is None:
                            item.state, item.close_reason, item.closed_at = (
                                "closed",
                                "completed",
                                service.now(),
                            )
                            item.version += 1
                            service.event(db, item, actor_id, "closed_by_commit")
                db.commit()
    if len(commits) > 5:
        with Session(engine) as db:
            next_payload = {**payload, "offset": offset + 5}
            # Branch names are reduced to a stable digest for bounded job keys.
            branch = hashlib.sha256(payload["ref"].encode()).hexdigest()[:16]
            enqueue(
                db,
                f"index-push:{rid}:{payload['old']}:{payload['new']}:{branch}:{offset + 5}",
                "index-push",
                next_payload,
            )
            db.commit()
    return {"indexed": min(5, len(commits))}
