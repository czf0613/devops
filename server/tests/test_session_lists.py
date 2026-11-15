import secrets
from datetime import UTC, datetime, timedelta

import pytest
from conftest import mutate
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devops_server.models import User, UserSession


def add_session(db, user_id, **overrides):
    now = datetime.now(UTC)
    session = UserSession(
        **{
            "user_id": user_id,
            "token": secrets.token_urlsafe(48),
            "password_version": 0,
            "created_at": now - timedelta(days=2),
            "expires_at": now + timedelta(days=5),
            "last_seen_at": now,
            **overrides,
        }
    )
    db.add(session)
    db.flush()
    return session.id


@pytest.mark.parametrize("as_admin", [False, True])
def test_device_lists_filter_before_counting_and_pagination_and_keep_history(system, as_admin):
    user_id, other_id = system.seed("alice"), system.seed("bob")
    member = system.client()
    system.login(member, "alice@example.com")
    client, path = member, "/api/me/sessions"
    if as_admin:
        system.seed("admin", "admin")
        client = system.client()
        system.login(client, "admin@example.com")
        path = f"/api/users/{user_id}/sessions"
    with Session(system.engine) as db:
        current_id = db.scalar(select(UserSession.id).where(UserSession.user_id == user_id))
        first_id = add_session(db, user_id)
        second_id = add_session(db, user_id)
        add_session(db, user_id, revoked_at=datetime.now(UTC), revoke_reason="manually_revoked")
        add_session(db, user_id, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        add_session(db, user_id, password_version=1)
        add_session(db, other_id)
        db.commit()

    first = client.get(f"{path}?page=1&page_size=2")
    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert [item["id"] for item in first.json()["items"]] == [second_id, first_id]
    second = client.get(f"{path}?page=2&page_size=2").json()
    assert second["total"] == 3
    assert [item["id"] for item in second["items"]] == [current_id]
    assert second["items"][0]["current"] is (not as_admin)
    for item in first.json()["items"] + second["items"]:
        assert item["active"] and item["revoked_at"] is None
        assert item["user_id"] == user_id and "token" not in item

    assert mutate(client, "DELETE", f"{path}/{second_id}").status_code == 200
    remaining = client.get(path).json()
    assert remaining["total"] == 2
    assert [item["id"] for item in remaining["items"]] == [first_id, current_id]
    assert member.get("/api/me").status_code == 200
    with Session(system.engine) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)
            )
            == 6
        )
        assert db.get(UserSession, second_id).revoked_at is not None


def test_admin_device_list_hides_sessions_of_a_disabled_account(system):
    system.seed("admin", "admin")
    admin = system.client()
    system.login(admin, "admin@example.com")
    user_id = system.seed("alice")
    with Session(system.engine) as db:
        session_id = add_session(db, user_id)
        db.get(User, user_id).status = "disabled"
        db.commit()
    response = admin.get(f"/api/users/{user_id}/sessions")
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["total"] == 0
    with Session(system.engine) as db:
        assert db.get(UserSession, session_id) is not None
