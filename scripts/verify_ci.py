"""Native macOS runner against the isolated HTTPS Gateway harness; synthetic data only."""

import json
import os
import platform
import subprocess
import time
from pathlib import Path

from devops_server.models import Role, UserRole
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from verify_registry_gc import synthetic_email_code


def verify_ci(request, write_headers, engine, origin, ca_file, temp, git, owner_id):
    def web(path, method="GET", body=None):
        status, _, raw = request(
            path,
            method=method,
            headers=write_headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        assert status in {200, 201, 202, 204}, (path, status)
        return json.loads(raw) if raw else None

    with Session(engine) as db, db.begin():
        role_id = db.scalar(select(Role.id).where(Role.code == "admin"))
        db.add(UserRole(user_id=owner_id, role_id=role_id))
    process = None
    try:
        worker = web(
            "/api/admin/ci/workers",
            "POST",
            {
                "name": "Native integration worker",
                "code": synthetic_email_code(engine, owner_id),
            },
        )
        repo = web("/api/repositories", "POST", {"name": "ci-acceptance"})
        base = f"/api/repositories/{repo['id']}/ci"
        assert web(base)["enabled"] and web(base)["pools"] == ["default"]
        checkout = temp / "ci-source"
        git("clone", origin + repo["clone_path"], str(checkout))
        git("lfs", "install", "--local", cwd=checkout)
        git("lfs", "track", "*.lfsbin", cwd=checkout)
        (checkout / "fixture.lfsbin").write_text("native-ci-lfs-ok\n" * 100)
        git("add", ".gitattributes", "fixture.lfsbin", cwd=checkout)
        environment = {
            "os": {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[
                platform.system()
            ],
            "arch": "arm64"
            if platform.machine().lower() in {"arm64", "aarch64"}
            else "x64",
        }
        labels = ["self-hosted", environment["os"], environment["arch"]]
        workflow = {
            "name": "Native GitHub workflow",
            "on": ["push", "workflow_dispatch", "pull_request"],
            "jobs": {
                "build": {
                    "runs-on": labels,
                    "steps": [
                        {
                            "uses": "actions/checkout@v4",
                            "with": {"lfs": True, "fetch-depth": 0},
                        },
                        {
                            "name": "Build",
                            "run": 'test -z "${GIT_CONFIG_VALUE_0:-}"\ntest "$(head -n 1 fixture.lfsbin)" = native-ci-lfs-ok\nmkdir -p dist\necho native-ci-ok > dist/result.txt',
                        },
                        {
                            "uses": "actions/upload-artifact@v4",
                            "with": {"name": "build", "path": "dist/"},
                        },
                    ],
                },
                "consume": {
                    "runs-on": labels,
                    "needs": ["build"],
                    "steps": [
                        {
                            "uses": "actions/download-artifact@v4",
                            "with": {"name": "build", "path": "artifacts"},
                        },
                        {
                            "name": "Consume",
                            "run": 'test "$(cat artifacts/result.txt)" = native-ci-ok',
                        },
                    ],
                },
            },
        }

        def push(document, branch="main"):
            directory = checkout / ".github" / "workflows"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "ci.yml").write_text(json.dumps(document))
            git("add", ".github/workflows/ci.yml", cwd=checkout)
            git(
                "-c",
                "user.name=CI fixture",
                "-c",
                "user.email=ci@example.invalid",
                "commit",
                "-m",
                "CI integration",
                cwd=checkout,
            )
            git("push", "origin", f"HEAD:{branch}", cwd=checkout)

        push(workflow)
        runner = Path(__file__).resolve().parents[1] / "runner"
        config = temp / "ci-worker.json"
        env = {
            **os.environ,
            "SSL_CERT_FILE": str(ca_file),
            # WSS, Git/LFS and artifacts must all bypass proxies for loopback.
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
        command = [
            str(runner / ".venv/bin/python"),
            "-m",
            "devops_runner.main",
            "--config",
            str(config),
        ]
        registration = subprocess.run(
            [
                *command,
                "register",
                "--server",
                origin,
                "--work-dir",
                str(temp / "ci-work"),
                "--token-stdin",
            ],
            input=worker["registration_token"] + "\n",
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        assert registration.returncode == 0, "Native worker registration failed"
        output = (temp / "ci-worker.log").open("w")
        process = subprocess.Popen(
            [*command, "run"], env=env, stdout=output, stderr=output
        )

        def wait_for(path, predicate, timeout=70):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = web(path)
                if predicate(result):
                    return result
                if process.poll() is not None:
                    raise AssertionError(
                        "Native worker stopped unexpectedly; inspect isolated log"
                    )
                time.sleep(0.4)
            raise AssertionError(
                f"CI did not reach expected state: {path}; {result.get('state')}"
            )

        listing = wait_for(base + "/runs", lambda r: bool(r["items"]))
        run_id = listing["items"][0]["id"]
        finished = wait_for(
            base + f"/runs/{run_id}", lambda r: r["state"] in {"success", "failure"}
        )
        if finished["state"] != "success":
            for job in finished["jobs"]:
                for attempt in job["attempts"]:
                    print(web(base + f"/attempts/{attempt['id']}/logs"), flush=True)
        assert finished["state"] == "success", finished
        assert len(finished["jobs"]) == 2
        assert (
            web(base + "/workflows")["items"][0]["path"] == ".github/workflows/ci.yml"
        )
        assert web(base + "/checks/" + finished["sha"])["state"] == "success"
        for job in finished["jobs"]:
            for attempt in job["attempts"]:
                logs = web(base + f"/attempts/{attempt['id']}/logs")
                assert "/root/" not in "".join(
                    chunk["text"] for chunk in logs["chunks"]
                )
        caps = next(
            w for w in web("/api/admin/ci/workers")["items"] if w["id"] == worker["id"]
        )["capabilities"]
        assert caps["os_version"] and caps["kernel_version"] and caps["runtime"] == 2
        attempt = finished["jobs"][0]["attempts"][0]
        artifact = attempt["artifacts"][0]
        assert request(base + f"/artifacts/{artifact['id']}")[2] == b"native-ci-ok\n"
        assert web(base + f"/attempts/{attempt['id']}/logs")["complete"]
        manual = web(base + "/runs", "POST", {"ref": "main"})
        assert wait_for(
            base + f"/runs/{manual['id']}", lambda r: r["state"] == "success"
        )

        long = {
            "on": ["workflow_dispatch"],
            "jobs": {
                "long": {
                    "runs-on": labels,
                    "steps": [{"name": "Wait", "run": "echo waiting; sleep 120"}],
                }
            },
        }
        push(long)
        cancelled = web(base + "/runs", "POST", {"ref": "main"})
        path = base + f"/runs/{cancelled['id']}"
        wait_for(
            path, lambda r: any(a["steps"] for j in r["jobs"] for a in j["attempts"])
        )
        web(path + "/cancel", "POST")
        cancelled = wait_for(path, lambda r: r["state"] == "cancelled")
        assert cancelled["jobs"][0]["attempts"][0]["steps"][0]["state"] == "cancelled"
        interrupted = web(base + "/runs", "POST", {"ref": "main"})
        path = base + f"/runs/{interrupted['id']}"
        wait_for(
            path, lambda r: any(a["steps"] for j in r["jobs"] for a in j["attempts"])
        )
        process.kill()
        process.wait(timeout=10)
        process = subprocess.Popen(
            [*command, "run"], env=env, stdout=output, stderr=output
        )
        lost = wait_for(path, lambda r: r["state"] == "failure")
        assert (
            lost["jobs"][0]["state"] == "lost" and len(lost["jobs"][0]["attempts"]) == 1
        )
        assert lost["jobs"][0]["attempts"][0]["steps"][0]["state"] == "lost"
        print(
            "CI: native runner → WSS enrollment → push/manual → pinned Git/LFS checkout → steps/logs → artifacts/dependencies → cancel/crash recovery passed",
            flush=True,
        )
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        with Session(engine) as db, db.begin():
            db.execute(
                delete(UserRole).where(
                    UserRole.user_id == owner_id, UserRole.role_id == role_id
                )
            )
