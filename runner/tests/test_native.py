import asyncio
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from devops_runner.agent import Execution, origin
from devops_runner.executor import NativeExecutor


@pytest.mark.skipif(os.name == "nt", reason="POSIX actual-process test")
def test_native_execution_failure_and_background_cleanup(tmp_path):
    logs = []

    async def log(value):
        logs.append(value)

    async def exercise():
        executor = NativeExecutor(tmp_path, log, asyncio.Event())
        await executor.prepare()
        code = await executor.execute({"run": "echo started; false; echo unreachable"}, 0, {})
        assert code != 0 and "unreachable" not in "".join(logs)
        code = await executor.execute(
            {"run": "sleep 120 & echo $! > child.pid; echo finished"}, 1, {}
        )
        assert code == 0, logs
        child = int((executor.workspace / "child.pid").read_text())
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("background child remained alive")
        await executor.cleanup()
        assert not executor.workspace.exists()

    asyncio.run(exercise())


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipe supervisor test")
def test_supervisor_parent_pipe_loss_stops_build(tmp_path):
    pid_file = tmp_path / "child.pid"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "devops_runner.supervisor",
            "bash",
            "-c",
            f"echo $$ > '{pid_file}'; trap '' TERM; sleep 120",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if pid_file.exists():
                break
            time.sleep(0.02)
        child = int(pid_file.read_text())
        proc.stdin.close()
        proc.wait(timeout=6)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_artifacts_exclude_links_and_metadata(tmp_path):
    async def log(_):
        pass

    executor = NativeExecutor(tmp_path, log, asyncio.Event())
    asyncio.run(executor.prepare())
    (executor.workspace / "out.txt").write_text("result")
    (executor.workspace / ".git").mkdir()
    (executor.workspace / ".git" / "config").write_text("private")
    (executor.workspace / "link").symlink_to(tmp_path)
    assert [name for name, _ in executor.collect(["*.txt", ".git/*"])] == ["out.txt"]


def test_outbox_redacts_task_token_and_is_durable(tmp_path):
    class Agent:
        root = tmp_path

    execution = Execution(Agent(), {"attempt_id": 1, "token": "secret-token", "log_limit": 1000})
    asyncio.run(execution.log("hello secret-token"))
    file = next((execution.root / "outbox").glob("*.json"))
    event = json.loads(file.read_text())
    assert event["seq"] == 1 and event["text"] == "hello [redacted]"
    if os.name != "nt":
        assert file.stat().st_mode & 0o777 == 0o600


def test_origin_requires_tls_except_loopback():
    assert origin("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    for value in (
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/path",
    ):
        with pytest.raises(ValueError):
            origin(value)


def test_cancellation_interrupts_checkout_and_persists_final_result(tmp_path):
    class Agent:
        root = tmp_path
        server = "https://example.invalid"
        stopping = asyncio.Event()

    async def exercise():
        entered = asyncio.Event()
        cleaned = []

        class WaitingExecutor:
            async def prepare(self):
                pass

            async def checkout(self, *_):
                entered.set()
                await asyncio.sleep(120)

            async def cleanup(self):
                cleaned.append(True)

        execution = Execution(
            Agent(),
            {
                "attempt_id": 1,
                "sha": "a" * 40,
                "source_path": "/git/fixture/example.git",
                "source_ref": "refs/devops/ci/1",
                "token": "synthetic",
                "log_limit": 10000,
                "spec": {"timeout_minutes": 1},
            },
        )
        execution.executor = WaitingExecutor()
        execution.task = asyncio.create_task(execution.run())
        await entered.wait()
        execution.request_cancel()
        await asyncio.wait_for(execution.task, timeout=2)
        assert cleaned
        assert json.loads((execution.root / "finished.json").read_text())["state"] == "cancelled"
        final = json.loads(sorted((execution.root / "outbox").glob("*.json"))[-1].read_text())
        assert final["type"] == "job.complete" and final["state"] == "cancelled"

    asyncio.run(exercise())


def test_proxy_selection_is_consistent_and_honors_bypass(monkeypatch):
    from devops_runner import network

    monkeypatch.setattr(
        network,
        "getproxies",
        lambda: {"https": "http://proxy.invalid:8080", "socks": "socks5://proxy.invalid:1080"},
    )
    monkeypatch.setattr(network, "proxy_bypass", lambda host: host == "internal.example")
    assert network.network_proxy("https://devops.example") == "http://proxy.invalid:8080"
    assert network.network_proxy("https://internal.example") is None
    assert network.network_proxy("https://127.0.0.1:8443") is None


def test_loopback_artifacts_bypass_environment_proxy(monkeypatch):
    from devops_runner.network import artifact_client

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    async def exercise():
        async def respond(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nartifact")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
            origin = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with artifact_client(origin, timeout=2) as client:
                response = await client.get(origin + "/artifact")
                assert response.content == b"artifact"

    asyncio.run(exercise())
