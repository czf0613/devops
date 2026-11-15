"""Real Registry checks used by the isolated Gateway integration harness."""

import base64
import gzip
import hashlib
import io
import json
import tarfile
from urllib.parse import urlencode

from devops_server.models import Repository
from devops_server.registry_models import RegistryNamespace
from sqlalchemy.orm import Session


def verify_registry(request, write_headers, engine):
    def web(path, method="GET", body=None):
        return request(
            path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=write_headers,
            method=method,
        )

    status, _, raw = web("/api/repositories", "POST", {"name": "registry-acceptance"})
    assert status == 201
    repo = json.loads(raw)
    base = f"/api/repositories/{repo['id']}/registry"
    status, _, raw = web(base + "/namespaces", "POST", {"name": "acceptance"})
    assert status == 201
    ns = json.loads(raw)
    ns_path = base + f"/namespaces/{ns['id']}"
    assert (
        web(
            "/api/me/registry-credentials",
            "POST",
            {"name": "unconfirmed", "days": None},
        )[0]
        == 422
    )
    status, _, raw = web(
        "/api/me/registry-credentials",
        "POST",
        {
            "name": "integration",
            "access": "write",
            "days": None,
            "acknowledge_permanent": True,
        },
    )
    assert status == 201
    credential = json.loads(raw)
    assert credential["expires_at"] is None
    auth = {
        "Authorization": "Basic "
        + base64.b64encode(
            f"{credential['username']}:{credential['token']}".encode()
        ).decode()
    }
    assert request("/v2/")[0] == 401  # Existing Cookie must not act as registry auth.
    assert request("/_internal/registry/authorize", headers=auth)[0] == 404
    assert request("/v2/", headers=auth)[0] == 200
    assert request("/v2/_catalog", headers=auth)[0] == 403
    assert request("/v2/other/app/tags/list", headers=auth)[0] == 403
    assert request("/v2/acceptance%2Fapp/tags/list", headers=auth)[0] == 403

    def upload(data):
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        status, headers, _ = request(
            "/v2/acceptance/app/blobs/uploads/", data=b"", headers=auth, method="POST"
        )
        assert status == 202
        location = headers["Location"]
        assert location.startswith("/v2/")  # No bypass URL or internal host leakage.
        status, headers, _ = request(
            location,
            data=data,
            headers={**auth, "Content-Type": "application/octet-stream"},
            method="PATCH",
        )
        assert status == 202
        status, _, _ = request(
            headers["Location"] + "&" + urlencode({"digest": digest}),
            data=b"",
            headers=auth,
            method="PUT",
        )
        assert status == 201
        assert request(f"/v2/acceptance/app/blobs/{digest}", headers=auth)[2] == data
        return digest

    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as archive:
        payload = b"isolated registry acceptance\n"
        info = tarfile.TarInfo("README.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    layer = gzip.compress(tar.getvalue())
    layer_digest = upload(layer)
    config = json.dumps(
        {
            "architecture": "arm64",
            "os": "linux",
            "config": {},
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + hashlib.sha256(tar.getvalue()).hexdigest()],
            },
        }
    ).encode()
    config_digest = upload(config)
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": layer_digest,
                    "size": len(layer),
                }
            ],
        }
    ).encode()
    manifest_headers = {
        **auth,
        "Content-Type": "application/vnd.oci.image.manifest.v1+json",
    }
    for tag in ("latest", "v1"):
        status, _, _ = request(
            f"/v2/acceptance/app/manifests/{tag}",
            data=manifest,
            headers=manifest_headers,
            method="PUT",
        )
        assert status == 201
    assert (
        request(
            "/v2/acceptance/app/manifests/latest",
            headers={**auth, "Accept": manifest_headers["Content-Type"]},
        )[2]
        == manifest
    )
    status, _, raw = web(ns_path + "/images")
    assert status == 200 and json.loads(raw)["items"][0]["name"] == "app"
    status, _, raw = web(ns_path + "/tags?image=app")
    assert status == 200
    tags = json.loads(raw)["items"]
    assert {tag["tag"] for tag in tags} == {"latest", "v1"}
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    assert {tag["digest"] for tag in tags} == {digest}
    # The same login immediately loses access on unbind and regains it on rebind.
    assert web(ns_path, "PUT", {"enabled": False})[0] == 200
    assert request("/v2/acceptance/app/blobs/" + layer_digest, headers=auth)[0] == 403
    assert web(ns_path, "PUT", {"enabled": True})[0] == 200
    assert request("/v2/acceptance/app/blobs/" + layer_digest, headers=auth)[0] == 200
    with Session(engine) as db:
        db.get(Repository, repo["id"]).archived = True
        db.commit()
    assert (
        request(
            "/v2/acceptance/app/manifests/latest",
            headers={**auth, "Accept": manifest_headers["Content-Type"]},
            method="HEAD",
        )[0]
        == 200
    )
    assert (
        request(
            "/v2/acceptance/app/manifests/denied",
            data=manifest,
            headers=manifest_headers,
            method="PUT",
        )[0]
        == 403
    )
    with Session(engine) as db:
        db.get(Repository, repo["id"]).archived = False
        db.commit()
    # An unreadable source cannot be mounted, but a writable destination can
    # start an ordinary upload, including when Buildx caches a deleted source.
    status, fallback, _ = request(
        "/v2/acceptance/clone/blobs/uploads/?"
        + urlencode({"mount": layer_digest, "from": "other/app"}),
        data=b"",
        headers=auth,
        method="POST",
    )
    assert status == 202
    assert request(f"/v2/acceptance/clone/blobs/{layer_digest}", headers=auth)[0] == 404
    assert request(fallback["Location"], headers=auth, method="DELETE")[0] == 204
    status, _, raw = web(base + "/namespaces", "POST", {"name": "mount-destination"})
    assert status == 201
    with Session(engine) as db, db.begin():
        db.get(RegistryNamespace, ns["id"]).enabled = False
    status, fallback, _ = request(
        "/v2/mount-destination/app/blobs/uploads/?"
        + urlencode({"mount": layer_digest, "from": "acceptance/app"}),
        data=b"",
        headers=auth,
        method="POST",
    )
    assert status == 202
    assert request(f"/v2/acceptance/app/blobs/{layer_digest}", headers=auth)[0] == 403
    assert (
        request(f"/v2/mount-destination/app/blobs/{layer_digest}", headers=auth)[0]
        == 404
    )
    assert (
        request(
            fallback["Location"] + "&" + urlencode({"digest": layer_digest}),
            data=layer,
            headers={**auth, "Content-Type": "application/octet-stream"},
            method="PUT",
        )[0]
        == 201
    )
    assert (
        request(f"/v2/mount-destination/app/blobs/{layer_digest}", headers=auth)[2]
        == layer
    )
    assert (
        request(
            "/v2/other/app/blobs/uploads/",
            data=b"",
            headers={**auth, "X-Registry-Upload-Only": "1"},
            method="POST",
        )[0]
        == 403
    )
    with Session(engine) as db, db.begin():
        db.get(RegistryNamespace, ns["id"]).enabled = True
    assert (
        request(
            "/v2/acceptance/clone/blobs/uploads/?"
            + urlencode({"mount": layer_digest, "from": "acceptance/app"}),
            data=b"",
            headers=auth,
            method="POST",
        )[0]
        == 201
    )
    assert web(ns_path + "/manifest?image=app", "DELETE", {"digest": digest})[0] == 204
    assert (
        request(
            "/v2/acceptance/app/manifests/latest",
            headers={**auth, "Accept": manifest_headers["Content-Type"]},
            method="HEAD",
        )[0]
        == 404
    )
    assert (
        request(
            "/v2/acceptance/app/manifests/v1",
            headers={**auth, "Accept": manifest_headers["Content-Type"]},
            method="HEAD",
        )[0]
        == 404
    )
    assert web(f"/api/me/registry-credentials/{credential['id']}", "DELETE")[0] == 204
    assert request("/v2/", headers=auth)[0] == 401
    print(
        "Registry: real OCI upload/pull, metadata, mount ACL, unbind/rebind, archive, digest deletion and immediate revocation passed",
        flush=True,
    )
