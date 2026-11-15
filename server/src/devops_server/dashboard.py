"""Small cross-repository work lists; all queries apply current repository ACL in SQL."""

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import aliased

from devops_server.collaboration_models import (
    DiscussionComment,
    ItemAssignee,
    ItemEvent,
    PullRequest,
    PullReview,
    PullReviewer,
    WorkItem,
)
from devops_server.dependencies import Authenticated, Database
from devops_server.models import Repository, User
from devops_server.repositories import visible_repositories

router = APIRouter(prefix="/api/me/dashboard", tags=["dashboard"])
Kind = Literal["issue", "pull"]
Scope = Literal["related", "authored", "assigned", "review_requested"]
PageNumber = Annotated[int, Query(ge=1, le=100000)]
PageSize = Annotated[int, Query(ge=1, le=25)]


def public_comment():
    published_review = select(PullReview.id).where(
        PullReview.id == DiscussionComment.review_id,
        PullReview.state != "pending",
        PullReview.submitted_at.is_not(None),
    )
    return and_(
        DiscussionComment.deleted_at.is_(None),
        or_(DiscussionComment.review_id.is_(None), published_review.exists()),
    )


def item_scope(user_id: int, scope: Scope):
    authored = WorkItem.author_id == user_id
    assigned = select(ItemAssignee.id).where(
        ItemAssignee.item_id == WorkItem.id, ItemAssignee.user_id == user_id
    )
    requested = select(PullReviewer.id).where(
        PullReviewer.item_id == WorkItem.id, PullReviewer.user_id == user_id
    )
    if scope == "authored":
        return authored
    if scope == "assigned":
        return assigned.exists()
    if scope == "review_requested":
        completed = (
            select(PullReview.id)
            .where(
                PullReview.item_id == WorkItem.id,
                PullReview.author_id == user_id,
                PullReview.head_sha == PullRequest.head_sha,
                PullReview.state.in_(["approved", "changes_requested"]),
                PullReview.submitted_at >= PullReviewer.created_at,
            )
            .correlate(WorkItem, PullRequest, PullReviewer)
        )
        return and_(
            WorkItem.state == "open",
            ~authored,
            PullRequest.draft.is_(False),
            Repository.archived.is_(False),
            requested.where(~completed.exists()).exists(),
        )
    commented = select(DiscussionComment.id).where(
        DiscussionComment.item_id == WorkItem.id,
        DiscussionComment.author_id == user_id,
        public_comment(),
    )
    reviewed = select(PullReview.id).where(
        PullReview.item_id == WorkItem.id,
        PullReview.author_id == user_id,
        PullReview.state != "pending",
        PullReview.submitted_at.is_not(None),
    )
    return or_(
        authored, assigned.exists(), requested.exists(), commented.exists(), reviewed.exists()
    )


def item_query(user_id: int):
    owner = aliased(User)
    author = aliased(User)
    return (
        select(
            WorkItem.id,
            WorkItem.repository_id,
            WorkItem.number,
            WorkItem.kind,
            WorkItem.title,
            WorkItem.state,
            WorkItem.close_reason,
            WorkItem.updated_at,
            Repository.name.label("repository_name"),
            Repository.archived,
            owner.username.label("owner_name"),
            author.id.label("author_id"),
            author.username.label("author_username"),
            author.display_name.label("author_name"),
            PullRequest.draft,
        )
        .join(Repository, Repository.id == WorkItem.repository_id)
        .join(owner, owner.id == Repository.owner_id)
        .join(author, author.id == WorkItem.author_id)
        .outerjoin(PullRequest, PullRequest.item_id == WorkItem.id)
        .where(
            WorkItem.deleted_at.is_(None),
            WorkItem.repository_id.in_(
                visible_repositories(user_id).with_only_columns(Repository.id)
            ),
        )
    )


def repository_summary(repository_id, owner, name, archived):
    return {
        "id": repository_id,
        "full_name": f"{owner}/{name}",
        "url": f"/repositories/{quote(owner, safe='')}/{quote(name, safe='')}",
        "archived": archived,
    }


def item_summary(row):
    repo = repository_summary(row.repository_id, row.owner_name, row.repository_name, row.archived)
    return {
        "id": row.id,
        "repository_id": row.repository_id,
        "number": row.number,
        "kind": row.kind,
        "title": row.title,
        "state": row.state,
        "close_reason": row.close_reason,
        "updated_at": row.updated_at,
        "url": f"{repo['url']}/{'pr' if row.kind == 'pull' else 'issue'}/{row.number}",
        "repository": repo,
        "draft": bool(row.draft),
        "author": {
            "id": row.author_id,
            "username": row.author_username,
            "display_name": row.author_name,
        },
    }


@router.get("/items")
def items(
    actor: Authenticated,
    db: Database,
    kind: Kind,
    scope: Scope = "related",
    state: Literal["open", "closed", "merged", "all"] = "open",
    q: Annotated[str, Query(max_length=100)] = "",
    page: PageNumber = 1,
    page_size: PageSize = 5,
):
    if (kind == "issue" and scope == "review_requested") or (
        kind == "pull" and scope == "assigned"
    ):
        raise HTTPException(422, "筛选条件不适用于此列表")
    statement = item_query(actor.user.id).where(
        WorkItem.kind == kind, item_scope(actor.user.id, scope)
    )
    if state != "all":
        statement = statement.where(WorkItem.state == state)
    if q.strip():
        statement = statement.where(WorkItem.title.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(
        statement.order_by(WorkItem.updated_at.desc(), WorkItem.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [item_summary(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/activity")
def activity(actor: Authenticated, db: Database, page_size: PageSize = 6):
    # Only publish a small set of collaboration events. Never forward raw event data
    # (which may contain links to inaccessible items or private review drafts).
    comment = (
        select(DiscussionComment.id)
        .where(
            cast(DiscussionComment.id, String) == ItemEvent.data["comment_id"].as_string(),
            DiscussionComment.item_id == WorkItem.id,
            public_comment(),
        )
        .correlate(ItemEvent, WorkItem)
    )
    review = (
        select(PullReview.id)
        .where(
            cast(PullReview.id, String) == ItemEvent.data["review_id"].as_string(),
            PullReview.item_id == WorkItem.id,
            PullReview.state != "pending",
            PullReview.submitted_at.is_not(None),
        )
        .correlate(ItemEvent, WorkItem)
    )
    person = aliased(User)
    statement = (
        item_query(actor.user.id)
        .join(ItemEvent, ItemEvent.item_id == WorkItem.id)
        .outerjoin(person, person.id == ItemEvent.actor_id)
        .add_columns(
            ItemEvent.id.label("event_id"),
            ItemEvent.action,
            ItemEvent.created_at,
            person.id.label("actor_id"),
            person.username.label("actor_username"),
            person.display_name.label("actor_name"),
        )
        .where(
            or_(
                ItemEvent.action.in_(["created", "closed", "reopened", "merged"]),
                and_(ItemEvent.action == "commented", comment.exists()),
                and_(ItemEvent.action == "reviewed", review.exists()),
            )
        )
        .order_by(ItemEvent.created_at.desc(), ItemEvent.id.desc())
        .limit(page_size)
    )
    return {
        "items": [
            {
                "id": row.event_id,
                "action": row.action,
                "created_at": row.created_at,
                "item": item_summary(row),
                "actor": {
                    "id": row.actor_id,
                    "username": row.actor_username,
                    "display_name": row.actor_name,
                }
                if row.actor_id
                else None,
            }
            for row in db.execute(statement)
        ]
    }


@router.get("/repositories")
def repositories(
    actor: Authenticated,
    db: Database,
    recent: Annotated[list[int] | None, Query(max_length=20)] = None,
    q: Annotated[str, Query(max_length=100)] = "",
    page_size: PageSize = 8,
):
    if any(value <= 0 or value > 2**63 - 1 for value in (recent or [])):
        raise HTTPException(422, "仓库编号无效")
    recent_ids = list(dict.fromkeys(recent or []))
    statement = visible_repositories(actor.user.id).join(User, User.id == Repository.owner_id)
    if q.strip():
        statement = statement.where(
            (User.username + "/" + Repository.name).icontains(q.strip(), autoescape=True)
        )
    order = [Repository.updated_at.desc(), Repository.id.desc()]
    if recent_ids and not q.strip():
        order.insert(
            0,
            case(
                {value: index for index, value in enumerate(recent_ids)},
                value=Repository.id,
                else_=len(recent_ids),
            ),
        )
    rows = db.execute(
        statement.with_only_columns(
            Repository.id, Repository.name, Repository.archived, User.username
        )
        .order_by(*order)
        .limit(page_size)
    )
    return {
        "items": [repository_summary(row.id, row.username, row.name, row.archived) for row in rows]
    }
