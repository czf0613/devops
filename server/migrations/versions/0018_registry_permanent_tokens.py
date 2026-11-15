"""Allow explicitly acknowledged, revocable Docker credentials without expiry."""

import sqlalchemy as sa
from alembic import op

revision = "0018_registry_permanent_tokens"
down_revision = "0017_repository_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "registry_credentials",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM registry_credentials WHERE expires_at IS NULL")
    ):
        raise RuntimeError("Cannot downgrade permanent registry credentials")
    op.alter_column(
        "registry_credentials",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
