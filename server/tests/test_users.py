from datetime import UTC, datetime, timedelta

import pytest
from conftest import mutate
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from devops_server.models import AuditEvent, User, UserRole, UserSession
from devops_server.security import decrypt_password


def admin_client(system):
    user_id = system.seed("admin", "admin")
    client = system.client()
    system.login(client, "admin@example.com")
    return client, user_id


def test_admin_created_in_database_can_manage_members_but_cannot_grant_admin(system):
    admin, _ = admin_client(system)
    body = {"username": "alice", "email": "ALICE@example.com", "display_name": "Alice"}
    created = mutate(admin, "POST", "/api/users", body)
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    assert created.json()["roles"] == ["member"] and created.json()["phone"] is None
    assert mutate(admin, "POST", "/api/users", body | {"roles": ["admin"]}).status_code == 422
    assert (
        mutate(admin, "PUT", f"/api/users/{user_id}/roles", {"roles": ["admin"]}).status_code == 403
    )
    assert mutate(admin, "POST", "/api/users", body | {"username": "other"}).status_code == 409
    listed = admin.get("/api/users?q=alice")
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert all(
        name not in listed.text for name in ("password_hash", "password_ciphertext", "token")
    )
    response = mutate(
        admin,
        "PATCH",
        f"/api/users/{user_id}",
        {"display_name": "Alice Chen", "phone": "+86.13912345678"},
    )
    assert response.status_code == 200 and response.json()["phone"] == "+86.13912345678"


def test_members_cannot_read_or_modify_other_users_or_audit(system):
    alice_id, bob_id = system.seed("alice"), system.seed("bob")
    client = system.client()
    system.login(client, "alice@example.com")
    for path in (
        "/api/users",
        f"/api/users/{bob_id}",
        "/api/audit-events",
        "/api/roles",
        f"/api/users/{bob_id}/sessions",
    ):
        assert client.get(path).status_code == 403
    assert (
        mutate(client, "PATCH", f"/api/users/{bob_id}", {"display_name": "Hacked"}).status_code
        == 403
    )
    assert mutate(client, "PATCH", "/api/me", {"roles": ["admin"]}).status_code == 422
    assert mutate(client, "PATCH", "/api/me", {"email": "bob@example.com"}).status_code == 422
    profile = mutate(client, "PATCH", "/api/me", {"display_name": "Alice Updated", "phone": None})
    assert profile.status_code == 200 and profile.json()["id"] == alice_id


@pytest.mark.parametrize(
    "phone", ["13912345678", "+86 13912345678", "+0.12345", "+861.12345678901234"]
)
def test_phone_validation(system, phone):
    admin, _ = admin_client(system)
    response = mutate(
        admin,
        "POST",
        "/api/users",
        {
            "username": "alice",
            "display_name": "Alice",
            "email": "alice@example.com",
            "phone": phone,
        },
    )
    assert response.status_code == 422


def test_disabled_account_cannot_login_and_reenable_does_not_restore_sessions(system):
    admin, _ = admin_client(system)
    user_id = system.seed("alice")
    member = system.client()
    system.login(member, "alice@example.com")
    assert (
        mutate(admin, "PATCH", f"/api/users/{user_id}", {"status": "disabled"}).status_code == 200
    )
    assert member.get("/api/me").status_code == 401
    pending = system.client()
    system.limiter.client.delete(system.limiter.keys("alice@example.com", "127.0.0.1")[2])
    assert (
        mutate(
            pending,
            "POST",
            "/api/auth/email-codes",
            {"email": "alice@example.com", "purpose": "login"},
        ).status_code
        == 202
    )
    code = system.mailbox.code("alice@example.com", "login")
    assert (
        mutate(
            pending, "POST", "/api/auth/login", {"email": "alice@example.com", "code": code}
        ).status_code
        == 403
    )
    assert mutate(admin, "PATCH", f"/api/users/{user_id}", {"status": "active"}).status_code == 200
    assert member.get("/api/me").status_code == 401


def test_last_admin_and_admin_identity_are_protected(system):
    admin, admin_id = admin_client(system)
    assert (
        mutate(admin, "PATCH", f"/api/users/{admin_id}", {"status": "disabled"}).status_code == 409
    )
    assert (
        mutate(admin, "PATCH", f"/api/users/{admin_id}", {"email": "other@example.com"}).status_code
        == 403
    )
    assert mutate(admin, "PUT", f"/api/users/{admin_id}/roles", {"roles": []}).status_code == 200
    assert admin.get("/api/me").json()["roles"] == ["admin"]


def test_role_removal_takes_effect_without_reissuing_token(system):
    admin, admin_id = admin_client(system)
    assert admin.get("/api/users").status_code == 200
    with Session(system.engine) as db:
        db.execute(delete(UserRole).where(UserRole.user_id == admin_id))
        db.commit()
    assert admin.get("/api/me").status_code == 200
    assert admin.get("/api/users").status_code == 403


def test_setting_and_clearing_password_preserves_valid_email_sessions(system):
    admin, _ = admin_client(system)
    user_id = system.seed("alice")
    member = system.client()
    system.login(member, "alice@example.com")
    system.limiter.client.delete(system.limiter.keys("alice@example.com", "127.0.0.1")[2])
    assert (
        mutate(
            member,
            "POST",
            "/api/auth/email-codes",
            {"email": "alice@example.com", "purpose": "legacy_password"},
        ).status_code
        == 202
    )
    code = system.mailbox.code("alice@example.com", "legacy_password")
    password = "你" * 24  # Exactly 72 UTF-8 bytes.
    assert (
        mutate(
            member, "PUT", "/api/me/legacy-password", {"password": password, "code": code}
        ).status_code
        == 200
    )
    assert member.get("/api/me").status_code == 200
    with Session(system.engine) as db:
        user = db.get(User, user_id)
        assert user.password_version == 1 and user.password_hash.startswith("$2b$")
        assert (
            decrypt_password(
                system.settings, user.id, user.password_ciphertext, user.password_key_id
            )
            == password
        )
        assert (
            db.scalar(select(UserSession.revoked_at).where(UserSession.user_id == user_id)) is None
        )
        assert password not in str(list(db.scalars(select(AuditEvent.details))))
    too_long = mutate(
        admin, "PUT", f"/api/users/{user_id}/legacy-password", {"password": password + "a"}
    )
    assert too_long.status_code == 422 and password not in too_long.text
    assert (
        mutate(
            admin, "PUT", f"/api/users/{user_id}/legacy-password", {"password": None}
        ).status_code
        == 200
    )
    with Session(system.engine) as db:
        user = db.get(User, user_id)
        assert user.password_version == 2
        assert (
            user.password_hash is None
            and user.password_ciphertext is None
            and user.password_key_id is None
        )
    assert member.get("/api/me").status_code == 200


def test_email_change_requires_own_purpose_bound_code_and_revokes_login(system):
    system.seed("alice")
    client = system.client()
    system.login(client, "alice@example.com")
    assert (
        mutate(
            client,
            "POST",
            "/api/auth/email-codes",
            {"email": "new@example.com", "purpose": "change_email"},
        ).status_code
        == 202
    )
    code = system.mailbox.code("new@example.com", "change_email")
    response = mutate(client, "POST", "/api/me/email", {"email": "NEW@example.com", "code": code})
    assert response.status_code == 200
    assert client.get("/api/me").status_code == 401
    with Session(system.engine) as db:
        assert db.scalar(select(User.email)) == "new@example.com"


def test_session_lists_exclude_tokens_and_cannot_revoke_another_user_session(system):
    alice_id, bob_id = system.seed("alice"), system.seed("bob")
    alice, bob = system.client(), system.client()
    system.login(alice, "alice@example.com")
    system.login(bob, "bob@example.com")
    data = alice.get("/api/me/sessions").json()
    assert data["total"] == 1 and data["items"][0]["current"]
    assert "token" not in data["items"][0]
    bob_session = bob.get("/api/me/sessions").json()["items"][0]["id"]
    assert mutate(alice, "DELETE", f"/api/me/sessions/{bob_session}").status_code == 404
    assert bob.get("/api/me").status_code == 200
    assert mutate(alice, "DELETE", "/api/me/sessions").status_code == 200
    assert alice.get("/api/me").status_code == 401
    with Session(system.engine) as db:
        assert db.scalar(select(UserSession.revoked_at).where(UserSession.user_id == alice_id))
        assert (
            db.scalar(select(UserSession.revoked_at).where(UserSession.user_id == bob_id)) is None
        )


def test_expired_sessions_and_changed_password_version_are_rejected(system):
    user_id = system.seed("alice")
    client = system.client()
    system.login(client, "alice@example.com")
    with Session(system.engine) as db:
        db.execute(update(User).where(User.id == user_id).values(password_version=1))
        db.commit()
    assert client.get("/api/me").status_code == 401
    with Session(system.engine) as db:
        db.execute(update(User).where(User.id == user_id).values(password_version=0))
        db.execute(
            update(UserSession).values(
                created_at=datetime.now(UTC) - timedelta(days=2),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        db.commit()
    assert client.get("/api/me").status_code == 401
