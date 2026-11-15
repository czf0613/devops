"""Build locally, upload a tarball with scp, and atomically publish mounted static files.

Only the Python standard library is needed on the server (Python >= 3.12).
The gateway mounts the parent directory, not the current symlink itself.
"""

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DEVOPS = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = "/dockerdata/gateway/website"
DEFAULT_URL = "https://devops.kevinc.ltd"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assets_in_html(html):
    return set(re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', html))


def validate_site(directory):
    index = directory / "index.html"
    if not index.is_file() or not index.stat().st_size:
        raise ValueError("Website build has no index.html")
    assets = assets_in_html(index.read_text())
    if not assets:
        raise ValueError("Website build has no bundled assets")
    for asset in assets:
        path = PurePosixPath(asset)
        if ".." in path.parts or not (directory / asset.lstrip("/")).is_file():
            raise ValueError(f"Missing or invalid website asset: {asset}")


def package(dist, archive, release):
    validate_site(dist)
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(dist.rglob("*")):
            if path.is_symlink():
                raise ValueError("Website builds must not contain symlinks")
            if not path.is_file() or path.name == "release.json":
                continue
            info = tarfile.TarInfo(path.relative_to(dist).as_posix())
            info.size, info.mode = path.stat().st_size, 0o644
            with path.open("rb") as source:
                bundle.addfile(info, source)
        marker = json.dumps({"release": release}).encode()
        info = tarfile.TarInfo("release.json")
        info.size, info.mode = len(marker), 0o644
        bundle.addfile(info, io.BytesIO(marker))


def install(root, archive, checksum, release):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", release):
        raise ValueError("Invalid release name")
    if digest(archive) != checksum:
        raise ValueError("Uploaded archive checksum mismatch")
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    root.chmod(0o755)
    with (root / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        releases = root / "releases"
        releases.mkdir(exist_ok=True, mode=0o755)
        releases.chmod(0o755)
        destination = releases / release
        if destination.exists():
            raise ValueError("Release already exists; publish with a new release name")
        current = root / "current"
        if current.exists() and not current.is_symlink():
            raise ValueError(
                "current must be a symlink, refusing to replace a directory"
            )
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases))
        try:
            with tarfile.open(archive, "r:gz") as bundle:
                for item in bundle.getmembers():
                    path = PurePosixPath(item.name)
                    if path.is_absolute() or ".." in path.parts or not item.isfile():
                        raise ValueError(
                            "Archive must contain only relative regular files"
                        )
                bundle.extractall(staging, filter="data")
            validate_site(staging)
            if json.loads((staging / "release.json").read_text()) != {
                "release": release
            }:
                raise ValueError("Release marker mismatch")
            # Normalize read permissions for the nginx UID, including under restrictive umasks.
            staging.chmod(0o755)
            for path in staging.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            # Retain previous hashes so open browsers can still load their lazy chunks.
            for source in (staging / "assets").rglob("*"):
                if not source.is_file():
                    continue
                target = root / "assets" / source.relative_to(staging / "assets")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                if target.exists():
                    if digest(target) != digest(source):
                        raise ValueError(f"Asset name collision: {target.name}")
                    continue
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, delete=False
                ) as stream:
                    temporary = Path(stream.name)
                try:
                    shutil.copyfile(source, temporary)
                    temporary.chmod(0o644)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            staging.rename(destination)
            for directory in [root / "assets", *(root / "assets").rglob("*")]:
                if directory.is_dir():
                    directory.chmod(0o755)
            pointer = root / f".current-{release}"
            pointer.symlink_to(f"releases/{release}")
            os.replace(pointer, current)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {"release": release, "root": str(root)}


def verify(url, release):
    def fetch(path):
        with urllib.request.urlopen(url.rstrip("/") + path, timeout=30) as response:
            return response.read()

    marker = json.loads(fetch("/release.json?verify=" + release))
    if marker != {"release": release}:
        raise RuntimeError("Gateway is not serving the expected website release")
    html = fetch("/").decode()
    assets = assets_in_html(html)
    if not assets:
        raise RuntimeError("Gateway returned an invalid website index")
    for asset in sorted(assets):
        if not fetch(asset):
            raise RuntimeError(f"Empty website asset: {asset}")
    fetch("/api/health/ready")
    print(
        f"Verified website {release}: index, {len(assets)} assets and API readiness",
        flush=True,
    )


def command(*args, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture).stdout


def publish(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", args.host):
        raise ValueError("Use an SSH host name or configured alias")
    command("pnpm", "--dir", str(DEVOPS / "website"), "install", "--frozen-lockfile")
    command("pnpm", "--dir", str(DEVOPS / "website"), "build")
    revision = command(
        "git", "-C", str(DEVOPS), "rev-parse", "--short", "HEAD", capture=True
    ).strip()
    release = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{revision}-{secrets.token_hex(3)}"
    )
    output = DEVOPS / ".data" / "website-releases"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{release}.tar.gz"
    package(DEVOPS / "website" / "dist", archive, release)

    def ssh(*words):
        return command(
            "ssh", "-o", "BatchMode=yes", args.host, shlex.join(words), capture=True
        )

    remote = ssh("mktemp", "-d", "/tmp/devops-website.XXXXXXXX").strip()
    if not re.fullmatch(r"/tmp/devops-website\.[A-Za-z0-9]+", remote):
        raise RuntimeError("Unexpected remote temporary directory")
    try:
        command(
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            str(archive),
            f"{args.host}:{remote}/website.tar.gz",
        )
        command(
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            __file__,
            f"{args.host}:{remote}/deploy_website.py",
        )
        result = ssh(
            "python3",
            f"{remote}/deploy_website.py",
            "install",
            "--root",
            args.root,
            "--archive",
            f"{remote}/website.tar.gz",
            "--checksum",
            digest(archive),
            "--release",
            release,
        )
        print(result.strip(), flush=True)
    finally:
        ssh("rm", "-rf", "--", remote)
    if args.prepare_only:
        print(
            f"Prepared {release}; start the mounted gateway, then run verify --release {release}."
        )
    else:
        verify(args.url, release)
    print(f"Local archive: {archive}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    deploy = actions.add_parser(
        "publish", help="Build locally, scp, extract and switch current"
    )
    deploy.add_argument("--host", default="devops")
    deploy.add_argument("--root", default=DEFAULT_ROOT)
    deploy.add_argument("--url", default=DEFAULT_URL)
    deploy.add_argument(
        "--prepare-only",
        action="store_true",
        help="First installation: defer HTTP verification until gateway starts",
    )
    check = actions.add_parser(
        "verify", help="Verify an already published release through HTTPS"
    )
    check.add_argument("--url", default=DEFAULT_URL)
    check.add_argument("--release", required=True)
    unpack = actions.add_parser(
        "install", help="Internal: unpack and switch on the server"
    )
    unpack.add_argument("--root", type=Path, required=True)
    unpack.add_argument("--archive", type=Path, required=True)
    unpack.add_argument("--checksum", required=True)
    unpack.add_argument("--release", required=True)
    args = parser.parse_args()
    if args.action == "publish":
        publish(args)
    elif args.action == "verify":
        verify(args.url, args.release)
    else:
        print(json.dumps(install(args.root, args.archive, args.checksum, args.release)))


if __name__ == "__main__":
    main()
