import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import lifecycle_mutate, mutate
from test_git_oauth import git_auth, issue
from test_repositories import add
from test_repositories import workspace as workspace

from devops_server import repository_code


@pytest.fixture
def code_repo(workspace, tmp_path):
    directory = tmp_path / "checkout"
    directory.mkdir()
    bare = Path(workspace.settings.git_repositories_root) / f"{workspace.repo['id']}.git"

    def git(*args):
        return (
            subprocess.check_output(
                ["git", "-C", str(directory), *args],
                stderr=subprocess.PIPE,
                env=os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
            )
            .decode()
            .strip()
        )

    git("clone", str(bare), ".")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.com")
    (directory / "src").mkdir()
    (directory / "src" / "中文 文件.txt").write_text("<script>alert('safe text')</script>\n")
    (directory / "README.md").write_text("# Fixture\n")
    (directory / "src" / "ReadMe.md").write_text("# Nested\n\n[Root](../README.md)\n")
    (directory / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    (directory / "binary.bin").write_bytes(b"\x00\xff")
    (directory / "large.txt").write_text("x" * (1024 * 1024 + 1))
    (directory / "link").symlink_to("/etc/passwd")
    git("add", ".")
    git("commit", "-m", "First")
    first = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "Second")
    second = git("rev-parse", "HEAD")
    git("branch", "feature/code")
    git("push", "--all", "origin")
    workspace.git, workspace.first, workspace.second = git, first, second
    workspace.bare = bare
    return workspace


def test_code_browsing_branch_paths_permissions_and_limits(code_repo):
    w = code_repo
    client = w.people["owner"][1]
    branches = client.get(w.path + "/branches").json()
    assert [item["name"] for item in branches["items"]] == ["feature/code", "main"]
    tree = client.get(w.path + "/contents").json()
    assert tree["commit"] == w.second and tree["entries"][0]["name"] == "src"
    assert next(item for item in tree["entries"] if item["name"] == "link")["type"] == "symlink"
    blob = client.get(
        w.path + "/contents", params={"ref": "feature/code", "path": "src/中文 文件.txt"}
    )
    assert blob.status_code == 200
    assert blob.json()["content"] == "<script>alert('safe text')</script>\n"
    assert (
        client.get(w.path + "/contents", params={"path": "link"}).json()["content"] == "/etc/passwd"
    )
    assert client.get(w.path + "/contents", params={"path": "binary.bin"}).json()["binary"]
    assert client.get(w.path + "/contents", params={"path": "large.txt"}).json()["too_large"]
    for params in [
        {"ref": "main~1"},
        {"ref": w.first},
        {"path": "../config"},
        {"path": "/HEAD"},
        {"path": "*.md"},
        {"ref": "--help"},
        {"path": "README.md\0"},
    ]:
        assert client.get(w.path + "/contents", params=params).status_code in {404, 422}
    outsider = w.people["outsider"][1]
    for suffix in ("/contents", "/branches", "/activity"):
        assert outsider.get(w.path + suffix).status_code == 404
        assert w.client().get(w.path + suffix).status_code == 401
    assert add(w, "reader").status_code == 201
    assert w.people["reader"][1].get(w.path + "/contents").status_code == 200
    activity = w.people["reader"][1].get(w.path + "/activity").json()
    assert activity["total"] == 3  # Shared main / feature history is counted once.
    assert len(activity["weeks"]) == 12 and not activity["truncated"]
    assert (
        lifecycle_mutate(w, client, "PUT", w.path + "/archive", {"archived": True}).status_code
        == 200
    )
    assert w.people["reader"][1].get(w.path + "/contents").status_code == 200
    assert (
        lifecycle_mutate(
            w, client, "DELETE", w.path, {"confirm_name": w.repo["full_name"]}
        ).status_code
        == 200
    )
    assert client.get(w.path + "/contents").status_code == 404
    assert client.get(w.path + "/activity").status_code == 404


def test_empty_repository_and_owner_only_protection(workspace):
    w = workspace
    owner = w.people["owner"][1]
    # Older empty repositories stay readable; new creations have an initial README.
    bare = Path(w.settings.git_repositories_root) / f"{w.repo['id']}.git"
    subprocess.run(["git", "-C", str(bare), "update-ref", "-d", "refs/heads/main"], check=True)
    assert owner.get(w.path + "/branches").json()["items"] == []
    assert owner.get(w.path + "/contents").json()["type"] == "empty"
    activity = owner.get(w.path + "/activity").json()
    assert activity["total"] == 0 and not activity["truncated"]
    assert all(week["commits"] == 0 for week in activity["weeks"])
    assert not w.repo["protect_main"] and not w.repo["protect_push"]
    assert add(w, "manager", is_admin=True).status_code == 201
    assert add(w, "reader", access="write").status_code == 201
    body = {"protect_main": True, "protect_push": True}
    for name in ("manager", "reader", "outsider"):
        assert mutate(w.people[name][1], "PUT", w.path + "/protection", body).status_code in {
            403,
            404,
        }
    assert owner.put(w.path + "/protection", json=body).status_code == 403
    result = mutate(owner, "PUT", w.path + "/protection", body)
    assert (
        result.status_code == 200
        and result.json()["protect_main"]
        and result.json()["protect_push"]
    )
    tokens, _ = issue(owner)
    auth = git_auth(owner, tokens["access_token"], repository="owner/example", operation="write")
    assert auth.headers["X-Git-Protect-Main"] == auth.headers["X-Git-Protect-Push"] == "1"
    assert (
        mutate(
            owner, "PUT", w.path + "/protection", {"protect_main": "false", "protect_push": False}
        ).status_code
        == 422
    )
    assert (
        mutate(
            owner, "PUT", w.path + "/protection", {"protect_main": False, "protect_push": False}
        ).status_code
        == 200
    )


def test_weekly_activity_utc_boundaries_out_of_order_history_and_limit(tmp_path, monkeypatch):
    bare = tmp_path / "activity.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    monday = datetime(2026, 9, 28, tzinfo=UTC)
    start = monday - timedelta(weeks=11)

    def git(*args, when=now):
        environment = {
            "PATH": os.environ["PATH"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_DATE": when.isoformat(),
            "GIT_COMMITTER_DATE": when.isoformat(),
        }
        return (
            subprocess.check_output(["git", f"--git-dir={bare}", *args], input=b"", env=environment)
            .decode()
            .strip()
        )

    tree = git("mktree")
    old = git("commit-tree", tree, "-m", "old", when=start - timedelta(seconds=1))
    first = git("commit-tree", tree, "-p", old, "-m", "first", when=start)
    # Traversal must not stop when a child has an older clock than its parent.
    skew = git("commit-tree", tree, "-p", first, "-m", "skew", when=start - timedelta(days=1))
    recent = git("commit-tree", tree, "-p", skew, "-m", "recent", when=monday)
    side = git("commit-tree", tree, "-p", first, "-m", "side", when=monday - timedelta(seconds=1))
    future = git("commit-tree", tree, "-p", recent, "-m", "future", when=now + timedelta(days=1))
    tag = git("commit-tree", tree, "-m", "tag only")
    for name, oid in [
        ("main", recent),
        ("duplicate", recent),
        ("feature", side),
        ("future", future),
    ]:
        git("update-ref", f"refs/heads/{name}", oid)
    git("update-ref", "refs/tags/tag-only", tag)
    result = repository_code.weekly_activity(bare, now)
    assert result["total"] == 3
    assert result["weeks"][0] == {"start": start.date().isoformat(), "commits": 1}
    assert result["weeks"][-2]["commits"] == 1
    assert result["weeks"][-1] == {"start": "2026-09-28", "commits": 1}
    assert not result["truncated"]
    monkeypatch.setattr(repository_code, "ACTIVITY_COMMIT_LIMIT", 2)
    limited = repository_code.weekly_activity(bare, now)
    assert limited["truncated"] and limited["commit_limit"] == 2
    assert limited["total"] <= 2


def test_receive_hook_enforces_entire_push_and_fails_closed(code_repo):
    w = code_repo
    hook = Path(__file__).resolve().parents[2] / "gateway/hooks/pre-receive"
    zero = "0" * 40

    def check(lines, main="0", push="0"):
        result = subprocess.run(
            ["sh", str(hook)],
            cwd=w.bare,
            input=lines,
            text=True,
            capture_output=True,
            env=os.environ
            | {"GIT_DIR": str(w.bare), "DEVOPS_PROTECT_MAIN": main, "DEVOPS_PROTECT_PUSH": push},
        )
        return result.returncode

    assert check(f"{w.first} {w.second} refs/heads/main\n", push="1") == 0
    assert check(f"{w.second} {w.first} refs/heads/main\n", push="1") != 0
    assert check(f"{w.second} {w.first} refs/heads/main\n") == 0
    assert check(f"{zero} {w.second} refs/heads/topic\n", main="1", push="1") == 0
    assert check(f"{zero} {w.second} refs/heads/main\n", main="1") != 0
    assert (
        check(
            f"{zero} {w.second} refs/heads/topic\n{w.first} {w.second} refs/heads/main\n", main="1"
        )
        != 0
    )
    for ref in ("refs/heads/topic", "refs/tags/v1"):
        assert check(f"{w.second} {zero} {ref}\n", push="1") != 0
    assert check(f"{w.first} {w.second} refs/tags/v1\n", push="1") != 0
    assert check(f"{w.first} {w.second} refs/heads/main\n", main="") != 0


def test_readme_selection_pagination_safety_and_private_images(code_repo):
    w = code_repo
    client = w.people["owner"][1]
    root = client.get(w.path + "/contents").json()
    assert root["readme"]["path"] == "README.md"
    assert root["readme"]["content"] == "# Fixture\n"
    nested = client.get(w.path + "/contents", params={"path": "src"}).json()
    assert nested["readme"]["path"] == "src/ReadMe.md"
    assert nested["readme"]["content"].startswith("# Nested")
    image = client.get(w.path + "/image", params={"path": "picture.png"})
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["cache-control"] == "no-store"
    assert w.people["outsider"][1].get(w.path + "/image?path=picture.png").status_code == 404
    assert w.client().get(w.path + "/image?path=picture.png").status_code == 401
    for path in ("README.md", "link"):
        assert client.get(w.path + "/image", params={"path": path}).status_code == 415
    assert client.get(w.path + "/image?path=../config").status_code == 422
    checkout = Path(w.git("rev-parse", "--show-toplevel"))
    for number in range(201):
        (checkout / f"a{number:03}.txt").write_text("item")
    (checkout / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024))
    (checkout / "src/ReadMe.md").unlink()
    (checkout / "src/README.md").symlink_to("../README.md")
    w.git("add", ".")
    w.git("commit", "-m", "README boundary fixtures")
    w.git("push", "origin", "main")
    first = client.get(w.path + "/contents").json()
    assert not any(e["name"] == "README.md" for e in first["entries"])
    assert first["readme"]["content"] == "# Fixture\n"
    assert client.get(w.path + "/contents?page=2").json()["readme"] == first["readme"]
    assert client.get(w.path + "/contents?path=src").json()["readme"] is None
    assert client.get(w.path + "/image?path=large.png").status_code == 413
    # The README follows the chosen branch, not the current branch or previous response.
    assert client.get(w.path + "/contents?ref=feature/code&path=src").json()["readme"] is not None
    (checkout / "README.md").write_text("x" * (1024 * 1024 + 1))
    w.git("add", ".")
    w.git("commit", "-m", "Large README")
    w.git("push", "origin", "main")
    readme = client.get(w.path + "/contents").json()["readme"]
    assert readme["too_large"] and readme["content"] is None
