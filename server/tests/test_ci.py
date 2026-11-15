import json
import secrets
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import admin_code, mutate
from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect
from test_git_oauth import git_auth
from test_repositories import add
from test_repositories import workspace as repository_workspace

from devops_server import ci_service as ci
from devops_server import ci_socket as socket
from devops_server import collaboration_git as git
from devops_server.ci_models import CIAttempt, CIJob, CIRun
from devops_server.ci_schema import parse_workflow

WORKFLOW = {
    "version": 1,
    "on": ["manual", "push", "pull_request"],
    "jobs": {
        "build": {
            "environment": {"os": "macos", "arch": "arm64"},
            "steps": [{"name": "Build", "run": "echo hello"}],
        }
    },
}
CAPS = {"os": "macos", "arch": "arm64", "executors": ["native"], "labels": [], "version": "1.0.0"}


def commit(w, workflow=WORKFLOW, branch="main"):
    source = Path(w.settings.git_repositories_root) / f"{w.repo['id']}.git"
    raw = workflow if isinstance(workflow, str) else json.dumps(workflow)
    blob = git.text_git(source, "hash-object", "-w", "--stdin", content=raw.encode())
    child = git.text_git(source, "mktree", content=f"100644 blob {blob}\tci.yml\n".encode())
    tree = git.text_git(source, "mktree", content=f"040000 tree {child}\t.devops\n".encode())
    sha = git.commit_tree(source, tree, [git.branch_sha(source, "main")], "CI fixture")
    git.run(source, "update-ref", f"refs/heads/{branch}", sha)
    return sha


@pytest.fixture
def workspace(system):
    w = repository_workspace.__wrapped__(system)
    w.owner, w.admin = w.people["owner"][1], w.people["outsider"][1]
    response = mutate(
        w.admin,
        "POST",
        "/api/admin/ci/workers",
        {"name": "Test Mac", "pool": "default", "code": admin_code(w, w.admin)},
    )
    assert response.status_code == 201, response.text
    w.registration = response.json()["registration_token"]
    w.worker_id = response.json()["id"]
    response = mutate(w.owner, "PUT", w.path + "/ci", {"enabled": True, "pools": ["default"]})
    assert response.status_code == 200, response.text
    w.sha = commit(w)
    w.token = secrets.token_urlsafe(48)
    hello = socket.Hello(
        v=1, type="hello", capabilities=CAPS, registration_digest=ci.digest(w.token)
    )
    _, w.generation = socket.connect(w.engine, w.registration, hello)
    w.reported = None
    return w


def trigger(w):
    response = mutate(w.owner, "POST", w.path + "/ci/runs", {"ref": "main"})
    assert response.status_code == 201, response.text
    return response.json()


def message(w, kind="heartbeat", **fields):
    w.reported, replies = socket.transact(
        w.engine,
        w.settings,
        w.worker_id,
        w.generation,
        w.reported,
        socket.Message(v=1, id=secrets.token_hex(12), type=kind, **fields),
    )
    return replies


def start(w):
    run = trigger(w)
    offer = next(r for r in message(w) if r["type"] == "job.offer")
    attempt_id = offer["attempt_id"]
    payload = next(
        r for r in message(w, "job.accept", attempt_id=attempt_id) if r["type"] == "job.start"
    )
    message(w, "job.started", attempt_id=attempt_id)
    return run, payload


def complete(w, payload):
    attempt_id = payload["attempt_id"]
    message(w, "job.step", attempt_id=attempt_id, index=0, state="success", exit_code=0)
    return message(w, "job.complete", attempt_id=attempt_id, seq=0, state="success")


def test_worker_enrollment_authentication_and_generation(workspace):
    w = workspace
    hello = socket.Hello(v=1, type="hello", capabilities=CAPS)
    with pytest.raises(HTTPException):
        socket.connect(w.engine, w.registration, hello)
    _, generation = socket.connect(w.engine, w.token, hello)
    assert generation > w.generation
    with pytest.raises(HTTPException):
        message(w)
    assert "token" not in json.dumps(w.admin.get("/api/admin/ci/workers").json())
    assert w.owner.get("/api/admin/ci/workers").status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with w.owner.websocket_connect("/api/ci/worker"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with w.owner.websocket_connect(
            "/api/ci/worker",
            headers={"Authorization": "Bearer " + w.token, "Origin": "http://testserver"},
        ):
            pass
    with w.owner.websocket_connect(
        "/api/ci/worker", headers={"Authorization": "Bearer " + w.token}
    ) as ws:
        ws.send_json(hello.model_dump())
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"v": 1, "type": "heartbeat", "id": "hello"})
        assert ws.receive_json()["reply_to"] == "hello"


def test_repository_permissions_are_independent_of_admin(workspace):
    w = workspace
    assert w.admin.get(w.path + "/ci").status_code == 404
    assert mutate(w.admin, "POST", w.path + "/ci/runs", {"ref": "main"}).status_code == 404
    assert add(w, "reader").status_code == 201
    reader = w.people["reader"][1]
    assert reader.get(w.path + "/ci").status_code == 200
    assert mutate(reader, "POST", w.path + "/ci/runs", {"ref": "main"}).status_code == 403
    assert add(w, "manager", access="write", is_admin=True).status_code == 201
    manager = w.people["manager"][1]
    assert (
        mutate(manager, "PUT", w.path + "/ci", {"enabled": True, "pools": ["default"]}).status_code
        == 403
    )
    assert mutate(manager, "POST", w.path + "/ci/runs", {"ref": "main"}).status_code == 201


def test_immutable_snapshot_logs_results_and_scoped_tokens(workspace):
    w = workspace
    run, payload = start(w)
    new_sha = commit(w, {**WORKFLOW, "name": "Changed later"})
    assert payload["sha"] == w.sha != new_sha
    assert git_auth(w.owner, payload["token"], repository="owner/example").status_code == 204
    assert (
        git_auth(
            w.owner, payload["token"], repository="owner/example", operation="write"
        ).status_code
        == 403
    )
    another = mutate(w.owner, "POST", "/api/repositories", {"name": "another"}).json()
    assert another["id"] != w.repo["id"]
    assert git_auth(w.owner, payload["token"], repository="owner/another").status_code == 403
    assert (
        w.owner.get(
            "/api/repositories", headers={"Authorization": "Bearer " + payload["token"]}
        ).status_code
        == 403
    )
    a = payload["attempt_id"]
    for _ in range(2):
        message(w, "job.log", attempt_id=a, seq=1, text="hello\n")
    logs = w.owner.get(w.path + f"/ci/attempts/{a}/logs").json()
    assert logs["chunks"] == [{"seq": 1, "text": "hello\n"}]
    with pytest.raises(HTTPException):
        message(w, "job.complete", attempt_id=a, seq=1, state="success")
    message(w, "job.step", attempt_id=a, index=0, state="success", exit_code=0)
    message(w, "job.complete", attempt_id=a, seq=1, state="success")
    message(w, "job.complete", attempt_id=a, seq=1, state="success")
    assert w.owner.get(w.path + f"/ci/runs/{run['id']}").json()["state"] == "success"
    assert git_auth(w.owner, payload["token"], repository="owner/example").status_code == 401
    rerun = mutate(w.owner, "POST", w.path + f"/ci/runs/{run['id']}/rerun").json()
    assert rerun["sha"] == w.sha and rerun["rerun_of"] == run["id"]


def test_cancel_permission_changes_and_expired_lease_do_not_retry_started_work(workspace):
    w = workspace
    run, payload = start(w)
    a = payload["attempt_id"]
    message(w, "job.step", attempt_id=a, index=0, state="running")
    assert mutate(w.owner, "POST", w.path + f"/ci/runs/{run['id']}/cancel").status_code == 200
    assert git_auth(w.owner, payload["token"], repository="owner/example").status_code == 403
    assert any(r["type"] == "job.cancel" for r in message(w, attempt_id=a))
    message(w, "job.complete", attempt_id=a, seq=0, state="cancelled")
    detail = w.owner.get(w.path + f"/ci/runs/{run['id']}").json()
    assert detail["jobs"][0]["attempts"][0]["steps"][0]["state"] == "cancelled"
    message(w)
    run, payload = start(w)
    message(w, "job.step", attempt_id=payload["attempt_id"], index=0, state="running")
    with Session(w.engine) as db:
        db.get(CIAttempt, payload["attempt_id"]).lease_until = ci.now() - timedelta(seconds=1)
        db.commit()
        ci.settle(db)
        db.commit()
        assert db.get(CIRun, run["id"]).state == "failure"
        assert db.get(CIJob, payload["job_id"]).state == "lost"
        assert db.get(CIAttempt, payload["attempt_id"]).steps[0]["state"] == "lost"
    message(w)
    run, payload = start(w)
    mutate(w.owner, "PUT", w.path + "/ci", {"enabled": False, "pools": []})
    assert any(r["type"] == "job.cancel" for r in message(w, attempt_id=payload["attempt_id"]))
    complete(w, payload)
    assert w.owner.get(w.path + f"/ci/runs/{run['id']}").json()["state"] == "cancelled"


def test_artifact_transport_acl_idempotence_and_live_revocation(workspace):
    w = workspace
    run, payload = start(w)
    path = f"/api/ci/artifacts/{payload['attempt_id']}"
    headers = {"Authorization": "Bearer " + payload["token"]}
    assert w.owner.put(path, params={"name": "build.txt"}, content=b"hello").status_code == 401
    upload = w.owner.put(path, params={"name": "build.txt"}, content=b"hello", headers=headers)
    assert upload.status_code == 200, upload.text
    assert (
        w.owner.put(path, params={"name": "build.txt"}, content=b"hello", headers=headers).json()
        == upload.json()
    )
    assert (
        w.owner.put(
            path, params={"name": "build.txt"}, content=b"changed", headers=headers
        ).status_code
        == 409
    )
    assert (
        w.owner.put(path, params={"name": "../bad"}, content=b"hi", headers=headers).status_code
        == 422
    )
    a = upload.json()["id"]
    assert w.owner.get(w.path + f"/ci/artifacts/{a}").content == b"hello"
    assert w.admin.get(w.path + f"/ci/artifacts/{a}").status_code == 404
    assert w.owner.get(f"/api/ci/artifacts/{a}", headers=headers).status_code == 404
    complete(w, payload)
    assert (
        w.owner.put(path, params={"name": "late"}, content=b"hello", headers=headers).status_code
        == 401
    )


def test_push_dedup_invalid_yaml_and_pool_authorization(workspace):
    w = workspace
    event = {
        "repository_id": w.repo["id"],
        "actor_id": w.people["owner"][0],
        "ref": "refs/heads/main",
        "old": "0" * 40,
        "new": w.sha,
    }
    ci.push_event(w.engine, w.settings, event)
    ci.push_event(w.engine, w.settings, event)
    assert w.owner.get(w.path + "/ci/runs").json()["total"] == 1
    commit(w, "version: [invalid")
    assert trigger(w)["state"] == "failure"
    invalid = json.loads(json.dumps(WORKFLOW))
    invalid["jobs"]["build"]["environment"]["pool"] = "unauthorized"
    commit(w, invalid)
    assert trigger(w)["state"] == "failure"


@pytest.mark.parametrize(
    "raw",
    [
        "version: [",
        "version: 1\nversion: 1",
        "a: &a [*a]",
        "a: !foo 1",
        "[" * 30 + "1" + "]" * 30,
    ],
)
def test_yaml_rejects_ambiguous_and_unbounded_documents(raw):
    with pytest.raises(ValueError):
        parse_workflow(raw)


def test_dependency_cycle_rejected():
    document = json.loads(json.dumps(WORKFLOW))
    document["jobs"]["build"]["needs"] = ["build"]
    with pytest.raises(ValueError):
        parse_workflow(json.dumps(document))


def test_pr_checks_are_automatic_and_cannot_be_spoofed(workspace):
    w = workspace
    sha = commit(w, branch="feature")
    pr = mutate(w.owner, "POST", w.path + "/pulls", {"title": "CI PR", "head_ref": "feature"})
    assert pr.status_code == 201, pr.text
    number = pr.json()["number"]
    run = w.owner.get(w.path + "/ci/runs").json()["items"][0]
    assert run["event"] == "pull_request" and run["sha"] == sha
    response = mutate(
        w.owner,
        "PUT",
        w.path + f"/pulls/{number}/checks",
        {"name": "ci/build", "head_sha": sha, "status": "success"},
    )
    assert response.status_code == 422, response.text
    offer = next(r for r in message(w) if r["type"] == "job.offer")
    payload = next(
        r
        for r in message(w, "job.accept", attempt_id=offer["attempt_id"])
        if r["type"] == "job.start"
    )
    message(w, "job.started", attempt_id=payload["attempt_id"])
    complete(w, payload)
    detail = w.owner.get(w.path + f"/pulls/{number}").json()
    check = next(c for c in detail["checks"] if c["name"] == "ci/build")
    assert check["status"] == "success" and check["head_sha"] == sha
    assert check["external_url"].endswith(f"/ci/{run['id']}")
    newer = mutate(w.owner, "POST", w.path + f"/ci/runs/{run['id']}/rerun")
    assert newer.status_code == 201
    complete(w, payload)
    detail = w.owner.get(w.path + f"/pulls/{number}").json()
    assert next(c for c in detail["checks"] if c["name"] == "ci/build")["status"] == "queued"


def test_concurrent_dispatch_and_offer_recovery(workspace):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import select

    from devops_server.ci_models import CIWorker

    w = workspace
    run = trigger(w)
    with Session(w.engine) as db:
        second = CIWorker(
            name="Second",
            pool="default",
            token_digest=ci.digest("other"),
            capabilities=CAPS,
            generation=1,
            last_seen_at=ci.now(),
        )
        db.add(second)
        db.commit()
        second_id = second.id

    def poll(worker_id):
        return socket.transact(
            w.engine,
            w.settings,
            worker_id,
            1,
            None,
            socket.Message(v=1, id="poll", type="heartbeat"),
        )[1]

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(poll, [w.worker_id, second_id]))
    assert sum(r["type"] == "job.offer" for replies in results for r in replies) == 1
    with Session(w.engine) as db:
        attempt = db.scalar(select(CIAttempt))
        attempt.lease_until = ci.now() - timedelta(seconds=1)
        db.commit()
        ci.settle(db)
        db.commit()
        assert db.get(CIRun, run["id"]).state == "queued"
        assert attempt.state == "lost"
    assert any(r["type"] == "job.offer" for r in poll(w.worker_id))


def test_worker_revocation_and_drain(workspace):
    w = workspace
    path = f"/api/admin/ci/workers/{w.worker_id}"
    assert mutate(w.admin, "POST", path, {"action": "drain", "code": "000000"}).status_code == 400
    assert (
        mutate(
            w.admin, "POST", path, {"action": "drain", "code": admin_code(w, w.admin)}
        ).status_code
        == 200
    )
    trigger(w)
    assert not any(r["type"] == "job.offer" for r in message(w))
    assert (
        mutate(
            w.admin, "POST", path, {"action": "resume", "code": admin_code(w, w.admin)}
        ).status_code
        == 200
    )
    offer = next(r for r in message(w) if r["type"] == "job.offer")
    assert (
        mutate(
            w.admin, "POST", path, {"action": "revoke", "code": admin_code(w, w.admin)}
        ).status_code
        == 200
    )
    with pytest.raises(HTTPException):
        message(w, "job.accept", attempt_id=offer["attempt_id"])


def test_server_merge_triggers_ci_for_the_result_commit(workspace):
    from test_collaboration_pulls import drain, pull, request_merge

    w = workspace
    feature_sha = commit(w, {**WORKFLOW, "name": "Feature workflow"}, branch="feature")
    pr = pull(w)
    drain(w)
    assert request_merge(w, pr["number"], "merge").status_code == 202
    drain(w)
    source = Path(w.settings.git_repositories_root) / f"{w.repo['id']}.git"
    merged_sha = git.branch_sha(source, "main")
    assert merged_sha != feature_sha
    pushes = [r for r in w.owner.get(w.path + "/ci/runs").json()["items"] if r["event"] == "push"]
    assert len(pushes) == 1 and pushes[0]["sha"] == merged_sha


def test_unmatched_queue_does_not_starve_a_later_eligible_job(workspace):
    w = workspace
    run = trigger(w)
    with Session(w.engine) as db:
        from sqlalchemy import select

        original = db.scalar(select(CIJob).where(CIJob.run_id == run["id"]))
        blocked = {
            **original.spec,
            "environment": {**original.spec["environment"], "os": "windows"},
        }
        original.spec = blocked
        # The scheduler must filter in SQL before applying its candidate limit.
        for index in range(205):
            db.add(CIJob(run_id=run["id"], name=f"pending-{index}", spec=blocked))
        db.flush()
        ready = CIJob(
            run_id=run["id"],
            name="ready",
            spec=parse_workflow(json.dumps(WORKFLOW)).model_dump()["jobs"]["build"],
        )
        db.add(ready)
        db.commit()
        ready_id = ready.id
    offer = next(r for r in message(w) if r["type"] == "job.offer")
    with Session(w.engine) as db:
        assert db.get(CIAttempt, offer["attempt_id"]).job_id == ready_id


def commit_github(w, documents, branch="main"):
    source = Path(w.settings.git_repositories_root) / f"{w.repo['id']}.git"
    blobs = []
    for name, document in documents.items():
        raw = document if isinstance(document, str) else json.dumps(document)
        oid = git.text_git(source, "hash-object", "-w", "--stdin", content=raw.encode())
        blobs.append(f"100644 blob {oid}\t{name}\n")
    workflows = git.text_git(source, "mktree", content="".join(blobs).encode())
    github = git.text_git(
        source, "mktree", content=f"040000 tree {workflows}\tworkflows\n".encode()
    )
    tree = git.text_git(source, "mktree", content=f"040000 tree {github}\t.github\n".encode())
    sha = git.commit_tree(source, tree, [git.branch_sha(source, "main")], "GitHub CI fixture")
    git.run(source, "update-ref", f"refs/heads/{branch}", sha)
    return sha


GITHUB = {
    "name": "Checks",
    "on": ["push", "pull_request", "workflow_dispatch"],
    "jobs": {
        "build": {
            "runs-on": ["self-hosted", "macos", "arm64", "macos>=15"],
            "steps": [
                {"uses": "actions/checkout@v4"},
                {"name": "Build", "run": "echo hello"},
            ],
        },
    },
}


def test_default_pool_is_enabled_without_settings_and_respects_explicit_disable(system):
    w = repository_workspace.__wrapped__(system)
    w.owner = w.people["owner"][1]
    config = w.owner.get(w.path + "/ci").json()
    assert config["enabled"] and config["pools"] == ["default"]
    assert "default" in config["available_pools"]
    sha = commit_github(w, {"checks.yml": GITHUB})
    run = trigger(w)
    assert run["state"] == "queued" and run["sha"] == sha
    assert (
        mutate(w.owner, "PUT", w.path + "/ci", {"enabled": False, "pools": ["default"]}).status_code
        == 200
    )
    assert not w.owner.get(w.path + "/ci").json()["enabled"]
    assert mutate(w.owner, "POST", w.path + "/ci/runs", {"ref": "main"}).status_code == 403


def test_multiple_github_workflows_dedup_filter_and_commit_checks(workspace):
    w = workspace
    sha = commit_github(w, {"checks.yml": GITHUB, "second.yaml": {**GITHUB, "name": "Second"}})
    event = {
        "repository_id": w.repo["id"],
        "actor_id": w.people["owner"][0],
        "ref": "refs/heads/main",
        "old": w.sha,
        "new": sha,
    }
    ci.push_event(w.engine, w.settings, event)
    ci.push_event(w.engine, w.settings, event)
    assert w.owner.get(w.path + "/ci/runs").json()["total"] == 2
    assert (
        w.owner.get(w.path + "/ci/runs?workflow=.github/workflows/checks.yml").json()["total"] == 1
    )
    workflows = w.owner.get(w.path + "/ci/workflows").json()["items"]
    assert len(workflows) == 2 and all(row["manual"] and not row["error"] for row in workflows)
    checks = w.owner.get(w.path + "/ci/checks/" + sha).json()
    assert checks["state"] == "running" and len(checks["items"]) == 2
    assert w.admin.get(w.path + "/ci/checks/" + sha).status_code == 404
    assert w.admin.get(w.path + "/ci/workflows").status_code == 404
    assert w.owner.get(w.path + "/ci/checks/" + "f" * 40).json()["state"] == "none"
    assert not any(r["type"] == "job.offer" for r in message(w))  # Old worker cannot run v2 jobs.
    caps = {**CAPS, "runtime": 2, "os_version": "26.0"}
    _, w.generation = socket.connect(
        w.engine, w.token, socket.Hello(v=1, type="hello", capabilities=caps)
    )
    assert any(r["type"] == "job.offer" for r in message(w))


def test_pr_workflow_filters_use_target_branch_and_checks_include_workflow_identity(workspace):
    w = workspace
    workflow = {**GITHUB, "on": {"pull_request": {"branches": ["main"]}}}
    sha = commit_github(w, {"one.yml": workflow, "two.yml": workflow}, branch="feature")
    response = mutate(
        w.owner, "POST", w.path + "/pulls", {"title": "GitHub workflows", "head_ref": "feature"}
    )
    assert response.status_code == 201, response.text
    checks = w.owner.get(w.path + f"/pulls/{response.json()['number']}").json()["checks"]
    assert {c["name"] for c in checks} == {"ci/one.yml/build", "ci/two.yml/build"}
    assert all(c["head_sha"] == sha for c in checks)


def test_github_matrix_inputs_env_defaults_and_unsupported_actions():
    document = {
        "name": "Matrix",
        "on": {
            "workflow_dispatch": {"inputs": {"greeting": {"type": "string", "default": "hello"}}}
        },
        "env": {"MESSAGE": "${{ inputs.greeting }}"},
        "defaults": {"run": {"shell": "bash"}},
        "jobs": {
            "build": {
                "runs-on": ["self-hosted", "${{ matrix.os }}"],
                "strategy": {"matrix": {"os": ["macos", "linux"]}},
                "steps": [
                    {
                        "run": "echo $MESSAGE",
                        "working-directory": "src",
                        "env": {"OS": "${{ matrix.os }}"},
                    }
                ],
            },
            "verify": {"runs-on": "macos", "needs": "build", "steps": [{"run": "true"}]},
        },
    }
    workflow = parse_workflow(
        json.dumps(document), github=True, inputs={"greeting": "changed"}
    ).model_dump()
    assert workflow["jobs"]["verify"]["needs"] == ["build-1", "build-2"]
    assert workflow["jobs"]["build-1"]["environment"]["os"] == "macos"
    assert workflow["jobs"]["build-1"]["env"]["MESSAGE"] == "changed"
    assert workflow["jobs"]["build-1"]["steps"][0]["env"]["OS"] == "macos"
    assert workflow["jobs"]["build-1"]["steps"][0]["shell"] == "bash"
    assert workflow["jobs"]["build-1"]["fail_fast"] is True
    invalid = json.loads(json.dumps(GITHUB))
    invalid["jobs"]["build"]["steps"] = [{"uses": "unimplemented/action@v1"}]
    with pytest.raises(ValueError, match="uses Action"):
        parse_workflow(json.dumps(invalid), github=True)
    invalid["jobs"]["build"]["steps"] = [{"run": "true", "env": {"GITHUB_TOKEN": "forbidden"}}]
    with pytest.raises(ValueError):
        parse_workflow(json.dumps(invalid), github=True)


def test_host_versions_libc_architecture_and_unknown_facts():
    from devops_server.ci_environment import matches, runs_on

    def accepts(labels, caps):
        environment = runs_on(labels)
        return matches(caps, environment["constraints"])

    assert accepts("macos>=15", {"os_version": "26.6.2"})
    assert accepts("macos-15", {"os_version": "15.7"})
    assert not accepts("macos-15", {"os_version": "26.0"})
    assert not accepts("macos>=15", {})
    assert accepts(
        ["linux", "glibc>=2.31", "kernel>=5.15"],
        {"libc": {"name": "glibc", "version": "2.39"}, "kernel_version": "6.8.0-generic"},
    )
    assert not accepts("glibc>=2.31", {"libc": {"name": "musl", "version": "9.9"}})
    assert not accepts("glibc>=2.31", {"libc": {"name": "glibc", "version": "2.28"}})
    assert accepts(
        "windows-2022", {"os_edition": "Windows Server", "os_version": "10.0.20348.3000"}
    )
    assert not accepts("windows-2022", {"os_edition": "Windows 11", "os_version": "10.0.26100"})
    assert accepts("ubuntu-24.04", {"distribution": {"id": "ubuntu", "version": "24.04"}})
    assert not accepts("ubuntu-24.04", {"distribution": {"id": "debian", "version": "12"}})
    with pytest.raises(ValueError, match="32 位"):
        runs_on(["self-hosted", "x86"])


def test_workflow_filter_keeps_pre_upgrade_run_history(workspace):
    w = workspace
    run = trigger(w)
    with Session(w.engine) as db, db.begin():
        previous = db.get(CIRun, run["id"])
        snapshot = dict(previous.workflow)
        snapshot.pop("path")
        previous.workflow = snapshot
    response = w.owner.get(w.path + "/ci/runs?workflow=.devops/ci.yml").json()
    assert response["total"] == 1 and response["items"][0]["id"] == run["id"]
