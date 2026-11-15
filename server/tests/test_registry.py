import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import admin_code, mutate
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_repositories import add, workspace  # noqa: F401

from devops_server.models import AuditEvent, Repository, RepositoryMember, User
from devops_server.registry import protocol_operations
from devops_server.registry_client import RegistryClient
from devops_server.registry_models import RegistryCredential, RegistryNamespace

DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def registry(workspace):  # noqa: F811
    workspace.settings.registry_enabled = True
    owner = workspace.people["owner"][1]
    result = mutate(owner, "POST", workspace.path + "/registry/namespaces", {"name": "example"})
    assert result.status_code == 201, result.text
    workspace.namespace = result.json()
    workspace.registry_path = workspace.path + f"/registry/namespaces/{result.json()['id']}"
    return workspace


def credential(system, who="owner", **values):
    result = mutate(
        system.people[who][1],
        "POST",
        "/api/me/registry-credentials",
        {
            "name": "isolated-test",
            "access": "write",
            **values,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def check(system, value, method="GET", uri="/v2/example/app/manifests/latest", who="owner"):
    auth = base64.b64encode(f"{who}:{value['token']}".encode()).decode()
    return system.client().get(
        "/_internal/registry/authorize",
        headers={
            "Authorization": "Basic " + auth,
            "X-Original-Method": method,
            "X-Original-URI": uri,
        },
    )


def test_user_token_is_one_time_hashed_expiring_and_revocable(registry):
    owner = registry.people["owner"][1]
    value = credential(registry)
    listed = owner.get("/api/me/registry-credentials").json()
    assert value["token"] not in str(listed) and "token_digest" not in str(listed)
    with Session(registry.engine) as db:
        stored = db.get(RegistryCredential, value["id"])
        assert stored.token_digest == hashlib.sha256(value["token"].encode()).hexdigest()
    assert check(registry, value, uri="/v2/").status_code == 204
    assert check(registry, value).status_code == 204
    with Session(registry.engine) as db:
        db.get(RegistryCredential, value["id"]).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    assert check(registry, value).status_code == 401
    value = credential(registry)
    assert mutate(owner, "DELETE", f"/api/me/registry-credentials/{value['id']}").status_code == 204
    assert check(registry, value).status_code == 401
    assert (
        owner.get(
            "/_internal/registry/authorize",
            headers={
                "X-Original-Method": "GET",
                "X-Original-URI": "/v2/",
            },
        ).status_code
        == 401
    )  # A website cookie cannot authenticate docker.
    assert owner.post("/api/me/registry-credentials", json={"name": "no-csrf"}).status_code == 403


@pytest.mark.parametrize("expiry", [{}, {"days": None, "acknowledge_permanent": True}])
def test_acl_changes_take_effect_on_same_token_and_admin_has_no_bypass(registry, expiry):
    assert add(registry, "manager", access="read", is_admin=True).status_code == 201
    value = credential(registry, "manager", **expiry)
    assert check(registry, value, who="manager").status_code == 204
    assert check(registry, value, "PUT", who="manager").status_code == 403
    path = registry.path + f"/members/{registry.people['manager'][0]}"
    owner = registry.people["owner"][1]
    assert mutate(owner, "PATCH", path, {"access": "write"}).status_code == 200
    assert check(registry, value, "PUT", who="manager").status_code == 204
    assert (
        check(
            registry, value, "DELETE", f"/v2/example/app/manifests/{DIGEST}", "manager"
        ).status_code
        == 403
    )
    assert mutate(owner, "PATCH", path, {"access": "read"}).status_code == 200
    assert check(registry, value, "PUT", who="manager").status_code == 403
    assert mutate(owner, "DELETE", path).status_code == 200
    assert check(registry, value, who="manager").status_code == 403
    assert check(registry, credential(registry, "outsider"), who="outsider").status_code == 403


@pytest.mark.parametrize("expiry", [{}, {"days": None, "acknowledge_permanent": True}])
def test_credential_read_cap_user_disabled_and_repository_archive_delete(registry, expiry):
    value = credential(registry, access="read", **expiry)
    assert check(registry, value, "PUT").status_code == 403
    value = credential(registry, **expiry)
    with Session(registry.engine) as db:
        db.get(Repository, registry.repo["id"]).archived = True
        db.commit()
    assert check(registry, value).status_code == 204
    assert check(registry, value, "PUT").status_code == 403
    assert (
        mutate(
            registry.people["owner"][1], "PUT", registry.registry_path, {"enabled": False}
        ).status_code
        == 403
    )
    with Session(registry.engine) as db:
        db.get(Repository, registry.repo["id"]).deleted_at = datetime.now(UTC)
        db.commit()
    assert check(registry, value).status_code == 403
    with Session(registry.engine) as db:
        db.get(User, registry.people["owner"][0]).status = "disabled"
        db.commit()
    assert check(registry, value, uri="/v2/").status_code == 401


def test_permanent_credentials_require_acknowledgement_and_remain_revocable(registry):
    owner = registry.people["owner"][1]
    for values in [
        {"days": None},
        {"days": None, "acknowledge_permanent": False},
        {"days": None, "acknowledge_permanent": "true"},
        {"days": 0},
        {"days": 366},
    ]:
        assert (
            mutate(
                owner, "POST", "/api/me/registry-credentials", {"name": "test", **values}
            ).status_code
            == 422
        )
    assert owner.get("/api/me/registry-credentials").json()["items"] == []
    value = credential(registry, days=None, acknowledge_permanent=True)
    assert value["expires_at"] is None
    with Session(registry.engine) as db, db.begin():
        stored = db.get(RegistryCredential, value["id"])
        stored.created_at = datetime(2000, 1, 1, tzinfo=UTC)
        assert stored.expires_at is None
        assert stored.token_digest == hashlib.sha256(value["token"].encode()).hexdigest()
    assert check(registry, value, uri="/v2/").status_code == 204
    assert check(registry, value, "PUT").status_code == 204
    listed = owner.get("/api/me/registry-credentials").json()["items"]
    assert listed[0]["expires_at"] is None and "token" not in listed[0]
    assert mutate(owner, "DELETE", f"/api/me/registry-credentials/{value['id']}").status_code == 204
    assert check(registry, value, uri="/v2/").status_code == 401


def test_permanent_credentials_count_toward_active_quota_and_remain_listed(registry):
    active = credential(registry, days=None, acknowledge_permanent=True)
    with Session(registry.engine) as db, db.begin():
        for i in range(150):
            db.add(
                RegistryCredential(
                    user_id=registry.people["owner"][0],
                    name="Quota test",
                    access="read",
                    token_digest=f"quota-{i}",
                    expires_at=None if i < 49 else datetime.now(UTC) - timedelta(days=1),
                )
            )
    owner = registry.people["owner"][1]
    items = owner.get("/api/me/registry-credentials").json()["items"]
    assert active["id"] in [item["id"] for item in items[:50]]
    assert all(item["expires_at"] is None for item in items[:50])
    for expiry in [{"days": 30}, {"days": None, "acknowledge_permanent": True}]:
        assert (
            mutate(
                owner, "POST", "/api/me/registry-credentials", {"name": "over quota", **expiry}
            ).status_code
            == 409
        )
    assert (
        mutate(owner, "DELETE", f"/api/me/registry-credentials/{active['id']}").status_code == 204
    )
    assert credential(registry, days=30)["expires_at"] is not None


def test_unbind_reserved_name_and_transfer_change_access_immediately(registry):
    owner = registry.people["owner"][1]
    value = credential(registry)
    assert mutate(owner, "PUT", registry.registry_path, {"enabled": False}).status_code == 200
    assert check(registry, value).status_code == 403
    assert (
        mutate(
            owner, "POST", registry.path + "/registry/namespaces", {"name": "example"}
        ).status_code
        == 409
    )
    assert mutate(owner, "PUT", registry.registry_path, {"enabled": True}).status_code == 200
    assert check(registry, value).status_code == 204
    assert add(registry, "reader").status_code == 201
    reader = credential(registry, "reader")
    new = mutate(owner, "POST", "/api/repositories", {"name": "target"}).json()
    target = f"/api/repositories/{new['id']}/registry"
    assert (
        mutate(
            registry.people["reader"][1],
            "POST",
            registry.path + "/registry/namespaces",
            {"name": "denied"},
        ).status_code
        == 403
    )
    body = {"namespace_id": registry.namespace["id"], "expected_repository_id": registry.repo["id"]}
    assert mutate(owner, "POST", target + "/bind", body).status_code == 200
    assert check(registry, reader, who="reader").status_code == 403
    assert check(registry, value).status_code == 204  # User remains the destination owner.
    assert check(registry, credential(registry)).status_code == 204
    assert mutate(owner, "POST", target + "/bind", body).status_code == 409


def test_cross_namespace_mount_requires_both_current_permissions(registry):
    owner = registry.people["owner"][1]
    new = mutate(owner, "POST", "/api/repositories", {"name": "source"}).json()
    assert (
        mutate(
            owner, "POST", f"/api/repositories/{new['id']}/registry/namespaces", {"name": "source"}
        ).status_code
        == 201
    )
    assert add(registry, "reader", access="write").status_code == 201
    value = credential(registry, "reader")
    uri = f"/v2/example/app/blobs/uploads/?mount={DIGEST}&from=source/app"
    fallback = check(registry, value, "POST", uri, "reader")
    assert fallback.status_code == 204
    assert fallback.headers["X-Registry-Upload-Only"] == "1"
    assert (
        check(registry, value, uri="/v2/source/app/blobs/" + DIGEST, who="reader").status_code
        == 403
    )
    with Session(registry.engine) as db:
        db.add(
            RepositoryMember(
                repository_id=new["id"],
                user_id=registry.people["reader"][0],
                access="read",
                role="开发人员",
            )
        )
        db.commit()
    mounted = check(registry, value, "POST", uri, "reader")
    assert mounted.status_code == 204
    assert "X-Registry-Upload-Only" not in mounted.headers
    with Session(registry.engine) as db, db.begin():
        db.scalar(
            select(RegistryNamespace).where(RegistryNamespace.name == "source")
        ).deleted_at = datetime.now(UTC)
    assert check(registry, value, "POST", uri, "reader").headers["X-Registry-Upload-Only"] == "1"
    assert (
        check(registry, value, uri="/v2/source/app/manifests/v1", who="reader").status_code == 403
    )
    # A fallback must never authorize a destination that is read-only or inaccessible.
    readonly = credential(registry, "reader", access="read")
    assert check(registry, readonly, "POST", uri, "reader").status_code == 403
    assert (
        check(
            registry, value, "POST", uri.replace("example/app", "missing/app"), "reader"
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "method,uri",
    [
        ("GET", "/v2/_catalog"),
        ("GET", "/v2/example%2Fapp/tags/list"),
        ("GET", "/v2/example/../other/tags/list"),
        ("GET", "/v2/example/app/tags/list?n=1&n=2"),
        ("DELETE", "/v2/example/app/manifests/latest"),
        ("DELETE", f"/v2/example/app/blobs/{DIGEST}"),
        ("POST", f"/v2/example/app/blobs/uploads/?mount={DIGEST}"),
        ("POST", "/v2/example/app/blobs/uploads/?from=other/app"),
        ("PUT", "/v2/example/app/manifests/latest?unknown=true"),
        ("GET", "/v2//example/app/tags/list"),
        ("GET", "/v2/example/app/tags/list#fragment"),
    ],
)
def test_protocol_ambiguous_or_unscoped_requests_fail_closed(method, uri):
    with pytest.raises(HTTPException) as error:
        protocol_operations(method, uri)
    assert error.value.status_code == 403


def test_upload_and_referrer_paths_are_supported():
    assert protocol_operations(
        "PATCH", "/v2/example/nested/app/blobs/uploads/uuid-123?_state=abc%3D"
    ) == [("example/nested/app", "write")]
    assert protocol_operations("DELETE", "/v2/example/app/blobs/uploads/uuid-123?_state=abc") == [
        ("example/app", "write")
    ]
    assert protocol_operations("GET", f"/v2/example/app/referrers/{DIGEST}") == [
        ("example/app", "read")
    ]


def test_catalog_uses_upstream_directory_order_and_last_cursor(monkeypatch):
    first = [f"team/app-{index:03}" for index in range(100)]
    second = ["team-extra/app", "team-second/nested/app"]
    calls = []

    def data(self, path):
        calls.append(parse_qs(urlsplit(path).query, keep_blank_values=True)["last"][0])
        return {"repositories": first if len(calls) == 1 else second}

    monkeypatch.setattr(RegistryClient, "data", data)
    assert (
        RegistryClient("http://registry:5000").pages("/v2/_catalog", "repositories")
        == first + second
    )
    assert calls == ["", first[-1]]


@pytest.mark.parametrize("field", ["repositories", "tags"])
def test_metadata_pagination_rejects_repeated_entries(monkeypatch, field):
    repeated = [f"item-{index:03}" for index in range(100)]
    monkeypatch.setattr(RegistryClient, "data", lambda self, path: {field: repeated})
    with pytest.raises(HTTPException) as error:
        RegistryClient("http://registry:5000").pages("/v2/_catalog", field)
    assert error.value.status_code == 502


def test_metadata_and_manifest_delete_use_same_live_acl(registry, monkeypatch):
    calls = []
    monkeypatch.setattr(RegistryClient, "images", lambda self, ns: [ns + "/app"])
    monkeypatch.setattr(RegistryClient, "tags", lambda self, name: ["latest", "v1"])
    monkeypatch.setattr(
        RegistryClient,
        "manifest",
        lambda self, name, tag: {"digest": DIGEST, "size_bytes": 42, "platforms": []},
    )
    monkeypatch.setattr(
        RegistryClient, "delete", lambda self, name, digest: calls.append((name, digest))
    )
    assert add(registry, "reader", access="write").status_code == 201
    reader, owner = registry.people["reader"][1], registry.people["owner"][1]
    assert reader.get(registry.registry_path + "/images").json()["items"][0]["name"] == "app"
    assert reader.get(registry.registry_path + "/tags?image=app").json()["total"] == 2
    path = registry.registry_path + "/manifest?image=app"
    assert mutate(reader, "DELETE", path, {"digest": DIGEST}).status_code == 403
    assert not calls
    assert mutate(owner, "DELETE", path, {"digest": DIGEST}).status_code == 204
    assert calls == [("example/app", DIGEST)]
    assert reader.get(registry.registry_path + "/tags?image=../other/app").status_code == 422
    assert mutate(owner, "PUT", registry.registry_path, {"enabled": False}).status_code == 200
    assert reader.get(registry.registry_path + "/images").status_code == 403
    assert not registry.people["outsider"][1].get(registry.path + "/registry").status_code == 200


def test_disabled_registry_cannot_create_or_authenticate(registry):
    value = credential(registry)
    registry.settings.registry_enabled = False
    assert check(registry, value).status_code == 403
    assert (
        mutate(
            registry.people["owner"][1], "POST", "/api/me/registry-credentials", {"name": "test"}
        ).status_code
        == 403
    )


def test_expired_history_never_hides_active_credentials_from_revocation(registry):
    active = credential(registry)
    with Session(registry.engine) as db:
        db.add_all(
            [
                RegistryCredential(
                    user_id=registry.people["owner"][0],
                    name="Expired test",
                    access="read",
                    token_digest=f"expired-{i}",
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
                for i in range(101)
            ]
        )
        db.commit()
    items = registry.people["owner"][1].get("/api/me/registry-credentials").json()["items"]
    assert items[0]["id"] == active["id"] and len(items) == 100


def test_one_user_token_follows_membership_across_projects_without_relogin(registry):
    owner = registry.people["owner"][1]
    new = mutate(owner, "POST", "/api/repositories", {"name": "second-project"}).json()
    path = f"/api/repositories/{new['id']}"
    assert (
        mutate(owner, "POST", path + "/registry/namespaces", {"name": "second"}).status_code == 201
    )
    assert add(registry, "reader", access="write").status_code == 201
    value = credential(registry, "reader")
    assert "repository_id" not in value
    assert check(registry, value, "PUT", who="reader").status_code == 204
    uri = "/v2/second/app/manifests/latest"
    assert check(registry, value, "GET", uri, "reader").status_code == 403
    user_id = registry.people["reader"][0]
    assert (
        mutate(
            owner, "POST", path + "/members", {"user_id": user_id, "access": "write"}
        ).status_code
        == 201
    )
    assert check(registry, value, "PUT", uri, "reader").status_code == 204
    assert mutate(owner, "DELETE", path + f"/members/{user_id}").status_code == 200
    assert check(registry, value, "GET", uri, "reader").status_code == 403
    assert check(registry, value, "PUT", who="reader").status_code == 204
    assert (
        mutate(
            owner,
            "POST",
            "/api/me/registry-credentials",
            {"name": "obsolete scope", "repository_id": registry.repo["id"]},
        ).status_code
        == 422
    )


def test_global_management_rebinds_unbound_namespace_with_audit_and_live_acl(registry):
    owner, admin, manager = (registry.people[who][1] for who in ("owner", "outsider", "manager"))
    target = mutate(manager, "POST", "/api/repositories", {"name": "destination"}).json()
    path = f"/api/admin/registry/namespaces/{registry.namespace['id']}/binding"
    owner_token, manager_token, admin_token = (
        credential(registry),
        credential(registry, "manager"),
        credential(registry, "outsider"),
    )
    body = {"repository_id": None, "expected_version": 1, "code": admin_code(registry, admin)}
    assert owner.get("/api/admin/registry").status_code == 403
    assert owner.get("/api/admin/registry/repositories").status_code == 403
    assert mutate(owner, "PUT", path, body).status_code == 403
    assert admin.put(path, json=body).status_code == 403  # CSRF still required.
    assert admin.get(registry.path + "/registry").status_code == 404
    assert check(registry, admin_token, who="outsider").status_code == 403
    assert mutate(admin, "PUT", path, body).status_code == 200
    assert check(registry, owner_token).status_code == 403
    listed = admin.get("/api/admin/registry?state=unbound&q=example").json()
    assert listed["total"] == 1 and listed["items"][0]["bound"] is False
    assert listed["items"][0]["repository"]["full_name"] == "owner/example"
    assert admin.get("/api/admin/registry?state=bound").json()["total"] == 0
    result = mutate(
        admin,
        "PUT",
        path,
        {"repository_id": target["id"], "expected_version": 2, "code": admin_code(registry, admin)},
    )
    assert result.status_code == 200
    assert check(registry, manager_token, "PUT", who="manager").status_code == 204
    assert check(registry, owner_token).status_code == 403
    assert check(registry, admin_token, who="outsider").status_code == 403
    assert mutate(owner, "PUT", registry.registry_path, {"enabled": True}).status_code == 404
    assert mutate(admin, "PUT", path, body).status_code == 409
    choices = admin.get("/api/admin/registry/repositories?q=manager/destination").json()
    assert choices["items"] == [{"id": target["id"], "full_name": "manager/destination"}]
    with Session(registry.engine) as db:
        events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(["registry.admin_bound", "registry.admin_unbound"])
            )
        ).all()
        assert len(events) == 2 and all(
            event.actor_user_id == registry.people["outsider"][0] for event in events
        )


def test_admin_recovers_deleted_repo_namespace_and_rejects_stale_or_invalid_binding(registry):
    owner, admin = registry.people["owner"][1], registry.people["outsider"][1]
    target = mutate(owner, "POST", "/api/repositories", {"name": "recovered"}).json()
    path = f"/api/admin/registry/namespaces/{registry.namespace['id']}/binding"
    assert (
        mutate(
            owner, "PUT", registry.registry_path, {"enabled": False, "expected_version": 1}
        ).status_code
        == 200
    )
    assert (
        mutate(
            owner, "PUT", registry.registry_path, {"enabled": True, "expected_version": 2}
        ).status_code
        == 200
    )
    assert (
        mutate(
            admin,
            "PUT",
            path,
            {"repository_id": target["id"], "expected_version": 1, "code": "000000"},
        ).status_code
        == 409
    )
    assert (
        mutate(
            owner, "PUT", registry.registry_path, {"enabled": False, "expected_version": 1}
        ).status_code
        == 409
    )
    with Session(registry.engine) as db:
        db.get(Repository, registry.repo["id"]).deleted_at = datetime.now(UTC)
        db.get(Repository, target["id"]).archived = True
        db.commit()
    data = admin.get("/api/admin/registry?state=unbound").json()
    assert data["items"][0]["repository"]["deleted"] is True
    assert not admin.get("/api/admin/registry/repositories").json()["items"]
    assert (
        mutate(
            admin,
            "PUT",
            path,
            {
                "repository_id": target["id"],
                "expected_version": 3,
                "code": admin_code(registry, admin),
            },
        ).status_code
        == 409
    )
    with Session(registry.engine) as db:
        db.get(Repository, target["id"]).archived = False
        db.commit()
    assert (
        mutate(
            admin,
            "PUT",
            path,
            {
                "repository_id": target["id"],
                "expected_version": 3,
                "code": admin_code(registry, admin),
            },
        ).status_code
        == 200
    )
    assert check(registry, credential(registry)).status_code == 204


def test_multi_platform_metadata_separates_build_attestations(monkeypatch):
    raw = json.dumps(
        {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"platform": {"os": "linux", "architecture": "amd64"}},
                {"platform": {"os": "linux", "architecture": "arm64"}},
                *[
                    {
                        "platform": {"os": "unknown", "architecture": "unknown"},
                        "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
                    }
                ]
                * 2,
            ],
        }
    ).encode()
    monkeypatch.setattr(RegistryClient, "request", lambda *args: (raw, {}))
    data = RegistryClient("http://registry:5000").manifest("example/app", "latest")
    assert data["platforms"] == ["linux/amd64", "linux/arm64"]
    assert data["attestation_count"] == 2


def test_admin_delete_namespace_verifies_then_revokes_all_images_and_reserves_name(registry):
    owner, admin = registry.people["owner"][1], registry.people["outsider"][1]
    path = f"/api/admin/registry/namespaces/{registry.namespace['id']}"
    token = credential(registry)
    body = {"confirm_name": "example", "expected_version": 1}
    assert mutate(owner, "DELETE", path, body).status_code == 403
    assert mutate(admin, "DELETE", path, body).status_code == 422
    body["code"] = admin_code(registry, admin)
    assert mutate(admin, "DELETE", path, {**body, "expected_version": 9}).status_code == 409
    assert mutate(admin, "DELETE", path, {**body, "confirm_name": "wrong"}).status_code == 409
    assert (
        mutate(
            admin,
            "DELETE",
            path,
            {**body, "code": "000000" if body["code"] != "000000" else "111111"},
        ).status_code
        == 400
    )
    assert mutate(admin, "DELETE", path, body).status_code == 200
    assert admin.get("/api/admin/registry").json()["total"] == 0
    deleted = admin.get("/api/admin/registry?state=deleted").json()["items"][0]
    assert deleted["deleted_at"] and deleted["purged_at"] is None
    assert admin.get("/api/admin/registry/gc").json()["pending_namespaces"] == 1
    assert owner.get(registry.path + "/registry").json()["items"] == []
    assert mutate(owner, "PUT", registry.registry_path, {"enabled": True}).status_code == 404
    assert check(registry, token).status_code == 403
    assert (
        mutate(
            admin,
            "PUT",
            path + "/binding",
            {"repository_id": registry.repo["id"], "expected_version": 3, "code": body["code"]},
        ).status_code
        == 404
    )
    assert (
        mutate(
            owner, "POST", registry.path + "/registry/namespaces", {"name": "example"}
        ).status_code
        == 409
    )
    with Session(registry.engine) as db:
        namespace = db.get(RegistryNamespace, registry.namespace["id"])
        assert namespace.deleted_at is not None and not namespace.enabled
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "registry.admin_deleted"))


def test_namespace_deletion_revokes_access_even_when_storage_is_offline(registry, monkeypatch):
    owner, admin = registry.people["owner"][1], registry.people["outsider"][1]
    assert mutate(owner, "PUT", registry.registry_path, {"enabled": False}).status_code == 200

    def failure(*_):
        raise HTTPException(503, "镜像仓库暂时不可用")

    monkeypatch.setattr(RegistryClient, "images", failure)
    result = mutate(
        admin,
        "DELETE",
        f"/api/admin/registry/namespaces/{registry.namespace['id']}",
        {
            "confirm_name": "example",
            "expected_version": 2,
            "code": admin_code(registry, admin),
        },
    )
    assert result.status_code == 200
    with Session(registry.engine) as db:
        namespace = db.get(RegistryNamespace, registry.namespace["id"])
        assert namespace.deleted_at is not None and namespace.purged_at is None


def test_personal_namespace_catalog_filters_acl_before_pagination_and_tracks_changes(registry):
    owner, reader, admin = (registry.people[name][1] for name in ["owner", "reader", "outsider"])
    path = "/api/me/registry/namespaces"
    assert registry.client().get(path).status_code == 401
    assert admin.get(path).json()["total"] == 0
    assert reader.get(path).json()["total"] == 0
    assert add(registry, "reader").status_code == 201
    with Session(registry.engine) as db, db.begin():
        for index in range(22):
            db.add(
                RegistryNamespace(
                    name=f"ns-{index:02}", repository_id=registry.repo["id"], enabled=index != 0
                )
            )
    first = reader.get(path).json()
    assert first["total"] == 23 and len(first["items"]) == 20
    assert all(not ns["repository"]["permissions"]["is_owner"] for ns in first["items"])
    assert len(reader.get(path + "?page=2").json()["items"]) == 3
    assert reader.get(path + "?state=unbound").json()["total"] == 1
    assert reader.get(path + "?q=owner/example").json()["total"] == 23
    assert reader.get(path + "?q=example").json()["total"] == 23
    assert reader.get(path + "?q=ns-").json()["total"] == 22
    assert reader.get("/api/me/registry/repositories").json()["total"] == 0
    assert admin.get("/api/me/registry/repositories").json()["total"] == 0
    assert (
        owner.get("/api/me/registry/repositories").json()["items"][0]["id"] == registry.repo["id"]
    )
    with Session(registry.engine) as db, db.begin():
        db.get(Repository, registry.repo["id"]).archived = True
        db.get(RegistryNamespace, registry.namespace["id"]).deleted_at = datetime.now(UTC)
    assert owner.get("/api/me/registry/repositories").json()["total"] == 0
    listed = reader.get(path).json()
    assert listed["total"] == 22
    assert all(ns["repository"]["permissions"]["access"] == "read" for ns in listed["items"])
    assert (
        mutate(
            owner, "DELETE", registry.path + f"/members/{registry.people['reader'][0]}"
        ).status_code
        == 200
    )
    assert reader.get(path).json()["total"] == 0
    with Session(registry.engine) as db, db.begin():
        db.get(Repository, registry.repo["id"]).deleted_at = datetime.now(UTC)
    assert owner.get(path).json()["total"] == 0
