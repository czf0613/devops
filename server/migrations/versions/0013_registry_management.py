"""Version namespace bindings for concurrent owner and global management."""

import sqlalchemy as sa
from alembic import op

revision = "0013_registry_management"
down_revision = "0012_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registry_namespaces",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("registry_namespaces", "version")
