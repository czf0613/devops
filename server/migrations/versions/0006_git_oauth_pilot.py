"""Revocable PKCE authorizations for the administrator-only Git pilot."""

import sqlalchemy as sa
from alembic import op

revision = "0006_git_oauth_pilot"
down_revision = "0005_password_email_verification"
branch_labels = None
depends_on = None


def identity():
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def created():
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "git_oauth_requests",
        identity(),
        created(),
        sa.Column("public_id", sa.Text(collation="C"), nullable=False, unique=True),
        sa.Column("browser_digest", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("challenge", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("code", sa.Text(collation="C"), unique=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_git_oauth_requests_expires_at", "git_oauth_requests", ["expires_at"])
    op.create_table(
        "git_oauth_grants",
        identity(),
        created(),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_git_oauth_grants_user_id", "git_oauth_grants", ["user_id"])
    op.create_table(
        "git_oauth_tokens",
        identity(),
        created(),
        sa.Column(
            "grant_id",
            sa.BigInteger(),
            sa.ForeignKey("git_oauth_grants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_token", sa.Text(collation="C"), unique=True, nullable=False),
        sa.Column("refresh_token", sa.Text(collation="C"), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("access_token ~ '^[A-Za-z0-9_-]{64}$'", name="access_token_format"),
        sa.CheckConstraint("refresh_token ~ '^[A-Za-z0-9_-]{64}$'", name="refresh_token_format"),
    )
    op.create_index("ix_git_oauth_tokens_grant_id", "git_oauth_tokens", ["grant_id"])
    # Preserve existing config; an empty database must stay empty until config_cli init.
    op.execute("""
        INSERT INTO config (key, value, is_secret)
        SELECT 'GIT_OAUTH_ENABLED', 'false'::jsonb, false
        WHERE EXISTS (SELECT 1 FROM config)
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("git_oauth_tokens")
    op.drop_table("git_oauth_grants")
    op.drop_table("git_oauth_requests")
    op.execute("DELETE FROM config WHERE key = 'GIT_OAUTH_ENABLED'")
