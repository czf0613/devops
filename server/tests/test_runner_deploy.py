import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "deploy_runner", Path(__file__).resolve().parents[2] / "scripts/deploy_runner.py"
)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def wheel(path, code="print('worker')", extra=None):
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("devops_runner/main.py", code)
        output.writestr(
            "devops_runner-1.0.0.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: devops-runner\nVersion: 1.0.0\n",
        )
        output.writestr("devops_runner-1.0.0.dist-info/RECORD", "fixture")
        if extra:
            output.writestr(extra, "forbidden")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_worker_wheel_publication_is_immutable_and_checksummed(tmp_path):
    tmp_path = tmp_path.resolve()
    archive, checksum = wheel(tmp_path / "worker.whl")
    root = tmp_path / "website"
    deploy.install(root, archive, checksum, "1.0.0")
    deploy.install(root, archive, checksum, "1.0.0")
    target = root / "res/devops-runner/devops_runner-1.0.0-py3-none-any.whl"
    assert target.read_bytes() == archive.read_bytes()
    changed, second = wheel(tmp_path / "changed.whl", code="changed")
    with pytest.raises(ValueError, match="already published"):
        deploy.install(root, changed, second, "1.0.0")
    with pytest.raises(ValueError, match="checksum"):
        deploy.install(root, archive, "0" * 64, "1.0.0")
    target.unlink()
    target.symlink_to(archive)
    with pytest.raises(ValueError, match="linked"):
        deploy.install(root, archive, checksum, "1.0.0")


@pytest.mark.parametrize("name", ["../escape", "devops_runner/.env", "private/config.json"])
def test_worker_wheel_rejects_private_and_escaping_paths(tmp_path, name):
    archive, _ = wheel(tmp_path / "worker.whl", extra=name)
    with pytest.raises(ValueError, match="Unexpected"):
        deploy.validate(archive, "1.0.0")
