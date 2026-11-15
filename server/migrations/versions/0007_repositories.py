"""Private repositories, independent member permissions and retirement of pilot grants."""

import sqlalchemy as sa
from alembic import op

revision = "0007_repositories"
down_revision = "0006_git_oauth_pilot"
branch_labels = None
depends_on = None


def records():
    return (
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    op.create_table(
        "repositories",
        *records(),
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("owner_id", "name", name="uq_repositories_owner_name"),
        sa.CheckConstraint(
            "name ~ '^[a-z0-9][a-z0-9._-]{0,99}$' AND right(name, 4) <> '.git'",
            name="name_format",
        ),
    )
    op.create_table(
        "repository_members",
        *records(),
        sa.Column(
            "repository_id",
            sa.BigInteger(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("access", sa.Text(), nullable=False, server_default="read"),
        sa.Column("role", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "repository_id", "user_id", name="uq_repository_members_repository_user"
        ),
        sa.CheckConstraint("access IN ('read', 'write')", name="valid_access"),
        sa.CheckConstraint("length(role) <= 50", name="role_length"),
    )
    op.create_index("ix_repository_members_user_id", "repository_members", ["user_id"])
    op.execute("""
        INSERT INTO config (key, value, is_secret)
        SELECT 'GIT_REPOSITORIES_ROOT', '"/mnt/git-repos"'::jsonb, false
        WHERE EXISTS (SELECT 1 FROM config)
        ON CONFLICT (key) DO NOTHING
    """)
    # Pilot consent covered a single fixed repository. Never silently widen old consent.
    op.execute("UPDATE git_oauth_grants SET revoked_at = now() WHERE revoked_at IS NULL")
    op.execute(
        "UPDATE git_oauth_requests SET expires_at = LEAST(expires_at, now()), "
        "code_expires_at = LEAST(code_expires_at, now()), consumed_at = now() "
        "WHERE consumed_at IS NULL"
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM repositories)")):
        raise RuntimeError(
            "Cannot downgrade populated repositories; back up and migrate explicitly"
        )
    op.drop_table("repository_members")
    op.drop_table("repositories")
    op.execute("DELETE FROM config WHERE key = 'GIT_REPOSITORIES_ROOT'")
