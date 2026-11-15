"""Purpose-bound email verification for self-service legacy password changes."""

import sqlalchemy as sa
from alembic import op

revision = "0005_password_email_verification"
down_revision = "0004_plaintext_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_email_challenges_valid_purpose"), "email_challenges", type_="check")
    op.create_check_constraint(
        op.f("ck_email_challenges_valid_purpose"),
        "email_challenges",
        "purpose IN ('login', 'register', 'change_email', 'legacy_password')",
    )
    op.create_check_constraint(
        op.f("ck_email_challenges_password_subject"),
        "email_challenges",
        "purpose <> 'legacy_password' OR subject_user_id IS NOT NULL",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM email_challenges WHERE purpose = 'legacy_password'")
    ):
        raise RuntimeError("Cannot downgrade while legacy password verification records exist")
    op.drop_constraint(
        op.f("ck_email_challenges_password_subject"), "email_challenges", type_="check"
    )
    op.drop_constraint(op.f("ck_email_challenges_valid_purpose"), "email_challenges", type_="check")
    op.create_check_constraint(
        op.f("ck_email_challenges_valid_purpose"),
        "email_challenges",
        "purpose IN ('login', 'register', 'change_email')",
    )
