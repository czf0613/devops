import argparse
import asyncio
import getpass
import json
import os
import signal
import sys
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from websockets.exceptions import WebSocketException

from devops_runner.agent import Agent, capabilities, register


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


async def run_agent(config, path):
    agent = Agent(config, path)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: agent.stopping.set())
    await agent.run()


def main():
    parser = argparse.ArgumentParser(description="DevOps CI worker")
    parser.add_argument("--version", action="version", version=version("devops-runner"))
    parser.add_argument(
        "--config", type=Path, default=Path.home() / ".devops-runner" / "config.json"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    registration = commands.add_parser("register", help="使用一次性令牌注册")
    registration.add_argument("--server", required=True)
    registration.add_argument(
        "--work-dir", type=Path, default=Path.home() / ".devops-runner" / "work"
    )
    registration.add_argument("--label", action="append", default=[])
    registration.add_argument("--token-stdin", action="store_true")
    commands.add_parser("run", help="前台运行 worker")
    commands.add_parser("doctor", help="检查平台及工具链")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            import shutil

            print(
                json.dumps(
                    {
                        **capabilities([]),
                        "git": bool(shutil.which("git")),
                        "git_lfs": bool(shutil.which("git-lfs")),
                        "shell": bool(shutil.which("powershell" if os.name == "nt" else "bash")),
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "register":
            token = (
                sys.stdin.readline().strip()
                if args.token_stdin
                else getpass.getpass("一次性注册令牌: ")
            )
            asyncio.run(
                register(args.config.resolve(), args.server, token, args.work_dir, args.label)
            )
        else:
            if args.config.is_symlink():
                raise RuntimeError("配置不能是符号链接")
            config = json.loads(args.config.read_text())
            with exclusive(Path(config["work_dir"]) / ".worker.lock"):
                asyncio.run(run_agent(config, args.config))
    except (OSError, RuntimeError, ValueError, KeyError, WebSocketException) as exc:
        print(f"worker 操作失败（{type(exc).__name__}），请检查配置、工具链与连接", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
