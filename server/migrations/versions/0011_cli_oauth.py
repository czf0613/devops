"""CLI public client, device authorization and revocable merge credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0011_cli_oauth"
down_revision = "0010_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("git_oauth_requests", "git_oauth_grants"):
        op.add_column(
            table, sa.Column("client_id", sa.Text(), server_default="devops-gcm", nullable=False)
        )
    op.create_table(
        "oauth_device_requests",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("device_digest", sa.Text(), unique=True, nullable=False),
        sa.Column("user_code_digest", sa.Text(), unique=True, nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True)),
        sa.Column("interval", sa.BigInteger(), server_default="5", nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.alter_column("merge_intents", "session_id", nullable=True)
    op.add_column("merge_intents", sa.Column("oauth_grant_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_merge_intents_oauth_grant_id_git_oauth_grants"),
        "merge_intents",
        "git_oauth_grants",
        ["oauth_grant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_merge_intents_one_credential"),
        "merge_intents",
        "(session_id IS NULL) <> (oauth_grant_id IS NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM merge_intents WHERE oauth_grant_id IS NOT NULL)")
    ).scalar():
        raise RuntimeError("CLI merge history exists; cannot discard its authorization provenance")
    # Older servers cannot distinguish clients, so never leave CLI credentials valid there.
    bind.execute(
        sa.text(
            "UPDATE git_oauth_grants SET revoked_at = coalesce(revoked_at, now()) "
            "WHERE client_id <> 'devops-gcm'"
        )
    )
    bind.execute(sa.text("DELETE FROM git_oauth_requests WHERE client_id <> 'devops-gcm'"))
    op.drop_constraint(op.f("ck_merge_intents_one_credential"), "merge_intents", type_="check")
    op.drop_constraint(
        op.f("fk_merge_intents_oauth_grant_id_git_oauth_grants"),
        "merge_intents",
        type_="foreignkey",
    )
    op.drop_column("merge_intents", "oauth_grant_id")
    op.alter_column("merge_intents", "session_id", nullable=False)
    op.drop_table("oauth_device_requests")
    for table in ("git_oauth_requests", "git_oauth_grants"):
        op.drop_column(table, "client_id")
