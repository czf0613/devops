import base64
import hashlib
from pathlib import Path

import pytest
from conftest import lifecycle_mutate, mutate
from sqlalchemy.orm import Session
from test_git_oauth import issue
from test_repositories import add
from test_repositories import workspace as workspace

from devops_server.git_lfs import MAX_OBJECT_BYTES, MEDIA_TYPE
from devops_server.models import Repository, User


@pytest.fixture
def lfs(workspace):
    w = workspace
    w.owner = w.people["owner"][1]
    w.tokens, _ = issue(w.owner)
    w.headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"oauth2:{w.tokens['access_token']}".encode()).decode(),
        "Content-Type": MEDIA_TYPE,
    }
    w.lfs = "/git/owner/example.git/info/lfs"
    w.data = b"LFS fixture\0" * 4096
    w.oid = hashlib.sha256(w.data).hexdigest()
    w.spec = {"oid": w.oid, "size": len(w.data)}
    w.object_url = f"{w.lfs}/objects/{w.oid}"
    return w


def upload(w, data=None, size=None):
    return w.owner.put(
        w.object_url,
        params={"size": len(w.data) if size is None else size},
        content=w.data if data is None else data,
        headers=w.headers,
    )


def test_batch_basic_transfer_integrity_and_browser_download(lfs):
    w = lfs
    body = {"operation": "upload", "objects": [w.spec], "ref": {"name": "refs/heads/main"}}
    batch = w.owner.post(w.lfs + "/objects/batch", json=body, headers=w.headers)
    assert batch.status_code == 200 and batch.headers["content-type"] == MEDIA_TYPE
    actions = batch.json()["objects"][0]["actions"]
    assert actions["upload"]["href"] == "http://testserver" + w.object_url + f"?size={len(w.data)}"
    assert w.tokens["access_token"] not in batch.text
    assert upload(w).status_code == 200
    assert w.owner.get(w.object_url, headers=w.headers).content == w.data
    assert w.owner.get(w.path + f"/lfs/{w.oid}").content == w.data
    verify = w.owner.post(w.object_url + "/verify", json=w.spec, headers=w.headers)
    assert verify.status_code == 200
    repeated = w.owner.post(w.lfs + "/objects/batch", json=body, headers=w.headers)
    assert "actions" not in repeated.json()["objects"][0]
    downloaded = w.owner.post(
        w.lfs + "/objects/batch", json={**body, "operation": "download"}, headers=w.headers
    )
    assert downloaded.json()["objects"][0]["actions"]["download"]["href"].endswith(w.oid)
    assert w.owner.post(w.lfs + "/locks/verify", json={}, headers=w.headers).status_code == 501


def test_rejects_mismatch_oversize_and_never_publishes_partial_objects(lfs):
    w = lfs
    for data, size in [(b"wrong", len(w.data)), (b"x" * len(w.data), len(w.data)), (w.data, 1)]:
        assert upload(w, data, size).status_code == 422
    root = Path(w.settings.git_repositories_root)
    assert list(root.rglob(".upload-*")) == [] and list(root.rglob(w.oid)) == []
    for spec in [
        {"oid": "../config", "size": 1},
        {"oid": w.oid, "size": -1},
        {"oid": w.oid, "size": True},
        {"oid": w.oid, "size": MAX_OBJECT_BYTES + 1},
    ]:
        assert (
            w.owner.post(
                w.lfs + "/objects/batch",
                json={"operation": "upload", "objects": [spec]},
                headers=w.headers,
            ).status_code
            == 422
        )
    assert upload(w, size=MAX_OBJECT_BYTES + 1).status_code == 422
    assert (
        w.owner.post(w.lfs + "/objects/batch", content=b"x" * 65537, headers=w.headers).status_code
        == 413
    )
    result = w.owner.post(
        w.lfs + "/objects/batch",
        json={"operation": "download", "objects": [w.spec]},
        headers=w.headers,
    )
    assert result.status_code == 200 and result.json()["objects"][0]["error"]["code"] == 404


def test_lfs_permissions_cookie_rejection_isolation_and_live_revocation(lfs):
    w = lfs
    body = {"operation": "upload", "objects": [w.spec]}
    assert (
        w.owner.post(
            w.lfs + "/objects/batch", json=body, headers={"Content-Type": MEDIA_TYPE}
        ).status_code
        == 401
    )
    assert w.owner.put(w.object_url + f"?size={len(w.data)}", content=w.data).status_code == 401
    assert upload(w).status_code == 200
    assert add(w, "reader").status_code == 201
    reader = w.people["reader"][1]
    tokens, _ = issue(reader)
    headers = {
        **w.headers,
        "Authorization": "Basic "
        + base64.b64encode(f"oauth2:{tokens['access_token']}".encode()).decode(),
    }
    assert reader.get(w.object_url, headers=headers).content == w.data
    assert (
        reader.put(
            w.object_url, params={"size": len(w.data)}, content=w.data, headers=headers
        ).status_code
        == 403
    )
    sibling = mutate(w.owner, "POST", "/api/repositories", {"name": "sibling"}).json()
    assert (
        w.owner.get(
            w.object_url.replace("example.git", "sibling.git"), headers=w.headers
        ).status_code
        == 404
    )
    assert reader.get(f"/api/repositories/{sibling['id']}/lfs/{w.oid}").status_code == 404
    assert (
        mutate(w.owner, "DELETE", w.path + f"/members/{w.people['reader'][0]}").status_code == 200
    )
    assert reader.get(w.object_url, headers=headers).status_code == 404
    assert (
        lifecycle_mutate(w, w.owner, "PUT", w.path + "/archive", {"archived": True}).status_code
        == 200
    )
    assert upload(w).status_code == 403
    assert w.owner.get(w.object_url, headers=w.headers).content == w.data
    grants = w.owner.get("/api/me/git-authorizations").json()
    assert (
        mutate(w.owner, "DELETE", f"/api/me/git-authorizations/{grants[0]['id']}").status_code
        == 200
    )
    assert w.owner.get(w.object_url, headers=w.headers).status_code == 401


def test_transfer_delete_and_disabled_account(lfs):
    w = lfs
    assert upload(w).status_code == 200
    changed = lifecycle_mutate(
        w,
        w.owner,
        "POST",
        w.path + "/transfer",
        {"new_owner_id": w.people["manager"][0], "confirm_name": "owner/example"},
    )
    assert changed.status_code == 200
    assert w.owner.get(w.object_url, headers=w.headers).status_code == 404
    w.object_url = w.object_url.replace("/owner/", "/manager/")
    assert w.owner.get(w.object_url, headers=w.headers).content == w.data
    with Session(w.engine) as db:
        db.get(User, w.people["owner"][0]).status = "disabled"
        db.commit()
    assert w.owner.get(w.object_url, headers=w.headers).status_code == 403
    assert (
        lifecycle_mutate(
            w, w.people["manager"][1], "DELETE", w.path, {"confirm_name": "manager/example"}
        ).status_code
        == 200
    )
    assert w.people["manager"][1].get(w.path + f"/lfs/{w.oid}").status_code == 404


def test_rechecks_permission_at_end_of_stream(lfs):
    w = lfs

    def stream():
        yield w.data[:100]
        # Revoked while bytes are in flight: publication must fail after buffering.
        with Session(w.engine) as db:
            db.get(Repository, w.repo["id"]).archived = True
            db.commit()
        yield w.data[100:]

    response = w.owner.put(
        w.object_url, params={"size": len(w.data)}, content=stream(), headers=w.headers
    )
    assert response.status_code == 403
    assert list(Path(w.settings.git_repositories_root).rglob(w.oid)) == []
