"""Dependency-free GC worker probe; the file is updated only after a DB heartbeat."""

from pathlib import Path

from devops_server.collaboration_health import healthy

HEARTBEAT = Path("/tmp/devops-registry-gc.heartbeat")

if __name__ == "__main__":
    raise SystemExit(0 if healthy(HEARTBEAT) else 1)
