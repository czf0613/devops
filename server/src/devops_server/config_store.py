"""Shared, typed configuration stored directly as editable PostgreSQL JSON values."""

import base64
import json
import secrets
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from devops_server.config import DEVOPS_ROOT, Settings
from devops_server.models import ConfigEntry

SECRET_KEYS = frozenset({"PASSWORD_CIPHER_KEYS", "AUTH_HMAC_KEY", "SMTP_PASSWORD"})
RUNTIME_KEYS = frozenset(name.upper() for name in Settings.model_fields)


class ConfigurationError(Exception):
    pass


def reveal(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: reveal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [reveal(item) for item in value]
    return value


def settings_values(settings: Settings) -> dict[str, Any]:
    # model_dump deliberately redacts SecretStr values; persist the real values explicitly.
    return {name.upper(): reveal(getattr(settings, name)) for name in Settings.model_fields}


def validate_values(values: dict[str, Any]) -> Settings:
    missing = RUNTIME_KEYS - values.keys()
    if missing:
        raise ConfigurationError("Configuration not initialized; run make config-init")
    settings = Settings.model_validate(values)
    settings.require_auth_secrets()
    return settings


def read_values(db: Session) -> dict[str, Any]:
    return {
        entry.key: entry.value
        for entry in db.scalars(select(ConfigEntry).order_by(ConfigEntry.key))
    }


def load_settings(engine: Engine) -> Settings:
    with Session(engine) as db:
        return validate_values(read_values(db))


def development_defaults() -> dict[str, Any]:
    return settings_values(
        Settings(
            environment="development",
            password_cipher_keys={"local-v1": base64.b64encode(secrets.token_bytes(32)).decode()},
            auth_hmac_key=secrets.token_urlsafe(32),
            session_cookie_name="devops_dev_session",
            csrf_cookie_name="devops_dev_csrf",
            session_cookie_secure=False,
            allowed_origins=["http://127.0.0.1:5188", "http://localhost:5188"],
            git_repositories_root=str(DEVOPS_ROOT / ".data" / "git-repos"),
        )
    )


def parse_legacy_values(legacy: dict[str, str | None]) -> dict[str, Any]:
    values = {}
    defaults = settings_values(Settings())
    for key, raw in legacy.items():
        if key not in RUNTIME_KEYS:
            raise ConfigurationError(f"Unknown runtime configuration key {key}")
        if isinstance(defaults[key], str):
            values[key] = raw or ""
        elif raw in {None, ""}:
            values[key] = None
        else:
            try:
                values[key] = json.loads(raw)
            except ValueError:
                values[key] = raw
    return values


def initialize_config(engine: Engine, legacy: dict[str, str | None] | None = None) -> Settings:
    imported = parse_legacy_values(legacy or {})
    with Session(engine) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('devops:config', 0))"))
        exists = db.scalar(select(ConfigEntry.id).limit(1)) is not None
        values = read_values(db) if exists else development_defaults()
        candidate = validate_values(values | imported)
        # Persist the conventional transport explicitly when migrating an empty SMTP_SECURITY.
        if candidate.smtp_security is None:
            candidate.smtp_security = candidate.smtp_transport
        normalized = settings_values(candidate)
        if exists:
            validate_values(values)
            if any(normalized[key] != values[key] for key in imported):
                raise ConfigurationError(
                    "Existing database configuration differs from .env; resolve with config_cli set"
                )
        else:
            db.add_all(
                ConfigEntry(key=key, value=value, is_secret=key in SECRET_KEYS)
                for key, value in normalized.items()
            )
    # Confirm that a new connection can read the committed configuration before removing .env data.
    return load_settings(engine)


def update_configuration(engine: Engine, updates: dict[str, Any]) -> None:
    unknown = updates.keys() - RUNTIME_KEYS
    if unknown:
        raise ConfigurationError("Unknown runtime configuration key")
    with Session(engine) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('devops:config', 0))"))
        settings = validate_values(read_values(db) | updates)
        normalized = settings_values(settings)
        entries = {
            entry.key: entry
            for entry in db.scalars(select(ConfigEntry).where(ConfigEntry.key.in_(updates)))
        }
        for key in updates:
            entry = entries.get(key)
            if entry is None:
                entry = ConfigEntry(key=key)
                db.add(entry)
            entry.value = normalized[key]
            entry.is_secret = key in SECRET_KEYS
