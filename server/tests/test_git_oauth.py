import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import mutate
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from devops_server import git_oauth
from devops_server.models import GitOAuthGrant, GitOAuthRequest, GitOAuthToken, User

VERIFIER = "v" * 64
CALLBACK = "http://127.0.0.1:49152/"
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)


def start(client, **overrides):
    return client.get(
        "/oauth/authorize",
        params={
            "client_id": "devops-gcm",
            "response_type": "code",
            "redirect_uri": CALLBACK,
            "scope": "git:read git:write",
            "state": "gcm-random-state",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            **overrides,
        },
        follow_redirects=False,
    )


def decide(client, approve=True, **overrides):
    response = start(client, **overrides)
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
    path = f"/api/oauth/authorization-requests/{request_id}"
    details = client.get(path)
    assert details.status_code == 200, details.text
    response = mutate(client, "POST", path + "/decision", {"approve": approve})
    return response, path


def exchange(client, code, **overrides):
    return client.post(
        "/oauth/token",
        data={
            "client_id": "devops-gcm",
            "client_secret": "",
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": VERIFIER,
            "redirect_uri": CALLBACK,
            **overrides,
        },
    )


def issue(client, **overrides):
    response, _ = decide(client, **overrides)
    assert response.status_code == 200, response.text
    callback = parse_qs(urlsplit(response.json()["redirect_uri"]).query)
    assert callback["state"] == ["gcm-random-state"]
    result = exchange(client, callback["code"][0])
    assert result.status_code == 200, result.text
    return result.json(), callback["code"][0]


def refresh(client, token, **overrides):
    # GCM refresh sends its configured redirect_uri, without the ephemeral listener port.
    return client.post(
        "/oauth/token",
        data={
            "client_id": "devops-gcm",
            "grant_type": "refresh_token",
            "refresh_token": token,
            "redirect_uri": "http://127.0.0.1",
            **overrides,
        },
    )


def git_auth(client, token="", *, operation="read", repository="gitowner/example", **headers):
    service = "git-upload-pack" if operation == "read" else "git-receive-pack"
    owner, _, name = repository.partition("/")
    encoded = base64.b64encode(f"oauth2:{token}".encode()).decode()
    return client.get(
        "/_internal/git/authorize",
        headers={
            "Authorization": "Basic " + encoded,
            "X-Original-Method": "GET",
            "X-Original-URI": f"/git/{repository}.git/info/refs?service={service}",
            "X-Git-Repository": name,
            "X-Git-Owner": owner,
            "X-Git-Operation": operation,
            **headers,
        },
    )


@pytest.fixture
def git_system(system):
    system.settings.git_oauth_enabled = True
    owner_id = system.seed("gitowner")
    client = system.client()
    system.login(client, "gitowner@example.com")
    system.owner_id = owner_id
    system.owner_client = client
    created = mutate(client, "POST", "/api/repositories", {"name": "example"})
    assert created.status_code == 201, created.text
    system.repository_id = created.json()["id"]
    return system


@pytest.fixture
def oauth_clock(monkeypatch):
    class Clock(datetime):
        current = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(git_oauth, "datetime", Clock)
    return Clock


def test_pkce_flow_replay_and_scoped_git_authorization(git_system):
    client = git_system.owner_client
    tokens, code = issue(client)
    assert len(tokens["access_token"]) == len(tokens["refresh_token"]) == 64
    assert exchange(client, code).json() == {"error": "invalid_grant"}
    for operation in ("read", "write"):
        response = git_auth(client, tokens["access_token"], operation=operation)
        assert response.status_code == 204
        assert response.headers["X-Git-Repository-ID"] == str(git_system.repository_id)
        assert response.headers["X-OA-User-ID"] == str(git_system.owner_id)
    assert git_auth(client).status_code == 401
    assert git_auth(client, tokens["refresh_token"]).status_code == 401
    assert git_auth(client, tokens["access_token"], repository="other").status_code == 403
    assert (
        git_auth(
            client, tokens["access_token"], **{"X-Original-URI": "/git/gitowner/example.git/HEAD"}
        ).status_code
        == 403
    )
    listings = client.get("/api/me/git-authorizations").json()
    assert len(listings) == 1
    assert tokens["access_token"] not in str(listings)
    assert tokens["refresh_token"] not in str(listings)


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("redirect_uri", "https://attacker.example/callback"),
        ("redirect_uri", "http://localhost:49152/"),
        ("redirect_uri", "http://127.0.0.1:49152/?next=bad"),
        ("redirect_uri", "http://127.0.0.1:99999/"),
        ("code_challenge_method", "plain"),
        ("client_id", "unregistered"),
        ("scope", "git:read admin"),
        ("state", ""),
    ],
)
def test_invalid_authorization_never_redirects(git_system, parameter, value):
    response = start(git_system.owner_client, **{parameter: value})
    assert response.status_code in {400, 401}
    assert "location" not in response.headers


def test_consent_requires_same_browser_csrf_and_allows_ordinary_members(git_system):
    client = git_system.owner_client
    started = start(client)
    request_id = parse_qs(urlsplit(started.headers["location"]).query)["request"][0]
    path = f"/api/oauth/authorization-requests/{request_id}"
    assert client.post(path + "/decision", json={"approve": True}).status_code == 403
    other = git_system.client()
    git_system.seed("member")
    git_system.login(other, "member@example.com")
    assert other.get(path).status_code == 404
    assert mutate(other, "POST", path + "/decision", {"approve": True}).status_code == 404
    approved, _ = decide(other)
    assert approved.status_code == 200
    cancelled, _ = decide(other, approve=False)
    assert parse_qs(urlsplit(cancelled.json()["redirect_uri"]).query)["error"] == ["access_denied"]
    assert mutate(client, "POST", path + "/decision", {"approve": True}).status_code == 200
    assert mutate(client, "POST", path + "/decision", {"approve": True}).status_code == 404


def test_exchange_requires_matching_verifier_redirect_and_live_code(git_system):
    client = git_system.owner_client
    response, _ = decide(client)
    code = parse_qs(urlsplit(response.json()["redirect_uri"]).query)["code"][0]
    assert exchange(client, code, code_verifier="x" * 64).status_code == 400
    assert exchange(client, code, redirect_uri="http://127.0.0.1:49153/").status_code == 400
    with Session(git_system.engine) as db:
        pending = db.scalar(select(GitOAuthRequest).where(GitOAuthRequest.code == code))
        pending.code_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert exchange(client, code).status_code == 400


def test_refresh_rotation_replay_revokes_entire_grant(git_system, oauth_clock):
    client = git_system.owner_client
    tokens, _ = issue(client)
    oauth_clock.current += timedelta(minutes=10)
    renewed = refresh(client, tokens["refresh_token"])
    assert renewed.status_code == 200, renewed.text
    fresh = renewed.json()
    assert fresh["access_token"] != tokens["access_token"]
    assert git_auth(client, tokens["access_token"]).status_code == 401
    assert git_auth(client, fresh["access_token"]).status_code == 204
    renewed_expiry = oauth_clock.current + timedelta(days=30)
    oauth_clock.current += timedelta(minutes=10)
    assert refresh(client, tokens["refresh_token"]).status_code == 400
    assert git_auth(client, fresh["access_token"]).status_code == 401
    assert refresh(client, fresh["refresh_token"]).status_code == 400
    with Session(git_system.engine) as db:
        assert db.scalar(select(GitOAuthGrant.revoked_at)) is not None
        assert db.scalar(select(GitOAuthGrant.expires_at)) == renewed_expiry


@pytest.mark.parametrize("change", ["disable", "feature_off"])
def test_authorization_rechecks_current_status_and_roles(git_system, change):
    client = git_system.owner_client
    tokens, _ = issue(client)
    with Session(git_system.engine) as db:
        original_expiry = db.scalar(select(GitOAuthGrant.expires_at))
        if change == "disable":
            db.get(User, git_system.owner_id).status = "disabled"
        else:
            git_system.settings.git_oauth_enabled = False
        db.commit()
    assert git_auth(client, tokens["access_token"]).status_code == 403
    assert refresh(client, tokens["refresh_token"]).status_code in {400, 404}
    with Session(git_system.engine) as db:
        assert db.scalar(select(GitOAuthGrant.expires_at)) == original_expiry
        assert db.scalar(select(GitOAuthToken.rotated_at)) is None


def test_revocation_owner_isolation_and_scope(git_system, oauth_clock):
    client = git_system.owner_client
    tokens, _ = issue(client, scope="git:read")
    assert git_auth(client, tokens["access_token"], operation="write").status_code == 403
    grant_id = client.get("/api/me/git-authorizations").json()[0]["id"]
    original_expiry = oauth_clock.current + timedelta(days=30)
    oauth_clock.current += timedelta(minutes=10)
    git_system.seed("outsider")
    other = git_system.client()
    git_system.login(other, "outsider@example.com")
    assert mutate(other, "DELETE", f"/api/me/git-authorizations/{grant_id}").status_code == 404
    assert mutate(client, "DELETE", f"/api/me/git-authorizations/{grant_id}").status_code == 200
    assert client.get("/api/me/git-authorizations").json() == []
    assert git_auth(client, tokens["access_token"]).status_code == 401
    assert refresh(client, tokens["refresh_token"]).status_code == 400
    with Session(git_system.engine) as db:
        assert db.scalar(select(GitOAuthGrant.expires_at)) == original_expiry
        assert db.scalar(select(GitOAuthToken.rotated_at)) is None
    tokens, _ = issue(client)
    assert (
        client.post(
            "/oauth/revoke", data={"client_id": "devops-gcm", "token": tokens["access_token"]}
        ).status_code
        == 200
    )
    assert git_auth(client, tokens["access_token"]).status_code == 401


def test_refresh_extends_active_grant_from_current_time(git_system, oauth_clock):
    client = git_system.owner_client
    started = oauth_clock.current
    tokens, _ = issue(client)
    original = client.get("/api/me/git-authorizations").json()[0]
    assert datetime.fromisoformat(original["expires_at"]) == started + timedelta(days=30)
    # Continue beyond the original deadline, renewing only when the client needs credentials.
    for days in (29, 58):
        oauth_clock.current = started + timedelta(days=days)
        assert git_auth(client, tokens["access_token"]).status_code == 401
        renewed = refresh(client, tokens["refresh_token"])
        assert renewed.status_code == 200
        tokens = renewed.json()
        assert tokens["expires_in"] == 3600
        assert git_auth(client, tokens["access_token"]).status_code == 204
        listed = client.get("/api/me/git-authorizations").json()[0]
        assert listed["id"] == original["id"]
        assert listed["created_at"] == original["created_at"]
        assert listed["scope"] == original["scope"]
        assert datetime.fromisoformat(listed["expires_at"]) == (
            oauth_clock.current + timedelta(days=30)
        )


@pytest.mark.parametrize("seconds_after_expiry", [0, 1])
def test_expired_grant_cannot_be_renewed(git_system, oauth_clock, seconds_after_expiry):
    client = git_system.owner_client
    tokens, _ = issue(client)
    expiry = oauth_clock.current + timedelta(days=30)
    oauth_clock.current = expiry + timedelta(seconds=seconds_after_expiry)
    assert git_auth(client, tokens["access_token"]).status_code == 401
    rejected = refresh(client, tokens["refresh_token"])
    assert rejected.status_code == 400
    assert rejected.json() == {"error": "invalid_grant"}
    with Session(git_system.engine) as db:
        assert db.scalar(select(GitOAuthGrant.expires_at)) == expiry
        assert db.scalar(select(GitOAuthToken.rotated_at)) is None
        assert db.scalar(select(func.count()).select_from(GitOAuthToken)) == 1


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"client_id": "unregistered"}, "invalid_client"),
        ({"scope": "git:read git:write"}, "invalid_scope"),
        ({"refresh_token": "x" * 64}, "invalid_grant"),
    ],
)
def test_rejected_refresh_does_not_renew_or_rotate(git_system, oauth_clock, overrides, error):
    client = git_system.owner_client
    tokens, _ = issue(client, scope="git:read")
    expiry = oauth_clock.current + timedelta(days=30)
    oauth_clock.current += timedelta(days=29)
    rejected = refresh(client, tokens["refresh_token"], **overrides)
    assert rejected.status_code in {400, 401}
    assert rejected.json() == {"error": error}
    with Session(git_system.engine) as db:
        assert db.scalar(select(GitOAuthGrant.expires_at)) == expiry
        assert db.scalar(select(GitOAuthToken.rotated_at)) is None
        assert db.scalar(select(func.count()).select_from(GitOAuthToken)) == 1
    renewed = refresh(client, tokens["refresh_token"])
    assert renewed.status_code == 200
    assert renewed.json()["scope"] == "git:read"
    assert git_auth(client, renewed.json()["access_token"], operation="write").status_code == 403


def test_concurrent_refresh_rotates_once_and_replay_revokes_renewal(git_system, oauth_clock):
    tokens, _ = issue(git_system.owner_client)
    oauth_clock.current += timedelta(days=29)
    clients = [git_system.client(), git_system.client()]
    barrier = Barrier(2)

    def rotate(client):
        barrier.wait(timeout=5)
        return refresh(client, tokens["refresh_token"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(rotate, clients))
    assert sorted(response.status_code for response in responses) == [200, 400]
    fresh = next(response.json() for response in responses if response.status_code == 200)
    assert git_auth(clients[0], fresh["access_token"]).status_code == 401
    with Session(git_system.engine) as db:
        grant = db.scalar(select(GitOAuthGrant))
        assert grant.revoked_at is not None
        assert grant.expires_at == oauth_clock.current + timedelta(days=30)
        assert db.scalar(select(func.count()).select_from(GitOAuthToken)) == 2


def test_refresh_waiting_for_grant_lock_cannot_revive_expired_grant(git_system, oauth_clock):
    tokens, _ = issue(git_system.owner_client)
    expiry = oauth_clock.current + timedelta(days=30)
    oauth_clock.current = expiry - timedelta(seconds=1)
    client = git_system.client()
    locking = Event()

    def observe_lock(conn, cursor, statement, parameters, context, executemany):
        if "git_oauth_grants" in statement and "FOR UPDATE" in statement:
            locking.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(git_system.engine) as blocker:
                blocker.scalar(select(GitOAuthGrant).with_for_update())
                event.listen(git_system.engine, "before_cursor_execute", observe_lock)
                try:
                    result = pool.submit(refresh, client, tokens["refresh_token"])
                    assert locking.wait(timeout=5)
                    oauth_clock.current = expiry
                finally:
                    blocker.rollback()
            rejected = result.result(timeout=5)
            assert rejected.status_code == 400
            assert rejected.json() == {"error": "invalid_grant"}
    finally:
        event.remove(git_system.engine, "before_cursor_execute", observe_lock)
    with Session(git_system.engine) as db:
        assert db.scalar(select(GitOAuthGrant.expires_at)) == expiry
        assert db.scalar(select(GitOAuthToken.rotated_at)) is None
        assert db.scalar(select(func.count()).select_from(GitOAuthToken)) == 1


def test_authorization_list_excludes_revocations_before_limit_and_retains_history(git_system):
    client = git_system.owner_client
    issue(client)
    active_id = client.get("/api/me/git-authorizations").json()[0]["id"]
    now = datetime.now(UTC)
    outsider_id = git_system.seed("other-grant-owner")
    with Session(git_system.engine) as db:
        expired = GitOAuthGrant(
            user_id=git_system.owner_id, scope="git:read", expires_at=now - timedelta(days=1)
        )
        db.add(expired)
        db.add(
            GitOAuthGrant(user_id=outsider_id, scope="git:read", expires_at=now + timedelta(days=1))
        )
        for _ in range(101):
            db.add(
                GitOAuthGrant(
                    user_id=git_system.owner_id,
                    scope="git:read",
                    expires_at=now + timedelta(days=1),
                    revoked_at=now,
                )
            )
        db.commit()
        expired_id = expired.id
    grants = client.get("/api/me/git-authorizations").json()
    assert [grant["id"] for grant in grants] == [expired_id, active_id]
    assert all(grant["revoked_at"] is None for grant in grants)
    # Hiding revoked rows does not delete the records needed by refresh replay detection.
    with Session(git_system.engine) as db:
        revoked = db.scalars(
            select(GitOAuthGrant).where(GitOAuthGrant.revoked_at.is_not(None))
        ).all()
        assert len(revoked) == 101


def test_protocol_inputs_and_cookie_sessions_are_not_git_credentials(git_system):
    client = git_system.owner_client
    assert client.post("/oauth/token", json={"grant_type": "authorization_code"}).status_code == 400
    assert client.post(
        "/oauth/token",
        content="client_id=devops-gcm&client_id=devops-gcm",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json() == {"error": "invalid_request"}
    assert git_auth(client).status_code == 401
    assert client.post(
        "/oauth/token", data={"client_id": "devops-gcm", "grant_type": "password"}
    ).json() == {"error": "unsupported_grant_type"}


def test_git_system_disabled_by_default(system):
    assert start(system.client()).status_code == 404
