"""Read-only host metrics for the administrator server status page."""

import os
import platform
import socket
import time
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import psutil
from fastapi import APIRouter, Request

from devops_server.config import Settings
from devops_server.dependencies import Administrator

router = APIRouter(prefix="/api/admin/system", tags=["system"])

CPU_SAMPLE_SECONDS = 0.2
STORAGE_CACHE_SECONDS = 60
STORAGE_SCAN_SECONDS = 1.5
STORAGE_SCAN_ENTRIES = 200_000

_storage_lock = Lock()
_storage_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _percent(used: float, total: float) -> float:
    """Describe usage as a percentage capped at 100 for the overall server."""
    if total <= 0:
        return 0.0
    return round(min(100.0, max(0.0, used / total * 100)), 1)


def _cpu_metrics() -> dict[str, Any]:
    try:
        usage = psutil.cpu_percent(interval=CPU_SAMPLE_SECONDS)
    except OSError, psutil.Error:
        usage = None
    try:
        load = [round(value, 2) for value in psutil.getloadavg()]
    except AttributeError, OSError:
        load = None
    return {
        "usage_percent": _percent(usage, 100) if usage is not None else None,
        "logical_cores": psutil.cpu_count(logical=True) or os.cpu_count() or 0,
        "load_average": load,
    }


def _memory_metrics() -> dict[str, Any] | None:
    try:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
    except OSError, psutil.Error:
        return None
    used = max(0, memory.total - memory.available)
    return {
        "total_bytes": int(memory.total),
        "used_bytes": int(used),
        "available_bytes": int(memory.available),
        "usage_percent": _percent(used, memory.total),
        "swap_total_bytes": int(swap.total),
        "swap_used_bytes": int(swap.used),
    }


def _uptime_seconds() -> int | None:
    try:
        return max(0, int(time.time() - psutil.boot_time()))
    except OSError, psutil.Error:
        return None


def _host_info() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "uptime_seconds": _uptime_seconds(),
    }


def _disk_metrics(repository_root: str) -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    devices: set[int] = set()
    totals: set[int] = set()
    for path, label in (("/", "系统盘"), (repository_root, "Git 数据盘")):
        try:
            device = os.stat(path).st_dev
            usage = psutil.disk_usage(path)
        except OSError, psutil.Error:
            continue
        # Docker's overlay root and a host bind mount can expose the same
        # underlying filesystem through different device IDs; equal capacity
        # identifies that shared host disk without depending on changing usage.
        if device in devices or int(usage.total) in totals:
            continue
        devices.add(device)
        totals.add(int(usage.total))
        disks.append(
            {
                "path": path,
                "label": label,
                "total_bytes": int(usage.total),
                "used_bytes": int(usage.used),
                "free_bytes": int(usage.free),
                "usage_percent": _percent(usage.used, usage.total),
            }
        )
    return disks


def _scan_storage(path: str, label: str) -> dict[str, Any]:
    if not os.path.isdir(path):
        return {"path": path, "label": label, "bytes": None, "partial": False}
    total = entries = 0
    partial = False
    started = time.monotonic()
    pending = [path]
    while pending:
        if time.monotonic() - started >= STORAGE_SCAN_SECONDS or entries >= STORAGE_SCAN_ENTRIES:
            partial = True
            break
        try:
            with os.scandir(pending.pop()) as children:
                for child in children:
                    entries += 1
                    if (
                        entries > STORAGE_SCAN_ENTRIES
                        or time.monotonic() - started >= STORAGE_SCAN_SECONDS
                    ):
                        partial = True
                        break
                    try:
                        if child.is_dir(follow_symlinks=False):
                            pending.append(child.path)
                        elif child.is_file(follow_symlinks=False):
                            total += child.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
        if partial:
            break
    return {"path": path, "label": label, "bytes": total, "partial": partial}


def _storage_metrics(path: str, label: str) -> dict[str, Any]:
    """Scan a data directory with a short cache; large trees report a lower bound."""
    now = time.monotonic()
    with _storage_lock:
        cached = _storage_cache.get(path)
    if cached is not None and now - cached[0] < STORAGE_CACHE_SECONDS:
        return cached[1]
    result = _scan_storage(path, label)
    with _storage_lock:
        _storage_cache[path] = (now, result)
    return result


def collect(settings: Settings) -> dict[str, Any]:
    repository_root = settings.git_repositories_root
    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "host": _host_info(),
        "cpu": _cpu_metrics(),
        "memory": _memory_metrics(),
        "disks": _disk_metrics(repository_root),
        "storage": [_storage_metrics(repository_root, "Git 仓库数据")],
    }


@router.get("")
def server_status(request: Request, actor: Administrator):
    # The dependency is the access boundary; this endpoint only reads host counters.
    return collect(request.app.state.settings)
