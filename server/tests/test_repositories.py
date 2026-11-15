import subprocess
from pathlib import Path

import pytest
from conftest import lifecycle_mutate, mutate
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_git_oauth import git_auth, issue

from devops_server.models import AuditEvent, Repository, RepositoryMember, User


@pytest.fixture
def workspace(system):
    system.settings.git_oauth_enabled = True
    people = {}
    for username, role in [
        ("owner", "member"),
        ("manager", "member"),
        ("reader", "member"),
        ("outsider", "admin"),
    ]:
        user_id = system.seed(username, role)
        client = system.client()
        system.login(client, f"{username}@example.com")
        people[username] = (user_id, client)
    response = mutate(
        people["owner"][1],
        "POST",
        "/api/repositories",
        {"name": "example", "description": "Project"},
    )
    assert response.status_code == 201, response.text
    system.people = people
    system.repo = response.json()
    system.path = f"/api/repositories/{system.repo['id']}"
    return system


def add(workspace, who, *, access="read", role="测试人员", is_admin=False, actor="owner"):
    return mutate(
        workspace.people[actor][1],
        "POST",
        workspace.path + "/members",
        {"user_id": workspace.people[who][0], "access": access, "role": role, "is_admin": is_admin},
    )


def test_create_provisions_private_bare_repository_and_owner_namespace(workspace, tmp_path):
    repo = workspace.repo
    assert repo["full_name"] == "owner/example"
    assert repo["clone_path"] == "/git/owner/example.git"
    assert repo["member_count"] == 1
    assert repo["permissions"]["is_owner"] and repo["permissions"]["access"] == "write"
    directory = Path(workspace.settings.git_repositories_root) / f"{repo['id']}.git"
    assert (directory / "HEAD").read_text() == "ref: refs/heads/main\n"
    checkout = tmp_path / "clone"
    subprocess.run(["git", "clone", str(directory), str(checkout)], check=True, capture_output=True)
    readme = (checkout / "README.md").read_text()
    assert readme.startswith("# owner/example\n")
    assert "git clone http://testserver/git/owner/example.git" in readme
    assert "cd example" in readme
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-list", "--count", "HEAD"], text=True
        ).strip()
        == "1"
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "branch", "--show-current"], text=True
        ).strip()
        == "main"
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "--is-bare-repository"], text=True
        ).strip()
        == "true"
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(directory), "config", "http.getanyfile"], text=True
        ).strip()
        == "false"
    )
    assert (
        mutate(
            workspace.people["owner"][1], "POST", "/api/repositories", {"name": "EXAMPLE"}
        ).status_code
        == 409
    )
    another = mutate(
        workspace.people["reader"][1], "POST", "/api/repositories", {"name": "example"}
    )
    assert another.status_code == 201
    assert another.json()["id"] != repo["id"]
    assert another.json()["full_name"] == "reader/example"
    assert workspace.people["outsider"][1].get(workspace.path).status_code == 404
    assert workspace.people["outsider"][1].get("/api/repositories").json()["total"] == 0


def test_ordinary_member_visibility_search_and_no_contact_data(workspace):
    reader = workspace.people["reader"][1]
    assert reader.get(workspace.path + "/members").status_code == 404
    assert add(workspace, "reader").status_code == 201
    listed = reader.get("/api/repositories").json()
    assert listed["total"] == 1
    assert listed["items"][0]["permissions"]["access"] == "read"
    assert reader.get(workspace.path + "/members").status_code == 200
    assert reader.get(workspace.path + "/member-candidates?q=man").status_code == 403
    assert (
        mutate(reader, "PATCH", workspace.path, {"description": "not allowed"}).status_code == 403
    )
    candidates = workspace.people["owner"][1].get(workspace.path + "/member-candidates?q=@man")
    assert candidates.status_code == 200
    assert candidates.json() == [
        {"id": workspace.people["manager"][0], "username": "manager", "display_name": "manager"}
    ]
    assert "email" not in str(reader.get(workspace.path + "/members").json())
    assert add(workspace, "reader").status_code == 409


def test_only_owner_can_appoint_or_manage_administrators(workspace):
    assert add(workspace, "manager", is_admin=True).status_code == 201
    assert add(workspace, "reader", actor="manager", role="开发人员").status_code == 201
    manager = workspace.people["manager"][1]
    reader_path = workspace.path + f"/members/{workspace.people['reader'][0]}"
    owner_path = workspace.path + f"/members/{workspace.people['owner'][0]}"
    own_path = workspace.path + f"/members/{workspace.people['manager'][0]}"
    for path in [owner_path, own_path]:
        assert mutate(manager, "PATCH", path, {"is_admin": True}).status_code == 403
        assert mutate(manager, "DELETE", path).status_code == 403
    assert mutate(manager, "PATCH", reader_path, {"is_admin": True}).status_code == 403
    assert add(workspace, "outsider", is_admin=True, actor="manager").status_code == 403
    changed = mutate(manager, "PATCH", reader_path, {"access": "write", "role": "产品经理"})
    assert changed.status_code == 200
    assert changed.json()["access"] == "write" and changed.json()["role"] == "产品经理"
    assert mutate(manager, "PATCH", workspace.path, {"description": "Updated"}).status_code == 200
    owner = workspace.people["owner"][1]
    assert mutate(owner, "PATCH", own_path, {"is_admin": False}).status_code == 200
    assert mutate(manager, "DELETE", reader_path).status_code == 403
    assert mutate(owner, "PATCH", owner_path, {"access": "read"}).status_code == 403


def test_member_removal_respects_repository_hierarchy(workspace):
    assert add(workspace, "manager", is_admin=True).status_code == 201
    assert add(workspace, "outsider", is_admin=True).status_code == 201
    assert add(workspace, "reader").status_code == 201
    owner = workspace.people["owner"][1]
    manager = workspace.people["manager"][1]
    reader = workspace.people["reader"][1]
    paths = {
        name: workspace.path + f"/members/{person[0]}" for name, person in workspace.people.items()
    }
    assert mutate(reader, "DELETE", paths["manager"]).status_code == 403
    assert mutate(owner, "DELETE", paths["owner"]).status_code == 403
    assert mutate(manager, "DELETE", paths["outsider"]).status_code == 403
    assert mutate(manager, "DELETE", paths["reader"]).status_code == 200
    assert reader.get(workspace.path).status_code == 404
    assert mutate(owner, "DELETE", paths["outsider"]).status_code == 200
    assert mutate(owner, "DELETE", paths["manager"]).status_code == 200


def test_live_git_acl_owner_reader_writer_removed_and_admin_outsider(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    outsider = workspace.people["outsider"][1]
    assert add(workspace, "reader", role="开发人员").status_code == 201
    reader_token, _ = issue(reader)
    owner_token, _ = issue(owner)
    outsider_token, _ = issue(outsider)

    def access(client, tokens, operation="read", **kwargs):
        return git_auth(
            client,
            tokens["access_token"],
            repository="owner/example",
            operation=operation,
            **kwargs,
        ).status_code

    assert access(owner, owner_token, "write") == 204
    assert access(reader, reader_token) == 204
    assert access(reader, reader_token, "write") == 403
    assert access(outsider, outsider_token) == 403  # Global admin is not a repo bypass.
    sibling = mutate(owner, "POST", "/api/repositories", {"name": "private-sibling"})
    assert sibling.status_code == 201
    assert reader.get(f"/api/repositories/{sibling.json()['id']}").status_code == 404
    assert (
        git_auth(
            reader, reader_token["access_token"], repository="owner/private-sibling"
        ).status_code
        == 403
    )
    member_path = workspace.path + f"/members/{workspace.people['reader'][0]}"
    assert mutate(owner, "PATCH", member_path, {"role": "测试人员"}).status_code == 200
    assert access(reader, reader_token, "write") == 403  # Job role grants nothing.
    assert mutate(owner, "PATCH", member_path, {"access": "write"}).status_code == 200
    assert access(reader, reader_token, "write") == 204
    assert mutate(owner, "PATCH", member_path, {"access": "read"}).status_code == 200
    assert access(reader, reader_token, "write") == 403
    assert mutate(owner, "DELETE", member_path).status_code == 200
    assert access(reader, reader_token) == 403
    assert reader.get(workspace.path).status_code == 404
    # Request headers cannot smuggle a different directory or URI into a permitted repo.
    assert access(owner, owner_token, **{"X-Original-URI": "/git/owner/example.git/HEAD"}) == 403
    assert access(owner, owner_token, **{"X-Git-Repository": "../example"}) == 403


def test_creation_validation_and_failed_storage_never_overwrite_existing_data(workspace):
    owner = workspace.people["owner"][1]
    for name in ["../escape", "a/b", "x.git", "", "x" * 101]:
        assert mutate(owner, "POST", "/api/repositories", {"name": name}).status_code == 422
    assert owner.post("/api/repositories", json={"name": "no-csrf"}).status_code == 403
    assert (
        mutate(owner, "POST", "/api/repositories", {"name": "foreign", "owner_id": 2}).status_code
        == 422
    )
    root = Path(workspace.settings.git_repositories_root)
    existing = root / f"{workspace.repo['id'] + 1}.git"
    existing.mkdir()
    (existing / "keep").write_text("existing data")
    assert mutate(owner, "POST", "/api/repositories", {"name": "collision"}).status_code == 503
    assert (existing / "keep").read_text() == "existing data"
    with Session(workspace.engine) as db:
        assert db.scalar(select(Repository).where(Repository.name == "collision")) is None
    created = mutate(owner, "POST", "/api/repositories", {"name": "retry"})
    assert created.status_code == 201


def test_inactive_users_invalid_access_and_unowned_members_are_rejected(workspace):
    with Session(workspace.engine) as db:
        db.get(User, workspace.people["reader"][0]).status = "disabled"
        db.commit()
    assert add(workspace, "reader").status_code == 422
    assert add(workspace, "manager", access="owner").status_code == 422
    assert add(workspace, "manager", role="x" * 51).status_code == 422
    assert add(workspace, "manager").status_code == 201
    owner = workspace.people["owner"][1]
    member_path = workspace.path + f"/members/{workspace.people['manager'][0]}"
    assert mutate(owner, "PATCH", member_path, {"access": None}).status_code == 422
    assert mutate(owner, "PATCH", member_path, {"is_admin": "true"}).status_code == 422
    assert mutate(owner, "DELETE", workspace.path + "/members/99999").status_code == 404
    with Session(workspace.engine) as db:
        member = db.scalar(select(RepositoryMember))
        assert member.access == "read" and not member.is_admin


def test_repository_name_lookup_preserves_private_access(workspace):
    path = "/api/repositories/by-name/owner/example"
    owner = workspace.people["owner"][1]
    assert owner.get(path).json()["id"] == workspace.repo["id"]
    assert workspace.people["outsider"][1].get(path).status_code == 404
    assert owner.get("/api/repositories/by-name/owner/missing").status_code == 404
    assert add(workspace, "reader").status_code == 201
    assert workspace.people["reader"][1].get(path).status_code == 200


@pytest.mark.parametrize("existing_member", [False, True])
def test_transfer_updates_namespace_members_and_live_git_access(workspace, existing_member):
    owner = workspace.people["owner"][1]
    reader_id, reader = workspace.people["reader"]
    if existing_member:
        assert add(workspace, "reader").status_code == 201
    owner_token, _ = issue(owner)
    reader_token, _ = issue(reader)
    storage = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    head = (storage / "HEAD").read_bytes()
    response = lifecycle_mutate(
        workspace,
        owner,
        "POST",
        workspace.path + "/transfer",
        {
            "new_owner_id": reader_id,
            "confirm_name": "owner/example",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == workspace.repo["id"]
    assert response.json()["full_name"] == "reader/example"
    assert response.json()["clone_path"] == "/git/reader/example.git"
    assert response.json()["permissions"]["is_admin"]
    assert not response.json()["permissions"]["is_owner"]
    assert reader.get(workspace.path).json()["permissions"]["is_owner"]
    people = reader.get(workspace.path + "/members").json()
    assert len(people) == 2
    assert people[0]["user_id"] == reader_id and people[0]["is_owner"]
    assert people[1]["is_admin"] and people[1]["access"] == "write"
    assert (storage / "HEAD").read_bytes() == head
    assert owner.get("/api/repositories/by-name/owner/example").status_code == 404
    assert owner.get("/api/repositories/by-name/reader/example").status_code == 200
    for client, token in [(owner, owner_token), (reader, reader_token)]:
        assert (
            git_auth(
                client, token["access_token"], repository="reader/example", operation="write"
            ).status_code
            == 204
        )
        assert (
            git_auth(client, token["access_token"], repository="owner/example").status_code == 403
        )
    # A stale owner page cannot transfer again after becoming a repository administrator.
    assert (
        lifecycle_mutate(
            workspace,
            owner,
            "POST",
            workspace.path + "/transfer",
            {
                "new_owner_id": workspace.people["manager"][0],
                "confirm_name": "reader/example",
            },
        ).status_code
        == 403
    )
    with Session(workspace.engine) as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "repository.transferred"))
        assert event.details["previous_owner_id"] == workspace.people["owner"][0]
        assert event.details["new_owner_id"] == reader_id


def test_transfer_owner_checks_candidates_confirmation_and_conflicts(workspace):
    owner_id, owner = workspace.people["owner"]
    reader_id, reader = workspace.people["reader"]
    assert add(workspace, "reader").status_code == 201
    assert add(workspace, "manager", is_admin=True).status_code == 201
    candidate_path = workspace.path + "/transfer-candidates?q=read"
    assert owner.get(candidate_path).json() == [
        {"id": reader_id, "username": "reader", "display_name": "reader"},
    ]
    body = {"new_owner_id": reader_id, "confirm_name": "owner/example"}
    for who, status in [("manager", 403), ("reader", 403), ("outsider", 404)]:
        client = workspace.people[who][1]
        assert client.get(candidate_path).status_code == status
        assert (
            lifecycle_mutate(
                workspace, client, "POST", workspace.path + "/transfer", body
            ).status_code
            == status
        )
    assert owner.post(workspace.path + "/transfer", json=body).status_code == 403
    assert (
        lifecycle_mutate(
            workspace,
            owner,
            "POST",
            workspace.path + "/transfer",
            body | {"confirm_name": "other/example"},
        ).status_code
        == 409
    )
    for invalid_id in [owner_id, 999999]:
        assert (
            lifecycle_mutate(
                workspace,
                owner,
                "POST",
                workspace.path + "/transfer",
                body | {"new_owner_id": invalid_id},
            ).status_code
            == 422
        )
    assert mutate(reader, "POST", "/api/repositories", {"name": "example"}).status_code == 201
    assert (
        lifecycle_mutate(workspace, owner, "POST", workspace.path + "/transfer", body).status_code
        == 409
    )
    assert owner.get(workspace.path).json()["permissions"]["is_owner"]
    assert reader.get(workspace.path).json()["permissions"]["access"] == "read"
    with Session(workspace.engine) as db:
        db.get(User, reader_id).status = "disabled"
        db.commit()
    assert owner.get(candidate_path).json() == []
    assert (
        lifecycle_mutate(workspace, owner, "POST", workspace.path + "/transfer", body).status_code
        == 422
    )


def test_archive_blocks_all_git_writes_and_restores_previous_access(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    assert add(workspace, "reader", access="write").status_code == 201
    assert add(workspace, "manager", is_admin=True).status_code == 201
    tokens = [(client, issue(client)[0]) for client in [owner, reader]]
    for who in ["manager", "reader"]:
        assert (
            lifecycle_mutate(
                workspace,
                workspace.people[who][1],
                "PUT",
                workspace.path + "/archive",
                {"archived": True},
            ).status_code
            == 403
        )
    assert owner.put(workspace.path + "/archive", json={"archived": True}).status_code == 403
    response = lifecycle_mutate(
        workspace, owner, "PUT", workspace.path + "/archive", {"archived": True}
    )
    assert response.status_code == 200 and response.json()["archived"]
    assert owner.get("/api/repositories").json()["items"][0]["archived"]
    for client, token in tokens:
        assert client.get(workspace.path).json()["permissions"]["access"] == "read"
        assert (
            git_auth(client, token["access_token"], repository="owner/example").status_code == 204
        )
        assert (
            git_auth(
                client, token["access_token"], repository="owner/example", operation="write"
            ).status_code
            == 403
        )
    assert (
        lifecycle_mutate(
            workspace, owner, "PUT", workspace.path + "/archive", {"archived": False}
        ).status_code
        == 200
    )
    for client, token in tokens:
        assert client.get(workspace.path).json()["permissions"]["access"] == "write"
        assert (
            git_auth(
                client, token["access_token"], repository="owner/example", operation="write"
            ).status_code
            == 204
        )


def test_delete_hides_repository_revokes_access_and_allows_fresh_name_reuse(workspace):
    owner = workspace.people["owner"][1]
    assert add(workspace, "manager", is_admin=True).status_code == 201
    assert add(workspace, "reader").status_code == 201
    tokens = [(client, issue(client)[0]) for client in [owner, workspace.people["reader"][1]]]
    body = {"confirm_name": "owner/example"}
    for who in ["manager", "reader"]:
        assert (
            lifecycle_mutate(
                workspace, workspace.people[who][1], "DELETE", workspace.path, body
            ).status_code
            == 403
        )
    assert owner.request("DELETE", workspace.path, json=body).status_code == 403
    assert (
        lifecycle_mutate(
            workspace, owner, "DELETE", workspace.path, {"confirm_name": "example"}
        ).status_code
        == 409
    )
    assert (
        lifecycle_mutate(
            workspace, owner, "PUT", workspace.path + "/archive", {"archived": True}
        ).status_code
        == 200
    )
    assert lifecycle_mutate(workspace, owner, "DELETE", workspace.path, body).status_code == 200
    for client, token in tokens:
        for suffix in ["", "/members", "/member-candidates?q=read", "/transfer-candidates?q=read"]:
            assert client.get(workspace.path + suffix).status_code == 404
        assert client.get("/api/repositories/by-name/owner/example").status_code == 404
        assert client.get("/api/repositories").json()["total"] == 0
        assert (
            git_auth(client, token["access_token"], repository="owner/example").status_code == 403
        )
    assert (
        lifecycle_mutate(
            workspace, owner, "PUT", workspace.path + "/archive", {"archived": False}
        ).status_code
        == 404
    )
    assert mutate(owner, "PATCH", workspace.path, {"description": "revive"}).status_code == 404
    assert (
        lifecycle_mutate(
            workspace,
            owner,
            "POST",
            workspace.path + "/transfer",
            {"new_owner_id": workspace.people["reader"][0], **body},
        ).status_code
        == 404
    )
    old_storage = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    assert (old_storage / "HEAD").exists()
    with Session(workspace.engine) as db:
        assert db.get(Repository, workspace.repo["id"]).deleted_at is not None
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "repository.deleted"))
    fresh = mutate(owner, "POST", "/api/repositories", {"name": "example"})
    assert fresh.status_code == 201
    assert fresh.json()["id"] != workspace.repo["id"]
    assert fresh.json()["member_count"] == 1
    assert (
        workspace.people["reader"][1].get("/api/repositories/by-name/owner/example").status_code
        == 404
    )
    assert (
        git_auth(tokens[1][0], tokens[1][1]["access_token"], repository="owner/example").status_code
        == 403
    )


def test_initial_commit_failure_cleans_only_new_directory(workspace, monkeypatch):
    from devops_server import repository_storage

    original = repository_storage.subprocess.run

    def fail_commit(command, **kwargs):
        if "commit-tree" in command:
            raise subprocess.CalledProcessError(1, command)
        return original(command, **kwargs)

    monkeypatch.setattr(repository_storage.subprocess, "run", fail_commit)
    root = Path(workspace.settings.git_repositories_root)
    before = set(root.iterdir())
    response = mutate(
        workspace.people["owner"][1], "POST", "/api/repositories", {"name": "failed-initial"}
    )
    assert response.status_code == 503
    assert set(root.iterdir()) == before
    with Session(workspace.engine) as db:
        assert db.scalar(select(Repository).where(Repository.name == "failed-initial")) is None
