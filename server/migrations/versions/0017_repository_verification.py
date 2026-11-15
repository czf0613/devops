"""Require subject-bound email verification for repository lifecycle changes."""

import sqlalchemy as sa
from alembic import op

revision = "0017_repository_verification"
down_revision = "0016_registry_namespace_purge"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_email_challenges_valid_purpose"), "email_challenges", type_="check")
    op.create_check_constraint(
        op.f("ck_email_challenges_valid_purpose"),
        "email_challenges",
        "purpose IN ('login', 'register', 'change_email', 'legacy_password', "
        "'admin_operation', 'repository_operation')",
    )
    op.create_check_constraint(
        op.f("ck_email_challenges_repository_subject"),
        "email_challenges",
        "purpose <> 'repository_operation' OR subject_user_id IS NOT NULL",
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM email_challenges WHERE purpose = 'repository_operation'")
    ):
        raise RuntimeError("Cannot downgrade repository operation verification records")
    op.drop_constraint(
        op.f("ck_email_challenges_repository_subject"), "email_challenges", type_="check"
    )
    op.drop_constraint(op.f("ck_email_challenges_valid_purpose"), "email_challenges", type_="check")
    op.create_check_constraint(
        op.f("ck_email_challenges_valid_purpose"),
        "email_challenges",
        "purpose IN ('login', 'register', 'change_email', 'legacy_password', 'admin_operation')",
    )
