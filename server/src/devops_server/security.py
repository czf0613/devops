import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import UTC, datetime

import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request, Response

from devops_server.config import Settings


def client_ip(request: Request) -> str | None:
    """Use the first valid configured CDN header, then the original socket peer."""
    for name in request.app.state.settings.client_ip_headers:
        value = request.headers.get(name, "").split(",", 1)[0].strip()
        if "%" in value:
            continue  # PostgreSQL INET cannot represent an IPv6 interface scope.
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    try:
        return str(ipaddress.ip_address(request.client.host)) if request.client else None
    except ValueError:
        return None


def digest(settings: Settings, value: str) -> str:
    return hmac.new(
        settings.auth_hmac_key.get_secret_value().encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def code_digest(settings: Settings, email: str, purpose: str, nonce: str, code: str) -> str:
    return digest(settings, f"email-code:{purpose}:{email}:{nonce}:{code}")


def set_csrf_cookie(response: Response, settings: Settings, session_token: str = "") -> str:
    nonce = secrets.token_urlsafe(32)
    token = nonce + "." + digest(settings, f"csrf:{session_token}:{nonce}")
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_days * 86400,
    )
    return token


def valid_csrf(request: Request) -> bool:
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    header = request.headers.get("x-csrf-token", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}\.[a-f0-9]{64}", header):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}\.[a-f0-9]{64}", cookie):
        return False
    if not hmac.compare_digest(cookie, header):
        return False
    nonce, signature = cookie.split(".")
    session_token = request.cookies.get(settings.session_cookie_name, "")
    return hmac.compare_digest(signature, digest(settings, f"csrf:{session_token}:{nonce}"))


def set_session_cookie(response: Response, settings: Settings, token: str) -> str:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_days * 86400,
    )
    return set_csrf_cookie(response, settings, token)


def clear_session_cookie(response: Response, settings: Settings) -> None:
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            name, path="/", httponly=True, secure=settings.session_cookie_secure, samesite="lax"
        )


def renew_session_cookies(response: Response, request: Request, expires_at: datetime) -> None:
    """Keep token values stable so other tabs can keep using their cached CSRF token.

    Starlette's ``expires=`` requires an exactly-UTC datetime, while a managed
    PostgreSQL (for example Aliyun RDS) returns timestamptz in its own session
    timezone. Normalize before writing the cookie.
    """
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    expires_at = expires_at.astimezone(UTC)
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name, "")
    values = {settings.session_cookie_name: token}
    csrf = request.cookies.get(settings.csrf_cookie_name, "")
    if re.fullmatch(r"[A-Za-z0-9_-]{43}\.[a-f0-9]{64}", csrf):
        nonce, signature = csrf.split(".")
        if hmac.compare_digest(signature, digest(settings, f"csrf:{token}:{nonce}")):
            values[settings.csrf_cookie_name] = csrf
    for name, value in values.items():
        response.set_cookie(
            name,
            value,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="lax",
            path="/",
            max_age=max(0, int((expires_at - datetime.now(UTC)).total_seconds())),
            expires=expires_at,
        )


def encrypt_password(settings: Settings, user_id: int, password: str) -> tuple[str, bytes, str]:
    raw = password.encode("utf-8")
    if not 8 <= len(raw) <= 72:
        raise ValueError("兼容密码必须为 8–72 个 UTF-8 字节")
    key_id = settings.password_cipher_current_key
    key = base64.b64decode(settings.password_cipher_keys[key_id].get_secret_value(), validate=True)
    nonce = secrets.token_bytes(12)
    aad = f"devops:users:{user_id}:legacy-password:v1".encode()
    ciphertext = b"\x01" + nonce + AESGCM(key).encrypt(nonce, raw, aad)
    password_hash = bcrypt.hashpw(raw, bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()
    return password_hash, ciphertext, key_id


def decrypt_password(settings: Settings, user_id: int, ciphertext: bytes, key_id: str) -> str:
    """Internal legacy integration primitive; never exposed by an HTTP endpoint."""
    if len(ciphertext) < 30 or ciphertext[0] != 1:
        raise ValueError("Unsupported password ciphertext format")
    key = base64.b64decode(settings.password_cipher_keys[key_id].get_secret_value(), validate=True)
    aad = f"devops:users:{user_id}:legacy-password:v1".encode()
    return AESGCM(key).decrypt(ciphertext[1:13], ciphertext[13:], aad).decode("utf-8")
