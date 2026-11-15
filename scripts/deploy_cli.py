"""Build and publish immutable public CLI tarballs. Remote install uses only stdlib."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

DEVOPS = Path(__file__).resolve().parents[1]
HOST = os.environ.get("DEVOPS_DEPLOY_HOST", "kdevops")
ROOT = Path("/dockerdata/gateway/website")
URL = "https://devops.kevinc.ltd"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_archive(archive, version):
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid CLI version")
    seen = set()
    total = 0
    with tarfile.open(archive, "r:gz") as bundle:
        for entry in bundle:
            path = PurePosixPath(entry.name)
            allowed = entry.name in {"package/package.json", "package/README.md"} or (
                len(path.parts) == 3
                and path.parts[:2] == ("package", "dist")
                and re.fullmatch(r"[a-zA-Z0-9_-]+\.(js|d\.ts)", path.name)
            )
            if not allowed or not entry.isfile() or entry.name in seen:
                raise ValueError(f"Unexpected package entry: {entry.name}")
            total += entry.size
            if total > 10 * 1024 * 1024:
                raise ValueError("CLI package exceeds expected size")
            seen.add(entry.name)
        required = {
            "package/package.json",
            "package/README.md",
            "package/dist/main.js",
            "package/dist/sdk.js",
            "package/dist/sdk.d.ts",
        }
        if not required <= seen:
            raise ValueError("CLI package is missing required files")
        metadata = json.load(bundle.extractfile("package/package.json"))
        if metadata["name"] != "@kevinc/devops-cli" or metadata["version"] != version:
            raise ValueError("Unexpected package name or version")
        if metadata.get("bin") != {"devops": "dist/main.js"}:
            raise ValueError("Unexpected executable")
        if any(
            key in metadata.get("scripts", {})
            for key in (
                "preinstall",
                "install",
                "postinstall",
                "prepare",
            )
        ):
            raise ValueError("Public CLI package must not run install scripts")
        for dependency in metadata.get("dependencies", {}).values():
            if not VERSION.fullmatch(dependency):
                raise ValueError("Runtime dependencies must use exact public versions")


def directory(path):
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"Refusing non-directory or symlink: {path}")
    else:
        directory(path.parent)
        path.mkdir(mode=0o755)


def install(root, archive, checksum, version):
    if not re.fullmatch(r"[a-f0-9]{64}", checksum) or digest(archive) != checksum:
        raise ValueError("Uploaded CLI checksum mismatch")
    validate_archive(archive, version)
    root = root.absolute()
    # Validate every ancestor, not only the leaf (public nginx follows real dirs).
    for ancestor in reversed((root, *root.parents)):
        directory(ancestor)
    destination = root / "res" / "devops-cli"
    directory(destination.parent)
    directory(destination)
    lockpath = root / ".cli-publish.lock"
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        if os.fstat(lock.fileno()).st_nlink != 1:
            raise ValueError("Publish lock must not be hardlinked")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = destination / f"{version}.tgz"
        sidecar = target.with_suffix(".tgz.sha256")
        expected = f"{checksum}  {version}.tgz\n"
        for path in (target, sidecar):
            if path.is_symlink() or (
                path.exists()
                and (not stat.S_ISREG(path.stat().st_mode) or path.stat().st_nlink != 1)
            ):
                raise ValueError(
                    "Existing release entry must be a regular unlinked file"
                )
        if target.exists() and digest(target) != checksum:
            raise ValueError(
                "This CLI version already contains different bytes; bump the version"
            )
        if sidecar.exists() and sidecar.read_text() != expected:
            raise ValueError("Existing release checksum conflicts")
        for path, data in (
            (target, archive.read_bytes()),
            (sidecar, expected.encode()),
        ):
            if path.exists():
                continue
            fd, name = tempfile.mkstemp(prefix=".cli-", dir=destination)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), 0o644)
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)
    return {"version": version, "sha256": checksum, "path": str(target)}


def run(*args, **kwargs):
    print("+ " + shlex.join(map(str, args)), flush=True)
    return subprocess.run(args, check=True, text=True, **kwargs)


def build():
    cli = DEVOPS / "cli"
    version = json.loads((cli / "package.json").read_text())["version"]
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid CLI version")
    run("pnpm", "--dir", str(cli), "install", "--frozen-lockfile")
    run("pnpm", "--dir", str(cli), "build")
    release = DEVOPS / ".data" / "cli-releases"
    release.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="devops-cli-") as temporary:
        packed = run(
            "npm",
            "pack",
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            temporary,
            cwd=cli,
            capture_output=True,
        )
        name = json.loads(packed.stdout)[0]["filename"]
        archive = Path(temporary) / name
        validate_archive(archive, version)
        target = release / f"{version}.tgz"
        # Local artifacts can be rebuilt before publishing; remote versions cannot.
        shutil.copyfile(archive, target)
        checksum = digest(target)
        target.with_suffix(".tgz.sha256").write_text(f"{checksum}  {version}.tgz\n")
    return target, checksum, version


def publish(dry_run=False):
    if dry_run:
        print(
            "CLI: local frozen install/build/pack → validate allowlist → scp to test host "
            "kdevops → immutable /res/devops-cli/<version>.tgz → verify public checksum"
        )
        return
    archive, checksum, version = build()
    remote = f"/tmp/devops-cli-{secrets.token_hex(12)}.tgz"
    try:
        run("scp", str(archive), HOST + ":" + remote)
        run(
            "ssh",
            "-o",
            "BatchMode=yes",
            HOST,
            shlex.join(
                ["python3", "-", "--remote", str(ROOT), remote, checksum, version]
            ),
            input=Path(__file__).read_text(),
        )
        with urllib.request.urlopen(
            f"{URL}/res/devops-cli/{version}.tgz", timeout=30
        ) as response:
            if hashlib.sha256(response.read()).hexdigest() != checksum:
                raise RuntimeError(
                    "Public CLI download differs from the published artifact"
                )
        with urllib.request.urlopen(
            f"{URL}/res/devops-cli/{version}.tgz.sha256", timeout=30
        ) as response:
            if response.read().decode() != f"{checksum}  {version}.tgz\n":
                raise RuntimeError("Public checksum differs from published artifact")
    finally:
        run("ssh", "-o", "BatchMode=yes", HOST, shlex.join(["rm", "-f", remote]))
    print(
        json.dumps(
            {
                "version": version,
                "sha256": checksum,
                "url": f"{URL}/res/devops-cli/{version}.tgz",
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "publish"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "publish":
        publish(args.dry_run)
    elif args.dry_run:
        print("CLI: local frozen install/build/pack; no remote changes")
    else:
        archive, checksum, version = build()
        print(
            json.dumps(
                {"archive": str(archive), "sha256": checksum, "version": version}
            )
        )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--remote"]:
        print(
            json.dumps(
                install(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5])
            )
        )
    else:
        main()
