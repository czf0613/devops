"""Persistent CI state. Connections and executors never own authoritative job state."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from devops_server.models import Base, CreatedRecord, IdentityRecord, UpdatedRecord


class CIWorker(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "ci_workers"
    name: Mapped[str] = mapped_column(Text, unique=True)
    pool: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    draining: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    token_digest: Mapped[str | None] = mapped_column(Text, unique=True, deferred=True)
    registration_digest: Mapped[str | None] = mapped_column(Text, unique=True, deferred=True)
    registration_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class CIRepository(CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "ci_repositories"
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT"), primary_key=True
    )
    authorized_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    pools: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))


class CIRun(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "ci_runs"
    __table_args__ = (Index("ix_ci_runs_repository_created", "repository_id", "id"),)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="RESTRICT"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    event_key: Mapped[str] = mapped_column(Text, unique=True)
    event: Mapped[str] = mapped_column(Text)
    ref: Mapped[str] = mapped_column(Text)
    sha: Mapped[str] = mapped_column(Text)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("work_items.id", ondelete="RESTRICT"))
    rerun_of: Mapped[int | None] = mapped_column(ForeignKey("ci_runs.id", ondelete="RESTRICT"))
    workflow: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    error: Mapped[str] = mapped_column(Text, server_default="")


class CIJob(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "ci_jobs"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_ci_jobs_run_name"),)
    run_id: Mapped[int] = mapped_column(ForeignKey("ci_runs.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(Text, server_default="queued", index=True)


class CIAttempt(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "ci_attempts"
    __table_args__ = (
        CheckConstraint(
            "state IN ('assigned','starting','running','success','failure',"
            "'cancelled','timed_out','lost')",
            name="valid_state",
        ),
        UniqueConstraint("job_id", "number", name="uq_ci_attempts_job_number"),
        Index(
            "uq_ci_attempts_active_worker",
            "worker_id",
            unique=True,
            postgresql_where=text("state IN ('assigned','starting','running')"),
        ),
        Index(
            "uq_ci_attempts_active_job",
            "job_id",
            unique=True,
            postgresql_where=text("state IN ('assigned','starting','running')"),
        ),
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("ci_jobs.id", ondelete="RESTRICT"), index=True)
    worker_id: Mapped[int] = mapped_column(
        ForeignKey("ci_workers.id", ondelete="RESTRICT"), index=True
    )
    number: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text, server_default="assigned")
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    grant_id: Mapped[int | None] = mapped_column(
        ForeignKey("git_oauth_grants.id", ondelete="RESTRICT"), unique=True
    )
    log_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    log_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    error: Mapped[str] = mapped_column(Text, server_default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CIArtifact(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "ci_artifacts"
    __table_args__ = (UniqueConstraint("attempt_id", "name", name="uq_ci_artifacts_attempt_name"),)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("ci_attempts.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
