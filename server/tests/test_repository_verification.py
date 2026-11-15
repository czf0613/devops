from datetime import UTC, datetime, timedelta

import pytest
from conftest import mutate, repository_code
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_cli_oauth import bearer, cli_tokens
from test_repositories import add, workspace  # noqa: F401

from devops_server import operation_verification
from devops_server.models import EmailChallenge, GitOAuthGrant, Repository, User, UserSession


def test_lifecycle_requires_current_email_bound_single_use_purpose(workspace):  # noqa: F811
    owner = workspace.people["owner"][1]
    path = workspace.path + "/archive"
    assert mutate(owner, "PUT", path, {"archived": True}).status_code == 422
    assert (
        mutate(
            owner,
            "POST",
            "/api/auth/email-codes",
            {
                "email": "reader@example.com",
                "purpose": "repository_operation",
            },
        ).status_code
        == 422
    )
    code = repository_code(workspace, owner)
    wrong = "000000" if code != "000000" else "111111"
    assert mutate(owner, "PUT", path, {"archived": True, "code": wrong}).status_code == 400
    with Session(workspace.engine) as db:
        challenge = db.scalar(
            select(EmailChallenge).where(EmailChallenge.purpose == "repository_operation")
        )
        assert challenge.attempts == 1 and challenge.subject_user_id == workspace.people["owner"][0]
        assert not db.get(Repository, workspace.repo["id"]).archived
    assert mutate(owner, "PUT", path, {"archived": True, "code": code}).status_code == 200
    assert mutate(owner, "PUT", path, {"archived": False, "code": code}).status_code == 400
    # Login/admin codes cannot act as repository-operation codes.
    code = repository_code(workspace, owner)
    with Session(workspace.engine) as db, db.begin():
        challenge = db.scalar(
            select(EmailChallenge)
            .where(EmailChallenge.purpose == "repository_operation")
            .order_by(EmailChallenge.id.desc())
        )
        challenge.purpose = "admin_operation"
    assert mutate(owner, "PUT", path, {"archived": False, "code": code}).status_code == 400
    code = repository_code(workspace, owner)
    with Session(workspace.engine) as db, db.begin():
        challenge = db.scalar(
            select(EmailChallenge)
            .where(EmailChallenge.purpose == "repository_operation")
            .order_by(EmailChallenge.id.desc())
        )
        challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        challenge.created_at = challenge.expires_at - timedelta(minutes=1)
    assert mutate(owner, "PUT", path, {"archived": False, "code": code}).status_code == 400


@pytest.mark.parametrize("change", ["email", "session", "disabled"])
def test_revalidates_identity_after_waiting_for_verification(workspace, monkeypatch, change):  # noqa: F811
    owner_id, owner = workspace.people["owner"]
    code = repository_code(workspace, owner)
    original = operation_verification.verify_code

    def changed(*args):
        original(*args)
        with Session(workspace.engine) as db, db.begin():
            if change == "email":
                db.get(User, owner_id).email = "changed@example.com"
            elif change == "disabled":
                db.get(User, owner_id).status = "disabled"
            else:
                for session in db.scalars(
                    select(UserSession).where(UserSession.user_id == owner_id)
                ):
                    session.revoked_at = datetime.now(UTC)

    monkeypatch.setattr(operation_verification, "verify_code", changed)
    assert (
        mutate(
            owner, "PUT", workspace.path + "/archive", {"archived": True, "code": code}
        ).status_code
        == 401
    )
    with Session(workspace.engine) as db:
        assert not db.get(Repository, workspace.repo["id"]).archived


def test_cli_requires_code_and_current_grant_without_cookie_fallback(workspace):  # noqa: F811
    owner = workspace.people["owner"][1]
    machine = workspace.client()
    tokens = cli_tokens(owner)
    headers = bearer(tokens)
    path = workspace.path + "/archive"
    assert machine.put(path, headers=headers, json={"archived": True}).status_code == 422
    workspace.limiter.client.delete(workspace.limiter.keys("owner@example.com", "127.0.0.1")[2])
    assert machine.post(workspace.path + "/operation-code", headers=headers).status_code == 202
    code = workspace.mailbox.code("owner@example.com", "repository_operation")
    assert (
        machine.put(path, headers=headers, json={"archived": True, "code": code}).status_code == 200
    )
    assert (
        machine.put(path, headers=headers, json={"archived": False, "code": code}).status_code
        == 400
    )
    assert add(workspace, "reader").status_code == 201
    reader_tokens = cli_tokens(workspace.people["reader"][1])
    assert (
        machine.post(workspace.path + "/operation-code", headers=bearer(reader_tokens)).status_code
        == 403
    )
    with Session(workspace.engine) as db, db.begin():
        db.scalar(
            select(GitOAuthGrant).where(GitOAuthGrant.user_id == workspace.people["owner"][0])
        ).revoked_at = datetime.now(UTC)
    assert (
        owner.put(path, headers=headers, json={"archived": False, "code": code}).status_code == 401
    )
