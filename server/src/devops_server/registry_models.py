"""Registry ownership and purpose-limited, revocable Docker credentials."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from devops_server.models import Base, CreatedRecord, IdentityRecord, UpdatedRecord


class RegistryNamespace(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "registry_namespaces"
    __table_args__ = (
        CheckConstraint(
            r"name ~ '^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$' AND length(name) <= 63",
            name="name_format",
        ),
    )

    # Names remain reserved even while unbound or after the owning repo is deleted.
    name: Mapped[str] = mapped_column(Text, unique=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistryCredential(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "registry_credentials"
    __table_args__ = (
        CheckConstraint("access IN ('read', 'write')", name="valid_access"),
        CheckConstraint("length(name) BETWEEN 1 AND 80", name="name_length"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(Text)
    token_digest: Mapped[str] = mapped_column(Text, unique=True, deferred=True)
    access: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistryGCWorker(IdentityRecord, Base):
    __tablename__ = "registry_gc_worker"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistryGCRun(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "registry_gc_runs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')", name="valid_state"
        ),
        Index(
            "uq_registry_gc_runs_active",
            text("(true)"),
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
        ),
    )

    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    session_id: Mapped[int] = mapped_column(ForeignKey("user_sessions.id", ondelete="RESTRICT"))
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    phase: Mapped[str] = mapped_column(Text, server_default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    before_bytes: Mapped[int | None] = mapped_column(BigInteger)
    after_bytes: Mapped[int | None] = mapped_column(BigInteger)
    summary: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)
