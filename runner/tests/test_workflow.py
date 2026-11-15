import asyncio
from types import SimpleNamespace

import pytest

from devops_runner import environment
from devops_runner.executor import NativeExecutor
from devops_runner.workflow import WorkflowSteps, expand, relative_path


def test_host_inventory_mac_linux_libc_and_32_bit_rejection(monkeypatch):
    monkeypatch.setattr(environment.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(environment.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(environment.platform, "mac_ver", lambda: ("15.7.1", (), "arm64"))
    caps = environment.capabilities(["xcode"])
    assert caps["os_version"] == "15.7.1" and caps["arch"] == "arm64" and caps["runtime"] == 2
    monkeypatch.setattr(environment.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        environment.platform,
        "freedesktop_os_release",
        lambda: {"ID": "ubuntu", "NAME": "Ubuntu", "VERSION_ID": "24.04"},
    )
    monkeypatch.setattr(environment.os, "confstr", lambda _: "glibc 2.39")
    caps = environment.capabilities([])
    assert caps["distribution"]["version"] == "24.04" and caps["libc"] == {
        "name": "glibc",
        "version": "2.39",
    }
    monkeypatch.setattr(environment.os, "confstr", lambda _: "")
    monkeypatch.setattr(environment.platform, "libc_ver", lambda: ("", ""))
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="", stderr="musl libc (aarch64)\nVersion 1.2.5\n"),
    )
    assert environment.libc() == {"name": "musl", "version": "1.2.5"}
    monkeypatch.setattr(environment.platform, "machine", lambda: "i686")
    with pytest.raises(ValueError, match="64 位"):
        environment.capabilities([])


def test_windows_client_and_server_have_distinct_versions(monkeypatch):
    import sys

    monkeypatch.setattr(environment.platform, "system", lambda: "Windows")
    monkeypatch.setattr(environment.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(environment.platform, "win32_ver", lambda: ("11", "10.0", "", ""))
    monkeypatch.setattr(
        sys,
        "getwindowsversion",
        lambda: SimpleNamespace(major=10, minor=0, build=26100, product_type=1),
        raising=False,
    )
    assert environment.capabilities([])["os_edition"] == "Windows 11"
    monkeypatch.setattr(
        sys,
        "getwindowsversion",
        lambda: SimpleNamespace(major=10, minor=0, build=20348, product_type=3),
    )
    caps = environment.capabilities([])
    assert caps["os_edition"] == "Windows Server" and caps["os_version"] == "10.0.20348"


def test_github_file_commands_contexts_and_working_directory(tmp_path):
    logs = []

    async def log(value):
        logs.append(value)

    async def scenario():
        executor = NativeExecutor(tmp_path / "attempt", log, asyncio.Event())
        await executor.prepare()
        (executor.workspace / "src").mkdir()
        payload = {
            "spec": {"env": {"INITIAL": "hello"}},
            "context": {"github": {"sha": "a" * 40}, "inputs": {}, "matrix": {}},
        }
        steps = WorkflowSteps(SimpleNamespace(executor=executor, payload=payload))
        result = await steps.run(
            {
                "id": "first",
                "name": "Export",
                "run": (
                    'echo "MESSAGE=from-file" >> "$GITHUB_ENV"\n'
                    'echo "result=done" >> "$GITHUB_OUTPUT"'
                ),
                "shell": "bash",
            },
            0,
        )
        assert result == 0
        result = await steps.run(
            {
                "name": "Read",
                "run": (
                    'test "$MESSAGE" = from-file\ntest "$INITIAL" = hello\n'
                    'test "$RESULT" = done\ntest "$GITHUB_SHA" = '
                )
                + "a" * 40
                + "\npwd",
                "env": {"RESULT": "${{ steps.first.outputs.result }}"},
                "working_directory": "src",
                "shell": "bash",
            },
            1,
        )
        assert result == 0 and str(executor.workspace / "src") in "".join(logs)
        await executor.cleanup()

    asyncio.run(scenario())


def test_no_unknown_expression_or_workspace_escape(tmp_path):
    with pytest.raises(ValueError):
        expand("${{ secrets.UNKNOWN }}", {})
    with pytest.raises(ValueError):
        expand("${{ github.sha || dangerous() }}", {"github": {"sha": "abc"}})
    with pytest.raises(ValueError):
        relative_path(tmp_path, "../elsewhere")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        relative_path(tmp_path, "link/file")


def test_darwin_zombie_group_race_does_not_hide_live_permission_failure(monkeypatch):
    from devops_runner import supervisor

    monkeypatch.setattr(supervisor.sys, "platform", "darwin")

    def denied(*args):
        raise PermissionError("synthetic denial")

    monkeypatch.setattr(supervisor.os, "killpg", denied)
    monkeypatch.setattr(
        supervisor.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="123 Z\n999 S\n")
    )
    supervisor.signal_group(123, 9)
    monkeypatch.setattr(
        supervisor.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="123 S\n")
    )
    with pytest.raises(PermissionError):
        supervisor.signal_group(123, 9)
