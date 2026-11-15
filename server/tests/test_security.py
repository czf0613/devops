import base64
from types import SimpleNamespace

import bcrypt
import pytest
from cryptography.exceptions import InvalidTag
from fastapi import Request
from pydantic import SecretStr, ValidationError

from devops_server.config import Settings
from devops_server.mail import MailDeliveryError, SMTPMailer, code_email_html
from devops_server.security import client_ip, decrypt_password, encrypt_password


def test_password_cipher_binds_user_uses_unique_nonces_and_supports_old_keys(auth_settings):
    password = "你好 password "
    first_hash, first_cipher, key_id = encrypt_password(auth_settings, 10, password)
    second_hash, second_cipher, _ = encrypt_password(auth_settings, 10, password)
    assert first_hash != second_hash and first_cipher != second_cipher
    assert bcrypt.checkpw(password.encode(), first_hash.encode())
    assert decrypt_password(auth_settings, 10, first_cipher, key_id) == password
    with pytest.raises(InvalidTag):
        decrypt_password(auth_settings, 11, first_cipher, key_id)
    with pytest.raises(InvalidTag):
        decrypt_password(
            auth_settings, 10, first_cipher[:-1] + bytes([first_cipher[-1] ^ 1]), key_id
        )
    auth_settings.password_cipher_keys["rotated"] = SecretStr(base64.b64encode(b"n" * 32).decode())
    auth_settings.password_cipher_current_key = "rotated"
    assert encrypt_password(auth_settings, 10, password)[2] == "rotated"
    assert decrypt_password(auth_settings, 10, first_cipher, key_id) == password


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"cf-connecting-ip": "203.0.113.4", "x-forwarded-for": "198.51.100.5"}, "203.0.113.4"),
        ({"x-forwarded-for": "198.51.100.5, 10.0.0.1", "x-real-ip": "192.0.2.5"}, "198.51.100.5"),
        ({"x-forwarded-for": "unknown", "x-real-ip": "2001:db8::1"}, "2001:db8::1"),
        ({"cf-connecting-ip": "invalid"}, "192.0.2.1"),
        ({}, "192.0.2.1"),
    ],
)
def test_ip_header_priority_validation_and_socket_fallback(auth_settings, headers, expected):
    request = Request(
        {
            "type": "http",
            "client": ("192.0.2.1", 1234),
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
            "app": SimpleNamespace(state=SimpleNamespace(settings=auth_settings)),
        }
    )
    assert client_ip(request) == expected


def test_production_cannot_disable_secure_cookies():
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            session_cookie_secure=False,
        )


@pytest.mark.parametrize(
    "purpose,action",
    [
        ("login", "登录"),
        ("register", "注册"),
        ("change_email", "更换邮箱"),
        ("legacy_password", "修改兼容密码"),
        ("admin_operation", "验证管理员操作"),
    ],
)
def test_smtp_uses_tls_and_sends_plain_and_html_code(auth_settings, monkeypatch, purpose, action):
    events = []

    class FakeSMTP:
        def __init__(self, host, port, **kwargs):
            events.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, **kwargs):
            events.append("tls")

        def login(self, username, password):
            events.append(("auth", username))

        def send_message(self, message):
            assert message["To"] == "member@example.com"
            assert message["Subject"] == f"DevOps {action}验证码"
            assert message.get_content_type() == "multipart/alternative"
            assert [part.get_content_type() for part in message.iter_parts()] == [
                "text/plain",
                "text/html",
            ]
            plain = message.get_body(preferencelist=("plain",)).get_content()
            html = message.get_body(preferencelist=("html",)).get_content()
            for body in (plain, html):
                assert "123456" in body and action in body
                assert "7 分钟" in body and "仅可使用一次" in body
                assert "DevOps" in body
            assert '<html lang="zh-CN">' in html and 'role="presentation"' in html
            assert "<img" not in html and "<script" not in html
            events.append("sent")
            return {}

    monkeypatch.setattr("devops_server.mail.smtplib.SMTP", FakeSMTP)
    auth_settings.smtp_username = "test-sender"
    auth_settings.smtp_password = SecretStr("test-secret")
    auth_settings.email_code_seconds = 420
    SMTPMailer(auth_settings).send_code("member@example.com", "123456", purpose)
    assert events[1:] == ["tls", ("auth", "test-sender"), "sent"]


def test_email_html_escapes_interpolated_content():
    body = code_email_html('<img src=x onerror="bad()">', "<登录>&", 10)
    assert "<img" not in body
    assert "&lt;img" in body and "&lt;登录&gt;&amp;" in body


def test_smtp_465_uses_implicit_tls_and_from_defaults_to_username(auth_settings, monkeypatch):
    events = []

    class FakeSSL:
        def __init__(self, host, port, **kwargs):
            assert port == 465
            assert kwargs["context"].check_hostname is True
            events.append("ssl")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, username, password):
            events.append("auth")

        def send_message(self, message):
            assert message["From"] == "noreply@example.com"
            events.append("sent")
            return {}

    def plaintext_forbidden(*args, **kwargs):
        raise AssertionError("Port 465 must connect with implicit TLS")

    monkeypatch.setattr("devops_server.mail.smtplib.SMTP_SSL", FakeSSL)
    monkeypatch.setattr("devops_server.mail.smtplib.SMTP", plaintext_forbidden)
    auth_settings.smtp_port = 465
    auth_settings.smtp_security = None
    auth_settings.smtp_from = ""
    auth_settings.smtp_username = "noreply@example.com"
    SMTPMailer(auth_settings).send_code("member@example.com", "123456", "login")
    assert events == ["ssl", "auth", "sent"]
    auth_settings.smtp_username = "non-email-account"
    with pytest.raises(MailDeliveryError):
        SMTPMailer(auth_settings).send_code("member@example.com", "123456", "login")
