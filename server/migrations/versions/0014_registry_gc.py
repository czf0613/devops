"""Retire project-scoped Docker credentials and record manual Registry GC runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_registry_gc"
down_revision = "0013_registry_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Explicitly retired before launch: never expand an old credential's authority.
    op.execute("DELETE FROM registry_credentials WHERE repository_id IS NOT NULL")
    op.drop_column("registry_credentials", "repository_id")
    op.create_table(
        "registry_gc_worker",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("id = 1", name="singleton"),
    )
    op.execute("INSERT INTO registry_gc_worker DEFAULT VALUES")
    op.create_table(
        "registry_gc_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "actor_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.BigInteger(),
            sa.ForeignKey("user_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("phase", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("before_bytes", sa.BigInteger()),
        sa.Column("after_bytes", sa.BigInteger()),
        sa.Column(
            "summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')", name="valid_state"
        ),
    )
    op.create_index(
        "uq_registry_gc_runs_active",
        "registry_gc_runs",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM registry_gc_runs)")):
        raise RuntimeError("Registry GC history exists; cannot discard it")
    op.drop_table("registry_gc_runs")
    op.drop_table("registry_gc_worker")
    # Deleted pilot credentials cannot be recovered by downgrade.
    op.add_column(
        "registry_credentials",
        sa.Column(
            "repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="RESTRICT")
        ),
    )
