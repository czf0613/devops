"""Registry namespaces and revocable Docker credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0012_registry"
down_revision = "0011_cli_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_namespaces",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "repository_id",
            sa.BigInteger(),
            sa.ForeignKey("repositories.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "name ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(name) <= 63", name="name_format"
        ),
    )
    op.create_table(
        "registry_credentials",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="RESTRICT")
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_digest", sa.Text(), nullable=False, unique=True),
        sa.Column("access", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("access IN ('read', 'write')", name="valid_access"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 80", name="name_length"),
    )
    op.execute(
        "INSERT INTO config (key, value, is_secret) "
        "SELECT key, value, false FROM (VALUES ('REGISTRY_ENABLED', 'false'::jsonb), "
        "('REGISTRY_INTERNAL_URL', '\"http://registry:5000\"'::jsonb)) AS defaults(key, value) "
        "WHERE EXISTS (SELECT 1 FROM config) ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM registry_namespaces) "
                "OR EXISTS (SELECT 1 FROM registry_credentials)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Registry ownership or credential history exists; cannot discard it")
    op.drop_table("registry_credentials")
    op.drop_table("registry_namespaces")
    op.execute("DELETE FROM config WHERE key IN ('REGISTRY_ENABLED', 'REGISTRY_INTERNAL_URL')")
