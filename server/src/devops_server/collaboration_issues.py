"""Repository issues, shared conversation endpoints, hierarchy and metadata."""

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import delete, func, or_, select

from devops_server import collaboration_references as refs
from devops_server import collaboration_service as service
from devops_server.collaboration_models import (
    ContentReference,
    ContentRevision,
    DiscussionAttachment,
    DiscussionComment,
    ItemAssignee,
    ItemCatalog,
    ItemEvent,
    ItemRedirect,
    ItemRelation,
    PullRequest,
    PullReview,
    Reaction,
    RepositoryCatalog,
    SavedFilter,
    WorkItem,
)
from devops_server.collaboration_schemas import (
    BulkItems,
    CatalogInput,
    ChildOrder,
    CommentInput,
    CreateItem,
    EditComment,
    FilterInput,
    MarkdownInput,
    ReactInput,
    RelationInput,
    SubscribeInput,
    TransferItem,
    UpdateItem,
)
from devops_server.dependencies import Database
from devops_server.dependencies import RepositoryAuthenticated as Authenticated
from devops_server.models import Repository
from devops_server.repositories import repository_access

router = APIRouter(prefix="/api/repositories", tags=["collaboration"])


@router.put("/{repository_id}/items/{number}/children/order")
def order_children(
    repository_id: int, number: int, body: ChildOrder, actor: Authenticated, db: Database
):
    service.graph_lock(db)
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    edges = list(
        db.scalars(
            select(ItemRelation).where(
                ItemRelation.source_id == item.id, ItemRelation.kind == "parent"
            )
        )
    )
    by_id = {edge.target_id: edge for edge in edges}
    if len(body.ids) != len(set(body.ids)) or set(body.ids) != set(by_id):
        raise HTTPException(409, "子 Issue 列表已变化，请刷新后排序")
    service.locked_items(db, [item.id, *body.ids], actor.user.id)
    for position, item_id in enumerate(body.ids):
        by_id[item_id].position = position
    service.event(db, item, actor.user.id, "children_reordered", notify=False)
    return {"ok": True}


def synchronize(request: Request, db, item, actor_id, comment=None):
    settings = request.app.state.settings
    body = comment.body if comment else item.body
    ids = {int(value) for value in re.findall(r"/api/attachments/([0-9]{1,18})", body)}
    if ids:
        for attachment in db.scalars(
            select(DiscussionAttachment).where(DiscussionAttachment.id.in_(ids))
        ):
            if (
                attachment.repository_id == item.repository_id
                and attachment.author_id == actor_id
                and attachment.item_id is None
            ):
                attachment.item_id = item.id
    db.flush()
    total_size = db.scalar(
        select(func.coalesce(func.sum(DiscussionAttachment.size), 0)).where(
            DiscussionAttachment.item_id == item.id
        )
    )
    if total_size > 100 * 1024 * 1024:
        raise HTTPException(413, "此讨论的附件总量不能超过 100 MiB")
    return refs.synchronize(
        db,
        item,
        actor_id,
        settings.git_repositories_root,
        settings.allowed_origins,
        comment=comment,
    )


def template_body(db, repo, body: CreateItem, kind: str) -> str:
    if not body.template_id:
        if body.fields:
            raise HTTPException(422, "表单值需要指定模板")
        return body.body
    template = db.get(RepositoryCatalog, body.template_id)
    if (
        not template
        or template.repository_id != repo.id
        or template.kind != "template"
        or template.data["item_kind"] != kind
    ):
        raise HTTPException(422, "模板不存在")
    fields = template.data.get("fields", [])
    if set(body.fields) - {field["id"] for field in fields}:
        raise HTTPException(422, "包含未知的表单字段")
    content = [body.body or template.data.get("body", "")]
    for field in fields:
        value = body.fields.get(field["id"], "")
        if field.get("required") and (not value or (isinstance(value, str) and not value.strip())):
            raise HTTPException(422, f"请填写：{field['label']}")
        if field["type"] == "select" and value and value not in field.get("options", []):
            raise HTTPException(422, "表单选项无效")
        if field["type"] == "checkbox" and not isinstance(value, bool):
            raise HTTPException(422, "复选框值无效")
        rendered = ("已确认" if value else "未确认") if isinstance(value, bool) else str(value)
        content.append(f"### {field['label']}\n\n{rendered}")
    result = "\n\n".join(content)
    if len(result) > 65536:
        raise HTTPException(422, "正文过长")
    return result


def create_item(db, request, repo, member, actor, body, kind="issue"):
    if (body.assignee_ids or body.catalog_ids) and not service.triage(repo, member, actor.user.id):
        raise HTTPException(403, "需要仓库写入或管理权限才能指定元数据")
    item = WorkItem(
        repository_id=repo.id,
        number=service.next_number(repo),
        kind=kind,
        author_id=actor.user.id,
        title=body.title,
        body=template_body(db, repo, body, kind),
    )
    db.add(item)
    db.flush()
    service.metadata(db, repo, item, assignees=body.assignee_ids, catalog=body.catalog_ids)
    service.subscribe(db, item.id, actor.user.id, automatic=True)
    mentioned = synchronize(request, db, item, actor.user.id)
    service.event(db, item, actor.user.id, "created", recipients=mentioned | set(body.assignee_ids))
    return item


@router.get("/{repository_id}/items")
def list_items(
    repository_id: int,
    actor: Authenticated,
    db: Database,
    kind: Literal["issue", "pull"] = "issue",
    state: Literal["open", "closed", "merged", "all"] = "open",
    q: Annotated[str, Query(max_length=256)] = "",
    author: Annotated[int | None, Query(gt=0)] = None,
    assignee: Annotated[int | None, Query(gt=0)] = None,
    label: Annotated[int | None, Query(gt=0)] = None,
    type_id: Annotated[int | None, Query(alias="type", gt=0)] = None,
    milestone: Annotated[int | None, Query(gt=0)] = None,
    updated_after: datetime | None = None,
    sort: Literal["created", "updated", "oldest", "comments"] = "updated",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
):
    service.repository(db, repository_id, actor.user.id)
    base = select(WorkItem).where(
        WorkItem.repository_id == repository_id,
        WorkItem.kind == kind,
        WorkItem.deleted_at.is_(None),
    )
    counts = dict(
        db.execute(
            select(WorkItem.state, func.count())
            .where(
                WorkItem.repository_id == repository_id,
                WorkItem.kind == kind,
                WorkItem.deleted_at.is_(None),
            )
            .group_by(WorkItem.state)
        ).all()
    )
    query = base
    if state != "all":
        query = query.where(
            WorkItem.state.in_(["closed", "merged"])
            if state == "closed"
            else WorkItem.state == state
        )
    if q.strip():
        query = query.where(
            or_(
                WorkItem.title.icontains(q.strip(), autoescape=True),
                WorkItem.body.icontains(q.strip(), autoescape=True),
            )
        )
    if author:
        query = query.where(WorkItem.author_id == author)
    if assignee:
        query = query.where(
            WorkItem.id.in_(select(ItemAssignee.item_id).where(ItemAssignee.user_id == assignee))
        )
    for catalog_id, catalog_kind in ((label, "label"), (type_id, "type"), (milestone, "milestone")):
        if catalog_id:
            query = query.where(
                WorkItem.id.in_(
                    select(ItemCatalog.item_id)
                    .join(RepositoryCatalog, RepositoryCatalog.id == ItemCatalog.catalog_id)
                    .where(
                        ItemCatalog.catalog_id == catalog_id, RepositoryCatalog.kind == catalog_kind
                    )
                )
            )
    if updated_after:
        query = query.where(WorkItem.updated_at >= updated_after)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = {
        "created": WorkItem.created_at.desc(),
        "updated": WorkItem.updated_at.desc(),
        "oldest": WorkItem.created_at.asc(),
        "comments": select(func.count())
        .select_from(DiscussionComment)
        .where(
            DiscussionComment.item_id == WorkItem.id,
            DiscussionComment.deleted_at.is_(None),
            DiscussionComment.review_id.is_(None),
        )
        .scalar_subquery()
        .desc(),
    }[sort]
    items = db.scalars(
        query.order_by(WorkItem.pinned.desc(), order, WorkItem.id.desc())
        .offset((page - 1) * 25)
        .limit(25)
    )
    return {
        "items": [service.item_out(db, item, actor.user.id) for item in items],
        "total": total,
        "page": page,
        "page_size": 25,
        "counts": counts,
    }


@router.post("/{repository_id}/issues", status_code=201)
def create_issue(
    repository_id: int, body: CreateItem, request: Request, actor: Authenticated, db: Database
):
    if body.parent_id:
        service.graph_lock(db)
        parent = db.get(WorkItem, body.parent_id)
        if parent is None:
            raise HTTPException(404, "父 Issue 不存在")
        # Acquire repository locks in a stable order even for cross-repository child creation.
        for rid in sorted({parent.repository_id, repository_id}):
            service.repository(db, rid, actor.user.id, write=True, manage=True)
        db.refresh(parent)
        if parent.deleted_at is not None or parent.kind != "issue":
            raise HTTPException(404, "父 Issue 不存在")
    repo, member = service.repository(db, repository_id, actor.user.id, write=True)
    item = create_item(db, request, repo, member, actor, body)
    if body.parent_id:
        service.connect(db, parent, item, "parent", 100)
        service.event(
            db,
            parent,
            actor.user.id,
            "relation_added",
            {"related_item_id": item.id, "kind": "parent"},
        )
    return service.item_out(db, item, actor.user.id, detail=True)


@router.get("/{repository_id}/items/{number}")
def get_item(repository_id: int, number: int, actor: Authenticated, db: Database):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    return {
        **service.item_out(db, item, actor.user.id, detail=True),
        "relations": service.relations_out(db, item, actor.user.id),
        "backlinks": refs.backlinks(db, actor.user.id, item_id=item.id),
    }


def update_item(db, request, repo, member, item, actor_id, body):
    service.can_edit(repo, member, item, actor_id)
    if body.version != item.version:
        raise HTTPException(409, "内容已被修改，请刷新后重试")
    values = body.model_dump(exclude_unset=True)
    if any(
        key in values for key in ("assignee_ids", "catalog_ids", "pinned", "locked")
    ) and not service.triage(repo, member, actor_id):
        raise HTTPException(403, "需要仓库写入或管理权限")
    if item.state == "merged" and "state" in values:
        raise HTTPException(409, "已合并的 PR 不能重新打开")
    if "close_reason" in values and values.get("state", item.state) != "closed":
        raise HTTPException(422, "只有关闭时可以设置关闭原因")
    if item.kind == "pull" and values.get("state") == "open":
        pull = db.scalar(select(PullRequest).where(PullRequest.item_id == item.id))
        if db.scalar(
            select(PullRequest.id)
            .join(WorkItem, WorkItem.id == PullRequest.item_id)
            .where(
                WorkItem.repository_id == repo.id,
                WorkItem.state == "open",
                WorkItem.deleted_at.is_(None),
                PullRequest.item_id != item.id,
                PullRequest.base_ref == pull.base_ref,
                PullRequest.head_ref == pull.head_ref,
            )
        ):
            raise HTTPException(409, "这两个分支已有未关闭的 PR")
    service.revision(db, item, actor_id)
    if "body" in values:
        item.body_repository_id = None
    for key in ("title", "body", "pinned", "locked"):
        if key in values:
            setattr(item, key, values[key])
    if "state" in values:
        changed = item.state != body.state
        item.state = body.state
        item.close_reason = (body.close_reason or "completed") if body.state == "closed" else None
        item.closed_at = service.now() if body.state == "closed" else None
        if changed:
            service.event(
                db,
                item,
                actor_id,
                "closed" if body.state == "closed" else "reopened",
                {"reason": item.close_reason},
            )
    elif "close_reason" in values:
        item.close_reason = body.close_reason
    service.metadata(db, repo, item, assignees=body.assignee_ids, catalog=body.catalog_ids)
    item.version += 1
    mentioned = synchronize(request, db, item, actor_id) if "body" in values else set()
    service.event(
        db,
        item,
        actor_id,
        "edited",
        {"fields": sorted(set(values) - {"version"})},
        recipients=mentioned | set(body.assignee_ids or []),
    )


@router.patch("/{repository_id}/items/{number}")
def edit_item(
    repository_id: int,
    number: int,
    body: UpdateItem,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    update_item(db, request, repo, member, item, actor.user.id, body)
    return service.item_out(db, item, actor.user.id, detail=True)


@router.post("/{repository_id}/items/bulk")
def bulk_items(
    repository_id: int, body: BulkItems, request: Request, actor: Authenticated, db: Database
):
    repo, member = service.repository(db, repository_id, actor.user.id, write=True, manage=True)
    items = list(
        db.scalars(
            select(WorkItem).where(
                WorkItem.repository_id == repository_id,
                WorkItem.number.in_(body.numbers),
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    if len(items) != len(set(body.numbers)):
        raise HTTPException(404, "选择的 Issue 或 PR 已不存在")
    changes = body.model_dump(exclude={"numbers"}, exclude_none=True)
    if "state" not in changes:
        changes.pop("close_reason", None)
    for item in items:
        update_item(
            db,
            request,
            repo,
            member,
            item,
            actor.user.id,
            UpdateItem(version=item.version, **changes),
        )
    return {"updated": len(items)}


@router.delete("/{repository_id}/items/{number}")
def delete_item(repository_id: int, number: int, actor: Authenticated, db: Database):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    if not service.triage(repo, member, actor.user.id):
        raise HTTPException(403, "需要仓库写入或管理权限")
    if item.kind != "issue":
        raise HTTPException(409, "PR 保留合并记录，请使用关闭操作")
    item.deleted_at = service.now()
    item.version += 1
    service.event(db, item, actor.user.id, "deleted", notify=False)
    return {"ok": True}


@router.get("/{repository_id}/items/{number}/timeline")
def timeline(
    repository_id: int,
    number: int,
    actor: Authenticated,
    db: Database,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    comment: Annotated[int | None, Query(gt=0)] = None,
):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    query = select(ItemEvent).where(ItemEvent.item_id == item.id)
    if comment:
        entry = db.scalar(
            query.where(
                ItemEvent.action == "commented", ItemEvent.data["comment_id"].astext == str(comment)
            )
        )
        if entry:
            before = db.scalar(
                select(func.count())
                .select_from(ItemEvent)
                .where(ItemEvent.item_id == item.id, ItemEvent.id < entry.id)
            )
            page = before // 50 + 1
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    entries = []
    for entry in db.scalars(query.order_by(ItemEvent.id).offset((page - 1) * 50).limit(50)):
        data = dict(entry.data)
        if data.get("related_item_id"):
            related = db.get(WorkItem, data["related_item_id"])
            if not service.visible(db, related, actor.user.id):
                continue
            data["related_item"] = service.summary(db, related)
        output = {
            "id": entry.id,
            "action": entry.action,
            "data": data,
            "created_at": entry.created_at,
            "actor": service.person(db, entry.actor_id),
        }
        if entry.action == "commented":
            record = db.get(DiscussionComment, data["comment_id"])
            if not record or record.deleted_at is not None:
                continue
            output["comment"] = service.comment_out(db, record, actor.user.id)
        entries.append(output)
    return {
        "items": entries,
        "total": total,
        "page": page,
        "page_size": 50,
        "pinned_comments": [
            service.comment_out(db, record, actor.user.id)
            for record in db.scalars(
                select(DiscussionComment)
                .where(
                    DiscussionComment.item_id == item.id,
                    DiscussionComment.pinned.is_(True),
                    DiscussionComment.deleted_at.is_(None),
                    DiscussionComment.review_id.is_(None),
                )
                .order_by(DiscussionComment.id)
                .limit(10)
            )
        ],
    }


@router.post("/{repository_id}/items/{number}/comments", status_code=201)
def add_comment(
    repository_id: int,
    number: int,
    body: CommentInput,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    service.conversation(repo, member, item, actor.user.id)
    if body.thread_id or body.review_id:
        raise HTTPException(422, "行评论请使用审阅接口")
    comment = DiscussionComment(item_id=item.id, author_id=actor.user.id, body=body.body)
    db.add(comment)
    db.flush()
    service.subscribe(db, item.id, actor.user.id, automatic=True)
    mentioned = synchronize(request, db, item, actor.user.id, comment)
    service.event(
        db, item, actor.user.id, "commented", {"comment_id": comment.id}, recipients=mentioned
    )
    return service.comment_out(db, comment, actor.user.id)


def editable_comment(db, repo, member, item, comment_id, actor_id):
    comment = db.get(DiscussionComment, comment_id)
    if not comment or comment.item_id != item.id or comment.deleted_at is not None:
        raise HTTPException(404, "评论不存在")
    if is_draft(db, comment) and comment.author_id != actor_id:
        raise HTTPException(404, "评论不存在")
    if comment.author_id != actor_id and not service.triage(repo, member, actor_id):
        raise HTTPException(403, "只能修改自己的评论")
    return comment


def is_draft(db, comment):
    return bool(comment.review_id and db.get(PullReview, comment.review_id).state == "pending")


@router.patch("/{repository_id}/items/{number}/comments/{comment_id}")
def edit_comment(
    repository_id: int,
    number: int,
    comment_id: int,
    body: EditComment,
    request: Request,
    actor: Authenticated,
    db: Database,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    comment = editable_comment(db, repo, member, item, comment_id, actor.user.id)
    service.conversation(repo, member, item, actor.user.id)
    if comment.version != body.version:
        raise HTTPException(409, "评论已被修改，请刷新后重试")
    service.revision(db, item, actor.user.id, comment)
    comment.body = body.body
    comment.body_repository_id = None
    comment.version += 1
    if not is_draft(db, comment):
        synchronize(request, db, item, actor.user.id, comment)
        service.event(
            db, item, actor.user.id, "comment_edited", {"comment_id": comment.id}, notify=False
        )
    return service.comment_out(db, comment, actor.user.id)


@router.delete("/{repository_id}/items/{number}/comments/{comment_id}")
def delete_comment(
    repository_id: int, number: int, comment_id: int, actor: Authenticated, db: Database
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    comment = editable_comment(db, repo, member, item, comment_id, actor.user.id)
    comment.deleted_at = service.now()
    for reference in db.scalars(
        select(ContentReference).where(ContentReference.source_comment_id == comment.id)
    ):
        reference.active = False
    if not is_draft(db, comment):
        service.event(
            db, item, actor.user.id, "comment_deleted", {"comment_id": comment.id}, notify=False
        )
    return {"ok": True}


@router.put("/{repository_id}/items/{number}/comments/{comment_id}/pin")
def pin_comment(
    repository_id: int,
    number: int,
    comment_id: int,
    actor: Authenticated,
    db: Database,
    pinned: bool = True,
):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    if not service.triage(repo, member, actor.user.id):
        raise HTTPException(403, "需要仓库写入或管理权限")
    comment = editable_comment(db, repo, member, item, comment_id, actor.user.id)
    if is_draft(db, comment):
        raise HTTPException(422, "不能置顶审阅草稿")
    count = db.scalar(
        select(func.count())
        .select_from(DiscussionComment)
        .where(
            DiscussionComment.item_id == item.id,
            DiscussionComment.pinned.is_(True),
            DiscussionComment.deleted_at.is_(None),
        )
    )
    if pinned and not comment.pinned and count >= 10:
        raise HTTPException(409, "最多置顶 10 条评论")
    comment.pinned = pinned
    return {"ok": True}


@router.get("/{repository_id}/items/{number}/history")
def history(
    repository_id: int,
    number: int,
    actor: Authenticated,
    db: Database,
    comment: Annotated[int | None, Query(gt=0)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    if comment:
        record = db.get(DiscussionComment, comment)
        if (
            not record
            or record.item_id != item.id
            or record.deleted_at is not None
            or (is_draft(db, record) and record.author_id != actor.user.id)
        ):
            raise HTTPException(404, "评论历史不存在")
    query = select(ContentRevision).where(
        ContentRevision.item_id == item.id, ContentRevision.comment_id == comment
    )
    return {
        "items": [
            {
                "version": revision.version,
                "title": revision.title,
                "body": revision.body,
                "created_at": revision.created_at,
                "editor": service.person(db, revision.author_id),
            }
            for revision in db.scalars(
                query.order_by(ContentRevision.id.desc()).offset((page - 1) * 20).limit(20)
            )
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "page": page,
        "page_size": 20,
    }


@router.put("/{repository_id}/items/{number}/subscription")
def subscription(
    repository_id: int, number: int, body: SubscribeInput, actor: Authenticated, db: Database
):
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    # Subscriptions are personal preferences and can change on an archived repository.
    db.execute(select(WorkItem).where(WorkItem.id == item.id).with_for_update())
    service.subscribe(db, item.id, actor.user.id, watching=body.watching, email=body.email)
    return body.model_dump()


@router.put("/{repository_id}/items/{number}/reaction")
def reaction(repository_id: int, number: int, body: ReactInput, actor: Authenticated, db: Database):
    repo, member, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    service.conversation(repo, member, item, actor.user.id)
    if body.comment_id:
        comment = db.get(DiscussionComment, body.comment_id)
        if (
            not comment
            or comment.item_id != item.id
            or comment.deleted_at is not None
            or is_draft(db, comment)
        ):
            raise HTTPException(404, "评论不存在")
    key = f"comment:{body.comment_id}" if body.comment_id else f"item:{item.id}"
    old = db.scalar(
        select(Reaction).where(
            Reaction.target_key == key,
            Reaction.user_id == actor.user.id,
            Reaction.emoji == body.emoji,
        )
    )
    if body.active and not old:
        db.add(
            Reaction(
                item_id=item.id,
                comment_id=body.comment_id,
                target_key=key,
                user_id=actor.user.id,
                emoji=body.emoji,
            )
        )
    elif old and not body.active:
        db.delete(old)
    db.flush()
    return service.reactions(db, item.id, actor.user.id, body.comment_id)


@router.post("/{repository_id}/items/{number}/relations", status_code=201)
def relation(
    repository_id: int, number: int, body: RelationInput, actor: Authenticated, db: Database
):
    service.graph_lock(db)
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    items = service.locked_items(db, [item.id, body.target_id], actor.user.id)
    target = items[body.target_id]
    entry = service.connect(db, item, target, body.kind, body.position)
    db.flush()
    for source, other in ((item, target), (target, item)):
        service.event(
            db,
            source,
            actor.user.id,
            "relation_added",
            {"related_item_id": other.id, "kind": body.kind},
        )
    return {"id": entry.id}


@router.delete("/{repository_id}/items/{number}/relations/{relation_id}")
def detach(repository_id: int, number: int, relation_id: int, actor: Authenticated, db: Database):
    service.graph_lock(db)
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    entry = db.get(ItemRelation, relation_id)
    if not entry or item.id not in {entry.source_id, entry.target_id}:
        raise HTTPException(404, "关联不存在")
    items = service.locked_items(db, [entry.source_id, entry.target_id], actor.user.id)
    for source_id, target_id in (
        (entry.source_id, entry.target_id),
        (entry.target_id, entry.source_id),
    ):
        service.event(
            db,
            items[source_id],
            actor.user.id,
            "relation_removed",
            {"related_item_id": target_id, "kind": entry.kind},
        )
    db.delete(entry)
    return {"ok": True}


@router.put("/{repository_id}/items/{number}/parent")
def reparent(
    repository_id: int, number: int, body: RelationInput, actor: Authenticated, db: Database
):
    service.graph_lock(db)
    _, _, item = service.get_item(db, repository_id, number, actor.user.id)
    old = db.scalar(
        select(ItemRelation).where(ItemRelation.target_id == item.id, ItemRelation.kind == "parent")
    )
    ids = [item.id, body.target_id] + ([old.source_id] if old else [])
    items = service.locked_items(db, list(set(ids)), actor.user.id)
    if old:
        db.delete(old)
        db.flush()
    entry = service.connect(db, items[body.target_id], item, "parent", body.position)
    db.flush()
    service.event(db, item, actor.user.id, "reparented", {"related_item_id": body.target_id})
    return {"id": entry.id}


@router.post("/{repository_id}/items/{number}/transfer")
def transfer(
    repository_id: int, number: int, body: TransferItem, actor: Authenticated, db: Database
):
    service.graph_lock(db)
    for rid in sorted({repository_id, body.repository_id}):
        service.repository(db, rid, actor.user.id, write=True, manage=True)
    _, _, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
    if item.kind != "issue" or repository_id == body.repository_id:
        raise HTTPException(422, "请选择另一个仓库移交 Issue")
    target = db.get(Repository, body.repository_id)
    db.add(ItemRedirect(repository_id=repository_id, number=item.number, item_id=item.id))
    # Relative references retain the repository in which each body was written.
    item.body_repository_id = item.body_repository_id or item.repository_id
    for comment in db.scalars(
        select(DiscussionComment).where(DiscussionComment.item_id == item.id)
    ):
        comment.body_repository_id = comment.body_repository_id or item.repository_id
    item.repository_id = target.id
    item.number = service.next_number(target)
    item.version += 1
    # Match metadata by kind/name in the destination; never silently create its catalog.
    associations = list(db.scalars(select(ItemCatalog).where(ItemCatalog.item_id == item.id)))
    for association in associations:
        old = db.get(RepositoryCatalog, association.catalog_id)
        replacement = db.scalar(
            select(RepositoryCatalog).where(
                RepositoryCatalog.repository_id == target.id,
                RepositoryCatalog.kind == old.kind,
                RepositoryCatalog.name == old.name,
            )
        )
        if replacement:
            association.catalog_id = replacement.id
        else:
            db.delete(association)
    for assignment in db.scalars(select(ItemAssignee).where(ItemAssignee.item_id == item.id)):
        if not repository_access(db, target, assignment.user_id):
            db.delete(assignment)
    for attachment in db.scalars(
        select(DiscussionAttachment).where(DiscussionAttachment.item_id == item.id)
    ):
        attachment.repository_id = target.id
    service.event(db, item, actor.user.id, "transferred")
    return service.item_out(db, item, actor.user.id, detail=True)


@router.get("/{repository_id}/catalog")
def catalog(repository_id: int, actor: Authenticated, db: Database):
    service.repository(db, repository_id, actor.user.id)
    return [
        service.catalog_out(entry)
        for entry in db.scalars(
            select(RepositoryCatalog)
            .where(RepositoryCatalog.repository_id == repository_id)
            .order_by(RepositoryCatalog.kind, RepositoryCatalog.name)
        )
    ]


def save_catalog(db, repository_id, body, catalog_id=None):
    duplicate = db.scalar(
        select(RepositoryCatalog).where(
            RepositoryCatalog.repository_id == repository_id,
            RepositoryCatalog.kind == body.kind,
            RepositoryCatalog.name == body.name,
        )
    )
    if duplicate and duplicate.id != catalog_id:
        raise HTTPException(409, "已存在同名条目")
    if catalog_id:
        record = db.get(RepositoryCatalog, catalog_id)
        if not record or record.repository_id != repository_id or record.kind != body.kind:
            raise HTTPException(404, "条目不存在")
    else:
        record = RepositoryCatalog(repository_id=repository_id, kind=body.kind)
        db.add(record)
    record.name, record.description = body.name, body.description
    record.data = body.data.model_dump(mode="json")
    db.flush()
    return service.catalog_out(record)


@router.post("/{repository_id}/catalog", status_code=201)
def add_catalog(repository_id: int, body: CatalogInput, actor: Authenticated, db: Database):
    service.repository(db, repository_id, actor.user.id, write=True, manage=True)
    return save_catalog(db, repository_id, body)


@router.put("/{repository_id}/catalog/{catalog_id}")
def edit_catalog(
    repository_id: int, catalog_id: int, body: CatalogInput, actor: Authenticated, db: Database
):
    service.repository(db, repository_id, actor.user.id, write=True, manage=True)
    return save_catalog(db, repository_id, body, catalog_id)


@router.delete("/{repository_id}/catalog/{catalog_id}")
def remove_catalog(repository_id: int, catalog_id: int, actor: Authenticated, db: Database):
    service.repository(db, repository_id, actor.user.id, write=True, manage=True)
    entry = db.get(RepositoryCatalog, catalog_id)
    if not entry or entry.repository_id != repository_id:
        raise HTTPException(404, "条目不存在")
    db.delete(entry)
    return {"ok": True}


@router.get("/{repository_id}/saved-filters")
def filters(repository_id: int, actor: Authenticated, db: Database):
    service.repository(db, repository_id, actor.user.id)
    return [
        {"id": entry.id, "name": entry.name, "query": entry.query}
        for entry in db.scalars(
            select(SavedFilter)
            .where(SavedFilter.repository_id == repository_id, SavedFilter.user_id == actor.user.id)
            .order_by(SavedFilter.id)
        )
    ]


@router.post("/{repository_id}/saved-filters", status_code=201)
def save_filter(repository_id: int, body: FilterInput, actor: Authenticated, db: Database):
    service.repository(db, repository_id, actor.user.id, write=True)
    entry = db.scalar(
        select(SavedFilter).where(
            SavedFilter.repository_id == repository_id,
            SavedFilter.user_id == actor.user.id,
            SavedFilter.name == body.name,
        )
    )
    if entry is None:
        entry = SavedFilter(repository_id=repository_id, user_id=actor.user.id, name=body.name)
        db.add(entry)
    entry.query = body.query
    db.flush()
    return {"id": entry.id, "name": entry.name, "query": entry.query}


@router.delete("/{repository_id}/saved-filters/{filter_id}")
def remove_filter(repository_id: int, filter_id: int, actor: Authenticated, db: Database):
    service.repository(db, repository_id, actor.user.id)
    db.execute(
        delete(SavedFilter).where(
            SavedFilter.id == filter_id,
            SavedFilter.repository_id == repository_id,
            SavedFilter.user_id == actor.user.id,
        )
    )
    return {"ok": True}


@router.post("/{repository_id}/markdown/resolve")
def markdown_references(
    repository_id: int, body: MarkdownInput, request: Request, actor: Authenticated, db: Database
):
    repo, _ = service.repository(db, repository_id, actor.user.id)
    if body.item_id:
        item = db.get(WorkItem, body.item_id)
        if not service.visible(db, item, actor.user.id) or item.repository_id != repo.id:
            raise HTTPException(404, "记录不存在")
        source = item
        if body.comment_id:
            source = db.get(DiscussionComment, body.comment_id)
            if (
                not source
                or source.item_id != item.id
                or source.deleted_at is not None
                or (is_draft(db, source) and source.author_id != actor.user.id)
            ):
                raise HTTPException(404, "评论不存在")
        if source.body == body.body and source.body_repository_id:
            repo = db.get(Repository, source.body_repository_id)
    settings = request.app.state.settings
    return refs.resolve(
        db, repo, body.body, actor.user.id, settings.git_repositories_root, settings.allowed_origins
    )
