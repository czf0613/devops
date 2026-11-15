from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import mutate
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_collaboration_pulls import commit, drain, pull
from test_git_oauth import decide, exchange, git_auth, issue, refresh
from test_git_oauth import git_system as oauth_system
from test_git_oauth import oauth_clock as clock_fixture
from test_repositories import add
from test_repositories import workspace as repository_workspace

from devops_server.models import GitOAuthGrant, GitOAuthToken, RepositoryMember, User


@pytest.fixture
def git_system(system):
    return oauth_system.__wrapped__(system)


@pytest.fixture
def workspace(system):
    return repository_workspace.__wrapped__(system)


@pytest.fixture
def oauth_clock(monkeypatch):
    return clock_fixture.__wrapped__(monkeypatch)


def cli_tokens(client, scope="api:read api:write"):
    response, _ = decide(client, client_id="devops-cli", scope=scope)
    assert response.status_code == 200, response.text
    code = parse_qs(urlsplit(response.json()["redirect_uri"]).query)["code"][0]
    result = exchange(client, code, client_id="devops-cli")
    assert result.status_code == 200, result.text
    return result.json()


def bearer(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


def test_cli_pkce_client_binding_and_live_api_permissions(git_system):
    owner = git_system.owner_client
    response, _ = decide(owner, client_id="devops-cli", scope="api:read api:write")
    code = parse_qs(urlsplit(response.json()["redirect_uri"]).query)["code"][0]
    assert exchange(owner, code).status_code == 400
    response = exchange(owner, code, client_id="devops-cli")
    assert response.status_code == 200
    tokens = response.json()
    assert exchange(owner, code, client_id="devops-cli").status_code == 400
    machine = git_system.client()
    headers = bearer(tokens)
    assert machine.get("/api/cli/me", headers=headers).json()["login"] == "gitowner"
    repo = f"/api/repositories/{git_system.repository_id}"
    created = machine.post(repo + "/issues", headers=headers, json={"title": "CLI issue"})
    assert created.status_code == 201, created.text
    item = machine.get(repo + "/items/1", headers=headers).json()
    assert (
        machine.patch(
            repo + "/items/1", headers=headers, json={"version": item["version"], "title": "Edited"}
        ).status_code
        == 200
    )
    assert (
        machine.patch(
            repo + "/items/1", headers=headers, json={"version": item["version"], "title": "Stale"}
        ).status_code
        == 409
    )
    assert git_auth(owner, tokens["access_token"]).status_code == 403
    for path in ("/api/me", "/api/users", "/api/me/git-authorizations"):
        assert machine.get(path, headers=headers).status_code == 401
    # A malformed/expired bearer must never inherit the browser cookie identity.
    assert owner.get(repo, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert owner.post(repo + "/issues", json={"title": "Missing CSRF"}).status_code == 403
    assert (
        owner.post(
            repo + "/issues", headers={"Authorization": "Bearer invalid"}, json={"title": "Bad"}
        ).status_code
        == 401
    )
    # Token endpoint client confusion cannot rotate or revoke another client grant.
    assert refresh(owner, tokens["refresh_token"]).status_code == 400
    assert (
        owner.post(
            "/oauth/revoke", data={"client_id": "devops-gcm", "token": tokens["access_token"]}
        ).status_code
        == 200
    )
    assert machine.get(repo, headers=headers).status_code == 200
    rotated = refresh(owner, tokens["refresh_token"], client_id="devops-cli")
    assert rotated.status_code == 200
    assert machine.get(repo, headers=headers).status_code == 401
    assert machine.get(repo, headers=bearer(rotated.json())).status_code == 200
    owner.post(
        "/oauth/revoke", data={"client_id": "devops-cli", "token": rotated.json()["refresh_token"]}
    )
    assert machine.get(repo, headers=bearer(rotated.json())).status_code == 401


def test_gcm_and_read_only_cli_scopes_never_gain_api_write(git_system):
    owner = git_system.owner_client
    tokens, _ = issue(owner)
    assert owner.get("/api/repositories", headers=bearer(tokens)).status_code == 403
    tokens = cli_tokens(owner, scope="api:read")
    assert owner.get("/api/repositories", headers=bearer(tokens)).status_code == 200
    assert (
        owner.post("/api/repositories", headers=bearer(tokens), json={"name": "denied"}).status_code
        == 403
    )
    assert decide_scope_invalid(owner)


def decide_scope_invalid(client):
    from test_git_oauth import start

    return start(client, client_id="devops-cli", scope="git:write").status_code == 400


def test_cli_grants_follow_acl_and_disabled_user(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    outsider = workspace.people["outsider"][1]
    headers = bearer(cli_tokens(reader))
    admin_headers = bearer(cli_tokens(outsider))
    machine = workspace.client()
    assert machine.get(workspace.path, headers=headers).status_code == 404
    assert machine.get(workspace.path, headers=admin_headers).status_code == 404
    assert add(workspace, "reader").status_code == 201
    assert machine.get(workspace.path, headers=headers).status_code == 200
    assert (
        machine.patch(workspace.path, headers=headers, json={"description": "denied"}).status_code
        == 403
    )
    assert (
        mutate(
            owner,
            "PATCH",
            workspace.path + f"/members/{workspace.people['reader'][0]}",
            {"access": "write", "role": "开发人员"},
        ).status_code
        == 200
    )
    with Session(workspace.engine) as db:
        member = db.scalar(
            select(RepositoryMember).where(
                RepositoryMember.repository_id == workspace.repo["id"],
                RepositoryMember.user_id == workspace.people["reader"][0],
            )
        )
        db.delete(member)
        db.commit()
    assert machine.get(workspace.path, headers=headers).status_code == 404
    with Session(workspace.engine) as db:
        db.get(User, workspace.people["reader"][0]).status = "disabled"
        db.commit()
    assert machine.get("/api/cli/me", headers=headers).status_code == 401


def device_start(client):
    response = client.post(
        "/oauth/device/code",
        data={
            "client_id": "devops-cli",
            "scope": "api:read api:write",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def poll(client, device, **overrides):
    return client.post(
        "/oauth/token",
        data={
            "client_id": "devops-cli",
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device["device_code"],
            **overrides,
        },
    )


def test_device_approval_polling_binding_and_single_use(git_system, oauth_clock):
    client = git_system.client()
    device = device_start(client)
    assert "device_code" not in device["verification_uri_complete"]
    assert poll(client, device).json()["error"] == "authorization_pending"
    assert poll(client, device).json()["error"] == "slow_down"
    oauth_clock.current += timedelta(seconds=11)
    assert poll(client, device, client_id="devops-gcm").json()["error"] == "invalid_grant"
    path = "/api/oauth/device-requests/" + device["user_code"]
    assert client.get(path).status_code == 401
    owner = git_system.owner_client
    assert owner.get(path).json()["client_name"] == "DevOps CLI"
    assert owner.post(path + "/decision", json={"approve": True}).status_code == 403
    assert mutate(owner, "POST", path + "/decision", {"approve": True}).status_code == 200
    assert mutate(owner, "POST", path + "/decision", {"approve": True}).status_code == 404
    result = poll(client, device)
    assert result.status_code == 200, result.text
    assert client.get("/api/cli/me", headers=bearer(result.json())).status_code == 200
    assert poll(client, device).json()["error"] == "invalid_grant"


@pytest.mark.parametrize("case", ["denied", "expired", "disabled", "feature-off"])
def test_device_rejection(git_system, oauth_clock, case):
    client = git_system.client()
    device = device_start(client)
    owner = git_system.owner_client
    if case == "expired":
        oauth_clock.current += timedelta(minutes=11)
        assert poll(client, device).json()["error"] == "expired_token"
        return
    path = "/api/oauth/device-requests/" + device["user_code"]
    assert (
        mutate(owner, "POST", path + "/decision", {"approve": case != "denied"}).status_code == 200
    )
    if case == "disabled":
        with Session(git_system.engine) as db:
            db.get(User, git_system.owner_id).status = "disabled"
            db.commit()
    if case == "feature-off":
        git_system.settings.git_oauth_enabled = False
        assert poll(client, device).status_code == 404
        return
    assert poll(client, device).json()["error"] == "access_denied"


@pytest.mark.parametrize("revoke_grant", [False, True])
def test_cli_merge_worker_rechecks_authorization(workspace, revoke_grant):
    owner = workspace.people["owner"][1]
    tokens = cli_tokens(owner)
    commit(workspace, "feature", {"cli.txt": "CLI merge\n"})
    pr = pull(workspace)
    drain(workspace)
    machine = workspace.client()
    headers = bearer(tokens)
    path = workspace.path + f"/pulls/{pr['number']}"
    detail = machine.get(path, headers=headers).json()
    response = machine.post(
        path + "/merge",
        headers=headers,
        json={
            "base_sha": detail["base_sha"],
            "head_sha": detail["head_sha"],
            "method": "merge",
        },
    )
    assert response.status_code == 202, response.text
    intent = response.json()["id"]
    if revoke_grant:
        with Session(workspace.engine) as db:
            grant = db.scalar(
                select(GitOAuthGrant)
                .join(GitOAuthToken)
                .where(
                    GitOAuthToken.access_token == tokens["access_token"],
                )
            )
            from datetime import UTC, datetime

            grant.revoked_at = datetime.now(UTC)
            db.commit()
    drain(workspace)
    # Cookie owner can inspect rejected tasks after its independent CLI grant is revoked.
    status = owner.get(path + f"/merges/{intent}").json()
    assert status["state"] == ("failed" if revoke_grant else "complete"), status
    item = owner.get(workspace.path + f"/items/{pr['number']}").json()
    assert item["state"] == ("open" if revoke_grant else "merged")
