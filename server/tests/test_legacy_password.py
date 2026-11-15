import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from conftest import mutate
from sqlalchemy import select
from sqlalchemy.orm import Session

from devops_server.models import EmailChallenge, User, UserSession


def verification_code(system, client, email):
    system.limiter.client.delete(system.limiter.keys(email, "127.0.0.1")[2])
    response = mutate(
        client,
        "POST",
        "/api/auth/email-codes",
        {
            "email": email,
            "purpose": "legacy_password",
        },
    )
    assert response.status_code == 202
    return system.mailbox.code(email, "legacy_password")


def test_password_change_requires_own_current_email_and_dedicated_code(system):
    alice_id = system.seed("alice")
    system.seed("bob")
    alice, bob, visitor = system.client(), system.client(), system.client()
    system.login(alice, "alice@example.com")
    system.login(bob, "bob@example.com")
    assert (
        mutate(
            visitor,
            "POST",
            "/api/auth/email-codes",
            {
                "email": "alice@example.com",
                "purpose": "legacy_password",
            },
        ).status_code
        == 401
    )
    assert (
        mutate(
            alice,
            "POST",
            "/api/auth/email-codes",
            {
                "email": "bob@example.com",
                "purpose": "legacy_password",
            },
        ).status_code
        == 422
    )
    assert (
        mutate(
            alice,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": "valid-password",
            },
        ).status_code
        == 422
    )
    login_code = system.mailbox.code("alice@example.com", "login")
    assert (
        mutate(
            alice,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": "valid-password",
                "code": login_code,
            },
        ).status_code
        == 400
    )
    code = verification_code(system, alice, "alice@example.com")
    assert (
        mutate(
            bob,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": "valid-password",
                "code": code,
            },
        ).status_code
        == 400
    )
    wrong = "000000" if code != "000000" else "111111"
    assert (
        mutate(
            alice,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": "valid-password",
                "code": wrong,
            },
        ).status_code
        == 400
    )
    with Session(system.engine) as db:
        challenge = db.scalar(
            select(EmailChallenge).where(EmailChallenge.purpose == "legacy_password")
        )
        assert challenge.subject_user_id == alice_id and challenge.attempts == 1
        assert db.get(User, alice_id).password_hash is None
    saved = mutate(
        alice,
        "PUT",
        "/api/me/legacy-password",
        {
            "password": "valid-password",
            "code": code,
        },
    )
    assert saved.status_code == 200 and saved.json()["user"]["has_legacy_password"]
    assert "set-cookie" not in saved.headers
    assert (
        mutate(
            alice,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": None,
                "code": code,
            },
        ).status_code
        == 400
    )
    fresh = verification_code(system, alice, "alice@example.com")
    assert (
        mutate(
            alice,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": None,
                "code": fresh,
            },
        ).status_code
        == 200
    )
    assert alice.get("/api/me").status_code == 200
    assert not alice.get("/api/me").json()["has_legacy_password"]


def test_admin_can_only_clear_and_cannot_read_or_set_password(system):
    system.seed("operator", "admin")
    user_id = system.seed("alice")
    admin, member = system.client(), system.client()
    system.login(admin, "operator@example.com")
    system.login(member, "alice@example.com")
    code = verification_code(system, member, "alice@example.com")
    assert (
        mutate(
            member,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": "member-password",
                "code": code,
            },
        ).status_code
        == 200
    )
    endpoint = f"/api/users/{user_id}/legacy-password"
    rejected = mutate(admin, "PUT", endpoint, {"password": "admin-replacement"})
    assert rejected.status_code == 422 and "admin-replacement" not in rejected.text
    assert admin.get(endpoint).status_code == 405
    assert mutate(member, "PUT", endpoint, {"password": None}).status_code == 403
    assert mutate(admin, "PUT", endpoint, {"password": None}).status_code == 200
    assert member.get("/api/me").status_code == 200
    with Session(system.engine) as db:
        user = db.get(User, user_id)
        assert user.password_hash is None and user.password_ciphertext is None


def test_password_change_keeps_active_sessions_without_restoring_invalid_ones(system):
    user_id = system.seed("alice")
    client = system.client()
    system.login(client, "alice@example.com")
    now = datetime.now(UTC)
    states = [
        {"password_version": 0, "expires_at": now + timedelta(days=1)},
        {"password_version": 0, "expires_at": now - timedelta(hours=1)},
        {"password_version": 0, "expires_at": now + timedelta(days=1), "revoked_at": now},
        {"password_version": 10, "expires_at": now + timedelta(days=1)},
    ]
    with Session(system.engine) as db:
        for state in states:
            db.add(
                UserSession(
                    user_id=user_id,
                    token=secrets.token_urlsafe(48),
                    created_at=now - timedelta(days=1),
                    **state,
                )
            )
        db.commit()
    code = verification_code(system, client, "alice@example.com")
    assert (
        mutate(
            client,
            "PUT",
            "/api/me/legacy-password",
            {
                "password": "valid-password",
                "code": code,
            },
        ).status_code
        == 200
    )
    sessions = client.get("/api/me/sessions").json()["items"]
    assert sum(item["active"] for item in sessions) == 2
    with Session(system.engine) as db:
        rows = list(db.scalars(select(UserSession).order_by(UserSession.id)))
        assert [row.password_version for row in rows] == [1, 1, 0, 0, 10]
        assert rows[3].revoked_at is not None


def test_password_code_cannot_be_consumed_twice_under_concurrency(system):
    user_id = system.seed("alice")
    client = system.client()
    system.login(client, "alice@example.com")
    code = verification_code(system, client, "alice@example.com")
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]

    def change():
        return client.put(
            "/api/me/legacy-password",
            json={
                "password": "valid-password",
                "code": code,
            },
            headers={"x-csrf-token": csrf, "origin": "http://testserver"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: change(), range(2))) == [200, 400]
    with Session(system.engine) as db:
        assert db.get(User, user_id).password_version == 1
