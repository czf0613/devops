"""Environment lifecycle contract; future container/VM executors implement the same methods."""

import asyncio
import base64
import glob
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

from devops_runner.network import network_proxy
from devops_runner.supervisor import signal_group


class Executor(Protocol):
    async def prepare(self): ...
    async def checkout(self, source_url: str, source_ref: str, sha: str, token: str): ...
    async def execute(self, step: dict, index: int, env: dict) -> int: ...
    def collect(self, patterns: list[str]) -> list[tuple[str, Path]]: ...
    async def cancel(self): ...
    async def cleanup(self): ...


class NativeExecutor:
    def __init__(self, root: Path, log, cancelled: asyncio.Event):
        self.root, self.log, self.cancelled = root, log, cancelled
        self.workspace = root / "workspace"
        self.process = None
        self.windows_job = None

    async def prepare(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.workspace.exists():
            raise RuntimeError("执行目录已存在，不能重复执行")
        self.workspace.mkdir(mode=0o700)

    async def command(self, args, *, env=None, cwd=None):
        if self.cancelled.is_set():
            raise asyncio.CancelledError
        values = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "DEVOPS_"))}
        values.update(env or {})
        command = [sys.executable, "-m", "devops_runner.supervisor", *args]
        windows = os.name == "nt"
        self.process = subprocess.Popen(
            command,
            cwd=cwd or self.workspace,
            env=values,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=not windows,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0,
        )
        if windows:
            from devops_runner.windows import Job

            self.windows_job = Job()
            self.windows_job.assign(self.process)
            self.process.stdin.write(b"G")
            self.process.stdin.flush()
        process = self.process

        async def read_output():
            while True:
                chunk = await asyncio.to_thread(process.stdout.read1, 4096)
                if not chunk:
                    break
                await self.log(chunk.decode("utf-8", errors="replace"))

        reader = asyncio.create_task(read_output())
        waiter = asyncio.create_task(asyncio.to_thread(process.wait))
        try:
            done, _ = await asyncio.wait((reader, waiter), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader  # Propagate log quota or storage errors before waiting for exit.
            code = await waiter
            await self.cancel()
            await reader
            return code
        finally:
            await self.cancel()
            await asyncio.gather(reader, waiter, return_exceptions=True)
            if process.stdout:
                process.stdout.close()
            if process.stdin:
                process.stdin.close()
            self.process = None

    async def checkout(self, source_url, source_ref, sha, token, *, depth=0, lfs=True):
        authorization = base64.b64encode(f"oauth2:{token}".encode()).decode()
        from urllib.parse import urlsplit

        parsed = urlsplit(source_url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        env = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_COUNT": "4",
            "GIT_CONFIG_KEY_0": f"http.{origin}.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {authorization}",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": os.devnull,
            "GIT_CONFIG_KEY_2": "lfs.url",
            "GIT_CONFIG_VALUE_2": source_url + "/info/lfs",
            "GIT_CONFIG_KEY_3": f"http.{origin}.proxy",
            "GIT_CONFIG_VALUE_3": network_proxy(source_url) or "",
        }
        if os.environ.get("SSL_CERT_FILE"):
            env["GIT_SSL_CAINFO"] = os.environ["SSL_CERT_FILE"]
        for args in (
            ["git", "init", "--quiet"],
            ["git", "remote", "add", "origin", source_url],
            [
                "git",
                "fetch",
                "--quiet",
                "--no-tags",
                *([f"--depth={depth}"] if depth else []),
                "origin",
                source_ref,
            ],
            ["git", "checkout", "--quiet", "--detach", sha],
        ):
            if await self.command(args, env=env):
                raise RuntimeError("源码获取失败")
        # Verify the exact commit without invoking a project hook or shell.
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **env},
        )
        if result.stdout.strip() != sha:
            raise RuntimeError("源码提交不匹配")
        if (self.workspace / ".gitmodules").exists():
            await self.log("提示：首版不自动获取 Git submodules。\n")
        # LFS uses the same short-lived task grant, pinned to this repository's origin.
        if not lfs:
            return
        if shutil.which("git-lfs"):
            # The checkout deliberately ignores host Git configuration, including its LFS
            # filters. Install filters only in this temporary repo so pull materializes files.
            for args in (
                ["git", "lfs", "install", "--local", "--skip-repo"],
                ["git", "lfs", "pull"],
            ):
                if await self.command(args, env=env):
                    raise RuntimeError("Git LFS 获取失败")
        elif (self.workspace / ".gitattributes").exists() and "filter=lfs" in (
            self.workspace / ".gitattributes"
        ).read_text(errors="replace"):
            raise RuntimeError("此仓库需要安装 Git LFS")

    async def execute(self, step, index, env):
        shell = step.get("shell") or ("powershell" if os.name == "nt" else "bash")
        if not shutil.which(shell):
            raise RuntimeError(f"构建机未安装 {shell}")
        powershell = shell in ("powershell", "pwsh")
        script = self.root / f"step-{index}.{'ps1' if powershell else 'sh'}"
        content = step["run"]
        if powershell:
            content = (
                "$ErrorActionPreference = 'Stop'\n"
                + content
                + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE }\n"
            )
        script.write_text(content, encoding="utf-8")
        args = (
            [
                shell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ]
            if powershell
            else (
                [shell, "-e", "-o", "pipefail", str(script)]
                if shell == "bash"
                else [shell, "-e", str(script)]
            )
        )
        cwd = (self.workspace / step.get("working_directory", "")).resolve()
        if not cwd.is_relative_to(self.workspace.resolve()):
            raise RuntimeError("步骤工作目录超出仓库")
        return await self.command(args, env={"CI": "true", **env, **step.get("env", {})}, cwd=cwd)

    def collect(self, patterns):
        found = {}
        for pattern in patterns:
            matched = glob.glob(str(self.workspace / pattern), recursive=True)
            expanded = [
                child
                for name in matched
                for child in (
                    list(Path(name).rglob("*"))
                    if Path(name).is_dir() and not Path(name).is_symlink()
                    else [Path(name)]
                )
            ]
            for name in expanded:
                path = Path(name)
                if not path.is_file() or path.is_symlink():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(self.workspace.resolve()):
                    raise RuntimeError("产物路径超出工作目录")
                if ".git" in path.relative_to(self.workspace).parts:
                    continue
                # Parent symlinks are also excluded from publication.
                if any(p.is_symlink() for p in [path, *path.parents] if p != self.workspace.parent):
                    continue
                found[path.relative_to(self.workspace).as_posix()] = path
                if len(found) > 100:
                    raise RuntimeError("产物不能超过 100 个")
        return sorted(found.items())

    async def cancel(self):
        process = self.process
        if not process:
            return
        if self.windows_job:
            self.windows_job.terminate()
            self.windows_job.close()
            self.windows_job = None
        elif os.name != "nt":
            signal_group(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 4
        while process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        if os.name != "nt":
            signal_group(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait)

    async def cleanup(self):
        await self.cancel()
        # Retain only the protocol journal until the server acknowledges the outcome.
        if self.workspace.exists():
            await asyncio.to_thread(shutil.rmtree, self.workspace)
        if (self.root / "temp").exists():
            await asyncio.to_thread(shutil.rmtree, self.root / "temp")
        for path in self.root.glob("step-*"):
            path.unlink(missing_ok=True)
