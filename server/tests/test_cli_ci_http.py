"""CI commands use real HTTP, scoped CLI OAuth and the existing worker protocol."""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from test_ci import CAPS, commit_github, message
from test_ci import workspace as ci_workspace
from test_cli_oauth import cli_tokens
from test_repositories import add

from devops_server import ci_socket


@pytest.fixture
def workspace(system):
    return ci_workspace.__wrapped__(system)


def test_cli_ci_real_http_and_permissions(workspace, tmp_path):
    w = workspace
    entry = Path(__file__).parents[2] / "cli" / "dist" / "main.js"
    if not entry.is_file():
        pytest.skip("Build devops/cli before the real HTTP CLI integration test")
    workflow = {
        "name": "CLI checks",
        "on": {"workflow_dispatch": {"inputs": {"message": {"type": "string", "required": True}}}},
        "jobs": {
            "build": {
                "runs-on": ["self-hosted", "macos", "arm64"],
                "steps": [{"name": "Build", "run": "echo hello"}],
            }
        },
    }
    sha = commit_github(w, {"checks.yml": workflow})
    _, w.generation = ci_socket.connect(
        w.engine,
        w.token,
        ci_socket.Hello(v=1, type="hello", capabilities={**CAPS, "runtime": 2}),
    )
    assert add(w, "reader").status_code == 201
    tokens = {
        "owner": cli_tokens(w.owner),
        "readonly": cli_tokens(w.owner, "api:read"),
        "reader": cli_tokens(w.people["reader"][1]),
        "admin": cli_tokens(w.admin),
    }
    server = uvicorn.Server(
        uvicorn.Config(w.app, log_level="error", lifespan="off", access_log=False)
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()

    def cli(*args, identity="owner", stdin=None, expected=0):
        result = subprocess.run(
            ["node", str(entry), *map(str, args)],
            env={
                **os.environ,
                "DEVOPS_HOST": origin,
                "DEVOPS_TOKEN": tokens[identity]["access_token"],
                "DEVOPS_REPO": "owner/example",
                "DEVOPS_CONFIG_DIR": str(tmp_path / "config"),
            },
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
        assert json.loads(cli("workflow", "list", "--json", "name,path,state")) == [
            {
                "name": "CLI checks",
                "path": ".github/workflows/checks.yml",
                "state": "active",
            }
        ]
        assert json.loads(cli("workflow", "view", "checks.yml", "--yaml")) == workflow
        output = cli("workflow", "run", "checks.yml", "--json", stdin='{"message":"from CLI"}')
        run_id = int(output.strip().rsplit("/", 1)[1])
        queued = json.loads(cli("run", "view", run_id, "--json", "status,conclusion,headSha"))
        assert queued == {"status": "queued", "conclusion": "", "headSha": sha}
        offer = next(r for r in message(w) if r["type"] == "job.offer")
        attempt_id = offer["attempt_id"]
        payload = next(
            r for r in message(w, "job.accept", attempt_id=attempt_id) if r["type"] == "job.start"
        )
        message(w, "job.started", attempt_id=attempt_id)
        message(w, "job.log", attempt_id=attempt_id, seq=1, text="CLI HTTP log\n")
        upload = w.owner.put(
            f"/api/ci/artifacts/{attempt_id}",
            params={"name": "build/result.bin"},
            content=b"\x00CLI artifact\xff",
            headers={"Authorization": "Bearer " + payload["token"]},
        )
        assert upload.status_code == 200
        message(w, "job.step", attempt_id=attempt_id, index=0, state="success", exit_code=0)
        message(w, "job.complete", attempt_id=attempt_id, seq=1, state="success")
        assert cli("run", "view", run_id, "--log", identity="readonly") == "CLI HTTP log\n"
        cli("run", "watch", run_id, "--exit-status", identity="reader")
        cli("run", "download", run_id, "-n", "build", "-D", "artifacts", identity="readonly")
        assert (tmp_path / "artifacts/result.bin").read_bytes() == b"\x00CLI artifact\xff"
        listed = json.loads(
            cli(
                "run",
                "list",
                "-w",
                "checks.yml",
                "-s",
                "success",
                "--json",
                "databaseId,event,headSha",
                identity="readonly",
            )
        )
        assert listed == [{"databaseId": run_id, "event": "workflow_dispatch", "headSha": sha}]
        rerun = int(cli("run", "rerun", run_id).strip().rsplit("/", 1)[1])
        assert rerun != run_id
        # Both missing OAuth scope and insufficient repository ACL return HTTP 403 / exit 1.
        for identity, expected in (("readonly", 1), ("reader", 1)):
            cli(
                "workflow",
                "run",
                "checks.yml",
                "-f",
                "message=denied",
                identity=identity,
                expected=expected,
            )
            cli("run", "cancel", rerun, identity=identity, expected=expected)
            cli("run", "rerun", run_id, identity=identity, expected=expected)
        cli("workflow", "list", identity="admin", expected=1)
        cli("run", "view", run_id, identity="admin", expected=1)
        assert w.owner.get(w.path + "/ci/runs").json()["total"] == 2
        cli("run", "cancel", rerun)
        cli("run", "watch", rerun, "--exit-status", expected=1)
        assert json.loads(cli("run", "view", rerun, "--json", "conclusion")) == {
            "conclusion": "cancelled"
        }
        assert (
            w.owner.post(
                "/oauth/revoke",
                data={"client_id": "devops-cli", "token": tokens["readonly"]["access_token"]},
            ).status_code
            == 200
        )
        cli("run", "view", run_id, identity="readonly", expected=4)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
