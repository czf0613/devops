"""Provision bare repositories using database IDs, never user-controlled paths."""

import os
import shutil
import subprocess
from pathlib import Path


class RepositoryStorageError(Exception):
    pass


def create_bare_repository(root: str, repository_id: int, full_name: str, clone_url: str) -> None:
    base = Path(root)
    directory = base / f"{repository_id}.git"
    created = False
    try:
        if repository_id < 1 or base.is_symlink():
            raise RepositoryStorageError()
        base.mkdir(mode=0o750, parents=True, exist_ok=True)
        # Never adopt or reinitialize an existing directory (including an old pilot repo).
        directory.mkdir(mode=0o750)
        created = True
        environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        commands = [
            [
                "git",
                "init",
                "--bare",
                "--initial-branch=main",
                "--template=",
                "--shared=0660",
                str(directory),
            ],
            ["git", "-C", str(directory), "config", "http.getanyfile", "false"],
            ["git", "-C", str(directory), "config", "http.receivepack", "true"],
        ]
        for command in commands:
            subprocess.run(command, env=environment, check=True, capture_output=True, timeout=5)
        # Build the initial commit directly as objects: no checkout, hooks, user Git
        # configuration, signing keys or real user email addresses are involved.
        environment.update(
            GIT_AUTHOR_NAME="DevOps",
            GIT_AUTHOR_EMAIL="noreply@devops.invalid",
            GIT_COMMITTER_NAME="DevOps",
            GIT_COMMITTER_EMAIL="noreply@devops.invalid",
        )

        def git(*args: str, content: str = "") -> str:
            return subprocess.run(
                ["git", "-C", str(directory), *args],
                input=content,
                text=True,
                env=environment,
                check=True,
                capture_output=True,
                timeout=5,
            ).stdout.strip()

        readme = (
            f"# {full_name}\n\n## 克隆仓库\n\n"
            f"```sh\ngit clone {clone_url}\ncd {full_name.split('/')[-1]}\n```\n\n"
            "默认分支为 `main`。\n\n"
            "## 推送已有的本地仓库\n\n"
            "在已有本地仓库的目录中执行。请先提交或妥善保存工作区改动。"
            "使用 `devops` 作为新远端名，保留原有的 `origin`；"
            "若已存在同名远端，请换一个名称，并相应替换以下命令。\n\n"
            "本站已生成 README 初始提交，首次推送前先合并它，保留两边的历史：\n\n"
            f"```sh\ngit remote add devops {clone_url}\n"
            "git fetch devops\n"
            "git merge --allow-unrelated-histories --no-edit devops/main\n```\n\n"
            "如果本地也有 README.md 等文件并出现合并冲突，先编辑冲突文件，"
            "用 `git add <已解决的文件>` 暂存，再执行 `git commit` 完成合并。"
            "合并成功后，将当前分支推送到本站的 main：\n\n"
            "```sh\ngit push -u devops HEAD:main\n```\n\n"
            "如果 main 已禁止直接推送，改为推送一个新分支，"
            "然后在本站创建以 main 为目标的 PR：\n\n"
            "```sh\ngit push -u devops HEAD:import-existing\n```\n"
        )
        blob = git("hash-object", "-w", "--stdin", content=readme)
        tree = git("mktree", content=f"100644 blob {blob}\tREADME.md\n")
        commit = git("commit-tree", tree, content="Initial commit\n")
        git("update-ref", "refs/heads/main", commit, "0" * 40)
    except OSError, subprocess.SubprocessError, RepositoryStorageError:
        if created:
            # This directory was created exclusively by this call and is not yet committed
            # in PostgreSQL, so no HTTP Git request can have written to it.
            shutil.rmtree(directory, ignore_errors=True)
        raise RepositoryStorageError() from None

    # The caller commits metadata only after provisioning succeeds. A lost DB connection
    # during commit may leave an orphan; leave it for recovery rather than risk deleting
    # a repository whose commit succeeded. IDs are never reused or paths overwritten.
