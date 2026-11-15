import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "deploy_cli", Path(__file__).resolve().parents[2] / "scripts" / "deploy_cli.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def bundle(tmp_path, *, version="0.1.0", code="export {}", extra=None, scripts=None):
    archive = tmp_path / ("package-" + str(len(list(tmp_path.glob("*.tgz")))) + ".tgz")
    files = {
        "package/package.json": json.dumps(
            {
                "name": "@kevinc/devops-cli",
                "version": version,
                "bin": {"devops": "dist/main.js"},
                "scripts": scripts or {},
                "dependencies": {"commander": "14.0.3"},
            }
        ),
        "package/README.md": "Usage",
        "package/dist/main.js": code,
        "package/dist/sdk.js": "export {}",
        "package/dist/sdk.d.ts": "export {}",
    }
    if extra:
        files.update(extra)
    with tarfile.open(archive, "w:gz") as output:
        for name, value in files.items():
            info = tarfile.TarInfo(name)
            data = value.encode()
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    return archive


def test_install_is_immutable_idempotent_and_independent_of_website(tmp_path):
    tmp_path = tmp_path.resolve()
    archive = bundle(tmp_path)
    root = tmp_path / "site"
    result = deploy.install(root, archive, deploy.digest(archive), "0.1.0")
    target = Path(result["path"])
    assert target.read_bytes() == archive.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o644
    deploy.install(root, archive, deploy.digest(archive), "0.1.0")
    different = bundle(tmp_path, code="changed")
    with pytest.raises(ValueError, match="different bytes"):
        deploy.install(root, different, deploy.digest(different), "0.1.0")
    assert target.read_bytes() == archive.read_bytes()
    assert not (root / "current").exists()
    newer = bundle(tmp_path, version="0.2.0")
    deploy.install(root, newer, deploy.digest(newer), "0.2.0")
    assert target.exists() and (target.parent / "0.2.0.tgz").exists()


@pytest.mark.parametrize(
    "extra",
    [
        {"package/.env": "private"},
        {"../escaped": "no"},
        {"package/server.py": "private"},
        {"package/dist/source.map": "source"},
    ],
)
def test_package_allowlist_rejects_private_or_unexpected_files(tmp_path, extra):
    archive = bundle(tmp_path, extra=extra)
    with pytest.raises(ValueError, match="Unexpected package entry"):
        deploy.validate_archive(archive, "0.1.0")


def test_checksum_version_and_install_scripts_are_checked(tmp_path):
    archive = bundle(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        deploy.install(tmp_path.resolve() / "site", archive, "0" * 64, "0.1.0")
    with pytest.raises(ValueError, match="version"):
        deploy.validate_archive(archive, "0.2.0")
    malicious = bundle(tmp_path, scripts={"postinstall": "unexpected"})
    with pytest.raises(ValueError, match="install scripts"):
        deploy.validate_archive(malicious, "0.1.0")


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_release_paths_cannot_redirect_publication(tmp_path, kind):
    tmp_path = tmp_path.resolve()
    archive = bundle(tmp_path)
    root = tmp_path / "site"
    target = root / "res" / "devops-cli" / "0.1.0.tgz"
    target.parent.mkdir(parents=True)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve")
    if kind == "symlink":
        target.symlink_to(sentinel)
    else:
        os.link(sentinel, target)
    with pytest.raises(ValueError, match="regular unlinked file"):
        deploy.install(root, archive, deploy.digest(archive), "0.1.0")
    assert sentinel.read_text() == "preserve"


def test_dry_run_does_not_build_upload_or_read_remote(monkeypatch):
    monkeypatch.setattr(deploy, "build", lambda: pytest.fail("unexpected build"))
    monkeypatch.setattr(deploy, "run", lambda *a, **kw: pytest.fail("unexpected subprocess"))
    deploy.publish(dry_run=True)
