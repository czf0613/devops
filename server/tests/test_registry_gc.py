import fcntl
import sys

# ruff: noqa: F811
from datetime import timedelta
from unittest.mock import Mock

import pytest
from conftest import admin_code, mutate
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_registry import check, credential, registry  # noqa: F401
from test_repositories import workspace  # noqa: F401

from devops_server import registry_gc_service as service
from devops_server import registry_gc_worker as worker
from devops_server.models import AuditEvent, UserSession
from devops_server.registry_models import RegistryGCRun, RegistryGCWorker, RegistryNamespace

PATH = "/api/admin/registry/gc"
CONFIRM = {"maintenance_confirmed": True, "code": "000000"}


def ready(system):
    with Session(system.engine) as db, db.begin():
        service.heartbeat(db)


def enqueue(system):
    ready(system)
    admin = system.people["outsider"][1]
    result = mutate(admin, "POST", PATH, {**CONFIRM, "code": admin_code(system, admin)})
    assert result.status_code == 202, result.text
    return result.json()["id"]


def test_admin_confirmation_csrf_availability_and_single_active_run(registry):
    admin, owner = registry.people["outsider"][1], registry.people["owner"][1]
    assert owner.get(PATH).status_code == 403
    assert mutate(owner, "POST", PATH, CONFIRM).status_code == 403
    assert admin.post(PATH, json=CONFIRM).status_code == 403
    assert mutate(admin, "POST", PATH, {}).status_code == 422
    assert mutate(admin, "POST", PATH, {"maintenance_confirmed": False}).status_code == 422
    assert mutate(admin, "POST", PATH, CONFIRM).status_code == 503
    run_id = enqueue(registry)
    assert mutate(admin, "POST", PATH, CONFIRM).status_code == 409
    listed = admin.get(PATH).json()
    assert listed["worker_available"] and listed["active"]["id"] == run_id
    assert listed["total"] == 1 and "session_id" not in listed["items"][0]
    # This release relies on the operator's maintenance window, not an auth write gate.
    assert check(registry, credential(registry), "PUT").status_code == 204
    with Session(registry.engine) as db:
        run = db.get(RegistryGCRun, run_id)
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "registry.gc_requested"))
        assert event.details["maintenance_confirmed"] is True
        db.add(RegistryGCRun(actor_id=run.actor_id, session_id=run.session_id))
        with pytest.raises(IntegrityError):
            db.flush()


@pytest.fixture
def executor(tmp_path, monkeypatch):
    sizes = iter([8192, 4096])
    monkeypatch.setattr(worker, "allocated_bytes", lambda *_: next(sizes))
    collect = Mock(return_value={"marked_blobs": 6, "eligible_blobs": 2, "eligible_manifests": 1})
    monkeypatch.setattr(worker, "collect", collect)
    return tmp_path, collect


def test_worker_previews_collects_measures_and_never_repeats(registry, executor):
    root, collect = executor
    run_id = enqueue(registry)
    assert worker.run_one(registry.engine, root)
    assert [call.kwargs["dry_run"] for call in collect.call_args_list] == [True, False]
    with Session(registry.engine) as db:
        run = db.get(RegistryGCRun, run_id)
        assert (run.state, run.before_bytes, run.after_bytes) == ("succeeded", 8192, 4096)
        assert run.finished_at and not run.error
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "registry.gc_completed"))
    assert not worker.run_one(registry.engine, root)
    assert collect.call_count == 2


@pytest.mark.parametrize("stage", ["preview", "collect"])
def test_worker_failures_are_recorded_without_automatic_retry(registry, executor, stage):
    root, collect = executor
    run_id = enqueue(registry)
    failure = worker.GCError("synthetic private upstream output")
    collect.side_effect = failure if stage == "preview" else [{"eligible_blobs": 2}, failure]
    assert worker.run_one(registry.engine, root)
    with Session(registry.engine) as db:
        run = db.get(RegistryGCRun, run_id)
        assert run.state == "failed" and "private" not in run.error
        assert ("未执行正式回收" if stage == "preview" else "可能已回收部分数据") in run.error
    assert not worker.run_one(registry.engine, root)
    assert collect.call_count == (1 if stage == "preview" else 2)


@pytest.mark.parametrize("reason", ["expired", "revoked", "interrupted"])
def test_delayed_unauthorized_and_interrupted_jobs_do_not_run(registry, executor, reason):
    root, collect = executor
    run_id = enqueue(registry)
    with Session(registry.engine) as db, db.begin():
        run = db.get(RegistryGCRun, run_id)
        if reason == "expired":
            run.created_at = service.now() - timedelta(seconds=61)
        elif reason == "revoked":
            db.get(UserSession, run.session_id).revoked_at = service.now()
        else:
            run.state = "running"
    assert not worker.run_one(registry.engine, root)
    with Session(registry.engine) as db:
        assert db.get(RegistryGCRun, run_id).state == "failed"
    collect.assert_not_called()


def test_storage_lock_prevents_gc_during_another_run_or_deployment(registry, executor):
    root, collect = executor
    run_id = enqueue(registry)
    with (root / ".gc.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not worker.run_one(registry.engine, root)
    with Session(registry.engine) as db:
        assert db.get(RegistryGCRun, run_id).state == "queued"
    collect.assert_not_called()


def test_command_streams_only_summaries_and_releases_child_on_heartbeat_failure(tmp_path):
    with (tmp_path / ".gc.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lines = []
        worker.command(
            [sys.executable, "-c", "print('bounded test output')"],
            lambda: None,
            lock.fileno(),
            lines.append,
        )
        assert lines == ["bounded test output"]
        tick = Mock(side_effect=RuntimeError("DB unavailable"))
        with pytest.raises(RuntimeError, match="DB unavailable"):
            worker.command(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                tick,
                lock.fileno(),
                lines.append,
            )
    # A leaked child inheriting the lock would prevent acquiring it here.
    with (tmp_path / ".gc.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_stale_worker_is_not_available(registry):
    ready(registry)
    with Session(registry.engine) as db, db.begin():
        db.get(RegistryGCWorker, 1).last_seen_at = service.now() - timedelta(seconds=31)
    assert not registry.people["outsider"][1].get(PATH).json()["worker_available"]
    assert mutate(registry.people["outsider"][1], "POST", PATH, CONFIRM).status_code == 503


def test_ready_gc_requires_email_verification_before_queueing(registry):
    ready(registry)
    admin = registry.people["outsider"][1]
    assert mutate(admin, "POST", PATH, {"maintenance_confirmed": True}).status_code == 422
    assert mutate(admin, "POST", PATH, CONFIRM).status_code == 400
    assert admin.get(PATH).json()["total"] == 0


@pytest.fixture
def deleted_namespace(registry, monkeypatch):
    admin = registry.people["outsider"][1]
    response = mutate(
        admin,
        "DELETE",
        f"/api/admin/registry/namespaces/{registry.namespace['id']}",
        {
            "confirm_name": "example",
            "expected_version": 1,
            "code": admin_code(registry, admin),
        },
    )
    assert response.status_code == 200
    image_tags = {
        "example/app": ["v1", "alias"],
        "example/nested/app": ["v2"],
        "example-other/keep": ["latest"],
    }
    client = Mock()
    client.pages.side_effect = lambda *_: sorted(image_tags)
    client.tags.side_effect = lambda name: list(image_tags[name])
    client.delete_tag.side_effect = lambda name, tag: image_tags[name].remove(tag)
    monkeypatch.setattr(worker, "load_settings", lambda *_: registry.settings)
    monkeypatch.setattr(worker, "RegistryClient", lambda *args, **kwargs: client)
    return registry, client, image_tags


def test_gc_cleans_all_deleted_namespace_tags_but_not_neighbors(deleted_namespace, executor):
    registry, upstream, image_tags = deleted_namespace
    root, collect = executor
    run_id = enqueue(registry)
    assert worker.run_one(registry.engine, root)
    assert image_tags == {
        "example/app": [],
        "example/nested/app": [],
        "example-other/keep": ["latest"],
    }
    assert upstream.delete_tag.call_count == 3
    assert collect.call_count == 2
    with Session(registry.engine) as db:
        assert db.get(RegistryNamespace, registry.namespace["id"]).purged_at is not None
        run = db.get(RegistryGCRun, run_id)
        assert run.state == "succeeded"
        assert run.summary["deleted_namespaces"] == 1 and run.summary["deleted_tags"] == 3


def test_namespace_purge_failure_remains_pending_and_requires_explicit_gc_retry(
    deleted_namespace, executor, monkeypatch
):
    registry, upstream, image_tags = deleted_namespace
    root, collect = executor
    monkeypatch.setattr(worker, "allocated_bytes", lambda *_: 4096)
    upstream.delete_tag.side_effect = HTTPException(503, "synthetic storage unavailable")
    run_id = enqueue(registry)
    assert worker.run_one(registry.engine, root)
    collect.assert_not_called()
    with Session(registry.engine) as db:
        assert db.get(RegistryNamespace, registry.namespace["id"]).purged_at is None
        assert db.get(RegistryGCRun, run_id).state == "failed"
    assert not worker.run_one(registry.engine, root)
    upstream.delete_tag.side_effect = lambda name, tag: image_tags[name].remove(tag)
    retry_id = enqueue(registry)
    assert worker.run_one(registry.engine, root)
    with Session(registry.engine) as db:
        assert db.get(RegistryNamespace, registry.namespace["id"]).purged_at is not None
        assert db.get(RegistryGCRun, retry_id).state == "succeeded"


def test_new_namespace_image_during_cleanup_aborts_before_gc(deleted_namespace, executor):
    registry, upstream, image_tags = deleted_namespace
    root, collect = executor

    def concurrent_write(name, tag):
        image_tags[name].remove(tag)
        image_tags["example/new-image"] = ["late"]

    upstream.delete_tag.side_effect = concurrent_write
    run_id = enqueue(registry)
    assert worker.run_one(registry.engine, root)
    collect.assert_not_called()
    with Session(registry.engine) as db:
        assert db.get(RegistryNamespace, registry.namespace["id"]).purged_at is None
        assert db.get(RegistryGCRun, run_id).state == "failed"
