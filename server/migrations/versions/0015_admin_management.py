"""Email-verified administrative operations and reserved namespace tombstones."""

import sqlalchemy as sa
from alembic import op

revision = "0015_admin_management"
down_revision = "0014_registry_gc"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_email_challenges_valid_purpose"), "email_challenges", type_="check")
    op.create_check_constraint(
        op.f("ck_email_challenges_valid_purpose"),
        "email_challenges",
        "purpose IN ('login', 'register', 'change_email', 'legacy_password', 'admin_operation')",
    )
    op.create_check_constraint(
        op.f("ck_email_challenges_admin_subject"),
        "email_challenges",
        "purpose <> 'admin_operation' OR subject_user_id IS NOT NULL",
    )
    op.add_column("registry_namespaces", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM registry_namespaces WHERE deleted_at IS NOT NULL")
    ):
        raise RuntimeError("Cannot downgrade deleted registry namespaces")
    if connection.scalar(
        sa.text("SELECT count(*) FROM email_challenges WHERE purpose = 'admin_operation'")
    ):
        raise RuntimeError("Cannot downgrade admin operation verification records")
    op.drop_column("registry_namespaces", "deleted_at")
    op.drop_constraint(op.f("ck_email_challenges_admin_subject"), "email_challenges", type_="check")
    op.drop_constraint(op.f("ck_email_challenges_valid_purpose"), "email_challenges", type_="check")
    op.create_check_constraint(
        op.f("ck_email_challenges_valid_purpose"),
        "email_challenges",
        "purpose IN ('login', 'register', 'change_email', 'legacy_password')",
    )
