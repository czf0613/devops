from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

from conftest import mutate
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from devops_server.mail import MailDeliveryError
from devops_server.models import AuditEvent, EmailChallenge, User, UserSession


def request_code(system, client, email, purpose="login"):
    response = mutate(client, "POST", "/api/auth/email-codes", {"email": email, "purpose": purpose})
    assert response.status_code == 202, response.text
    return system.mailbox.code(email, purpose)


def test_registration_is_passwordless_member_and_cookie_is_http_compatible(system):
    client = system.client()
    code = request_code(system, client, "ALICE@example.com", "register")
    response = mutate(
        client,
        "POST",
        "/api/auth/register",
        {
            "email": "ALICE@example.com",
            "code": code,
            "username": "Alice",
            "display_name": "Alice",
            "phone": "+86.13912345678",
        },
        headers={"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.5, 10.0.0.1"},
    )
    assert response.status_code == 201, response.text
    user = response.json()["user"]
    assert user["roles"] == ["member"]
    assert user["email"] == "alice@example.com"
    assert not user["has_legacy_password"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    assert client.get("/api/me").status_code == 200
    token = client.cookies.get(system.settings.session_cookie_name)
    assert len(token) == 64
    with Session(system.engine) as db:
        stored = db.get(User, user["id"])
        assert stored.password_hash is None and stored.password_ciphertext is None
        assert stored.password_version == 0
        session = db.scalar(select(UserSession))
        assert session.token == token and str(session.login_ip) == "203.0.113.7"
    assert (
        mutate(
            client,
            "POST",
            "/api/auth/register",
            {
                "email": "alice@example.com",
                "code": code,
                "username": "other",
                "display_name": "Other",
            },
        ).status_code
        == 400
    )


def test_registration_rejects_admin_fields_and_reserved_username(system):
    client = system.client()
    code = request_code(system, client, "owner@example.com", "register")
    body = {
        "email": "owner@example.com",
        "code": code,
        "username": "owner",
        "display_name": "Owner",
    }
    for extra in (
        {"roles": ["admin"]},
        {"is_admin": True},
        {"status": "active"},
        {"password": "secret"},
    ):
        response = mutate(client, "POST", "/api/auth/register", body | extra)
        assert response.status_code == 422
    assert (
        mutate(client, "POST", "/api/auth/register", body | {"username": "admin"}).status_code
        == 422
    )
    with Session(system.engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_codes_are_hashed_purpose_bound_and_attempts_persist(system):
    system.seed("alice")
    client = system.client()
    code = request_code(system, client, "alice@example.com")
    with Session(system.engine) as db:
        challenge = db.scalar(select(EmailChallenge))
        assert challenge.code_digest != code and len(challenge.code_digest) == 64
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert (
            mutate(
                client, "POST", "/api/auth/login", {"email": "alice@example.com", "code": wrong}
            ).status_code
            == 400
        )
    assert (
        mutate(
            client, "POST", "/api/auth/login", {"email": "alice@example.com", "code": code}
        ).status_code
        == 400
    )
    with Session(system.engine) as db:
        challenge = db.scalar(select(EmailChallenge))
        assert challenge.attempts == 5 and challenge.consumed_at is not None
        failures = list(db.scalars(select(AuditEvent).where(AuditEvent.result == "failure")))
        assert len(failures) == 6
        assert code not in str([item.details for item in failures])


def test_expired_and_wrong_purpose_code_cannot_login(system):
    system.seed("alice")
    client = system.client()
    register_code = request_code(system, client, "alice@example.com", "register")
    assert (
        mutate(
            client, "POST", "/api/auth/login", {"email": "alice@example.com", "code": register_code}
        ).status_code
        == 400
    )
    with Session(system.engine) as db:
        db.execute(
            update(EmailChallenge).values(
                purpose="login",
                created_at=datetime.now(UTC) - timedelta(hours=1),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        db.commit()
    assert (
        mutate(
            client, "POST", "/api/auth/login", {"email": "alice@example.com", "code": register_code}
        ).status_code
        == 400
    )


def test_send_rate_limit_and_failed_delivery_are_persistent(system):
    client = system.client()
    request_code(system, client, "alice@example.com")
    assert (
        mutate(
            client,
            "POST",
            "/api/auth/email-codes",
            {"email": "ALICE@example.com", "purpose": "register"},
        ).status_code
        == 429
    )
    assert len(system.mailbox.messages) == 1

    def broken(*args):
        raise MailDeliveryError()

    system.mailbox.send_code = broken
    response = mutate(
        client, "POST", "/api/auth/email-codes", {"email": "bob@example.com", "purpose": "login"}
    )
    assert response.status_code == 503
    with Session(system.engine) as db:
        challenge = db.scalar(
            select(EmailChallenge).where(EmailChallenge.email == "bob@example.com")
        )
        assert challenge.consumed_at is not None


def test_missing_smtp_is_explicit_without_issuing_a_code(system):
    system.settings.smtp_host = ""
    response = mutate(
        system.client(),
        "POST",
        "/api/auth/email-codes",
        {"email": "alice@example.com", "purpose": "login"},
    )
    assert response.status_code == 503 and "SMTP" in response.text
    assert not system.mailbox.messages


def test_no_password_login_and_validation_never_echoes_secrets(system):
    response = mutate(
        system.client(),
        "POST",
        "/api/auth/login",
        {"username": "admin", "password": "private-test-password"},
    )
    assert response.status_code == 422
    assert "private-test-password" not in response.text


def test_csrf_required_origin_checked_and_bound_to_session(system):
    client = system.client()
    body = {"email": "alice@example.com", "purpose": "login"}
    assert client.post("/api/auth/email-codes", json=body).status_code == 403
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    assert (
        client.post(
            "/api/auth/email-codes",
            json=body,
            headers={"x-csrf-token": csrf, "origin": "https://evil.example"},
        ).status_code
        == 403
    )
    system.seed("alice")
    system.login(client, "alice@example.com")
    assert client.post("/api/auth/logout", headers={"x-csrf-token": csrf}).status_code == 403
    assert mutate(client, "POST", "/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_one_code_can_create_only_one_session_under_concurrency(system):
    system.seed("alice")
    first, second = system.client(), system.client()
    code = request_code(system, first, "alice@example.com")
    barrier = Barrier(2)

    def login(client):
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        barrier.wait(timeout=5)
        return client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "code": code},
            headers={"x-csrf-token": csrf},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(login, [first, second]))
    assert sorted(results) == [200, 400]
    with Session(system.engine) as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 1


def test_login_rechecks_disabled_status_after_waiting_for_user_lock(system):
    user_id = system.seed("alice")
    client = system.client()
    code = request_code(system, client, "alice@example.com")
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    reached_user_lock = Event()

    def before_query(connection, cursor, statement, parameters, context, many):
        if "FOR UPDATE" in statement and "users.email =" in statement:
            reached_user_lock.set()

    event.listen(system.engine, "before_cursor_execute", before_query)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with Session(system.engine) as blocker:
                blocker.scalar(select(User).where(User.id == user_id).with_for_update())
                future = pool.submit(
                    client.post,
                    "/api/auth/login",
                    json={"email": "alice@example.com", "code": code},
                    headers={"x-csrf-token": csrf},
                )
                assert reached_user_lock.wait(timeout=5)
                blocker.execute(update(User).where(User.id == user_id).values(status="disabled"))
                blocker.commit()
            assert future.result(timeout=5).status_code == 403
        finally:
            event.remove(system.engine, "before_cursor_execute", before_query)
    with Session(system.engine) as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_session_token_unique_collision_retries(system, monkeypatch):
    import secrets

    system.seed("alice")
    system.seed("bob")
    original = secrets.token_urlsafe
    tokens = iter(["A" * 64, "A" * 64, "B" * 64])

    def random_value(size):
        return next(tokens) if size == 48 else original(size)

    monkeypatch.setattr("devops_server.services.secrets.token_urlsafe", random_value)
    first, second = system.client(), system.client()
    system.login(first, "alice@example.com")
    system.login(second, "bob@example.com")
    assert first.cookies.get(system.settings.session_cookie_name) == "A" * 64
    assert second.cookies.get(system.settings.session_cookie_name) == "B" * 64
    assert second.get("/api/me").json()["username"] == "bob"


def test_failed_commit_never_returns_success_or_login_cookie(system, monkeypatch):
    system.seed("alice")
    client = system.client()
    code = request_code(system, client, "alice@example.com")

    def fail_commit(self):
        raise OperationalError("COMMIT", {}, Exception("synthetic database failure"))

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_commit)
        response = mutate(
            client, "POST", "/api/auth/login", {"email": "alice@example.com", "code": code}
        )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    with Session(system.engine) as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0
        assert db.scalar(select(EmailChallenge.consumed_at)) is None
