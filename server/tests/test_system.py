import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from devops_server import system_status


def test_collect_caps_cpu_and_reports_host_and_storage(monkeypatch, auth_settings):
    repository_root = Path(auth_settings.git_repositories_root)
    repository_root.mkdir()
    (repository_root / "pack").write_bytes(b"x" * 2048)
    monkeypatch.setattr(system_status.psutil, "cpu_percent", lambda interval=None: 137.5)
    monkeypatch.setattr(system_status.psutil, "cpu_count", lambda logical=True: 4)
    monkeypatch.setattr(system_status.psutil, "getloadavg", lambda: (1.0, 0.5, 0.25))
    monkeypatch.setattr(
        system_status.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=1000, available=250),
    )
    monkeypatch.setattr(
        system_status.psutil,
        "swap_memory",
        lambda: SimpleNamespace(total=200, used=50),
    )
    monkeypatch.setattr(system_status.psutil, "boot_time", lambda: time.time() - 3600)
    monkeypatch.setattr(
        system_status.psutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1000, used=250, free=750),
    )
    result = system_status.collect(auth_settings)

    assert result["cpu"] == {
        "usage_percent": 100.0,
        "logical_cores": 4,
        "load_average": [1.0, 0.5, 0.25],
    }
    assert result["memory"]["usage_percent"] == 75.0
    assert result["memory"]["used_bytes"] == 750
    assert result["memory"]["swap_used_bytes"] == 50
    assert result["host"]["hostname"]
    assert 3590 <= result["host"]["uptime_seconds"] <= 3610
    # The mocked root and data path report the same filesystem usage; a bind
    # mount or a shared host disk must not appear as two separate disks.
    assert len(result["disks"]) == 1
    assert all(0 <= disk["usage_percent"] <= 100 for disk in result["disks"])
    assert result["storage"][0]["bytes"] >= 2048
    assert result["storage"][0]["partial"] is False
    datetime.fromisoformat(result["collected_at"])


def test_system_status_is_administrator_only(system):
    system.seed("admin", "admin")
    system.seed("member")
    anonymous = system.client()
    admin = system.client()
    member = system.client()
    system.login(admin, "admin@example.com")
    system.login(member, "member@example.com")

    assert anonymous.get("/api/admin/system").status_code == 401
    assert member.get("/api/admin/system").status_code == 403
    response = admin.get("/api/admin/system")
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"collected_at", "host", "cpu", "memory", "disks", "storage"}
    assert result["cpu"]["logical_cores"] >= 1
    assert result["cpu"]["usage_percent"] is None or 0 <= result["cpu"]["usage_percent"] <= 100
    assert result["memory"] is None or 0 <= result["memory"]["usage_percent"] <= 100
    assert result["disks"][0]["path"] == "/"
    assert all(0 <= disk["usage_percent"] <= 100 for disk in result["disks"])
    assert result["storage"][0]["path"] == system.settings.git_repositories_root
