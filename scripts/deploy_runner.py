"""Publish the independent, immutable Python worker wheel to the test environment."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import tomllib

DEVOPS = Path(__file__).resolve().parents[1]
HOST = os.environ.get("DEVOPS_DEPLOY_HOST", "kdevops")
ROOT = Path("/dockerdata/gateway/website")
URL = "https://devops.kevinc.ltd"


def validate(archive, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Invalid runner version")
    info = f"devops_runner-{version}.dist-info/"
    seen = set()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            allowed = re.fullmatch(r"devops_runner/[a-z_]+\.py", entry.filename) or (
                entry.filename.startswith(info)
                and entry.filename[len(info) :]
                in {"METADATA", "WHEEL", "entry_points.txt", "RECORD"}
            )
            if not allowed or entry.filename in seen or entry.file_size > 1024 * 1024:
                raise ValueError("Unexpected worker wheel entry")
            seen.add(entry.filename)
        if not {"devops_runner/main.py", info + "METADATA", info + "RECORD"} <= seen:
            raise ValueError("Incomplete worker wheel")
        metadata = bundle.read(info + "METADATA").decode()
        if (
            f"\nVersion: {version}\n" not in metadata
            or "\nName: devops-runner\n" not in metadata
        ):
            raise ValueError("Worker metadata mismatch")


def install(root, archive, checksum, version):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise ValueError("Worker checksum mismatch")
    validate(archive, version)
    destination = root.absolute() / "res/devops-runner"
    for path in reversed((destination, *destination.parents)):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError("Refusing non-directory release path")
        path.mkdir(exist_ok=True, mode=0o755)
    fd = os.open(
        root / ".runner-publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "a") as lock:
        if os.fstat(lock.fileno()).st_nlink != 1:
            raise ValueError("Refusing hardlinked lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        name = f"devops_runner-{version}-py3-none-any.whl"
        for filename, data in (
            (name, archive.read_bytes()),
            (name + ".sha256", f"{checksum}  {name}\n".encode()),
        ):
            target = destination / filename
            if target.is_symlink() or (
                target.exists()
                and (
                    not stat.S_ISREG(target.stat().st_mode)
                    or target.stat().st_nlink != 1
                )
            ):
                raise ValueError("Refusing linked release entry")
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError(
                        "Runner version already published; bump its version"
                    )
                continue
            fd, temporary = tempfile.mkstemp(prefix=".runner-", dir=destination)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), 0o644)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)


def build():
    runner = DEVOPS / "runner"
    version = tomllib.loads((runner / "pyproject.toml").read_text())["project"][
        "version"
    ]
    directory = DEVOPS / ".data/runner-releases"
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--project",
            str(runner),
            "--out-dir",
            str(directory),
        ],
        check=True,
    )
    archive = directory / f"devops_runner-{version}-py3-none-any.whl"
    validate(archive, version)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".whl.sha256").write_text(f"{checksum}  {archive.name}\n")
    return archive, checksum, version


def publish(dry_run=False):
    if dry_run:
        print(
            "Runner: uv build wheel → validate → immutable static install → verify HTTPS checksum"
        )
        return
    archive, checksum, version = build()
    remote = f"/tmp/devops-runner-{secrets.token_hex(12)}.whl"
    try:
        subprocess.run(["scp", str(archive), HOST + ":" + remote], check=True)
        subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                HOST,
                shlex.join(
                    [
                        "python3",
                        "-",
                        "--remote",
                        str(ROOT),
                        remote,
                        checksum,
                        version,
                    ]
                ),
            ],
            input=Path(__file__).read_text(),
            text=True,
            check=True,
        )
        url = f"{URL}/res/devops-runner/{archive.name}"
        with urllib.request.urlopen(url, timeout=30) as response:
            if hashlib.sha256(response.read()).hexdigest() != checksum:
                raise RuntimeError("Published runner checksum mismatch")
        with urllib.request.urlopen(url + ".sha256", timeout=30) as response:
            if response.read().decode() != f"{checksum}  {archive.name}\n":
                raise RuntimeError("Published runner checksum file mismatch")
        print(json.dumps({"version": version, "sha256": checksum, "url": url}))
    finally:
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", HOST, shlex.join(["rm", "-f", remote])],
            check=True,
        )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--remote"]:
        install(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("command", choices=["build", "publish"])
        parser.add_argument("--dry-run", action="store_true")
        args = parser.parse_args()
        if args.command == "publish":
            publish(args.dry_run)
        elif not args.dry_run:
            archive, checksum, version = build()
            print(
                json.dumps(
                    {"path": str(archive), "version": version, "sha256": checksum}
                )
            )
