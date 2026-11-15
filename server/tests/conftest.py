import base64
import secrets
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from devops_server.config import Settings, get_connections
from devops_server.database import build_engine
from devops_server.main import create_app
from devops_server.models import Role, User, UserRole
from devops_server.rate_limits import EmailCodeRateLimiter, build_redis


@pytest.fixture
def auth_settings(tmp_path):
    return Settings(
        environment="test",
        git_repositories_root=str(tmp_path / "repos"),
        password_cipher_keys={"test-v1": base64.b64encode(b"k" * 32).decode()},
        password_cipher_current_key="test-v1",
        auth_hmac_key="h" * 43,
        bcrypt_rounds=4,
        session_cookie_secure=False,
        allowed_origins=["http://testserver"],
        smtp_host="smtp.test.invalid",
        smtp_port=587,
        smtp_security="starttls",
        smtp_from="noreply@example.com",
    )


class Mailbox:
    def __init__(self):
        self.messages = []

    def send_code(self, email, code, purpose):
        self.messages.append((email, code, purpose))

    def code(self, email, purpose):
        return next(
            code
            for address, code, kind in reversed(self.messages)
            if address == email.lower() and kind == purpose
        )


def mutate(client, method, path, body=None, **kwargs):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    headers = {"x-csrf-token": csrf, "origin": "http://testserver", **kwargs.pop("headers", {})}
    return client.request(method, path, json=body, headers=headers, **kwargs)


def admin_code(system, client, username="outsider"):
    """Use the real email endpoint and isolated mailbox, with synthetic-user cooldown reset."""
    email = f"{username}@example.com"
    system.settings.email_code_email_hourly_limit = 100
    system.limiter.client.delete(system.limiter.keys(email, "127.0.0.1")[2])
    response = mutate(
        client,
        "POST",
        "/api/auth/email-codes",
        {
            "email": email,
            "purpose": "admin_operation",
        },
    )
    assert response.status_code == 202, response.text
    return system.mailbox.code(email, "admin_operation")


def repository_code(system, client):
    email = client.get("/api/me").json()["email"]
    system.settings.email_code_email_hourly_limit = 100
    system.settings.email_code_ip_hourly_limit = 200
    system.limiter.client.delete(system.limiter.keys(email, "127.0.0.1")[2])
    result = mutate(
        client,
        "POST",
        "/api/auth/email-codes",
        {
            "email": email,
            "purpose": "repository_operation",
        },
    )
    assert result.status_code == 202, result.text
    return system.mailbox.code(email, "repository_operation")


def lifecycle_mutate(system, client, method, path, body):
    """Existing lifecycle scenarios explicitly acquire a real mailbox code per operation."""
    return mutate(client, method, path, {**body, "code": repository_code(system, client)})


@pytest.fixture
def limiter():
    settings = get_connections()
    if settings.redis_host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Rate limit tests must use local Redis DB 0")
    client = build_redis(settings)
    namespace = "devops:test:" + secrets.token_hex(12)
    limiter = EmailCodeRateLimiter(client, namespace)
    try:
        client.ping()
        yield limiter
    finally:
        # Never FLUSHDB: tests share DB 0 with the local application.
        keys = list(client.scan_iter(match=namespace + ":*"))
        if keys:
            client.delete(*keys)
        client.close()


@pytest.fixture
def system(auth_settings, limiter):
    settings = get_connections()
    if settings.postgres_host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Authentication tests must use local PostgreSQL")
    root = build_engine(settings)
    schema = "devops_auth_test_" + secrets.token_hex(8)
    with root.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(
        settings.database_url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        mailbox = Mailbox()
        app = create_app(
            auth_settings,
            engine=engine,
            mailer=mailbox,
            redis_client=limiter.client,
            rate_limiter=limiter,
        )
        with ExitStack() as stack:

            def client():
                return stack.enter_context(TestClient(app, client=("127.0.0.1", 50000)))

            def seed(username, role="member"):
                with Session(engine) as db:
                    user = User(
                        username=username, display_name=username, email=f"{username}@example.com"
                    )
                    db.add(user)
                    db.flush()
                    db.add(
                        UserRole(
                            user_id=user.id,
                            role_id=db.scalar(select(Role.id).where(Role.code == role)),
                        )
                    )
                    db.commit()
                    return user.id

            def login(client, email):
                sent = mutate(
                    client, "POST", "/api/auth/email-codes", {"email": email, "purpose": "login"}
                )
                assert sent.status_code == 202, sent.text
                response = mutate(
                    client,
                    "POST",
                    "/api/auth/login",
                    {"email": email, "code": mailbox.code(email, "login")},
                )
                assert response.status_code == 200, response.text
                return response

            yield SimpleNamespace(
                engine=engine,
                app=app,
                mailbox=mailbox,
                client=client,
                seed=seed,
                login=login,
                settings=auth_settings,
                limiter=limiter,
            )
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        root.dispose()
