"""Exercise the compiled CLI against the real API on a disposable local schema."""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from test_cli_oauth import cli_tokens
from test_cli_oauth import workspace as cli_workspace
from test_collaboration_pulls import commit, drain


@pytest.fixture
def workspace(system):
    return cli_workspace.__wrapped__(system)


def test_cli_real_http_issue_pr_and_scoped_auth(workspace, tmp_path):
    entry = Path(__file__).parents[2] / "cli" / "dist" / "main.js"
    if not entry.is_file():
        pytest.skip("Build devops/cli before the real HTTP CLI integration test")
    tokens = cli_tokens(workspace.people["owner"][1])
    server = uvicorn.Server(
        uvicorn.Config(
            workspace.app,
            log_level="error",
            lifespan="off",
            access_log=False,
        )
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    environment = {
        **os.environ,
        "DEVOPS_HOST": origin,
        "DEVOPS_TOKEN": tokens["access_token"],
        "DEVOPS_CONFIG_DIR": str(tmp_path / "cli"),
    }

    def cli(*args, stdin=None, expected=0):
        result = subprocess.run(
            ["node", str(entry), *args],
            env=environment,
            text=True,
            input=stdin,
            capture_output=True,
            timeout=20,
            cwd=tmp_path,
        )
        assert result.returncode == expected, result.stderr
        return result.stdout

    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started
        assert json.loads(cli("auth", "status", "--json", "login")) == {"login": "owner"}
        assert len(json.loads(cli("repo", "list", "--json", "nameWithOwner"))) == 1
        created = cli(
            "issue",
            "create",
            "-R",
            "owner/example",
            "-t",
            "HTTP issue",
            "--body-file",
            "-",
            stdin="First\nSecond, `literal`",
        )
        assert created.strip().endswith("/issue/1")
        detail = json.loads(
            cli("issue", "view", "1", "-R", "owner/example", "--json", "title,body,state")
        )
        assert detail == {
            "title": "HTTP issue",
            "body": "First\nSecond, `literal`",
            "state": "OPEN",
        }
        cli("issue", "edit", "1", "-R", "owner/example", "-t", "Edited")
        cli("issue", "comment", "1", "-R", "owner/example", "-b", "Comment from CLI")
        comments = cli("issue", "view", "1", "-R", "owner/example", "--comments")
        assert "Comment from CLI" in comments
        cli("issue", "close", "1", "-R", "owner/example", "--reason", "not planned")
        cli("issue", "reopen", "1", "-R", "owner/example")
        commit(workspace, "feature", {"cli.txt": "HTTP integration\n"})
        cli(
            "pr",
            "create",
            "-R",
            "owner/example",
            "-t",
            "HTTP PR",
            "-H",
            "feature",
            "--body",
            "Closes #1",
        )
        drain(workspace)
        pr = json.loads(
            cli(
                "pr",
                "view",
                "2",
                "-R",
                "owner/example",
                "--json",
                "headRefName,baseRefName,mergeStateStatus",
            )
        )
        assert pr == {"headRefName": "feature", "baseRefName": "main", "mergeStateStatus": "CLEAN"}
        diff = cli("pr", "diff", "2", "-R", "owner/example")
        assert "+HTTP integration" in diff
        cli("pr", "merge", "2", "-R", "owner/example", "--timeout", "1", expected=8)
        drain(workspace)
        assert (
            json.loads(cli("pr", "view", "2", "-R", "owner/example", "--json", "state"))["state"]
            == "MERGED"
        )
        assert (
            json.loads(cli("issue", "view", "1", "-R", "owner/example", "--json", "state"))["state"]
            == "CLOSED"
        )
        cli("api", "/api/users", expected=4)
        workspace.limiter.client.delete(workspace.limiter.keys("owner@example.com", "127.0.0.1")[2])
        cli("repo", "archive", "owner/example", "--request-code")
        code = workspace.mailbox.code("owner@example.com", "repository_operation")
        cli("repo", "archive", "owner/example", "--yes", "--code-stdin", stdin=code)
        assert json.loads(cli("repo", "view", "owner/example", "--json", "isArchived"))[
            "isArchived"
        ]
        cli("repo", "unarchive", "owner/example", "--yes", "--code-stdin", stdin=code, expected=1)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
