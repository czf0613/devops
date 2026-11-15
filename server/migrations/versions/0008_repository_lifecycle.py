"""Archive and soft-delete repositories while preserving code and membership history."""

import sqlalchemy as sa
from alembic import op

revision = "0008_repository_lifecycle"
down_revision = "0007_repositories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("repositories", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.drop_constraint("uq_repositories_owner_name", "repositories", type_="unique")
    op.create_index(
        "uq_repositories_owner_name",
        "repositories",
        ["owner_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM repositories WHERE archived OR deleted_at IS NOT NULL)"
        )
    ):
        raise RuntimeError("Cannot downgrade archived or deleted repositories")
    op.drop_index("uq_repositories_owner_name", table_name="repositories")
    op.create_unique_constraint("uq_repositories_owner_name", "repositories", ["owner_id", "name"])
    op.drop_column("repositories", "deleted_at")
    op.drop_column("repositories", "archived")
