import base64
from email.utils import formataddr, parseaddr
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

DEVOPS_ROOT = Path(__file__).resolve().parents[3]


class ConnectionSettings(BaseSettings):
    """Only the connections needed to reach the shared configuration store."""

    model_config = SettingsConfigDict(
        env_file=DEVOPS_ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=55432, ge=1, le=65535)
    postgres_db: str = "devops"
    postgres_user: str = "devops"
    postgres_password: SecretStr

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=56379, ge=1, le=65535)
    redis_username: str = "default"
    redis_password: SecretStr
    # Redis defaults to 16 logical databases; other projects may use the rest.
    redis_db: int = Field(default=0, ge=0, le=15)
    redis_ssl: bool = False

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


class Settings(BaseModel):
    """Runtime configuration loaded from PostgreSQL, without environment overrides."""

    model_config = ConfigDict(
        extra="forbid", hide_input_in_errors=True, alias_generator=str.upper, populate_by_name=True
    )

    environment: Literal["development", "production", "test"] = "production"
    password_cipher_keys: dict[str, SecretStr] = Field(default_factory=dict)
    password_cipher_current_key: str = "local-v1"
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    auth_hmac_key: SecretStr = SecretStr("")
    session_days: int = Field(default=7, ge=1, le=30)
    session_cookie_name: str = "devops_session"
    csrf_cookie_name: str = "devops_csrf"
    session_cookie_secure: bool = True
    allowed_origins: list[str] = Field(default_factory=list)
    client_ip_headers: list[str] = Field(
        default_factory=lambda: ["CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"]
    )
    registration_enabled: bool = True
    # OAuth identifies the Git user; repository membership decides each operation.
    git_oauth_enabled: bool = False
    git_repositories_root: str = "/mnt/git-repos"
    registry_enabled: bool = False
    registry_internal_url: str = "http://registry:5000"
    email_code_seconds: int = Field(default=600, ge=60, le=1800)
    email_code_resend_seconds: int = Field(default=60, ge=1)
    email_code_attempts: int = Field(default=5, ge=1, le=10)
    email_code_email_hourly_limit: int = Field(default=5, ge=1)
    email_code_ip_hourly_limit: int = Field(default=30, ge=1)
    smtp_host: str = ""
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_security: Literal["none", "starttls", "ssl"] | None = None
    smtp_from: str = ""
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=30)

    @field_validator("git_repositories_root")
    @classmethod
    def absolute_repository_root(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("GIT_REPOSITORIES_ROOT must be an absolute path")
        return value

    @field_validator("registry_internal_url")
    @classmethod
    def registry_origin(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("REGISTRY_INTERNAL_URL must be a trusted HTTP origin")
        return value.rstrip("/")

    @field_validator("smtp_port", "smtp_security", mode="before")
    @classmethod
    def empty_smtp_value(cls, value):
        return None if value == "" else value

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_port and self.smtp_transport and self.smtp_sender)

    @property
    def smtp_transport(self) -> str | None:
        return self.smtp_security or {465: "ssl", 587: "starttls"}.get(self.smtp_port)

    @property
    def smtp_sender(self) -> str:
        sender = self.smtp_from or self.smtp_username
        if "\r" in sender or "\n" in sender:
            return ""
        try:
            name, address = parseaddr(sender)
            normalized = validate_email(address, check_deliverability=False).normalized
            return formataddr((name, normalized)) if name else normalized
        except EmailNotValidError:
            return ""

    @model_validator(mode="after")
    def validate_security(self) -> Settings:
        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("Production requires SESSION_COOKIE_SECURE=true")
        for key in self.password_cipher_keys.values():
            try:
                raw = base64.b64decode(key.get_secret_value(), validate=True)
            except ValueError:
                raise ValueError("Password encryption keys must use valid Base64") from None
            if len(raw) != 32:
                raise ValueError("Password encryption keys must contain 32 random bytes")
        return self

    def require_auth_secrets(self) -> None:
        if len(self.auth_hmac_key.get_secret_value()) < 43:
            raise ValueError("Configure AUTH_HMAC_KEY with at least 32 random bytes")
        if self.password_cipher_current_key not in self.password_cipher_keys:
            raise ValueError("PASSWORD_CIPHER_CURRENT_KEY must exist in PASSWORD_CIPHER_KEYS")
        if not self.allowed_origins:
            raise ValueError("Configure ALLOWED_ORIGINS for browser authentication")


@lru_cache
def get_connections() -> ConnectionSettings:
    return ConnectionSettings()
