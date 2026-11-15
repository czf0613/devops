from concurrent.futures import ThreadPoolExecutor

# ruff: noqa: F811
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import admin_code, mutate
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from test_registry import check, credential, registry  # noqa: F401
from test_repositories import add, workspace  # noqa: F401

from devops_server.models import AuditEvent, EmailChallenge, Repository, User, UserRole, UserSession


def path(system):
    return f"/api/admin/repositories/{system.repo['id']}"


def change(system, suffix, method, values):
    admin = system.people["outsider"][1]
    version = admin.get(path(system)).json()["updated_at"]
    code = admin_code(system, admin)
    return mutate(
        admin,
        method,
        path(system) + suffix,
        {**values, "expected_updated_at": version, "code": code},
    )


def test_admin_lists_metadata_without_implicit_content_access(workspace):
    admin, owner = workspace.people["outsider"][1], workspace.people["owner"][1]
    assert owner.get("/api/admin/repositories").status_code == 403
    assert owner.get(path(workspace)).status_code == 403
    listed = admin.get("/api/admin/repositories?q=owner/example").json()
    assert listed["total"] == 1
    assert "clone_path" not in listed["items"][0] and "permissions" not in listed["items"][0]
    detail = admin.get(path(workspace)).json()
    assert detail["members"][0]["is_owner"]
    assert admin.get(workspace.path).status_code == 404
    assert admin.get(workspace.path + "/code/branches").status_code == 404
    assert admin.get(workspace.path + "/members").status_code == 404
    assert admin.get(path(workspace) + "/candidates?q=reader").json()[0]["username"] == "reader"


def test_admin_email_binding_purpose_single_use_and_failed_attempts(workspace):
    admin, owner = workspace.people["outsider"][1], workspace.people["owner"][1]
    request = {"email": "outsider@example.com", "purpose": "admin_operation"}
    assert mutate(workspace.client(), "POST", "/api/auth/email-codes", request).status_code == 401
    assert mutate(owner, "POST", "/api/auth/email-codes", request).status_code == 403
    assert (
        mutate(
            admin, "POST", "/api/auth/email-codes", {**request, "email": "reader@example.com"}
        ).status_code
        == 422
    )
    body = {
        "archived": True,
        "expected_updated_at": admin.get(path(workspace)).json()["updated_at"],
    }
    endpoint = path(workspace) + "/archive"
    assert mutate(admin, "PUT", endpoint, body).status_code == 422
    code = admin_code(workspace, admin)
    wrong = "000000" if code != "000000" else "111111"
    assert admin.put(endpoint, json={**body, "code": code}).status_code == 403
    assert mutate(admin, "PUT", endpoint, {**body, "code": wrong}).status_code == 400
    with Session(workspace.engine) as db:
        challenge = db.scalar(
            select(EmailChallenge).where(EmailChallenge.purpose == "admin_operation")
        )
        assert challenge.attempts == 1
        assert challenge.subject_user_id == workspace.people["outsider"][0]
        assert not db.get(Repository, workspace.repo["id"]).archived
    result = mutate(admin, "PUT", endpoint, {**body, "code": code})
    assert result.status_code == 200 and result.json()["archived"]
    assert (
        mutate(
            admin,
            "PUT",
            endpoint,
            {
                **body,
                "archived": False,
                "expected_updated_at": result.json()["updated_at"],
                "code": code,
            },
        ).status_code
        == 400
    )
    # A login-purpose challenge, even if still unconsumed, is never a step-up code.
    code = admin_code(workspace, admin)
    with Session(workspace.engine) as db, db.begin():
        challenge = db.scalar(
            select(EmailChallenge)
            .where(EmailChallenge.purpose == "admin_operation")
            .order_by(EmailChallenge.id.desc())
        )
        challenge.purpose = "login"
    assert (
        mutate(
            admin,
            "PUT",
            endpoint,
            {**body, "expected_updated_at": result.json()["updated_at"], "code": code},
        ).status_code
        == 400
    )


@pytest.mark.parametrize("reason", ["expired", "attempts", "email", "role", "session", "disabled"])
def test_stale_verification_cannot_mutate(workspace, reason):
    admin = workspace.people["outsider"][1]
    code = admin_code(workspace, admin)
    version = admin.get(path(workspace)).json()["updated_at"]
    user_id = workspace.people["outsider"][0]
    with Session(workspace.engine) as db, db.begin():
        challenge = db.scalar(
            select(EmailChallenge).where(EmailChallenge.purpose == "admin_operation")
        )
        if reason == "expired":
            challenge.created_at = datetime.now(UTC) - timedelta(hours=1)
            challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif reason == "attempts":
            challenge.attempts = workspace.settings.email_code_attempts
        elif reason == "email":
            db.get(User, user_id).email = "changed@example.com"
        elif reason == "role":
            db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        elif reason == "session":
            for session in db.scalars(select(UserSession).where(UserSession.user_id == user_id)):
                session.revoked_at = datetime.now(UTC)
        else:
            db.get(User, user_id).status = "disabled"
    result = mutate(
        admin,
        "PUT",
        path(workspace) + "/archive",
        {"archived": True, "expected_updated_at": version, "code": code},
    )
    assert result.status_code in {400, 401, 403}
    with Session(workspace.engine) as db:
        assert not db.get(Repository, workspace.repo["id"]).archived


def test_forced_members_transfer_archive_and_soft_delete_update_real_registry_acl(registry):
    admin, reader = registry.people["outsider"][1], registry.people["reader"][1]
    value = credential(registry, "reader")
    reader_id = registry.people["reader"][0]
    result = change(
        registry,
        "/members",
        "POST",
        {"user_id": reader_id, "access": "read", "is_admin": True, "role": "测试人员"},
    )
    assert result.status_code == 201
    assert check(registry, value, who="reader").status_code == 204
    assert check(registry, value, "PUT", who="reader").status_code == 403
    result = change(
        registry, f"/members/{reader_id}", "PATCH", {"access": "write", "is_admin": False}
    )
    assert result.status_code == 200
    assert check(registry, value, "PUT", who="reader").status_code == 204
    assert admin.get(registry.path).status_code == 404
    assert change(registry, "/archive", "PUT", {"archived": True}).status_code == 200
    assert check(registry, value, "PUT", who="reader").status_code == 403
    assert check(registry, value, who="reader").status_code == 204
    assert change(registry, "/archive", "PUT", {"archived": False}).status_code == 200
    assert change(registry, f"/members/{reader_id}", "DELETE", {}).status_code == 200
    assert check(registry, value, who="reader").status_code == 403
    owner_id = registry.people["owner"][0]
    assert change(registry, f"/members/{owner_id}", "DELETE", {}).status_code == 409
    transferred = change(
        registry, "/transfer", "POST", {"confirm_name": "owner/example", "new_owner_id": reader_id}
    )
    assert transferred.status_code == 200
    assert transferred.json()["full_name"] == "reader/example"
    old_owner = next(
        member for member in transferred.json()["members"] if member["user_id"] == owner_id
    )
    assert old_owner["is_admin"] and old_owner["access"] == "write"
    assert check(registry, value, "PUT", who="reader").status_code == 204
    assert admin.get(registry.path).status_code == 404
    assert reader.get(registry.path).status_code == 200
    assert change(registry, "", "DELETE", {"confirm_name": "reader/example"}).status_code == 200
    assert reader.get(registry.path).status_code == 404
    assert check(registry, value, who="reader").status_code == 403
    assert (Path(registry.settings.git_repositories_root) / f"{registry.repo['id']}.git").exists()
    assert admin.get("/api/admin/repositories?state=deleted").json()["total"] == 1
    assert change(registry, "/archive", "PUT", {"archived": False}).status_code == 409
    with Session(registry.engine) as db:
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.action.like("repository.admin_%"))
        ).all()
        assert len(events) == 7
        assert all(event.details["forced"] and "code" not in event.details for event in events)


def test_conflicts_and_owner_mutations_invalidate_admin_form(workspace):
    admin = workspace.people["outsider"][1]
    version = admin.get(path(workspace)).json()["updated_at"]
    assert add(workspace, "reader").status_code == 201
    code = admin_code(workspace, admin)
    assert (
        mutate(
            admin,
            "PUT",
            path(workspace) + "/archive",
            {"archived": True, "expected_updated_at": version, "code": code},
        ).status_code
        == 409
    )
    duplicate = mutate(
        workspace.people["reader"][1], "POST", "/api/repositories", {"name": "example"}
    )
    assert duplicate.status_code == 201
    conflict = change(
        workspace,
        "/transfer",
        "POST",
        {"confirm_name": "owner/example", "new_owner_id": workspace.people["reader"][0]},
    )
    assert conflict.status_code == 409
    assert admin.get(path(workspace)).json()["owner"]["id"] == workspace.people["owner"][0]


def test_concurrent_same_code_cannot_perform_two_operations(workspace):
    admin = workspace.people["outsider"][1]
    code = admin_code(workspace, admin)
    version = admin.get(path(workspace)).json()["updated_at"]
    # Two independently requested operations race on the same repository and code.
    clients = [workspace.client(), workspace.client()]
    for client in clients:
        client.cookies.update(admin.cookies)
    body = {"archived": True, "expected_updated_at": version, "code": code}
    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                lambda client: mutate(client, "PUT", path(workspace) + "/archive", body), clients
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]


def test_one_code_cannot_verify_concurrent_operations_on_different_repositories(workspace):
    admin = workspace.people["outsider"][1]
    second = mutate(
        workspace.people["owner"][1], "POST", "/api/repositories", {"name": "other"}
    ).json()
    code = admin_code(workspace, admin)
    endpoints = [path(workspace), f"/api/admin/repositories/{second['id']}"]
    revisions = [admin.get(endpoint).json()["updated_at"] for endpoint in endpoints]
    clients = [workspace.client(), workspace.client()]
    for client in clients:
        client.cookies.update(admin.cookies)

    def archive(index):
        return mutate(
            clients[index],
            "PUT",
            endpoints[index] + "/archive",
            {
                "archived": True,
                "expected_updated_at": revisions[index],
                "code": code,
            },
        )

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(archive, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 400]
    assert sum(admin.get(endpoint).json()["archived"] for endpoint in endpoints) == 1


def test_other_admin_code_is_not_transferable_between_users(workspace):
    admin = workspace.people["outsider"][1]
    workspace.seed("second_admin", "admin")
    other = workspace.client()
    workspace.login(other, "second_admin@example.com")
    code = admin_code(workspace, other, "second_admin")
    version = admin.get(path(workspace)).json()["updated_at"]
    result = mutate(
        admin,
        "PUT",
        path(workspace) + "/archive",
        {
            "archived": True,
            "expected_updated_at": version,
            "code": code,
        },
    )
    assert result.status_code == 400
