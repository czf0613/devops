import os
import subprocess
import sys
import time
from pathlib import Path

from devops_server import collaboration_health


def test_probe_rejects_missing_stale_and_future_heartbeats(tmp_path):
    path = tmp_path / "heartbeat"
    assert not collaboration_health.healthy(path)
    path.touch()
    assert collaboration_health.healthy(path)
    for offset in (-241, 60):
        modified = time.time() + offset
        os.utime(path, (modified, modified))
        assert not collaboration_health.healthy(path)


def test_probe_imports_without_site_packages():
    source = Path(collaboration_health.__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import devops_server.collaboration_health; "
            "assert 'sqlalchemy' not in sys.modules and 'fastapi' not in sys.modules",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=5,
    )
