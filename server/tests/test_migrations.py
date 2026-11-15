import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import BigInteger, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from devops_server.config import Settings, get_connections
from devops_server.config_store import SECRET_KEYS, settings_values
from devops_server.database import build_engine
from devops_server.models import (
    ConfigEntry,
    EmailChallenge,
    GitOAuthGrant,
    GitOAuthRequest,
    GitOAuthToken,
    Repository,
    Role,
    User,
    UserRole,
    UserSession,
)
from devops_server.registry_models import RegistryCredential, RegistryNamespace
from devops_server.security import decrypt_password, encrypt_password

TABLES = {
    "ci_workers",
    "ci_repositories",
    "ci_runs",
    "ci_jobs",
    "ci_attempts",
    "ci_artifacts",
    "registry_gc_worker",
    "registry_gc_runs",
    "registry_namespaces",
    "registry_credentials",
    "oauth_device_requests",
    "work_items",
    "item_redirects",
    "repository_catalog",
    "item_catalog",
    "item_assignees",
    "item_relations",
    "pull_requests",
    "pull_reviewers",
    "pull_reviews",
    "review_threads",
    "discussion_comments",
    "content_revisions",
    "item_events",
    "content_references",
    "discussion_reactions",
    "item_subscriptions",
    "collaboration_notifications",
    "discussion_attachments",
    "saved_issue_filters",
    "pull_viewed_files",
    "pull_checks",
    "collaboration_jobs",
    "merge_intents",
    "repositories",
    "repository_members",
    "users",
    "roles",
    "user_roles",
    "user_sessions",
    "audit_events",
    "email_challenges",
    "config",
    "git_oauth_requests",
    "git_oauth_grants",
    "git_oauth_tokens",
}


@pytest.fixture
def migrated_database():
    """Exercise real PostgreSQL migrations in a disposable, isolated schema."""
    settings = get_connections()
    if settings.postgres_host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("These development tests require a local PostgreSQL host.")

    engine = build_engine(settings)
    schema = "devops_test_" + secrets.token_hex(8)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "head")
                yield connection, config, schema
            finally:
                connection.rollback()
                connection.exec_driver_sql("SET search_path TO public")
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
                connection.commit()
    finally:
        engine.dispose()


def test_initial_migration_and_roundtrip(migrated_database):
    connection, config, schema = migrated_database
    inspector = inspect(connection)
    assert set(inspector.get_table_names(schema=schema)) == TABLES | {"alembic_version"}
    for table in TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table, schema=schema)}
        if table == "ci_repositories":
            assert isinstance(columns["repository_id"]["type"], BigInteger)
            assert inspector.get_pk_constraint(table, schema=schema)["constrained_columns"] == [
                "repository_id"
            ]
            assert any(
                fk["constrained_columns"] == ["repository_id"]
                and fk["referred_table"] == "repositories"
                for fk in inspector.get_foreign_keys(table, schema=schema)
            )
            continue
        assert isinstance(columns["id"]["type"], BigInteger)
        assert columns["id"]["identity"]["always"] is True
        assert inspector.get_pk_constraint(table, schema=schema)["constrained_columns"] == ["id"]

    assert set(connection.scalars(select(Role.code))) == {"admin", "member"}

    connection.commit()
    command.check(config)
    command.downgrade(config, "base")
    assert set(inspect(connection).get_table_names(schema=schema)) == {"alembic_version"}
    connection.commit()
    command.upgrade(config, "head")
    assert set(connection.scalars(select(Role.code))) == {"admin", "member"}


def test_admin_verification_upgrade_and_namespace_tombstone_downgrade_guard(migrated_database):
    connection, config, _ = migrated_database
    command.downgrade(config, "0014_registry_gc")
    user_id = connection.scalar(
        insert(User)
        .values(username="admin_test", display_name="Admin", email="admin_test@example.com")
        .returning(User.id)
    )
    repo_id = connection.scalar(
        insert(Repository).values(name="example", owner_id=user_id).returning(Repository.id)
    )
    namespace_id = connection.scalar(
        text(
            "INSERT INTO registry_namespaces (name, repository_id, enabled) "
            "VALUES ('reserved', :repo, false) RETURNING id"
        ),
        {"repo": repo_id},
    )
    connection.commit()
    command.upgrade(config, "head")
    assert (
        connection.scalar(
            select(RegistryNamespace.deleted_at).where(RegistryNamespace.id == namespace_id)
        )
        is None
    )
    connection.execute(
        text("UPDATE registry_namespaces SET deleted_at = now() WHERE id = :id"),
        {"id": namespace_id},
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade deleted registry namespaces"):
        command.downgrade(config, "0014_registry_gc")


def test_admin_email_challenge_requires_subject_and_blocks_lossy_downgrade(migrated_database):
    connection, config, _ = migrated_database
    user_id = connection.scalar(
        insert(User)
        .values(username="admin_test", display_name="Admin", email="admin_test@example.com")
        .returning(User.id)
    )
    connection.commit()
    values = {"email": "admin_test@example.com", "subject": None}
    statement = text("""INSERT INTO email_challenges
        (email, purpose, subject_user_id, nonce, code_digest, expires_at)
        VALUES (:email, 'admin_operation', :subject, 'synthetic', 'synthetic',
                now() + interval '10 minutes')""")
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(statement, values)
    connection.execute(statement, {**values, "subject": user_id})
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade admin operation verification records"):
        command.downgrade(config, "0014_registry_gc")


def test_registry_upgrade_preserves_configuration_and_refuses_loss_of_credentials(
    migrated_database,
):
    connection, config, _ = migrated_database
    connection.commit()
    command.downgrade(config, "0011_cli_oauth")
    connection.execute(insert(ConfigEntry).values(key="ENVIRONMENT", value="test"))
    connection.commit()
    command.upgrade(config, "head")
    assert (
        connection.scalar(select(ConfigEntry.value).where(ConfigEntry.key == "ENVIRONMENT"))
        == "test"
    )
    assert (
        connection.scalar(select(ConfigEntry.value).where(ConfigEntry.key == "REGISTRY_ENABLED"))
        is False
    )
    user_id = connection.scalar(
        insert(User)
        .values(username="registry_user", display_name="Test", email="registry@example.com")
        .returning(User.id)
    )
    connection.execute(
        insert(RegistryCredential).values(
            user_id=user_id,
            name="test",
            token_digest="synthetic",
            access="read",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="cannot discard"):
        command.downgrade(config, "0011_cli_oauth")
    assert connection.scalar(select(RegistryCredential.user_id)) == user_id


def test_registry_upgrade_keeps_unbound_names_and_user_tokens_but_deletes_scoped_tokens(
    migrated_database,
):
    connection, config, _ = migrated_database
    connection.commit()
    command.downgrade(config, "0012_registry")
    user_id = connection.scalar(
        insert(User)
        .values(username="oldregistry", display_name="Test", email="oldregistry@example.com")
        .returning(User.id)
    )
    repo_id = connection.scalar(
        insert(Repository).values(owner_id=user_id, name="original").returning(Repository.id)
    )
    connection.execute(
        text(
            "INSERT INTO registry_namespaces (name, repository_id, enabled) "
            "VALUES ('retained', :repo, false)"
        ),
        {"repo": repo_id},
    )
    connection.execute(
        text(
            "INSERT INTO registry_credentials "
            "(user_id, repository_id, name, access, token_digest, expires_at) "
            "VALUES (:user, :repo, 'legacy', 'read', 'synthetic-old', now() + interval '1 day'), "
            "(:user, null, 'user token', 'write', 'synthetic-user', now() + interval '1 day')"
        ),
        {"user": user_id, "repo": repo_id},
    )
    connection.commit()
    command.upgrade(config, "head")
    ns = connection.execute(
        select(
            RegistryNamespace.repository_id, RegistryNamespace.enabled, RegistryNamespace.version
        )
    ).one()
    assert ns == (repo_id, False, 1)
    assert list(connection.scalars(select(RegistryCredential.name))) == ["user token"]
    assert "repository_id" not in {
        column["name"] for column in inspect(connection).get_columns("registry_credentials")
    }
    connection.commit()
    command.check(config)


def test_repository_upgrade_retires_pilot_consent_without_changing_users(migrated_database):
    connection, config, _ = migrated_database
    connection.commit()
    command.downgrade(config, "0006_git_oauth_pilot")
    future = datetime.now(UTC) + timedelta(days=1)
    user_id = connection.scalar(
        insert(User)
        .values(username="existing", display_name="Existing", email="existing@example.com")
        .returning(User.id)
    )
    grant_id = connection.scalar(
        insert(GitOAuthGrant)
        .values(user_id=user_id, scope="git:read git:write", expires_at=future)
        .returning(GitOAuthGrant.id)
    )
    connection.execute(
        insert(GitOAuthToken).values(
            grant_id=grant_id, access_token="a" * 64, refresh_token="r" * 64, expires_at=future
        )
    )
    connection.execute(
        insert(GitOAuthRequest).values(
            public_id="p" * 43,
            browser_digest="digest",
            redirect_uri="http://127.0.0.1:49152/",
            state="state",
            challenge="c" * 43,
            scope="git:read",
            expires_at=future,
            code="c" * 64,
            code_expires_at=future,
            user_id=user_id,
        )
    )
    connection.execute(insert(ConfigEntry).values(key="ENVIRONMENT", value="production"))
    connection.commit()
    command.upgrade(config, "head")
    assert connection.scalar(select(User.username).where(User.id == user_id)) == "existing"
    assert connection.scalar(select(GitOAuthGrant.revoked_at)) is not None
    assert connection.scalar(select(GitOAuthRequest.consumed_at)) is not None
    assert connection.scalar(select(GitOAuthRequest.code_expires_at)) <= datetime.now(UTC)
    assert (
        connection.scalar(
            select(ConfigEntry.value).where(ConfigEntry.key == "GIT_REPOSITORIES_ROOT")
        )
        == "/mnt/git-repos"
    )
    connection.execute(insert(Repository).values(owner_id=user_id, name="production-repo"))
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade populated repositories"):
        command.downgrade(config, "0006_git_oauth_pilot")


def test_repository_lifecycle_upgrade_preserves_existing_data(migrated_database):
    connection, config, _ = migrated_database
    connection.commit()
    command.downgrade(config, "0007_repositories")
    owner_id = connection.scalar(
        insert(User)
        .values(
            username="repoowner",
            display_name="Owner",
            email="repoowner@example.com",
        )
        .returning(User.id)
    )
    repository_id = connection.scalar(
        text(
            "INSERT INTO repositories (owner_id, name, description) "
            "VALUES (:owner, 'existing', 'Keep this') RETURNING id"
        ),
        {"owner": owner_id},
    )
    connection.commit()
    command.upgrade(config, "head")
    row = connection.execute(
        select(Repository.__table__).where(Repository.id == repository_id)
    ).one()
    assert row.description == "Keep this" and not row.archived and row.deleted_at is None
    connection.execute(
        text("UPDATE repositories SET deleted_at = now() WHERE id = :id"), {"id": repository_id}
    )
    connection.execute(insert(Repository).values(owner_id=owner_id, name="existing"))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(insert(Repository).values(owner_id=owner_id, name="existing"))
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade archived or deleted"):
        command.downgrade(config, "0007_repositories")


def test_repository_protection_upgrade_preserves_defaults_and_guards_downgrade(migrated_database):
    connection, config, _ = migrated_database
    connection.commit()
    command.downgrade(config, "0008_repository_lifecycle")
    owner_id = connection.scalar(
        insert(User)
        .values(username="protectedowner", display_name="Owner", email="protected@example.com")
        .returning(User.id)
    )
    connection.execute(
        text("INSERT INTO repositories (owner_id, name) VALUES (:owner, 'existing')"),
        {"owner": owner_id},
    )
    connection.commit()
    command.upgrade(config, "head")
    row = connection.execute(select(Repository.__table__)).one()
    assert not row.protect_main and not row.protect_push and row.owner_id == owner_id
    connection.execute(text("UPDATE repositories SET protect_main = true"))
    connection.commit()
    with pytest.raises(RuntimeError, match="Disable repository protections"):
        command.downgrade(config, "0008_repository_lifecycle")
    connection.rollback()
    assert connection.scalar(text("SELECT protect_main FROM repositories")) is True


def test_database_enforces_identity_contracts(migrated_database):
    connection, _, _ = migrated_database
    user_values = {
        "username": "fixture",
        "display_name": "Fixture",
        "email": "fixture@example.com",
        "password_hash": "synthetic-test-hash",
        "password_ciphertext": b"synthetic-test-ciphertext",
        "password_key_id": "test-only",
        "password_version": 1,
        "password_changed_at": datetime.now(UTC),
    }
    user_id = connection.scalar(insert(User).values(**user_values).returning(User.id))
    role_id = connection.scalar(select(Role.id).where(Role.code == "member"))
    connection.execute(insert(UserRole).values(user_id=user_id, role_id=role_id))

    session_values = {
        "user_id": user_id,
        "password_version": 1,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
    }
    for token in ("A" * 64, "a" * 64):
        connection.execute(insert(UserSession).values(**session_values, token=token))
    assert (
        connection.scalar(select(UserSession.token).where(UserSession.token == "A" * 64))
        == "A" * 64
    )
    assert (
        connection.scalar(select(UserSession.token).where(UserSession.token == "a" * 64))
        == "a" * 64
    )

    invalid_statements = [
        insert(User).values(**user_values),
        insert(User).values(**(user_values | {"username": "other", "status": "invalid"})),
        insert(User).values(**(user_values | {"username": "UPPERCASE"})),
        insert(UserRole).values(user_id=user_id, role_id=role_id),
        insert(UserRole).values(user_id=user_id, role_id=-1),
        insert(UserSession).values(**session_values, token="A" * 64),
        insert(UserSession).values(**session_values, token="short"),
        insert(UserSession).values(**(session_values | {"password_version": -1}), token="B" * 64),
        insert(User).values(**(user_values | {"username": "duplicate"})),
        insert(User).values(**(user_values | {"email": None})),
        insert(User).values(**(user_values | {"email": "UPPER@example.com"})),
        insert(User).values(**(user_values | {"phone": "13912345678"})),
        insert(User).values(**(user_values | {"password_hash": None})),
    ]
    for statement in invalid_statements:
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(statement)

    assert connection.scalar(text("SELECT count(*) FROM users")) == 1


def seed_legacy_config(connection, config, settings, key_file, *, plaintext_keys=()):
    connection.commit()
    command.downgrade(config, "0003_shared_config")
    master = secrets.token_bytes(32)
    key_file.write_text(base64.b64encode(master).decode())
    config.attributes["legacy_config_key_path"] = key_file
    values = settings_values(settings)
    for key, value in values.items():
        if key in SECRET_KEYS and key not in plaintext_keys:
            nonce = secrets.token_bytes(12)
            payload = nonce + AESGCM(master).encrypt(
                nonce, json.dumps(value).encode(), f"devops:config:v1:{key}".encode()
            )
            value = {"version": 1, "ciphertext": base64.b64encode(payload).decode()}
        connection.execute(
            insert(ConfigEntry).values(key=key, value=value, is_secret=key in SECRET_KEYS)
        )
    connection.commit()
    return values


@pytest.mark.parametrize("plaintext_keys", [(), ("SMTP_PASSWORD",), tuple(SECRET_KEYS)])
def test_plaintext_migration_preserves_values_and_password_compatibility(
    migrated_database, auth_settings, tmp_path, plaintext_keys
):
    connection, config, _ = migrated_database
    key_file = tmp_path / "config.key"
    expected = seed_legacy_config(
        connection, config, auth_settings, key_file, plaintext_keys=plaintext_keys
    )
    _, ciphertext, key_id = encrypt_password(auth_settings, 10, "existing-password")
    if set(plaintext_keys) == SECRET_KEYS:
        key_file.unlink()  # Already plain config does not need the retired key file.
    command.upgrade(config, "head")
    actual = dict(connection.execute(select(ConfigEntry.key, ConfigEntry.value)).all())
    assert actual == expected
    settings = Settings.model_validate(actual)
    assert decrypt_password(settings, 10, ciphertext, key_id) == "existing-password"
    assert settings.auth_hmac_key == auth_settings.auth_hmac_key
    # An old application cannot read this format; do not silently claim a safe downgrade.
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade populated plaintext"):
        command.downgrade(config, "0003_shared_config")
    connection.rollback()


@pytest.mark.parametrize("failure", ["missing_key", "wrong_key", "invalid_ciphertext"])
def test_plaintext_migration_failure_preserves_all_original_values(
    migrated_database, auth_settings, tmp_path, failure
):
    connection, config, _ = migrated_database
    key_file = tmp_path / "config.key"
    seed_legacy_config(connection, config, auth_settings, key_file)
    if failure == "missing_key":
        key_file.unlink()
    elif failure == "wrong_key":
        key_file.write_text(base64.b64encode(b"wrong-key".ljust(32, b"!")).decode())
    else:
        connection.execute(
            ConfigEntry.__table__.update()
            .where(ConfigEntry.key == "SMTP_PASSWORD")
            .values(value={"version": 1, "ciphertext": "broken"})
        )
    before = dict(connection.execute(select(ConfigEntry.key, ConfigEntry.value)).all())
    connection.commit()
    with pytest.raises(RuntimeError):
        command.upgrade(config, "head")
    connection.rollback()
    assert dict(connection.execute(select(ConfigEntry.key, ConfigEntry.value)).all()) == before
    assert (
        connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003_shared_config"
    )


def test_collaboration_downgrade_refuses_populated_data(migrated_database):
    from devops_server.collaboration_models import WorkItem

    connection, config, _ = migrated_database
    owner = connection.scalar(
        insert(User)
        .values(username="collaboration", display_name="Fixture", email="collaboration@example.com")
        .returning(User.id)
    )
    repository = connection.scalar(
        insert(Repository).values(owner_id=owner, name="collaboration").returning(Repository.id)
    )
    item = connection.scalar(
        insert(WorkItem)
        .values(
            repository_id=repository,
            number=1,
            author_id=owner,
            kind="issue",
            title="Keep this Issue",
        )
        .returning(WorkItem.id)
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
        command.downgrade(config, "0009_repository_protection")
    connection.rollback()
    assert connection.scalar(select(WorkItem.title).where(WorkItem.id == item)) == "Keep this Issue"


def test_repository_operation_challenges_require_subject_and_block_unsafe_downgrade(
    migrated_database,
):
    connection, config, _ = migrated_database
    values = dict(
        email="synthetic@example.com",
        purpose="repository_operation",
        nonce="fixture",
        code_digest="synthetic",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(insert(EmailChallenge).values(**values))
    subject = connection.scalar(
        insert(User)
        .values(username="fixture", display_name="Fixture", email="synthetic@example.com")
        .returning(User.id)
    )
    connection.execute(insert(EmailChallenge).values(**values, subject_user_id=subject))
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade repository operation"):
        command.downgrade(config, "0016_registry_namespace_purge")
    connection.rollback()
    assert connection.scalar(select(EmailChallenge.purpose)) == "repository_operation"


def test_permanent_registry_migration_preserves_expiry_and_guards_downgrade(migrated_database):
    connection, config, _ = migrated_database
    connection.commit()
    command.downgrade(config, "0017_repository_verification")
    user_id = connection.scalar(
        insert(User)
        .values(username="permanent", display_name="Test", email="permanent@example.com")
        .returning(User.id)
    )
    expiry = datetime.now(UTC) + timedelta(days=30)
    token_id = connection.scalar(
        insert(RegistryCredential)
        .values(
            user_id=user_id,
            name="Existing",
            access="read",
            token_digest="existing",
            expires_at=expiry,
        )
        .returning(RegistryCredential.id)
    )
    connection.commit()
    command.upgrade(config, "head")
    assert (
        connection.scalar(
            select(RegistryCredential.expires_at).where(RegistryCredential.id == token_id)
        )
        == expiry
    )
    connection.execute(
        insert(RegistryCredential).values(
            user_id=user_id,
            name="Permanent",
            access="read",
            token_digest="permanent",
            expires_at=None,
            revoked_at=datetime.now(UTC),
        )
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="Cannot downgrade permanent registry credentials"):
        command.downgrade(config, "0017_repository_verification")
    assert (
        connection.scalar(
            select(RegistryCredential.id).where(RegistryCredential.expires_at.is_(None))
        )
        is not None
    )


def test_ci_downgrade_refuses_worker_history(migrated_database):
    connection, config, _ = migrated_database
    connection.execute(text("INSERT INTO ci_workers (name, pool) VALUES ('fixture', 'default')"))
    connection.commit()
    with pytest.raises(RuntimeError, match="禁止破坏性降级"):
        command.downgrade(config, "0018_registry_permanent_tokens")
    connection.rollback()
    assert connection.scalar(text("SELECT count(*) FROM ci_workers")) == 1
