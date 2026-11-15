"""Shared Issue / PR permissions, numbering, events and safe response fields."""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from devops_server.collaboration_models import (
    ContentRevision,
    DiscussionComment,
    ItemAssignee,
    ItemCatalog,
    ItemEvent,
    ItemRedirect,
    ItemRelation,
    Notification,
    PullReview,
    Reaction,
    RepositoryCatalog,
    Subscription,
    WorkItem,
)
from devops_server.models import Repository, RepositoryMember, User
from devops_server.repositories import membership, repository_access, visible_repository


def now() -> datetime:
    return datetime.now(UTC)


def person(db: Session, user_id: int | None) -> dict | None:
    user = db.get(User, user_id) if user_id else None
    return (
        {"id": user.id, "username": user.username, "display_name": user.display_name}
        if user
        else None
    )


def repo_url(db: Session, repository: Repository) -> str:
    owner = db.get(User, repository.owner_id)
    return f"/repositories/{quote(owner.username, safe='')}/{quote(repository.name, safe='')}"


def item_url(db: Session, item: WorkItem) -> str:
    section = "issue" if item.kind == "issue" else "pr"
    return f"{repo_url(db, db.get(Repository, item.repository_id))}/{section}/{item.number}"


def visible(db: Session, item: WorkItem | None, user_id: int) -> bool:
    if item is None or item.deleted_at is not None:
        return False
    repo = db.get(Repository, item.repository_id)
    return bool(repo and repository_access(db, repo, user_id))


def triage(repo: Repository, member: RepositoryMember | None, user_id: int) -> bool:
    return repo.owner_id == user_id or bool(
        member and (member.is_admin or member.access == "write")
    )


def repository(db: Session, repository_id: int, user_id: int, *, write=False, manage=False):
    repo, member = visible_repository(db, repository_id, user_id, lock=write)
    if write and repo.archived:
        raise HTTPException(409, "仓库已归档，无法修改")
    if manage and not triage(repo, member, user_id):
        raise HTTPException(403, "需要仓库写入或管理权限")
    return repo, member


def get_item(db: Session, repository_id: int, number: int, user_id: int, *, write=False):
    repo, member = repository(db, repository_id, user_id, write=write)
    item = db.scalar(
        select(WorkItem)
        .where(WorkItem.repository_id == repository_id, WorkItem.number == number)
        .execution_options(populate_existing=True)
    )
    if item is None:
        redirect = db.scalar(
            select(ItemRedirect).where(
                ItemRedirect.repository_id == repository_id, ItemRedirect.number == number
            )
        )
        target = db.get(WorkItem, redirect.item_id) if redirect else None
        if target and visible(db, target, user_id):
            if write:
                raise HTTPException(409, "Issue 已移交，请刷新后在新地址操作")
            return repo, member, target
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, "Issue 或 PR 不存在")
    return repo, member, item


def can_edit(repo: Repository, member: RepositoryMember | None, item: WorkItem, user_id: int):
    if item.author_id != user_id and not triage(repo, member, user_id):
        raise HTTPException(403, "只能修改自己创建的内容")


def conversation(repo: Repository, member: RepositoryMember | None, item: WorkItem, user_id: int):
    if item.locked and not triage(repo, member, user_id):
        raise HTTPException(403, "此讨论已锁定")


def next_number(repo: Repository) -> int:
    # Every caller already owns the repository row lock. Rollback rolls back allocation too.
    number = repo.next_item_number
    repo.next_item_number += 1
    return number


def subscribe(
    db: Session, item_id: int, user_id: int, *, watching=True, email=False, automatic=False
) -> Subscription:
    subscription = db.scalar(
        select(Subscription).where(Subscription.item_id == item_id, Subscription.user_id == user_id)
    )
    if subscription is None:
        subscription = Subscription(
            item_id=item_id, user_id=user_id, watching=watching, email=email
        )
        db.add(subscription)
    elif not automatic:
        subscription.watching = watching
        subscription.email = email
    return subscription


def event(
    db: Session,
    item: WorkItem,
    actor_id: int | None,
    action: str,
    data: dict | None = None,
    *,
    notify=True,
    recipients: set[int] | None = None,
) -> ItemEvent:
    entry = ItemEvent(item_id=item.id, actor_id=actor_id, action=action, data=data or {})
    db.add(entry)
    item.updated_at = now()
    db.flush()
    if notify:
        subscribers = {
            s.user_id: s
            for s in db.scalars(select(Subscription).where(Subscription.item_id == item.id))
        }
        users = {user_id for user_id, s in subscribers.items() if s.watching} | (
            recipients or set()
        )
        for user_id in users - {actor_id}:
            preference = subscribers.get(user_id)
            if preference and not preference.watching:
                continue
            user = db.get(User, user_id)
            if user and user.status == "active" and visible(db, item, user_id):
                db.add(
                    Notification(
                        user_id=user_id,
                        item_id=item.id,
                        event_id=entry.id,
                        email_state="pending" if preference and preference.email else "disabled",
                    )
                )
    return entry


def revision(db: Session, item: WorkItem, actor_id: int, comment: DiscussionComment | None = None):
    db.add(
        ContentRevision(
            item_id=item.id,
            comment_id=comment.id if comment else None,
            author_id=actor_id,
            version=comment.version if comment else item.version,
            title=None if comment else item.title,
            body=comment.body if comment else item.body,
        )
    )


def validate_people(db: Session, repo: Repository, user_ids: list[int]):
    for user_id in set(user_ids):
        user = db.get(User, user_id)
        if not user or user.status != "active" or not repository_access(db, repo, user_id):
            raise HTTPException(422, "所选用户不是有效仓库成员")


def metadata(db: Session, repo: Repository, item: WorkItem, *, assignees=None, catalog=None):
    if assignees is not None:
        validate_people(db, repo, assignees)
        db.execute(delete(ItemAssignee).where(ItemAssignee.item_id == item.id))
        db.add_all(ItemAssignee(item_id=item.id, user_id=user_id) for user_id in set(assignees))
    if catalog is not None:
        records = list(
            db.scalars(select(RepositoryCatalog).where(RepositoryCatalog.id.in_(catalog)))
        )
        if len(records) != len(set(catalog)) or any(
            record.repository_id != repo.id or record.kind == "template" for record in records
        ):
            raise HTTPException(422, "所选元数据不属于当前仓库")
        for kind in ("type", "milestone"):
            if sum(record.kind == kind for record in records) > 1:
                raise HTTPException(422, "只能选择一个类型和一个里程碑")
        db.execute(delete(ItemCatalog).where(ItemCatalog.item_id == item.id))
        db.add_all(ItemCatalog(item_id=item.id, catalog_id=entry.id) for entry in records)


def catalog_out(entry: RepositoryCatalog) -> dict:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "name": entry.name,
        "description": entry.description,
        "data": entry.data,
    }


def summary(db: Session, item: WorkItem) -> dict:
    return {
        "id": item.id,
        "repository_id": item.repository_id,
        "number": item.number,
        "kind": item.kind,
        "title": item.title,
        "state": item.state,
        "close_reason": item.close_reason,
        "url": item_url(db, item),
    }


def reactions(db: Session, item_id: int, user_id: int, comment_id: int | None = None) -> list[dict]:
    entries = db.scalars(
        select(Reaction).where(Reaction.item_id == item_id, Reaction.comment_id == comment_id)
    )
    groups: dict[str, dict] = {}
    for entry in entries:
        group = groups.setdefault(entry.emoji, {"emoji": entry.emoji, "count": 0, "mine": False})
        group["count"] += 1
        group["mine"] |= entry.user_id == user_id
    return list(groups.values())


def item_out(db: Session, item: WorkItem, user_id: int, *, detail=False) -> dict:
    repo = db.get(Repository, item.repository_id)
    member = membership(db, repo.id, user_id)
    can_manage = triage(repo, member, user_id) and not repo.archived
    result = {
        **summary(db, item),
        "author": person(db, item.author_id),
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "closed_at": item.closed_at,
        "pinned": item.pinned,
        "locked": item.locked,
        "assignees": [
            person(db, uid)
            for uid in db.scalars(
                select(ItemAssignee.user_id).where(ItemAssignee.item_id == item.id)
            )
        ],
        "catalog": [
            catalog_out(c)
            for c in db.scalars(
                select(RepositoryCatalog)
                .join(ItemCatalog, ItemCatalog.catalog_id == RepositoryCatalog.id)
                .where(ItemCatalog.item_id == item.id)
            )
        ],
        "comment_count": db.scalar(
            select(func.count())
            .select_from(DiscussionComment)
            .where(
                DiscussionComment.item_id == item.id,
                DiscussionComment.deleted_at.is_(None),
                or_(
                    DiscussionComment.review_id.is_(None),
                    DiscussionComment.review_id.in_(
                        select(PullReview.id).where(PullReview.state != "pending")
                    ),
                ),
            )
        ),
        "permissions": {
            "can_manage": can_manage,
            "can_edit": not repo.archived and (item.author_id == user_id or can_manage),
            "can_comment": not repo.archived and (not item.locked or can_manage),
        },
    }
    if detail:
        result["body"] = item.body
        result["reactions"] = reactions(db, item.id, user_id)
        subscription = db.scalar(
            select(Subscription).where(
                Subscription.item_id == item.id, Subscription.user_id == user_id
            )
        )
        result["subscription"] = {
            "watching": bool(subscription and subscription.watching),
            "email": bool(subscription and subscription.email),
        }
    return result


def comment_out(db: Session, comment: DiscussionComment, user_id: int) -> dict:
    item = db.get(WorkItem, comment.item_id)
    repo = db.get(Repository, item.repository_id)
    manage = triage(repo, membership(db, repo.id, user_id), user_id)
    return {
        "id": comment.id,
        "body": comment.body,
        "version": comment.version,
        "author": person(db, comment.author_id),
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "pinned": comment.pinned,
        "thread_id": comment.thread_id,
        "review_id": comment.review_id,
        "pending": bool(
            comment.review_id and db.get(PullReview, comment.review_id).state == "pending"
        ),
        "url": f"{item_url(db, item)}#issuecomment-{comment.id}",
        "can_edit": not repo.archived and (comment.author_id == user_id or manage),
        "reactions": reactions(db, item.id, user_id, comment.id),
    }


def graph_lock(db: Session):
    # Serialize structural changes across repositories to prevent concurrent cycle/write skew.
    db.execute(text("SELECT pg_advisory_xact_lock(184923, 1)"))


def locked_items(db: Session, ids: list[int], user_id: int) -> dict[int, WorkItem]:
    records = list(db.scalars(select(WorkItem).where(WorkItem.id.in_(ids))))
    if len(records) != len(set(ids)):
        raise HTTPException(404, "关联 Issue 不存在")
    for repository_id in sorted({item.repository_id for item in records}):
        repository(db, repository_id, user_id, write=True, manage=True)
    for item in records:
        db.refresh(item)
        if item.deleted_at is not None:
            raise HTTPException(404, "关联 Issue 不存在")
    return {item.id: item for item in records}


def connect(db: Session, source: WorkItem, target: WorkItem, kind: str, position: int):
    if source.id == target.id:
        raise HTTPException(422, "不能关联自身")
    if kind != "development" and (source.kind != "issue" or target.kind != "issue"):
        raise HTTPException(422, "此关联仅支持 Issue")
    if kind == "development" and source.kind == target.kind:
        raise HTTPException(422, "开发关联需要一个 Issue 和一个 PR")
    old = db.scalar(
        select(ItemRelation).where(
            ItemRelation.source_id == source.id,
            ItemRelation.target_id == target.id,
            ItemRelation.kind == kind,
        )
    )
    if old:
        old.position = position
        return old
    if kind in {"parent", "blocks"}:
        reachable = db.scalar(
            text("""
            WITH RECURSIVE descendants(id) AS (
                SELECT CAST(:target AS bigint) UNION
                SELECT r.target_id FROM item_relations r JOIN descendants d ON r.source_id = d.id
                WHERE r.kind = :kind
            ) SELECT EXISTS(SELECT 1 FROM descendants WHERE id = :source)
        """),
            {"target": target.id, "source": source.id, "kind": kind},
        )
        if reachable:
            raise HTTPException(409, "此关联会形成循环")
    if kind == "parent":
        existing = db.scalar(
            select(ItemRelation).where(
                ItemRelation.kind == "parent", ItemRelation.target_id == target.id
            )
        )
        if existing:
            raise HTTPException(409, "子 Issue 已有父项，请先解除或使用移动操作")
        count = db.scalar(
            select(func.count())
            .select_from(ItemRelation)
            .where(ItemRelation.source_id == source.id, ItemRelation.kind == "parent")
        )
        if count >= 100:
            raise HTTPException(409, "每个 Issue 最多 100 个直接子项")
        depth = db.scalar(
            text("""
            WITH RECURSIVE ancestors(id, depth) AS (
                SELECT CAST(:source AS bigint), 1 UNION ALL SELECT r.source_id, a.depth + 1
                FROM item_relations r JOIN ancestors a ON r.target_id = a.id WHERE r.kind = 'parent'
            ), descendants(id, depth) AS (
                SELECT CAST(:target AS bigint), 1 UNION ALL SELECT r.target_id, d.depth + 1
                FROM item_relations r JOIN descendants d ON r.source_id = d.id
                WHERE r.kind = 'parent'
            ) SELECT (SELECT max(depth) FROM ancestors) + (SELECT max(depth) FROM descendants)
        """),
            {"source": source.id, "target": target.id},
        )
        if depth > 8:
            raise HTTPException(409, "子 Issue 最多嵌套 8 层")
    relation = ItemRelation(source_id=source.id, target_id=target.id, kind=kind, position=position)
    db.add(relation)
    return relation


def relations_out(db: Session, item: WorkItem, user_id: int) -> dict:
    result = []
    for relation in db.scalars(
        select(ItemRelation)
        .where(or_(ItemRelation.source_id == item.id, ItemRelation.target_id == item.id))
        .order_by(ItemRelation.position, ItemRelation.id)
    ):
        other_id = relation.target_id if relation.source_id == item.id else relation.source_id
        other = db.get(WorkItem, other_id)
        if visible(db, other, user_id):
            result.append(
                {
                    "id": relation.id,
                    "kind": relation.kind,
                    "source_id": relation.source_id,
                    "target_id": relation.target_id,
                    "position": relation.position,
                    "item": summary(db, other),
                }
            )
    children = [r["item"] for r in result if r["kind"] == "parent" and r["source_id"] == item.id]
    return {
        "items": result,
        "children_total": len(children),
        "children_completed": sum(child["state"] == "closed" for child in children),
    }
