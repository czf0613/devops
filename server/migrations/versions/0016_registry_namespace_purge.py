"""Track deferred whole-namespace reclamation in the manual maintenance window."""

import sqlalchemy as sa
from alembic import op

revision = "0016_registry_namespace_purge"
down_revision = "0015_admin_management"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("registry_namespaces", sa.Column("purged_at", sa.DateTime(timezone=True)))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM registry_namespaces WHERE deleted_at IS NOT NULL")
    ):
        raise RuntimeError("Cannot downgrade deleted registry namespaces or their cleanup state")
    op.drop_column("registry_namespaces", "purged_at")
