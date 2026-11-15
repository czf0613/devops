"""Real mark/sweep and shared multi-platform graph checks in the local test harness."""

import base64
import gzip
import hashlib
import io
import json
import secrets
import tarfile
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from devops_server.config_store import load_settings
from devops_server.models import EmailChallenge, Role, User, UserRole
from devops_server.security import code_digest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

MANIFEST = "application/vnd.oci.image.manifest.v1+json"
INDEX = "application/vnd.oci.image.index.v1+json"


def synthetic_email_code(engine, user_id, purpose="admin_operation"):
    """Seed only the local harness user's challenge; never deliver real email."""
    settings = load_settings(engine)
    code, nonce = f"{secrets.randbelow(1_000_000):06d}", secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        user = db.get(User, user_id)
        db.add(
            EmailChallenge(
                email=user.email,
                purpose=purpose,
                subject_user_id=user.id,
                nonce=nonce,
                code_digest=code_digest(settings, user.email, purpose, nonce, code),
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
    return code


def verify_registry_gc(request, write_headers, engine, storage_blob_exists):
    def web(path, method="GET", body=None):
        status, _, raw = request(
            path,
            method=method,
            headers=write_headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        assert status in {200, 201, 202, 204}, (path, status)
        return json.loads(raw) if raw else None

    repo = web("/api/repositories", "POST", {"name": "registry-gc-acceptance"})
    base = f"/api/repositories/{repo['id']}/registry"
    namespaces = {
        name: web(base + "/namespaces", "POST", {"name": name})
        for name in ("gc-deleted-keep", "gc-unbound", "gc-deleted")
    }
    token = web(
        "/api/me/registry-credentials",
        "POST",
        {"name": "gc-integration", "access": "write"},
    )
    auth = {
        "Authorization": "Basic "
        + base64.b64encode(f"{token['username']}:{token['token']}".encode()).decode()
    }
    content = {}

    def digest(data):
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def blob(name, data, media):
        dgst = digest(data)
        status, headers, _ = request(
            f"/v2/{name}/blobs/uploads/", method="POST", headers=auth, data=b""
        )
        assert status == 202
        status, _, _ = request(
            headers["Location"] + "&" + urlencode({"digest": dgst}),
            method="PUT",
            data=data,
            headers={**auth, "Content-Type": "application/octet-stream"},
        )
        assert status == 201
        content[dgst] = data
        return {"mediaType": media, "digest": dgst, "size": len(data)}

    def manifest(name, value, tag=None):
        data = json.dumps(value).encode()
        dgst = digest(data)
        status, _, _ = request(
            f"/v2/{name}/manifests/{tag or dgst}",
            method="PUT",
            data=data,
            headers={**auth, "Content-Type": value["mediaType"]},
        )
        assert status == 201
        content[dgst] = data
        return {"mediaType": value["mediaType"], "digest": dgst, "size": len(data)}

    def layer():
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            data = secrets.token_bytes(1024 * 1024)
            info = tarfile.TarInfo("gc-fixture.bin")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    shared, discard = layer(), layer()

    def platform(name, arch, raw):
        config = json.dumps(
            {
                "os": "linux",
                "architecture": arch,
                "config": {},
                "rootfs": {"type": "layers", "diff_ids": [digest(raw)]},
            }
        ).encode()
        descriptor = manifest(
            name,
            {
                "schemaVersion": 2,
                "mediaType": MANIFEST,
                "config": blob(
                    name, config, "application/vnd.oci.image.config.v1+json"
                ),
                "layers": [
                    blob(
                        name,
                        gzip.compress(raw, mtime=0),
                        "application/vnd.oci.image.layer.v1.tar+gzip",
                    )
                ],
            },
        )
        return {**descriptor, "platform": {"os": "linux", "architecture": arch}}

    def index(name, children, tag):
        return manifest(
            name, {"schemaVersion": 2, "mediaType": INDEX, "manifests": children}, tag
        )

    name = "gc-deleted-keep/app"
    amd64, arm64 = platform(name, "amd64", shared), platform(name, "arm64", shared)
    attestation = manifest(
        name,
        {
            "schemaVersion": 2,
            "mediaType": MANIFEST,
            "config": blob(
                name,
                b'{"architecture":"unknown","os":"unknown","rootfs":{"type":"layers","diff_ids":[]}}',
                "application/vnd.oci.image.config.v1+json",
            ),
            "layers": [
                blob(
                    name,
                    b'{"_type":"https://in-toto.io/Statement/v0.1","subject":[]}',
                    "application/vnd.in-toto+json",
                )
            ],
        },
    )
    proof = {
        **attestation,
        "platform": {"os": "unknown", "architecture": "unknown"},
        "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
    }
    kept = index(name, [amd64, arm64, proof], "keep")
    index(name, [amd64, arm64, proof], "alias")
    discarded = platform(name, "arm64", discard)
    deleted = index(name, [amd64, discarded], "delete")
    orphan_name = "gc-unbound/app"
    orphan = index(
        orphan_name,
        [
            platform(orphan_name, "amd64", shared),
            platform(orphan_name, "arm64", shared),
        ],
        "keep",
    )
    orphan_path = base + f"/namespaces/{namespaces['gc-unbound']['id']}"
    removed_name, exclusive = "gc-deleted/app", layer()
    removed_children = [
        platform(removed_name, "amd64", shared),
        platform(removed_name, "arm64", exclusive),
    ]
    index(removed_name, removed_children, "v1")
    index(removed_name, removed_children, "alias")
    nested_name = "gc-deleted/nested/app"
    index(nested_name, [platform(nested_name, "arm64", exclusive)], "nested")
    exclusive_digest = digest(gzip.compress(exclusive, mtime=0))
    shared_digest = digest(gzip.compress(shared, mtime=0))
    assert storage_blob_exists(exclusive_digest) and storage_blob_exists(shared_digest)
    web(orphan_path, "PUT", {"enabled": False})
    web(
        base + f"/namespaces/{namespaces['gc-deleted-keep']['id']}/manifest?image=app",
        "DELETE",
        {"digest": deleted["digest"]},
    )
    assert (
        request(
            f"/v2/{name}/manifests/{discarded['digest']}",
            headers={**auth, "Accept": MANIFEST},
        )[0]
        == 200
    )

    # Only this isolated synthetic user is temporarily an admin, then restored.
    with Session(engine) as db, db.begin():
        user_id = db.scalar(select(User.id).where(User.username == token["username"]))
        role_id = db.scalar(select(Role.id).where(Role.code == "admin"))
        db.add(UserRole(user_id=user_id, role_id=role_id))
    try:
        web(
            f"/api/admin/registry/namespaces/{namespaces['gc-deleted']['id']}",
            "DELETE",
            {
                "confirm_name": "gc-deleted",
                "expected_version": 1,
                "code": synthetic_email_code(engine, user_id),
            },
        )
        assert (
            request(
                f"/v2/{removed_name}/manifests/v1", headers={**auth, "Accept": INDEX}
            )[0]
            == 403
        )
        for _ in range(60):
            if web("/api/admin/registry/gc")["worker_available"]:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("GC worker did not become ready")
        job = web(
            "/api/admin/registry/gc",
            "POST",
            {
                "maintenance_confirmed": True,
                "code": synthetic_email_code(engine, user_id),
            },
        )
        # Maintenance window: no writes until the GC job reaches a terminal state.
        for _ in range(120):
            job = web("/api/admin/registry/gc")["items"][0]
            if job["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.5)
        assert job["state"] == "succeeded", job["error"]
        assert job["before_bytes"] - job["after_bytes"] >= 2 * 1024 * 1024
        assert job["summary"]["deleted_namespaces"] == 1
        assert job["summary"]["deleted_tags"] == 3
        assert not storage_blob_exists(exclusive_digest) and storage_blob_exists(
            shared_digest
        )
        assert web("/api/admin/registry?state=deleted")["items"][0]["purged_at"]
    finally:
        with Session(engine) as db, db.begin():
            db.execute(
                delete(UserRole).where(
                    UserRole.user_id == user_id, UserRole.role_id == role_id
                )
            )

    def verify_graph(image, descriptor):
        dgst = descriptor["digest"]
        media = descriptor["mediaType"]
        kind = "manifests" if media in {INDEX, MANIFEST} else "blobs"
        status, _, raw = request(
            f"/v2/{image}/{kind}/{dgst}", headers={**auth, "Accept": media}
        )
        assert status == 200 and raw == content[dgst]
        if kind == "manifests":
            value = json.loads(raw)
            children = (
                value["manifests"]
                if media == INDEX
                else [value["config"], *value["layers"]]
            )
            for child in children:
                verify_graph(image, child)

    for tag in ("keep", "alias"):
        assert (
            request(f"/v2/{name}/manifests/{tag}", headers={**auth, "Accept": INDEX})[0]
            == 200
        )
    verify_graph(name, kept)
    assert (
        request(
            f"/v2/{name}/manifests/{discarded['digest']}",
            headers={**auth, "Accept": MANIFEST},
        )[0]
        == 404
    )
    web(orphan_path, "PUT", {"enabled": True})
    verify_graph(orphan_name, orphan)
    web(f"/api/me/registry-credentials/{token['id']}", "DELETE")
    print(
        "PASS: manual GC job, physical reclamation, amd64/arm64 references, shared layers, "
        "alias tags, Buildx-style attestation and unbound namespace retention",
        flush=True,
    )
