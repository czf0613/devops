from pathlib import Path

import pytest
from conftest import mutate
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_collaboration import create
from test_repositories import add
from test_repositories import workspace as repository_workspace

from devops_server import collaboration_git as git
from devops_server import collaboration_worker as worker
from devops_server.collaboration_models import CollaborationJob, MergeIntent, WorkItem


@pytest.fixture
def workspace(system):
    return repository_workspace.__wrapped__(system)


def commit(workspace, branch, files, *, parent=None, message="Feature"):
    source = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    parent = parent or git.branch_sha(source, "main")
    rows = []
    for name, content in files.items():
        blob = git.text_git(source, "hash-object", "-w", "--stdin", content=content.encode())
        rows.append(f"100644 blob {blob}\t{name}\n")
    tree = git.text_git(source, "mktree", content="".join(rows).encode())
    sha = git.commit_tree(source, tree, [parent], message)
    git.run(source, "update-ref", f"refs/heads/{branch}", sha)
    return sha


def pull(workspace, **extra):
    response = mutate(
        workspace.people["owner"][1],
        "POST",
        workspace.path + "/pulls",
        {"title": "Feature PR", "head_ref": "feature", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def drain(workspace):
    for _ in range(20):
        if not worker.run_one(workspace.engine, workspace.settings):
            break
    else:
        pytest.fail("Worker did not drain bounded fixture jobs")


def request_merge(workspace, number, method="merge", **extra):
    owner = workspace.people["owner"][1]
    detail = owner.get(workspace.path + f"/pulls/{number}").json()
    response = mutate(
        owner,
        "POST",
        workspace.path + f"/pulls/{number}/merge",
        {"base_sha": detail["base_sha"], "head_sha": detail["head_sha"], "method": method, **extra},
    )
    return response


@pytest.mark.parametrize("method", ["merge", "squash", "rebase"])
def test_native_merge_shared_numbers_and_automatic_issue_closure(workspace, method):
    issue = create(workspace, "Resolve me")
    commit(workspace, "feature", {"file.txt": "new content\n"}, message="Implement feature")
    pr = pull(workspace, body="Closes #1")
    assert pr["number"] == issue["number"] + 1
    assert request_merge(workspace, 2, method).status_code == 409
    drain(workspace)
    response = request_merge(workspace, 2, method)
    assert response.status_code == 202, response.text
    drain(workspace)
    owner = workspace.people["owner"][1]
    assert owner.get(workspace.path + "/items/2").json()["state"] == "merged"
    assert owner.get(workspace.path + "/items/1").json()["state"] == "closed"
    detail = owner.get(workspace.path + "/pulls/2").json()
    source = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    assert git.branch_sha(source, "main") == detail["merged_sha"]
    assert git.text_git(source, "show", "refs/heads/main:file.txt") == "new content"
    parents = git.text_git(source, "rev-list", "--parents", "-n", "1", detail["merged_sha"]).split()
    assert len(parents) == (3 if method == "merge" else 2)
    assert detail["checks_configured"] is False


def test_conflicts_and_source_configuration_cannot_execute(workspace, tmp_path):
    original = commit(workspace, "main", {"file.txt": "first\n"})
    commit(
        workspace,
        "feature",
        {"file.txt": "feature\n", ".gitattributes": "*.txt merge=evil\n"},
        parent=original,
    )
    commit(
        workspace,
        "main",
        {"file.txt": "base\n", ".gitattributes": "*.txt merge=evil\n"},
        parent=original,
    )
    sentinel = tmp_path / "should-not-exist"
    source = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    git.run(source, "config", "merge.evil.driver", f"touch {sentinel}")
    pr = pull(workspace)
    drain(workspace)
    detail = workspace.people["owner"][1].get(workspace.path + f"/pulls/{pr['number']}").json()
    assert all(check["status"] == "conflict" for check in detail["merge_checks"].values())
    assert "file.txt" in detail["merge_checks"]["merge"]["conflicts"]
    assert request_merge(workspace, pr["number"]).status_code == 409
    assert not sentinel.exists()


def test_required_reviews_threads_checks_and_private_drafts(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    assert add(workspace, "reader", access="write").status_code == 201
    commit(workspace, "feature", {"file.txt": "line 1\nline 2\n"})
    pr = pull(workspace)
    policy = mutate(
        owner,
        "PUT",
        workspace.path + "/merge-policy",
        {"required_approvals": 1, "required_checks": ["unit"]},
    )
    assert policy.status_code == 200
    draft = mutate(
        reader, "POST", workspace.path + "/pulls/1/reviews", {"head_sha": pr["pull"]["head_sha"]}
    ).json()
    own_review = mutate(
        owner,
        "POST",
        workspace.path + "/pulls/1/reviews",
        {"head_sha": pr["pull"]["head_sha"], "state": "approved"},
    )
    assert own_review.status_code == 422
    thread = mutate(
        reader,
        "POST",
        workspace.path + "/pulls/1/threads",
        {
            "body": "Please adjust",
            "review_id": draft["id"],
            "path": "file.txt",
            "side": "right",
            "start_line": 1,
            "end_line": 2,
            "base_sha": pr["pull"]["base_sha"],
            "head_sha": pr["pull"]["head_sha"],
        },
    )
    assert thread.status_code == 201, thread.text
    assert owner.get(workspace.path + "/pulls/1/threads").json() == []
    assert len(reader.get(workspace.path + "/pulls/1/threads").json()) == 1
    submitted = mutate(
        reader,
        "POST",
        workspace.path + "/pulls/1/reviews",
        {"head_sha": pr["pull"]["head_sha"], "state": "approved", "body": "Looks good"},
    )
    assert submitted.status_code == 201, submitted.text
    assert len(owner.get(workspace.path + "/pulls/1/threads").json()) == 1
    drain(workspace)
    assert request_merge(workspace, 1).status_code == 409
    tid = thread.json()["id"]
    assert (
        mutate(owner, "PUT", workspace.path + f"/pulls/1/threads/{tid}/resolved").status_code == 200
    )
    assert request_merge(workspace, 1).status_code == 409
    assert (
        mutate(
            reader,
            "PUT",
            workspace.path + "/pulls/1/checks",
            {"name": "unit", "status": "success", "head_sha": pr["pull"]["head_sha"]},
        ).status_code
        == 403
    )
    assert (
        mutate(
            owner,
            "PUT",
            workspace.path + "/pulls/1/checks",
            {"name": "unit", "status": "success", "head_sha": pr["pull"]["head_sha"]},
        ).status_code
        == 200
    )
    assert request_merge(workspace, 1).status_code == 202


def test_push_after_merge_request_cannot_merge_stale_head(workspace):
    commit(workspace, "feature", {"file.txt": "first\n"})
    pull(workspace)
    drain(workspace)
    response = request_merge(workspace, 1)
    assert response.status_code == 202
    commit(workspace, "feature", {"file.txt": "new head\n"})
    drain(workspace)
    with Session(workspace.engine) as db:
        intent = db.get(MergeIntent, response.json()["id"])
        assert intent.state == "failed"
        assert db.scalar(select(WorkItem.state).where(WorkItem.number == 1)) == "open"


def test_ref_update_survives_database_finalize_failure(workspace, monkeypatch):
    commit(workspace, "feature", {"file.txt": "atomic\n"})
    pull(workspace)
    drain(workspace)
    response = request_merge(workspace, 1)
    original_finalize = worker.finalize_merge

    def failed(*_):
        raise OperationalError("synthetic", None, Exception("simulated database disconnect"))

    monkeypatch.setattr(worker, "finalize_merge", failed)
    assert worker.run_one(workspace.engine, workspace.settings)
    with Session(workspace.engine) as db:
        intent = db.get(MergeIntent, response.json()["id"])
        assert intent.state == "applied"
        source = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
        assert git.branch_sha(source, "main") == intent.result_sha
        job = db.scalar(select(CollaborationJob).where(CollaborationJob.kind == "merge"))
        job.available_at = worker.service.now()
        db.commit()
    monkeypatch.setattr(worker, "finalize_merge", original_finalize)
    drain(workspace)
    assert workspace.people["owner"][1].get(workspace.path + "/items/1").json()["state"] == "merged"


def test_private_attachments_notifications_and_commit_resolution(workspace):
    owner = workspace.people["owner"][1]
    reader = workspace.people["reader"][1]
    outsider = workspace.people["outsider"][1]
    assert add(workspace, "reader").status_code == 201
    create(workspace)
    assert (
        mutate(
            reader, "PUT", workspace.path + "/items/1/subscription", {"watching": True}
        ).status_code
        == 200
    )
    comment = mutate(
        owner, "POST", workspace.path + "/items/1/comments", {"body": "Update @reader"}
    )
    assert comment.status_code == 201
    notifications = reader.get("/api/me/notifications").json()
    assert notifications["unread_count"] == 1
    file = mutate(
        owner,
        "POST",
        workspace.path + "/attachments?filename=example.html&number=1",
        content=b"<script>bad()</script>",
    )
    assert file.status_code == 201, file.text
    url = file.json()["url"]
    assert outsider.get(url).status_code == 404
    assert reader.get(url).status_code == 404
    assert owner.get(url).status_code == 200
    assert (
        mutate(
            owner, "POST", workspace.path + "/items/1/comments", {"body": f"[Attachment]({url})"}
        ).status_code
        == 201
    )
    downloaded = reader.get(url)
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    sha = commit(workspace, "feature", {"file.txt": "hello\n"})
    detail = owner.get(workspace.path + f"/commits/{sha[:12]}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["sha"] == sha
    assert outsider.get(workspace.path + f"/commits/{sha}").status_code == 404
    assert (
        mutate(
            owner, "DELETE", workspace.path + f"/members/{workspace.people['reader'][0]}"
        ).status_code
        == 200
    )
    assert reader.get("/api/me/notifications").json()["total"] == 0
    assert reader.get(url).status_code == 404


def test_pending_replies_edits_history_and_mentions_stay_private(workspace):
    owner, reader = workspace.people["owner"][1], workspace.people["reader"][1]
    add(workspace, "reader", access="write")
    create(workspace, "Mention target")
    commit(workspace, "feature", {"file.txt": "one\n"})
    pr = pull(workspace)
    pp = workspace.path + "/pulls/2"
    ip = workspace.path + "/items/2"
    draft = mutate(reader, "POST", pp + "/reviews", {"head_sha": pr["pull"]["head_sha"]}).json()
    thread = mutate(
        reader,
        "POST",
        pp + "/threads",
        {
            "body": "Draft #1",
            "review_id": draft["id"],
            "path": "file.txt",
            "side": "right",
            "start_line": 1,
            "end_line": 1,
            "base_sha": pr["pull"]["base_sha"],
            "head_sha": pr["pull"]["head_sha"],
        },
    ).json()
    cid, tid = thread["comment"]["id"], thread["id"]
    reply = mutate(
        reader, "POST", pp + f"/threads/{tid}/comments", {"body": "Private reply #1"}
    ).json()
    assert reply["pending"] and reply["review_id"] == draft["id"]
    assert (
        mutate(owner, "PATCH", ip + f"/comments/{cid}", {"version": 1, "body": "No"}).status_code
        == 404
    )
    assert owner.get(ip + f"/history?comment={cid}").status_code == 404
    assert (
        mutate(
            owner,
            "POST",
            workspace.path + "/markdown/resolve",
            {"body": "Draft #1", "item_id": pr["id"], "comment_id": cid},
        ).status_code
        == 404
    )
    assert (
        mutate(
            reader, "PATCH", ip + f"/comments/{cid}", {"version": 1, "body": "Edited #1"}
        ).status_code
        == 200
    )
    assert mutate(reader, "PUT", pp + f"/threads/{tid}/resolved").status_code == 409
    assert owner.get(pp + "/threads").json() == []
    assert owner.get(workspace.path + "/items/1").json()["backlinks"] == []
    assert not any(
        event["action"].startswith("comment")
        for event in owner.get(ip + "/timeline").json()["items"]
    )
    mutate(
        reader, "POST", pp + "/reviews", {"head_sha": pr["pull"]["head_sha"], "state": "commented"}
    )
    assert len(owner.get(pp + "/threads").json()[0]["comments"]) == 2
    assert len(owner.get(workspace.path + "/items/1").json()["backlinks"]) == 2
    assert (
        mutate(owner, "PUT", ip + "/reaction", {"emoji": "heart", "comment_id": cid}).status_code
        == 200
    )


def test_changes_requested_survives_push_and_revocation_blocks_queued_merge(workspace):
    owner, reader = workspace.people["owner"][1], workspace.people["reader"][1]
    add(workspace, "reader", access="write")
    commit(workspace, "feature", {"file.txt": "one\n"})
    pr = pull(workspace)
    pp = workspace.path + "/pulls/1"
    mutate(
        reader,
        "POST",
        pp + "/reviews",
        {"head_sha": pr["pull"]["head_sha"], "state": "changes_requested"},
    )
    commit(workspace, "feature", {"file.txt": "two\n"})
    refreshed = owner.get(pp).json()
    assert any(review["state"] == "changes_requested" for review in refreshed["reviews"])
    assert refreshed["reasons"]
    mutate(
        reader, "POST", pp + "/reviews", {"head_sha": refreshed["head_sha"], "state": "approved"}
    )
    drain(workspace)
    response = request_merge(workspace, 1)
    assert response.status_code == 202
    from devops_server.models import UserSession

    with Session(workspace.engine) as db:
        for session in db.scalars(
            select(UserSession).where(UserSession.user_id == workspace.people["owner"][0])
        ):
            session.revoked_at = worker.service.now()
        db.commit()
    drain(workspace)
    with Session(workspace.engine) as db:
        assert db.get(MergeIntent, response.json()["id"]).state == "failed"
        assert db.scalar(select(WorkItem.state)) == "open"


def test_push_receipts_index_commit_backlinks_and_only_main_closes(workspace):
    import os
    import subprocess

    from devops_server.collaboration_pushes import ingest

    owner = workspace.people["owner"][1]
    create(workspace, "Referenced")
    source = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    old = git.branch_sha(source, "main")
    head = commit(workspace, "feature", {"file.txt": "receipt\n"}, message="Fixes #1")
    hook = Path(__file__).parents[2] / "gateway/hooks/post-receive"
    environment = {**os.environ, "REMOTE_USER": str(workspace.people["owner"][0])}
    for ref in ("feature", "main"):
        subprocess.run(
            [str(hook.resolve())],
            cwd=source,
            env=environment,
            input=f"{old} {head} refs/heads/{ref}\n",
            text=True,
            capture_output=True,
            check=True,
        )
        ingest(workspace.engine, workspace.settings)
        assert not list((source / "devops-events").glob("*.ready"))
        drain(workspace)
        issue = owner.get(workspace.path + "/items/1").json()
        assert issue["state"] == ("closed" if ref == "main" else "open")
        assert len(issue["backlinks"]) == 1
        assert issue["backlinks"][0]["url"].endswith("/commit/" + head)


def test_native_diff_statistics_and_reserved_receive_refs(workspace):
    import os
    import subprocess

    source = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"
    old = commit(workspace, "main", {"old.txt": "same\n", "change.txt": "one\n"})
    head = commit(workspace, "feature", {"new.txt": "same\n", "change.txt": "one\ntwo\n"})
    diff = git.compare(workspace.settings.git_repositories_root, workspace.repo["id"], old, head)
    assert diff["additions"] == 1 and diff["deletions"] == 0
    assert any(
        entry["status"].startswith("R") and entry["path"] == "new.txt" for entry in diff["files"]
    )
    patch = git.compare(
        workspace.settings.git_repositories_root, workspace.repo["id"], old, head, path="new.txt"
    )["patch"]
    assert "rename from old.txt" in patch and "rename to new.txt" in patch
    hook = Path(__file__).parents[2] / "gateway/hooks/pre-receive"
    result = subprocess.run(
        ["/bin/sh", str(hook.resolve())],
        cwd=source,
        env={**os.environ, "DEVOPS_PROTECT_MAIN": "0", "DEVOPS_PROTECT_PUSH": "0"},
        input=f"{'0' * 40} {head} refs/devops/merges/123\n",
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1 and "reserved" in result.stderr
