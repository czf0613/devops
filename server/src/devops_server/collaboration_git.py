"""Bounded native Git operations without a checkout or repository-controlled configuration."""

import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from devops_server.repository_code import repository_directory

SHA = re.compile(r"[0-9a-f]{40}\Z")
SHORT_SHA = re.compile(r"[0-9a-fA-F]{7,40}\Z")
MAX_FILES = 2000
MAX_COMMITS = 1000


class GitFailure(Exception):
    def __init__(self, message: str = "Git 操作失败，请重试", *, status=503):
        self.message = message
        self.status = status


def run(
    directory: Path | None,
    *args: str,
    content: bytes | None = None,
    accepted: tuple[int, ...] = (0,),
    timeout: float = 20,
    limit: int = 4 * 1024 * 1024,
    environment: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": "DevOps",
        "GIT_AUTHOR_EMAIL": "noreply@devops.invalid",
        "GIT_COMMITTER_NAME": "DevOps",
        "GIT_COMMITTER_EMAIL": "noreply@devops.invalid",
        **(environment or {}),
    }
    command = ["git", "--no-pager", "--literal-pathspecs"]
    if directory is not None:
        command.append(f"--git-dir={directory}")
    command.extend(
        [
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.useReplaceRefs=false",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "diff.external=",
            "-c",
            "diff.trustExitCode=false",
            *args,
        ]
    )
    # Input is bounded by callers. A regular temporary file avoids pipe input deadlocks.
    with tempfile.TemporaryFile() as stdin:
        if content:
            stdin.write(content)
            stdin.seek(0)
        try:
            with subprocess.Popen(
                command,
                env=env,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            ) as process:
                output = bytearray()
                count = 0
                deadline = time.monotonic() + timeout
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        selector.register(process.stderr, selectors.EVENT_READ)
                        while selector.get_map():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise GitFailure("Git 操作超时，请稍后重试")
                            for key, _ in selector.select(min(remaining, 0.2)):
                                chunk = os.read(key.fd, 65536)
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                count += len(chunk)
                                if count > limit:
                                    raise GitFailure("变更过大，超过单次读取上限", status=413)
                                if key.fileobj is process.stdout:
                                    output.extend(chunk)
                    code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except BaseException:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
        except OSError, ValueError, subprocess.SubprocessError:
            raise GitFailure() from None
    if code not in accepted:
        raise GitFailure()
    return code, bytes(output)


def text_git(directory: Path, *args: str, **kwargs) -> str:
    return run(directory, *args, **kwargs)[1].decode("utf-8", errors="replace").strip()


@contextmanager
def isolated(root: str, repository_id: int) -> Iterator[tuple[Path, Path]]:
    """Use only source objects, never its config, hooks, refs expressions or working files."""
    source = repository_directory(root, repository_id)
    objects = source / "objects"
    if objects.is_symlink() or not objects.is_dir():
        raise GitFailure()
    with tempfile.TemporaryDirectory(prefix="devops-git-") as temporary:
        scratch = Path(temporary) / "objects.git"
        run(None, "init", "--bare", "--template=", str(scratch))
        # Alternates are trusted server paths, not submitted Git paths.
        (scratch / "objects/info/alternates").write_text(str(objects.resolve()) + "\n")
        yield scratch, source


def branch_sha(source: Path, branch: str) -> str:
    if not branch or len(branch) > 1024 or "\0" in branch:
        raise GitFailure("分支名称无效", status=422)
    code, _ = run(None, "check-ref-format", f"refs/heads/{branch}", accepted=(0, 1))
    if code:
        raise GitFailure("分支名称无效", status=422)
    # Only this narrow ref lookup reads the source repository; no object transforms run.
    code, result = run(
        source, "show-ref", "--verify", "--hash", f"refs/heads/{branch}", accepted=(0, 1, 128)
    )
    sha = result.decode().strip()
    if code or not SHA.fullmatch(sha):
        raise GitFailure("分支不存在", status=404)
    return sha


def resolve_commit(root: str, repository_id: int, value: str) -> str:
    if not SHORT_SHA.fullmatch(value):
        raise GitFailure("提交哈希无效", status=422)
    with isolated(root, repository_id) as (scratch, source):
        ids = text_git(scratch, "rev-parse", f"--disambiguate={value.lower()}").splitlines()
        if (
            len(ids) != 1
            or not SHA.fullmatch(ids[0])
            or text_git(scratch, "cat-file", "-t", ids[0]) != "commit"
        ):
            raise GitFailure("提交不存在或短哈希不唯一", status=404)
        sha = ids[0]
        # Native traversal avoids spawning a process per branch for each reference.
        reachable = text_git(
            source,
            "for-each-ref",
            f"--contains={sha}",
            "--count=1",
            "--format=%(objectname)",
            "refs/heads/",
            "refs/tags/",
            "refs/devops/pulls/",
            timeout=5,
        )
        if reachable:
            return sha
    raise GitFailure("提交不在可访问的仓库历史中", status=404)


def merge_tree(scratch: Path, base: str, head: str) -> dict:
    code, data = run(
        scratch,
        "merge-tree",
        "--write-tree",
        "--name-only",
        "-z",
        base,
        head,
        accepted=(0, 1),
        timeout=45,
    )
    entries = data.split(b"\0")
    tree = entries[0].decode("ascii", errors="replace")
    if not SHA.fullmatch(tree):
        raise GitFailure("当前 Git 不支持安全的合并检测")
    conflicts = []
    if code == 1:
        for entry in entries[1:]:
            if not entry:
                break
            conflicts.append(entry.decode("utf-8", errors="replace"))
    return {
        "status": "clean" if code == 0 else "conflict",
        "tree": tree,
        "conflicts": conflicts[:MAX_FILES],
        "truncated": len(conflicts) > MAX_FILES,
    }


def commit_tree(
    scratch: Path,
    tree: str,
    parents: list[str],
    message: str,
    *,
    environment: dict[str, str] | None = None,
) -> str:
    arguments = ["commit-tree", tree]
    for parent in parents:
        arguments.extend(["-p", parent])
    return text_git(scratch, *arguments, content=message.encode(), environment=environment)


def rebased(scratch: Path, base: str, head: str) -> dict:
    # Recreate patches with merge-tree's explicit merge base, without checkout/cherry-pick.
    rows = text_git(
        scratch,
        "rev-list",
        "--reverse",
        "--topo-order",
        "--no-merges",
        f"--max-count={MAX_COMMITS + 1}",
        f"{base}..{head}",
    ).splitlines()
    if len(rows) > MAX_COMMITS:
        raise GitFailure("提交过多，请在本地 rebase 后重试", status=413)
    current = base
    deadline = time.monotonic() + 75
    for sha in rows:
        if time.monotonic() >= deadline:
            raise GitFailure("提交重放超时，请在本地 rebase 后重试")
        parents = text_git(scratch, "rev-list", "--parents", "-n", "1", sha).split()
        if len(parents) != 2:
            raise GitFailure("无法自动重放无父提交，请先在本地整理分支", status=409)
        code, data = run(
            scratch,
            "merge-tree",
            "--write-tree",
            "--name-only",
            "-z",
            f"--merge-base={parents[1]}",
            current,
            sha,
            accepted=(0, 1),
            timeout=min(45, max(1, deadline - time.monotonic())),
        )
        entries = data.split(b"\0")
        tree = entries[0].decode("ascii", errors="replace")
        if not SHA.fullmatch(tree):
            raise GitFailure("Git 不支持按提交重放，请升级 Git")
        if code:
            conflicts = []
            for entry in entries[1:]:
                if not entry:
                    break
                conflicts.append(entry.decode("utf-8", errors="replace"))
            return {"status": "conflict", "conflicts": conflicts, "commit": sha}
        info = run(scratch, "show", "-s", "--format=%an%x00%ae%x00%aI%x00%B", sha)[1]
        name, email, date, message = info.decode("utf-8", errors="replace").split("\0", 3)
        # Empty patches are intentionally dropped, matching normal rebase behavior.
        if tree != text_git(scratch, "rev-parse", f"{current}^{{tree}}"):
            current = commit_tree(
                scratch,
                tree,
                [current],
                message,
                environment={
                    "GIT_AUTHOR_NAME": name,
                    "GIT_AUTHOR_EMAIL": email,
                    "GIT_AUTHOR_DATE": date,
                },
            )
    return {"status": "clean", "commit": current, "conflicts": []}


def check_merge(root: str, repository_id: int, base: str, head: str, method: str) -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise GitFailure("提交无效", status=422)
    with isolated(root, repository_id) as (scratch, _):
        result = (
            rebased(scratch, base, head) if method == "rebase" else merge_tree(scratch, base, head)
        )
        return {key: value for key, value in result.items() if key not in {"tree", "commit"}}


def compare(
    root: str,
    repository_id: int,
    base: str,
    head: str,
    *,
    path: str | None = None,
    page: int = 1,
    commit_view=False,
) -> dict:
    with isolated(root, repository_id) as (scratch, _):
        if not SHA.fullmatch(base) or not SHA.fullmatch(head):
            raise GitFailure("提交无效", status=422)
        ancestor = base if commit_view else text_git(scratch, "merge-base", base, head)
        if path is not None:
            if "\0" in path or len(path) > 4096:
                raise GitFailure("文件路径无效", status=422)
            paths = [path]
            # Include the old name so a per-file patch retains rename semantics.
            names = run(
                scratch,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--find-renames",
                "-z",
                ancestor,
                head,
                "--",
            )[1].split(b"\0")
            index = 0
            while index + 1 < len(names) and names[index]:
                status = names[index]
                old_name = names[index + 1].decode("utf-8", errors="replace")
                index += 2
                if status.startswith((b"R", b"C")):
                    new_name = names[index].decode("utf-8", errors="replace")
                    index += 1
                    if new_name == path and old_name != path:
                        paths.insert(0, old_name)
                        break
            patch = run(
                scratch,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--unified=3",
                "--find-renames",
                ancestor,
                head,
                "--",
                *paths,
                limit=2 * 1024 * 1024,
            )[1].decode("utf-8", errors="replace")
            return {
                "base_sha": base,
                "head_sha": head,
                "merge_base": ancestor,
                "path": path,
                "patch": patch,
            }
        # --raw includes renames and blob IDs; --numstat supplies bounded file statistics.
        raw = run(
            scratch,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--raw",
            "--no-abbrev",
            "--find-renames",
            "-z",
            ancestor,
            head,
            "--",
        )[1].split(b"\0")
        files = []
        index = 0
        while index + 1 < len(raw) and raw[index]:
            metadata = raw[index].decode().split()
            old_path = raw[index + 1].decode("utf-8", errors="replace")
            index += 2
            new_path = old_path
            if metadata[4].startswith(("R", "C")):
                new_path = raw[index].decode("utf-8", errors="replace")
                index += 1
            files.append(
                {
                    "path": new_path,
                    "old_path": old_path,
                    "status": metadata[4],
                    "old_sha": metadata[2],
                    "new_sha": metadata[3],
                }
            )
            if len(files) > MAX_FILES:
                raise GitFailure("变更文件超过 2000 个，请拆分 PR", status=413)
        statistics = run(
            scratch,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            "--find-renames",
            "-z",
            ancestor,
            head,
            "--",
        )[1].split(b"\0")
        stats = {}
        index = 0
        while index < len(statistics) and statistics[index]:
            added, removed, name = statistics[index].split(b"\t", 2)
            index += 1
            if not name:
                name = statistics[index + 1]
                index += 2
            stats[name.decode("utf-8", errors="replace")] = {
                "additions": int(added) if added != b"-" else 0,
                "deletions": int(removed) if removed != b"-" else 0,
                "binary": added == b"-",
            }
        for entry in files:
            entry.update(
                stats.get(entry["path"], {"additions": 0, "deletions": 0, "binary": False})
            )
        commits = text_git(
            scratch,
            "log",
            "--no-show-signature",
            "--format=%H%x00%an%x00%aI%x00%s",
            f"--max-count={MAX_COMMITS + 1}",
            f"{base}..{head}",
            "--",
        ).splitlines()
        return {
            "base_sha": base,
            "head_sha": head,
            "merge_base": ancestor,
            "files": files[(page - 1) * 100 : page * 100],
            "total": len(files),
            "additions": sum(entry["additions"] for entry in files),
            "deletions": sum(entry["deletions"] for entry in files),
            "page": page,
            "page_size": 100,
            "commits": [
                dict(zip(("sha", "author", "date", "title"), line.split("\0", 3), strict=True))
                for line in commits[:MAX_COMMITS]
            ],
            "commits_truncated": len(commits) > MAX_COMMITS,
        }


def preserve(source: Path, item_id: int, base: str, head: str) -> None:
    # Immutable snapshots survive force pushes and source-branch deletion.
    for side, sha in (("base", base), ("head", head)):
        run(source, "update-ref", f"refs/devops/pulls/{item_id}/{side}/{sha}", sha)


def prepare_merge(
    root: str, repository_id: int, base: str, head: str, method: str, message: str
) -> str:
    with isolated(root, repository_id) as (scratch, source):
        result = (
            rebased(scratch, base, head) if method == "rebase" else merge_tree(scratch, base, head)
        )
        if result["status"] != "clean":
            raise GitFailure("分支存在合并冲突", status=409)
        sha = (
            result["commit"]
            if method == "rebase"
            else commit_tree(
                scratch, result["tree"], [base, head] if method == "merge" else [base], message
            )
        )
        # Publish only hash-named objects created in this trusted scratch repository.
        # Exclusive writes preserve existing content-addressed objects and run no Git hooks.
        total = 0
        for bucket in (scratch / "objects").iterdir():
            if not re.fullmatch(r"[0-9a-f]{2}", bucket.name) or not bucket.is_dir():
                continue
            destination = source / "objects" / bucket.name
            if destination.is_symlink():
                raise GitFailure()
            destination.mkdir(mode=0o2770, exist_ok=True)
            for obj in bucket.iterdir():
                if (
                    not re.fullmatch(r"[0-9a-f]{38}", obj.name)
                    or not obj.is_file()
                    or obj.is_symlink()
                ):
                    raise GitFailure()
                total += obj.stat().st_size
                if total > 128 * 1024 * 1024:
                    raise GitFailure("合并产生的对象过大，请在本地合并后推送", status=413)
                # Publish atomically: readers must never see a partially written object.
                with tempfile.NamedTemporaryFile(
                    dir=destination, prefix="tmp_merge_", delete=False
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    try:
                        with obj.open("rb") as stream:
                            shutil.copyfileobj(stream, temporary)
                        temporary.flush()
                        os.fsync(temporary.fileno())
                        os.chmod(temporary_path, 0o440)
                        try:
                            os.link(temporary_path, destination / obj.name)
                        except FileExistsError:
                            pass
                    finally:
                        temporary_path.unlink(missing_ok=True)
        return sha


def merge_marker(source: Path, intent_id: int) -> str | None:
    code, value = run(
        source,
        "show-ref",
        "--verify",
        "--hash",
        f"refs/devops/merges/{intent_id}",
        accepted=(0, 1, 128),
    )
    return value.decode().strip() if code == 0 else None


def apply_merge(
    source: Path, intent_id: int, base_ref: str, head_ref: str, base: str, head: str, result: str
):
    # Both refs are locked and compared by native Git. A push racing this transaction
    # either wins first (and this fails), or observes the new base afterward.
    transaction = (
        f"start\nverify refs/heads/{head_ref} {head}\n"
        f"update refs/heads/{base_ref} {result} {base}\n"
        f"create refs/devops/merges/{intent_id} {result}\nprepare\ncommit\n"
    )
    run(source, "update-ref", "--stdin", content=transaction.encode())


def api_failure(error: GitFailure) -> HTTPException:
    return HTTPException(error.status, error.message)
