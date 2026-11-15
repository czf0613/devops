"""Read Git objects, never check out or execute repository-controlled files."""

import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from devops_server.dependencies import Database
from devops_server.dependencies import RepositoryAuthenticated as Authenticated
from devops_server.repositories import visible_repository

router = APIRouter(prefix="/api/repositories", tags=["repository-code"])
PREVIEW_BYTES = 1024 * 1024
TREE_BYTES = 2 * 1024 * 1024
PAGE_SIZE = 200
ACTIVITY_WEEKS = 12
ACTIVITY_COMMIT_LIMIT = 10000
README_NAMES = ("readme.md", "readme.markdown", "readme.mdown", "readme")
Ref = Annotated[str, Query(min_length=1, max_length=1024)]
GitPath = Annotated[str, Query(max_length=4096)]


def repository_directory(root: str, repository_id: int) -> Path:
    base = Path(root)
    directory = base / f"{repository_id}.git"
    if repository_id < 1 or base.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise HTTPException(503, "仓库存储暂时不可用")
    return directory


def git(directory: Path, *args: str, missing=False) -> bytes:
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        result = subprocess.run(
            ["git", "--literal-pathspecs", f"--git-dir={directory}", *args],
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        raise HTTPException(503, "读取仓库超时或存储暂时不可用") from None
    if result.returncode:
        raise HTTPException(
            404 if missing else 503, "分支或文件不存在" if missing else "无法读取仓库"
        )
    return result.stdout


def branch_list(directory: Path) -> dict:
    rows = (
        git(
            directory,
            "for-each-ref",
            "--count=1001",
            "--sort=refname",
            "--format=%(refname:strip=2)%00%(objectname)",
            "refs/heads/",
        )
        .decode("utf-8", errors="replace")
        .splitlines()
    )
    return {
        "items": [
            dict(zip(("name", "commit"), row.split("\0", 1), strict=True)) for row in rows[:1000]
        ],
        "truncated": len(rows) > 1000,
        "default_branch": "main",
    }


@router.get("/{repository_id}/branches")
def branches(repository_id: int, request: Request, actor: Authenticated, db: Database):
    visible_repository(db, repository_id, actor.user.id)
    return branch_list(
        repository_directory(request.app.state.settings.git_repositories_root, repository_id)
    )


def weekly_activity(directory: Path, now: datetime) -> dict:
    # Committer dates, UTC Monday weeks, all local branches; Git de-duplicates shared history.
    now = now.astimezone(UTC)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = monday - timedelta(weeks=ACTIVITY_WEEKS - 1)
    rows = git(
        directory,
        "log",
        "--branches",
        f"--since-as-filter={start.isoformat()}",
        f"--max-count={ACTIVITY_COMMIT_LIMIT + 1}",
        "--format=%ct",
        "--no-patch",
        "--no-show-signature",
        "--",
    ).splitlines()
    counts = [0] * ACTIVITY_WEEKS
    start_timestamp, now_timestamp = start.timestamp(), now.timestamp()
    for row in rows[:ACTIVITY_COMMIT_LIMIT]:
        timestamp = int(row)
        # Ignore future-dated commits and don't let odd commit clocks escape the chart.
        if start_timestamp <= timestamp <= now_timestamp:
            counts[int((timestamp - start_timestamp) // (7 * 86400))] += 1
    return {
        "weeks": [
            {"start": (start + timedelta(weeks=index)).date().isoformat(), "commits": count}
            for index, count in enumerate(counts)
        ],
        "total": sum(counts),
        "truncated": len(rows) > ACTIVITY_COMMIT_LIMIT,
        "commit_limit": ACTIVITY_COMMIT_LIMIT,
    }


@router.get("/{repository_id}/activity")
def activity(repository_id: int, request: Request, actor: Authenticated, db: Database):
    visible_repository(db, repository_id, actor.user.id)
    return weekly_activity(
        repository_directory(request.app.state.settings.git_repositories_root, repository_id),
        datetime.now(UTC),
    )


def resolve(directory: Path, ref: str, path: str) -> tuple[str, str]:
    # Only branch names, not revision expressions, arbitrary object IDs or pathspecs.
    if "\0" in ref or "\0" in path or (path and any(p in {"", ".", ".."} for p in path.split("/"))):
        raise HTTPException(422, "分支或文件路径无效")
    git(directory, "check-ref-format", f"refs/heads/{ref}", missing=True)
    commit = (
        git(
            directory,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"refs/heads/{ref}^{{commit}}",
            missing=True,
        )
        .decode()
        .strip()
    )
    oid = (
        git(
            directory, "rev-parse", "--verify", "--end-of-options", f"{commit}:{path}", missing=True
        )
        .decode()
        .strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{40}", oid):
        raise HTTPException(422, "无法解析 Git 对象")
    return commit, oid


def lfs_pointer(content: str) -> dict | None:
    if not content.startswith("version https://git-lfs.github.com/spec/v1\n"):
        return None
    oid = re.search(r"^oid sha256:([0-9a-f]{64})$", content, re.MULTILINE)
    size = re.search(r"^size ([0-9]{1,20})$", content, re.MULTILINE)
    return {"oid": oid[1], "size": int(size[1])} if oid and size else None


def preview_blob(directory: Path, oid: str, size: int) -> dict:
    if size > PREVIEW_BYTES:
        return {"content": None, "too_large": True, "binary": False, "lfs": None}
    data = git(directory, "cat-file", "blob", oid)
    try:
        content = data.decode("utf-8")
        binary = "\0" in content
    except UnicodeDecodeError:
        content, binary = "", True
    return {
        "content": None if binary else content,
        "too_large": False,
        "binary": binary,
        "lfs": None if binary else lfs_pointer(content),
    }


@router.get("/{repository_id}/contents")
def contents(
    repository_id: int,
    request: Request,
    actor: Authenticated,
    db: Database,
    ref: Ref = "main",
    path: GitPath = "",
    page: Annotated[int, Query(ge=1)] = 1,
):
    visible_repository(db, repository_id, actor.user.id)
    directory = repository_directory(
        request.app.state.settings.git_repositories_root, repository_id
    )
    if not branch_list(directory)["items"]:
        return {"type": "empty", "path": "", "ref": ref}
    commit, oid = resolve(directory, ref, path)
    kind = git(directory, "cat-file", "-t", oid, missing=True).decode().strip()
    size = int(git(directory, "cat-file", "-s", oid))
    result = {"ref": ref, "commit": commit, "path": path, "size": size, "type": kind}
    if kind == "tree":
        if size > TREE_BYTES:
            raise HTTPException(413, "此目录过大，请通过 Git 客户端查看")
        entries = []
        for row in git(directory, "ls-tree", "-z", "-l", oid).split(b"\0"):
            if not row:
                continue
            meta, raw_name = row.split(b"\t", 1)
            mode, entry_type, object_id, object_size = meta.decode().split()
            name = raw_name.decode("utf-8", errors="replace")
            entries.append(
                {
                    "name": name,
                    "path": f"{path}/{name}" if path else name,
                    "type": "submodule"
                    if entry_type == "commit"
                    else "symlink"
                    if mode == "120000"
                    else entry_type,
                    "oid": object_id,
                    "size": int(object_size) if object_size != "-" else None,
                }
            )
        entries.sort(key=lambda entry: (entry["type"] != "tree", entry["name"].casefold()))
        # Select from the complete directory before pagination, at the same commit.
        # A README symlink must never be followed or rendered as a document.
        readmes = [
            entry
            for entry in entries
            if entry["type"] == "blob" and entry["name"].lower() in README_NAMES
        ]
        readmes.sort(key=lambda entry: (README_NAMES.index(entry["name"].lower()), entry["name"]))
        readme = readmes[0] if readmes else None
        return {
            **result,
            "entries": entries[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
            "total": len(entries),
            "page": page,
            "page_size": PAGE_SIZE,
            "readme": {**readme, **preview_blob(directory, readme["oid"], readme["size"])}
            if readme
            else None,
        }
    if kind != "blob":
        return {**result, "type": "submodule", "oid": oid}
    return {**result, **preview_blob(directory, oid, size)}


@router.get("/{repository_id}/image")
def image(
    repository_id: int,
    request: Request,
    actor: Authenticated,
    db: Database,
    ref: Ref = "main",
    path: GitPath = "",
):
    """Bounded raster images for Markdown; never serve active repository content."""
    visible_repository(db, repository_id, actor.user.id)
    directory = repository_directory(
        request.app.state.settings.git_repositories_root, repository_id
    )
    _, oid = resolve(directory, ref, path)
    if git(directory, "cat-file", "-t", oid).strip() != b"blob":
        raise HTTPException(404, "图片不存在")
    if int(git(directory, "cat-file", "-s", oid)) > 5 * 1024 * 1024:
        raise HTTPException(413, "图片超过 5 MiB")
    data = git(directory, "cat-file", "blob", oid)
    mime = (
        "image/png"
        if data.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if data.startswith(b"\xff\xd8\xff")
        else "image/gif"
        if data.startswith((b"GIF87a", b"GIF89a"))
        else "image/webp"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        else None
    )
    if mime is None:
        raise HTTPException(415, "仅支持 PNG、JPEG、GIF 和 WebP 图片")
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
