from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class IdentityRecord:
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)


class CreatedRecord:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UpdatedRecord:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Repository(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        Index(
            "uq_repositories_owner_name",
            "owner_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "name ~ '^[a-z0-9][a-z0-9._-]{0,99}$' AND right(name, 4) <> '.git'",
            name="name_format",
        ),
    )

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    protect_main: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    protect_push: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    next_item_number: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    merge_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepositoryMember(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "repository_members"
    __table_args__ = (
        UniqueConstraint("repository_id", "user_id", name="uq_repository_members_repository_user"),
        CheckConstraint("access IN ('read', 'write')", name="valid_access"),
        CheckConstraint("length(role) <= 50", name="role_length"),
    )

    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    access: Mapped[str] = mapped_column(Text, server_default="read")
    role: Mapped[str] = mapped_column(Text, server_default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class ConfigEntry(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "config"
    __table_args__ = (CheckConstraint("key ~ '^[A-Z][A-Z0-9_]*$'", name="key_format"),)

    key: Mapped[str] = mapped_column(Text, unique=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # Sensitivity metadata for tooling; JSON values themselves are stored directly.
    is_secret: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class GitOAuthRequest(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "git_oauth_requests"

    client_id: Mapped[str] = mapped_column(Text, server_default="devops-gcm")
    public_id: Mapped[str] = mapped_column(Text(collation="C"), unique=True)
    browser_digest: Mapped[str] = mapped_column(Text, deferred=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, deferred=True)
    challenge: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    code: Mapped[str | None] = mapped_column(Text(collation="C"), unique=True, deferred=True)
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitOAuthGrant(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "git_oauth_grants"

    client_id: Mapped[str] = mapped_column(Text, server_default="devops-gcm")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    scope: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitOAuthToken(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "git_oauth_tokens"
    __table_args__ = (
        CheckConstraint("access_token ~ '^[A-Za-z0-9_-]{64}$'", name="access_token_format"),
        CheckConstraint("refresh_token ~ '^[A-Za-z0-9_-]{64}$'", name="refresh_token_format"),
    )

    grant_id: Mapped[int] = mapped_column(
        ForeignKey("git_oauth_grants.id", ondelete="CASCADE"), index=True
    )
    access_token: Mapped[str] = mapped_column(Text(collation="C"), unique=True, deferred=True)
    refresh_token: Mapped[str] = mapped_column(Text(collation="C"), unique=True, deferred=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthDeviceRequest(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "oauth_device_requests"

    device_digest: Mapped[str] = mapped_column(Text, unique=True)
    user_code_digest: Mapped[str] = mapped_column(Text, unique=True)
    client_id: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interval: Mapped[int] = mapped_column(BigInteger, server_default="5")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class User(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="valid_status"),
        CheckConstraint("password_version >= 0", name="positive_password_version"),
        CheckConstraint("username = lower(username) AND length(username) > 0", name="username"),
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="normalized_email"),
        CheckConstraint(
            "phone IS NULL OR phone ~ '^\\+[1-9][0-9]{0,2}\\.[0-9]{4,14}$'", name="phone_format"
        ),
        CheckConstraint(
            "(password_hash IS NULL AND password_ciphertext IS NULL AND password_key_id IS NULL)"
            " OR (password_hash IS NOT NULL AND password_ciphertext IS NOT NULL"
            " AND password_key_id IS NOT NULL AND password_changed_at IS NOT NULL"
            " AND password_version > 0)",
            name="password_bundle",
        ),
    )

    username: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    phone: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    password_hash: Mapped[str | None] = mapped_column(Text, deferred=True)
    password_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    password_key_id: Mapped[str | None] = mapped_column(Text, deferred=True)
    password_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint("password_version >= 0", name="positive_password_version"),
        CheckConstraint("token ~ '^[A-Za-z0-9_-]{64}$'", name="token_format"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    token: Mapped[str] = mapped_column(Text(collation="C"), unique=True, deferred=True)
    password_version: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    login_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)


class Role(IdentityRecord, CreatedRecord, UpdatedRecord, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), index=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AuditEvent(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("result IN ('success', 'failure')", name="valid_result"),
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_target", "target_type", "target_id"),
    )

    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class EmailChallenge(IdentityRecord, CreatedRecord, Base):
    __tablename__ = "email_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('login', 'register', 'change_email', "
            "'legacy_password', 'admin_operation', 'repository_operation')",
            name="valid_purpose",
        ),
        CheckConstraint(
            "purpose <> 'legacy_password' OR subject_user_id IS NOT NULL",
            name="password_subject",
        ),
        CheckConstraint(
            "purpose <> 'admin_operation' OR subject_user_id IS NOT NULL",
            name="admin_subject",
        ),
        CheckConstraint(
            "purpose <> 'repository_operation' OR subject_user_id IS NOT NULL",
            name="repository_subject",
        ),
        CheckConstraint("attempts >= 0", name="valid_attempts"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_email_challenges_email_created_at", "email", "created_at"),
        Index("ix_email_challenges_request_ip_created_at", "request_ip", "created_at"),
    )

    email: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    subject_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    nonce: Mapped[str] = mapped_column(Text)
    code_digest: Mapped[str] = mapped_column(Text, deferred=True)
    attempts: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_ip: Mapped[str | None] = mapped_column(INET)


# Keep collaboration tables in a separate module while sharing one Alembic metadata.
from devops_server import ci_models as ci_models  # noqa: E402
from devops_server import collaboration_models as collaboration_models  # noqa: E402
from devops_server import registry_models as registry_models  # noqa: E402
