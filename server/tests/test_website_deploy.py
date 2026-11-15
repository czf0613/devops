import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "deploy_website", Path(__file__).resolve().parents[2] / "scripts" / "deploy_website.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def bundle(tmp_path, release, asset="app-123.js", content="export default 1"):
    dist = tmp_path / release
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / asset).write_text(content)
    (dist / "index.html").write_text(f'<html><script src="/assets/{asset}"></script></html>')
    archive = tmp_path / f"{release}.tar.gz"
    deploy.package(dist, archive, release)
    return archive


def test_switch_keeps_previous_assets_and_does_not_require_gateway_restart(tmp_path):
    root = tmp_path / "website"
    first = bundle(tmp_path, "first")
    deploy.install(root, first, deploy.digest(first), "first")
    second = bundle(tmp_path, "second", "app-456.js", "export default 2")
    deploy.install(root, second, deploy.digest(second), "second")
    assert (root / "current").readlink() == Path("releases/second")
    assert json.loads((root / "current/release.json").read_text()) == {"release": "second"}
    assert (root / "assets/app-123.js").read_text() == "export default 1"
    assert (root / "assets/app-456.js").read_text() == "export default 2"
    assert (root / "releases/first/index.html").exists()
    assert (root / "current/index.html").stat().st_mode & 0o777 == 0o644
    assert (root / "current").stat().st_mode & 0o777 == 0o755


def test_failed_checks_leave_current_release_in_place(tmp_path):
    root = tmp_path / "website"
    first = bundle(tmp_path, "first")
    deploy.install(root, first, deploy.digest(first), "first")
    collision = bundle(tmp_path, "collision", content="different content under same hash")
    with pytest.raises(ValueError, match="checksum"):
        deploy.install(root, collision, "incorrect", "collision")
    with pytest.raises(ValueError, match="collision"):
        deploy.install(root, collision, deploy.digest(collision), "collision")
    assert (root / "current").readlink() == Path("releases/first")
    assert (root / "assets/app-123.js").read_text() == "export default 1"
    assert not list((root / "releases").glob(".staging-*"))


@pytest.mark.parametrize("kind", ["traversal", "symlink", "missing_asset"])
def test_invalid_archives_cannot_publish_or_escape_the_root(tmp_path, kind):
    archive = tmp_path / "invalid.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("../escaped" if kind == "traversal" else "index.html")
        if kind == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            output.addfile(info)
        else:
            content = b'<script src="/assets/missing.js"></script>'
            info.size = len(content)
            output.addfile(info, io.BytesIO(content))
    root = tmp_path / "website"
    with pytest.raises(ValueError):
        deploy.install(root, archive, deploy.digest(archive), "invalid")
    assert not (root / "current").exists()
    assert not (tmp_path / "escaped").exists()
