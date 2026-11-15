"""Fast, dependency-free probe for the collaboration worker's successful loop."""

import time
from pathlib import Path

HEARTBEAT = Path("/tmp/devops-collaboration-worker.heartbeat")


def healthy(path: Path = HEARTBEAT) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < 240


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
