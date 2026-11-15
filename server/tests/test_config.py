import base64
import json

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from devops_server.config import ConnectionSettings
from devops_server.config_cli import CONNECTION_KEYS, migrate_env
from devops_server.config_store import (
    RUNTIME_KEYS,
    SECRET_KEYS,
    ConfigurationError,
    initialize_config,
    load_settings,
    settings_values,
    update_configuration,
)
from devops_server.main import create_app
from devops_server.models import ConfigEntry


def test_legacy_env_migration_preserves_secrets_and_is_idempotent(system, tmp_path):
    env = tmp_path / ".env"
    password = "smtp-${literal}-password"
    aes = {"old-key": base64.b64encode(b"a" * 32).decode()}
    env.write_text(
        "POSTGRES_PASSWORD='existing-database-password'\nREDIS_DB=0\n"
        f"PASSWORD_CIPHER_KEYS='{json.dumps(aes)}'\nPASSWORD_CIPHER_CURRENT_KEY=old-key\n"
        f"AUTH_HMAC_KEY={'h' * 43}\nSMTP_PASSWORD='{password}'\n"
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=465\nSMTP_SECURITY=\nSMTP_FROM=\n"
        "SMTP_USERNAME=noreply@example.com\n"
    )
    migrate_env(system.engine, env)
    settings = load_settings(system.engine)
    assert settings.smtp_configured
    assert settings.smtp_security == "ssl"
    assert settings.smtp_sender == "noreply@example.com"
    assert settings.smtp_password.get_secret_value() == password
    assert settings_values(settings)["PASSWORD_CIPHER_KEYS"] == aes
    assert settings.auth_hmac_key.get_secret_value() == "h" * 43
    remaining = dotenv_values(env)
    assert remaining.keys() <= CONNECTION_KEYS
    assert remaining["POSTGRES_PASSWORD"] == "existing-database-password"
    assert env.stat().st_mode & 0o777 == 0o600
    with Session(system.engine) as db:
        entries = list(db.scalars(select(ConfigEntry)))
        assert {entry.key for entry in entries} == RUNTIME_KEYS
        assert {entry.key for entry in entries if entry.is_secret} == SECRET_KEYS
        stored = {entry.key: entry.value for entry in entries}
        assert stored["SMTP_PASSWORD"] == password
        assert stored["PASSWORD_CIPHER_KEYS"] == aes
        assert stored["AUTH_HMAC_KEY"] == "h" * 43
    migrate_env(system.engine, env)
    assert settings_values(load_settings(system.engine)) == settings_values(settings)
    assert not (tmp_path / ".secrets").exists()


def test_invalid_import_preserves_original_env_and_rolls_back(system, tmp_path):
    env = tmp_path / ".env"
    original = "BCRYPT_ROUNDS=999\nSMTP_PASSWORD='never-delete-this'\n"
    env.write_text(original)
    with pytest.raises(ValidationError):
        migrate_env(system.engine, env)
    assert env.read_text() == original
    with Session(system.engine) as db:
        assert db.scalar(select(func.count()).select_from(ConfigEntry)) == 0


def test_sensitive_config_can_be_edited_directly_without_a_key_file(system, monkeypatch):
    initialize_config(system.engine)
    with Session(system.engine) as db:
        entry = db.scalar(select(ConfigEntry).where(ConfigEntry.key == "SMTP_PASSWORD"))
        entry.value = "manually-edited-password"
        entry.is_secret = False  # Redaction is based on the known key, not a mutable DB flag.
        db.commit()

    def no_key_file(*args, **kwargs):
        raise AssertionError("Runtime configuration must not read a key file")

    monkeypatch.setattr("pathlib.Path.read_text", no_key_file)
    settings = load_settings(system.engine)
    assert settings.smtp_password.get_secret_value() == "manually-edited-password"
    assert "manually-edited-password" not in repr(settings)


def test_database_config_is_loaded_at_startup_and_applied_after_restart(system, monkeypatch):
    initialize_config(system.engine)
    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")

    def application():
        return create_app(
            engine=system.engine,
            redis_client=system.limiter.client,
            mailer=system.mailbox,
        )

    with TestClient(application()) as first:
        assert first.get("/api/auth/csrf").json()["registration_enabled"] is True
        assert not first.app.state.settings.session_cookie_secure
        update_configuration(system.engine, {"REGISTRATION_ENABLED": False})
        assert first.get("/api/auth/csrf").json()["registration_enabled"] is True
        with TestClient(application()) as second:
            assert second.get("/api/auth/csrf").json()["registration_enabled"] is False


def test_invalid_multi_key_update_is_atomic_and_secrets_are_not_echoed(system):
    before = initialize_config(system.engine)
    with pytest.raises(ValidationError) as raised:
        update_configuration(
            system.engine,
            {"SMTP_PASSWORD": "sensitive-new-password", "BCRYPT_ROUNDS": 999},
        )
    assert "sensitive-new-password" not in str(raised.value)
    assert settings_values(load_settings(system.engine)) == settings_values(before)
    update_configuration(
        system.engine,
        {"ENVIRONMENT": "production", "SESSION_COOKIE_SECURE": True},
    )
    assert load_settings(system.engine).environment == "production"


def test_config_initialization_does_not_overwrite_existing_values(system):
    initialize_config(system.engine)
    update_configuration(system.engine, {"SESSION_DAYS": 5})
    assert initialize_config(system.engine).session_days == 5
    with pytest.raises(ConfigurationError, match="differs"):
        initialize_config(system.engine, {"SESSION_DAYS": "7"})
    assert load_settings(system.engine).session_days == 5


def test_env_changed_during_import_is_preserved(system, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SESSION_DAYS=7\n")

    def initialize_then_edit(*args):
        settings = initialize_config(*args)
        env.write_text("SESSION_DAYS=5\n")
        return settings

    monkeypatch.setattr("devops_server.config_cli.initialize_config", initialize_then_edit)
    with pytest.raises(ConfigurationError, match="changed during"):
        migrate_env(system.engine, env)
    assert env.read_text() == "SESSION_DAYS=5\n"


def test_only_redis_db_zero_is_accepted():
    with pytest.raises(ValidationError):
        ConnectionSettings(
            _env_file=None, postgres_password="test", redis_password="test", redis_db=1
        )


def test_incomplete_config_blocks_startup_and_can_be_repaired(system):
    initialize_config(system.engine)
    with Session(system.engine) as db:
        db.execute(delete(ConfigEntry).where(ConfigEntry.key == "SESSION_DAYS"))
        db.commit()
    with (
        pytest.raises(ConfigurationError),
        TestClient(create_app(engine=system.engine, redis_client=system.limiter.client)),
    ):
        pass
    update_configuration(system.engine, {"SESSION_DAYS": 5})
    assert load_settings(system.engine).session_days == 5


def test_malformed_env_is_never_rewritten(system, tmp_path):
    env = tmp_path / ".env"
    original = "SMTP_PASSWORD='missing closing quote\nSESSION_DAYS=7\n"
    env.write_text(original)
    with pytest.raises(ConfigurationError, match="syntax"):
        migrate_env(system.engine, env)
    assert env.read_text() == original


def test_config_cli_validates_atomic_updates_and_redacts_secrets(system, monkeypatch, capsys):
    import io

    from devops_server.config_cli import main

    initialize_config(system.engine)
    monkeypatch.setattr("devops_server.config_cli.build_engine", lambda _: system.engine)
    monkeypatch.setattr("sys.argv", ["config_cli", "set"])
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"SMTP_PORT":465,"SMTP_SECURITY":"ssl","SMTP_PASSWORD":"synthetic-secret"}'),
    )
    main()
    assert load_settings(system.engine).smtp_password.get_secret_value() == "synthetic-secret"
    with Session(system.engine) as db:
        entry = db.scalar(select(ConfigEntry).where(ConfigEntry.key == "SMTP_PASSWORD"))
        entry.is_secret = False
        db.commit()
    monkeypatch.setattr("sys.argv", ["config_cli", "list"])
    capsys.readouterr()
    main()
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    values = json.loads(output)
    assert values["SMTP_PORT"] == 465
    assert all(values[key] == "<redacted>" for key in SECRET_KEYS)
