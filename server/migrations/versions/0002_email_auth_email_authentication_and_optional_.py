"""email authentication and optional legacy credentials

Revision ID: 0002_email_auth
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_email_auth"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    invalid = connection.scalar(
        sa.text("SELECT count(*) FROM users WHERE email IS NULL OR btrim(email) = ''")
    )
    duplicate = connection.scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT lower(btrim(email)) FROM users "
            "GROUP BY lower(btrim(email)) HAVING count(*) > 1) duplicates"
        )
    )
    if invalid or duplicate:
        raise RuntimeError("Fill missing emails and resolve duplicate emails before migrating")
    op.execute("UPDATE users SET email = lower(btrim(email))")
    for table in ("users", "user_sessions"):
        op.drop_constraint(op.f(f"ck_{table}_positive_password_version"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_positive_password_version"), table, "password_version >= 0"
        )
    op.alter_column("users", "password_version", server_default=sa.text("0"))
    op.alter_column("users", "password_changed_at", server_default=None)
    op.create_check_constraint(
        op.f("ck_users_normalized_email"), "users", "email = lower(btrim(email)) AND email <> ''"
    )
    op.create_check_constraint(
        op.f("ck_users_phone_format"),
        "users",
        r"phone IS NULL OR phone ~ '^\+[1-9][0-9]{0,2}\.[0-9]{4,14}$'",
    )
    op.create_check_constraint(
        op.f("ck_users_password_bundle"),
        "users",
        "(password_hash IS NULL AND password_ciphertext IS NULL AND password_key_id IS NULL)"
        " OR (password_hash IS NOT NULL AND password_ciphertext IS NOT NULL"
        " AND password_key_id IS NOT NULL AND password_changed_at IS NOT NULL"
        " AND password_version > 0)",
    )
    op.create_table(
        "email_challenges",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("subject_user_id", sa.BigInteger(), nullable=True),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_digest", sa.Text(), nullable=False),
        sa.Column("attempts", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_ip", postgresql.INET(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('login', 'register', 'change_email')",
            name=op.f("ck_email_challenges_valid_purpose"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_email_challenges_valid_attempts")),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_email_challenges_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name=op.f("fk_email_challenges_subject_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_challenges")),
    )
    op.create_index(
        "ix_email_challenges_email_created_at",
        "email_challenges",
        ["email", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_challenges_expires_at"), "email_challenges", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_email_challenges_request_ip_created_at",
        "email_challenges",
        ["request_ip", "created_at"],
        unique=False,
    )
    op.alter_column("users", "email", existing_type=sa.TEXT(), nullable=False)
    op.alter_column("users", "password_hash", existing_type=sa.TEXT(), nullable=True)
    op.alter_column("users", "password_ciphertext", existing_type=postgresql.BYTEA(), nullable=True)
    op.alter_column("users", "password_key_id", existing_type=sa.TEXT(), nullable=True)
    op.alter_column(
        "users",
        "password_changed_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=True,
        existing_server_default=None,
    )
    op.create_unique_constraint(op.f("uq_users_email"), "users", ["email"])
    # ### end Alembic commands ###


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM users WHERE password_hash IS NULL OR password_changed_at IS NULL"
            " OR password_version < 1"
        )
    ) or op.get_bind().scalar(
        sa.text("SELECT count(*) FROM user_sessions WHERE password_version < 1")
    ):
        raise RuntimeError("Cannot restore mandatory passwords while passwordless accounts exist")
    for name in ("normalized_email", "phone_format", "password_bundle"):
        op.drop_constraint(op.f(f"ck_users_{name}"), "users", type_="check")
    for table in ("users", "user_sessions"):
        op.drop_constraint(op.f(f"ck_{table}_positive_password_version"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_positive_password_version"), table, "password_version >= 1"
        )
    op.alter_column("users", "password_version", server_default=sa.text("1"))
    op.alter_column("users", "password_changed_at", server_default=sa.text("now()"))
    op.drop_constraint(op.f("uq_users_email"), "users", type_="unique")
    op.alter_column(
        "users",
        "password_changed_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        existing_server_default=sa.text("now()"),
    )
    op.alter_column("users", "password_key_id", existing_type=sa.TEXT(), nullable=False)
    op.alter_column(
        "users", "password_ciphertext", existing_type=postgresql.BYTEA(), nullable=False
    )
    op.alter_column("users", "password_hash", existing_type=sa.TEXT(), nullable=False)
    op.alter_column("users", "email", existing_type=sa.TEXT(), nullable=True)
    op.drop_index("ix_email_challenges_request_ip_created_at", table_name="email_challenges")
    op.drop_index(op.f("ix_email_challenges_expires_at"), table_name="email_challenges")
    op.drop_index("ix_email_challenges_email_created_at", table_name="email_challenges")
    op.drop_table("email_challenges")
    # ### end Alembic commands ###
