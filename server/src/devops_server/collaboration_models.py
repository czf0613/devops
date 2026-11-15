"""Repository collaboration records. Content and audit history survive soft deletion."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from devops_server.models import Base, CreatedRecord, IdentityRecord, UpdatedRecord


class WorkItem(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_work_items_repository_number"),
        CheckConstraint("number > 0 AND version > 0", name="positive_numbers"),
        CheckConstraint("kind IN ('issue', 'pull')", name="valid_kind"),
        CheckConstraint("state IN ('open', 'closed', 'merged')", name="valid_state"),
        CheckConstraint("kind = 'pull' OR state <> 'merged'", name="issue_not_merged"),
        CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('completed', 'not_planned', 'duplicate')",
            name="valid_close_reason",
        ),
        Index("ix_work_items_list", "repository_id", "kind", "state", "updated_at"),
    )

    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="RESTRICT"))
    number: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, server_default="")
    body_repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(Text, server_default="open")
    close_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ItemRedirect(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "item_redirects"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_item_redirects_address"),
    )
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="RESTRICT"))
    number: Mapped[int] = mapped_column(BigInteger)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))


class RepositoryCatalog(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    """Validated repository labels, types, milestones and issue/PR templates."""

    __tablename__ = "repository_catalog"
    __table_args__ = (
        UniqueConstraint("repository_id", "kind", "name", name="uq_repository_catalog_name"),
        CheckConstraint("kind IN ('label', 'type', 'milestone', 'template')", name="valid_kind"),
    )
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class ItemCatalog(IdentityRecord, Base):
    __tablename__ = "item_catalog"
    __table_args__ = (UniqueConstraint("item_id", "catalog_id", name="uq_item_catalog_pair"),)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    catalog_id: Mapped[int] = mapped_column(ForeignKey("repository_catalog.id", ondelete="CASCADE"))


class ItemAssignee(IdentityRecord, Base):
    __tablename__ = "item_assignees"
    __table_args__ = (UniqueConstraint("item_id", "user_id", name="uq_item_assignees_pair"),)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class ItemRelation(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "item_relations"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "kind", name="uq_item_relations_edge"),
        CheckConstraint("source_id <> target_id", name="no_self_relation"),
        CheckConstraint(
            "kind IN ('parent', 'blocks', 'duplicate', 'development')", name="valid_kind"
        ),
        Index(
            "uq_item_relations_parent",
            "target_id",
            unique=True,
            postgresql_where=text("kind = 'parent'"),
        ),
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    target_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class PullRequest(IdentityRecord, UpdatedRecord, Base):
    __tablename__ = "pull_requests"
    item_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), unique=True
    )
    base_ref: Mapped[str] = mapped_column(Text)
    head_ref: Mapped[str] = mapped_column(Text)
    base_sha: Mapped[str] = mapped_column(Text)
    head_sha: Mapped[str] = mapped_column(Text)
    draft: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    merged_sha: Mapped[str | None] = mapped_column(Text)
    merged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    merge_method: Mapped[str | None] = mapped_column(Text)


class PullReviewer(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "pull_reviewers"
    __table_args__ = (UniqueConstraint("item_id", "user_id", name="uq_pull_reviewers_pair"),)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class PullReview(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "pull_reviews"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'commented', 'approved', 'changes_requested', 'dismissed')",
            name="valid_state",
        ),
        Index(
            "uq_pull_reviews_pending",
            "item_id",
            "author_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    head_sha: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    body: Mapped[str] = mapped_column(Text, server_default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewThread(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "review_threads"
    __table_args__ = (
        CheckConstraint("side IN ('left', 'right')", name="valid_side"),
        CheckConstraint("start_line > 0 AND end_line >= start_line", name="valid_lines"),
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    path: Mapped[str] = mapped_column(Text)
    base_sha: Mapped[str] = mapped_column(Text)
    head_sha: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(BigInteger)
    end_line: Mapped[int] = mapped_column(BigInteger)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscussionComment(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "discussion_comments"
    __table_args__ = (Index("ix_discussion_comments_timeline", "item_id", "id"),)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    body: Mapped[str] = mapped_column(Text)
    body_repository_id: Mapped[int | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_threads.id", ondelete="RESTRICT")
    )
    review_id: Mapped[int | None] = mapped_column(
        ForeignKey("pull_reviews.id", ondelete="RESTRICT")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentRevision(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "content_revisions"
    item_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), index=True
    )
    comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("discussion_comments.id", ondelete="RESTRICT"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)


class ItemEvent(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "item_events"
    item_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class ContentReference(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "content_references"
    __table_args__ = (
        UniqueConstraint("source_key", "target_key", "kind", name="uq_content_references_edge"),
        CheckConstraint("kind IN ('mention', 'closing')", name="valid_kind"),
        Index("ix_content_references_target", "target_item_id", "active"),
    )
    source_key: Mapped[str] = mapped_column(Text)
    source_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT")
    )
    source_comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("discussion_comments.id", ondelete="RESTRICT")
    )
    source_repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT")
    )
    source_sha: Mapped[str | None] = mapped_column(Text)
    target_key: Mapped[str] = mapped_column(Text)
    target_repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT")
    )
    target_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT")
    )
    target_sha: Mapped[str | None] = mapped_column(Text)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Reaction(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "discussion_reactions"
    __table_args__ = (
        UniqueConstraint("target_key", "user_id", "emoji", name="uq_discussion_reactions_person"),
        CheckConstraint(
            "emoji IN ('+1', '-1', 'laugh', 'hooray', 'confused', 'heart', 'rocket', 'eyes')",
            name="valid_emoji",
        ),
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("discussion_comments.id", ondelete="CASCADE")
    )
    target_key: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    emoji: Mapped[str] = mapped_column(Text)


class Subscription(IdentityRecord, Base):
    __tablename__ = "item_subscriptions"
    __table_args__ = (UniqueConstraint("item_id", "user_id", name="uq_item_subscriptions_person"),)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    watching: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    email: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Notification(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "collaboration_notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_collaboration_notifications_event"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    event_id: Mapped[int] = mapped_column(ForeignKey("item_events.id", ondelete="RESTRICT"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_state: Mapped[str] = mapped_column(Text, server_default="disabled")


class DiscussionAttachment(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "discussion_attachments"
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT"), index=True
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    filename: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class SavedFilter(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "saved_issue_filters"
    __table_args__ = (
        UniqueConstraint("repository_id", "user_id", "name", name="uq_saved_issue_filters_name"),
    )
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    query: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ViewedFile(IdentityRecord, Base):
    __tablename__ = "pull_viewed_files"
    __table_args__ = (
        UniqueConstraint("item_id", "user_id", "path", name="uq_pull_viewed_files_path"),
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    path: Mapped[str] = mapped_column(Text)
    head_sha: Mapped[str] = mapped_column(Text)


class PullCheck(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "pull_checks"
    __table_args__ = (
        UniqueConstraint("item_id", "head_sha", "name", name="uq_pull_checks_head_name"),
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'failure', 'cancelled')",
            name="valid_status",
        ),
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    head_sha: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    details: Mapped[str] = mapped_column(Text, server_default="")
    external_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="manual")
    ci_job_id: Mapped[int | None] = mapped_column(ForeignKey("ci_jobs.id", ondelete="RESTRICT"))


class CollaborationJob(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "collaboration_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'complete', 'failed')", name="valid_status"
        ),
        Index("ix_collaboration_jobs_claim", "status", "available_at"),
    )
    key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class MergeIntent(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "merge_intents"
    __table_args__ = (
        Index(
            "uq_merge_intents_active",
            "item_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'prepared', 'applied')"),
        ),
        CheckConstraint(
            "state IN ('pending', 'prepared', 'applied', 'complete', 'failed')", name="valid_state"
        ),
        CheckConstraint("method IN ('merge', 'squash', 'rebase')", name="valid_method"),
        CheckConstraint("(session_id IS NULL) <> (oauth_grant_id IS NULL)", name="one_credential"),
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("work_items.id", ondelete="RESTRICT"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="RESTRICT")
    )
    oauth_grant_id: Mapped[int | None] = mapped_column(
        ForeignKey("git_oauth_grants.id", ondelete="RESTRICT")
    )
    base_sha: Mapped[str] = mapped_column(Text)
    head_sha: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    result_sha: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    delete_branch: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
