"""Private discussion attachments, commit pages and personal notifications."""

from datetime import timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import and_, func, or_, select

from devops_server import collaboration_git as git
from devops_server import collaboration_references as refs
from devops_server import collaboration_service as service
from devops_server.collaboration_models import (
    DiscussionAttachment,
    ItemEvent,
    Notification,
    WorkItem,
)
from devops_server.dependencies import Database
from devops_server.dependencies import RepositoryAuthenticated as Authenticated
from devops_server.models import Repository, RepositoryMember

router = APIRouter(prefix="/api", tags=["collaboration-content"])
MAX_ATTACHMENT = 10 * 1024 * 1024


def attachment_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


@router.post("/repositories/{repository_id}/attachments", status_code=201)
async def upload(
    repository_id: int,
    request: Request,
    actor: Authenticated,
    db: Database,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    number: Annotated[int | None, Query(gt=0)] = None,
):
    if "/" in filename or "\\" in filename or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "文件名无效")
    # Read a bounded stream before taking the repository lock.
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_ATTACHMENT:
        raise HTTPException(413, "附件不能超过 10 MiB")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_ATTACHMENT:
            raise HTTPException(413, "附件不能超过 10 MiB")
        content.extend(chunk)
    if not content:
        raise HTTPException(422, "不能上传空文件")
    repo, member = service.repository(db, repository_id, actor.user.id, write=True)
    item = None
    if number:
        _, _, item = service.get_item(db, repository_id, number, actor.user.id, write=True)
        service.conversation(repo, member, item, actor.user.id)
    existing = db.scalar(
        select(func.coalesce(func.sum(DiscussionAttachment.size), 0)).where(
            DiscussionAttachment.repository_id == repository_id,
            DiscussionAttachment.item_id.is_(None),
            DiscussionAttachment.author_id == actor.user.id,
        )
    )
    if existing + len(content) > 100 * 1024 * 1024:
        raise HTTPException(413, "此讨论的附件总量不能超过 100 MiB")
    attachment = DiscussionAttachment(
        repository_id=repo.id,
        item_id=None,  # Private to the uploader until a body/comment is published.
        author_id=actor.user.id,
        filename=filename,
        media_type=attachment_type(content),
        size=len(content),
        content=bytes(content),
    )
    db.add(attachment)
    db.flush()
    return {
        "id": attachment.id,
        "filename": filename,
        "url": f"/api/attachments/{attachment.id}",
        "media_type": attachment.media_type,
        "size": attachment.size,
    }


@router.get("/attachments/{attachment_id}")
def attachment(attachment_id: int, actor: Authenticated, db: Database):
    record = db.get(DiscussionAttachment, attachment_id)
    if record is None:
        raise HTTPException(404, "附件不存在")
    service.repository(db, record.repository_id, actor.user.id)
    if record.item_id:
        if not service.visible(db, db.get(WorkItem, record.item_id), actor.user.id):
            raise HTTPException(404, "附件不存在")
    elif record.author_id != actor.user.id or record.created_at < service.now() - timedelta(days=1):
        raise HTTPException(404, "附件不存在")
    disposition = "inline" if record.media_type.startswith("image/") else "attachment"
    return Response(
        record.content,
        media_type=record.media_type,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename*=UTF-8''" + quote(record.filename, safe="")
            ),
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


def notification_query(user_id):
    return (
        select(Notification)
        .join(WorkItem, Notification.item_id == WorkItem.id)
        .join(Repository, WorkItem.repository_id == Repository.id)
        .outerjoin(
            RepositoryMember,
            and_(
                RepositoryMember.repository_id == Repository.id, RepositoryMember.user_id == user_id
            ),
        )
        .where(
            Notification.user_id == user_id,
            WorkItem.deleted_at.is_(None),
            Repository.deleted_at.is_(None),
            or_(Repository.owner_id == user_id, RepositoryMember.id.is_not(None)),
        )
    )


@router.get("/me/notifications")
def notifications(
    actor: Authenticated,
    db: Database,
    unread: bool = False,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
):
    base = notification_query(actor.user.id)
    query = base.where(Notification.read_at.is_(None)) if unread else base
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    unread_count = db.scalar(
        select(func.count()).select_from(base.where(Notification.read_at.is_(None)).subquery())
    )
    items = []
    for record in db.scalars(
        query.order_by(Notification.id.desc()).offset((page - 1) * 30).limit(30)
    ):
        item = db.get(WorkItem, record.item_id)
        event = db.get(ItemEvent, record.event_id)
        url = service.item_url(db, item)
        if event.data.get("comment_id"):
            url += f"#issuecomment-{event.data['comment_id']}"
        items.append(
            {
                "id": record.id,
                "item": service.summary(db, item),
                "action": event.action,
                "actor": service.person(db, event.actor_id),
                "url": url,
                "created_at": record.created_at,
                "read_at": record.read_at,
            }
        )
    return {
        "items": items,
        "total": total,
        "unread_count": unread_count,
        "page": page,
        "page_size": 30,
    }


@router.put("/me/notifications/read")
def read_all(actor: Authenticated, db: Database):
    for entry in db.scalars(
        notification_query(actor.user.id).where(Notification.read_at.is_(None))
    ):
        entry.read_at = service.now()
    return {"ok": True}


@router.put("/me/notifications/{notification_id}/read")
def read_notification(notification_id: int, actor: Authenticated, db: Database, read: bool = True):
    record = db.scalar(notification_query(actor.user.id).where(Notification.id == notification_id))
    if record is None:
        raise HTTPException(404, "通知不存在")
    record.read_at = service.now() if read else None
    return {"ok": True}


@router.get("/repositories/{repository_id}/commits/{commit}")
def commit_detail(
    repository_id: int,
    commit: str,
    request: Request,
    actor: Authenticated,
    db: Database,
    path: Annotated[str | None, Query(max_length=4096)] = None,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
):
    service.repository(db, repository_id, actor.user.id)
    root = request.app.state.settings.git_repositories_root
    try:
        sha = git.resolve_commit(root, repository_id, commit)
        with git.isolated(root, repository_id) as (scratch, _):
            raw = git.text_git(
                scratch,
                "show",
                "--no-show-signature",
                "-s",
                "--format=%H%x00%P%x00%an%x00%aI%x00%B",
                sha,
            )
            _, parent_text, author, date, message = raw.split("\0", 4)
            parents = parent_text.split()
            if parents:
                base = parents[0]
            else:
                tree = git.text_git(scratch, "mktree", content=b"")
                base = git.commit_tree(scratch, tree, [], "Empty comparison tree")
                # compare needs the empty commit to remain in this isolated object store.
                # Initial commits use diff-tree directly below instead of publishing an object.
                patch = git.run(
                    scratch,
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-color",
                    "--unified=3",
                    base,
                    sha,
                    "--",
                    *([path] if path else []),
                    limit=2 * 1024 * 1024,
                )[1].decode("utf-8", errors="replace")
        comparison = (
            git.compare(root, repository_id, base, sha, path=path, page=page, commit_view=True)
            if parents
            else {"patch": patch, "files": [], "total": 0, "page": 1, "page_size": 100}
        )
        return {
            "sha": sha,
            "parents": parents,
            "author": author,
            "date": date,
            "message": message,
            "comparison": comparison,
            "backlinks": refs.backlinks(db, actor.user.id, repository_id=repository_id, sha=sha),
        }
    except git.GitFailure as error:
        raise git.api_failure(error) from None
