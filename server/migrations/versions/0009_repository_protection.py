"""Owner-controlled main and destructive-push protection."""

import sqlalchemy as sa
from alembic import op

revision = "0009_repository_protection"
down_revision = "0008_repository_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("protect_main", "protect_push"):
        op.add_column(
            "repositories", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM repositories WHERE protect_main OR protect_push)")
    ):
        raise RuntimeError("Disable repository protections before downgrading")
    op.drop_column("repositories", "protect_push")
    op.drop_column("repositories", "protect_main")
