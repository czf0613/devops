from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from devops_server.dependencies import Authenticated
from devops_server.models import User, UserSession


@pytest.fixture
def browser_session(system, monkeypatch):
    clock = SimpleNamespace(current=datetime.now(UTC))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.current

    for module in ("dependencies", "services", "security"):
        monkeypatch.setattr(f"devops_server.{module}.datetime", Clock)
    user_id = system.seed("alice")
    client = system.client()
    login = system.login(client, "alice@example.com")
    with Session(system.engine) as db:
        session_id = db.scalar(select(UserSession.id))
    return SimpleNamespace(
        client=client,
        clock=clock,
        user_id=user_id,
        session_id=session_id,
        token=client.cookies.get(system.settings.session_cookie_name),
        csrf=login.json()["csrf_token"],
    )


def test_active_session_renews_beyond_original_expiry_and_preserves_csrf(system, browser_session):
    browser = browser_session
    started = browser.clock.current
    for days in (6, 12, 18):
        browser.clock.current = started + timedelta(days=days)
        response = browser.client.get("/api/me")
        assert response.status_code == 200
        cookies = SimpleCookie()
        for value in response.headers.get_list("set-cookie"):
            cookies.load(value)
        expiry = browser.clock.current + timedelta(days=system.settings.session_days)
        for name, expected in (
            (system.settings.session_cookie_name, browser.token),
            (system.settings.csrf_cookie_name, browser.csrf),
        ):
            cookie = cookies[name]
            assert cookie.value == expected
            assert int(cookie["max-age"]) == system.settings.session_days * 86400
            assert parsedate_to_datetime(cookie["expires"]) == expiry.replace(microsecond=0)
            assert cookie["httponly"] and cookie["samesite"] == "lax"
        with Session(system.engine) as db:
            session = db.get(UserSession, browser.session_id)
            assert session.created_at == started
            assert session.expires_at == expiry
            assert session.last_seen_at == browser.clock.current
    # An already-open tab can still write with the CSRF value received at login.
    response = browser.client.patch(
        "/api/me", json={"display_name": "Alice Updated"}, headers={"x-csrf-token": browser.csrf}
    )
    assert response.status_code == 200


def test_renewal_is_throttled_and_updates_secure_cookies(system, browser_session):
    browser = browser_session
    started = browser.clock.current
    browser.clock.current += timedelta(seconds=59)
    response = browser.client.get("/api/me")
    assert response.status_code == 200 and "set-cookie" not in response.headers
    with Session(system.engine) as db:
        assert db.get(UserSession, browser.session_id).last_seen_at == started
    browser.clock.current += timedelta(seconds=1)
    system.settings.session_cookie_secure = True
    response = browser.client.get("/api/me")
    assert response.status_code == 200
    assert all("Secure" in value for value in response.headers.get_list("set-cookie"))


def test_live_session_near_expiry_renews_despite_recent_activity(system, browser_session):
    browser = browser_session
    with Session(system.engine) as db:
        db.get(UserSession, browser.session_id).expires_at = browser.clock.current + timedelta(
            seconds=30
        )
        db.commit()
    assert browser.client.get("/api/me").status_code == 200
    with Session(system.engine) as db:
        assert db.get(UserSession, browser.session_id).expires_at == (
            browser.clock.current + timedelta(days=7)
        )


@pytest.mark.parametrize("invalid", ["expired", "revoked", "disabled", "password_version"])
def test_invalid_sessions_never_renew(system, browser_session, invalid):
    browser = browser_session
    browser.clock.current += timedelta(days=7)
    with Session(system.engine) as db:
        session = db.get(UserSession, browser.session_id)
        if invalid != "expired":
            session.expires_at += timedelta(days=1)
        if invalid == "revoked":
            session.revoked_at = browser.clock.current
        elif invalid == "disabled":
            db.get(User, browser.user_id).status = "disabled"
        elif invalid == "password_version":
            db.get(User, browser.user_id).password_version += 1
        expiry = session.expires_at
        db.commit()
    response = browser.client.get("/api/me")
    assert response.status_code == 401 and "set-cookie" not in response.headers
    with Session(system.engine) as db:
        assert db.get(UserSession, browser.session_id).expires_at == expiry


@pytest.mark.parametrize("path", ["/api/users", "/api/health", "/api/auth/csrf"])
def test_rejected_or_unauthenticated_endpoints_do_not_renew(system, browser_session, path):
    browser = browser_session
    browser.clock.current += timedelta(days=6)
    response = browser.client.get(path)
    assert response.status_code == (403 if path == "/api/users" else 200)
    assert not any(
        value.startswith(system.settings.session_cookie_name + "=")
        for value in response.headers.get_list("set-cookie")
    )
    with Session(system.engine) as db:
        session = db.get(UserSession, browser.session_id)
        assert session.expires_at == session.created_at + timedelta(days=7)


@pytest.mark.parametrize("path", ["/api/auth/logout", "/api/me/sessions", "current"])
def test_logout_cannot_reissue_cleared_session_cookies(system, browser_session, path):
    browser = browser_session
    browser.clock.current += timedelta(days=6)
    if path == "current":
        path = f"/api/me/sessions/{browser.session_id}"
    response = browser.client.request(
        "POST" if path.endswith("logout") else "DELETE",
        path,
        headers={"x-csrf-token": browser.csrf},
    )
    assert response.status_code == 200
    assert all("Max-Age=0" in value for value in response.headers.get_list("set-cookie"))
    with Session(system.engine) as db:
        session = db.get(UserSession, browser.session_id)
        assert session.revoked_at is not None
        assert session.expires_at == session.created_at + timedelta(days=7)


def test_commit_failure_does_not_send_renewal_cookies(system, browser_session, monkeypatch):
    browser = browser_session
    browser.clock.current += timedelta(days=6)

    def fail_commit(self):
        raise OperationalError("COMMIT", {}, Exception("synthetic database failure"))

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_commit)
        response = browser.client.get("/api/me")
    assert response.status_code == 503 and "set-cookie" not in response.headers
    with Session(system.engine) as db:
        session = db.get(UserSession, browser.session_id)
        assert session.expires_at == session.created_at + timedelta(days=7)


def test_response_objects_also_receive_renewal_cookies(system, browser_session):
    @system.app.get("/test/session-download")
    def download(actor: Authenticated):
        return Response(b"sample", media_type="application/octet-stream")

    browser_session.clock.current += timedelta(days=6)
    response = browser_session.client.get("/test/session-download")
    assert response.status_code == 200 and response.content == b"sample"
    assert len(response.headers.get_list("set-cookie")) == 2


@pytest.mark.parametrize("invalidate", ["expiry", "revocation"])
def test_renewal_waiting_for_lock_cannot_restore_invalid_session(
    system, browser_session, invalidate
):
    browser = browser_session
    browser.clock.current += timedelta(days=6)
    locking = Event()

    def observe_lock(conn, cursor, statement, parameters, context, executemany):
        if "user_sessions" in statement and "FOR UPDATE" in statement:
            locking.set()

    with Session(system.engine) as blocker:
        session = blocker.scalar(select(UserSession).with_for_update())
        expiry = session.expires_at
        event.listen(system.engine, "before_cursor_execute", observe_lock)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(browser.client.get, "/api/me")
                try:
                    assert locking.wait(timeout=5)
                    if invalidate == "expiry":
                        browser.clock.current = expiry
                    else:
                        session.revoked_at = browser.clock.current
                finally:
                    blocker.commit()
                response = future.result(timeout=5)
        finally:
            event.remove(system.engine, "before_cursor_execute", observe_lock)
    assert "set-cookie" not in response.headers
    with Session(system.engine) as db:
        assert db.get(UserSession, browser.session_id).expires_at == expiry


def test_parallel_requests_only_renew_once(system, browser_session):
    browser = browser_session
    browser.clock.current += timedelta(days=6)
    clients = [system.client(), system.client()]
    for client in clients:
        client.cookies.update(browser.client.cookies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda client: client.get("/api/me"), clients))
    assert all(response.status_code == 200 for response in responses)
    assert sum("set-cookie" in response.headers for response in responses) == 1
