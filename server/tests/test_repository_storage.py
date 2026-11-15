import os
import re
import shlex
import subprocess

import pytest

from devops_server.repository_storage import create_bare_repository


@pytest.mark.parametrize("existing_readme", [False, True])
def test_readme_import_commands_preserve_both_histories(tmp_path, existing_readme):
    remote = tmp_path / "repos" / "1.git"
    create_bare_repository(str(remote.parent), 1, "owner/example", remote.as_uri())
    local = tmp_path / "existing"
    local.mkdir()
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=local,
            env=environment,
            text=True,
            capture_output=True,
            check=check,
            timeout=10,
        )

    readme = git("--git-dir", str(remote), "show", "main:README.md").stdout
    remote_head = git("--git-dir", str(remote), "rev-parse", "main").stdout.strip()
    git("init", "--initial-branch=existing", "--template=")
    git("remote", "add", "origin", "https://example.invalid/original.git")
    (local / "app.py").write_text("print('existing code')\n")
    if existing_readme:
        (local / "README.md").write_text("# Existing project\n")
    git("add", ".")
    git("commit", "-m", "Existing history")
    local_head = git("rev-parse", "HEAD").stdout.strip()

    commands = re.findall(r"```sh\n(.*?)\n```", readme, re.S)
    for command in commands[1].splitlines():
        args = shlex.split(command)
        assert args.pop(0) == "git"
        result = git(*args, check=False)
        if args[0] == "merge" and existing_readme:
            assert result.returncode == 1
            assert "CONFLICT" in result.stdout
            (local / "README.md").write_text("# Existing project\n\n" + readme)
            git("add", "README.md")
            git("commit", "-m", "Resolve README conflict")
        else:
            assert result.returncode == 0, result.stderr
    git(*shlex.split(commands[2])[1:])
    assert (
        git("remote", "get-url", "origin").stdout.strip() == "https://example.invalid/original.git"
    )
    assert git("branch", "--show-current").stdout.strip() == "existing"
    for commit in (local_head, remote_head):
        git("--git-dir", str(remote), "merge-base", "--is-ancestor", commit, "main")
    assert git("--git-dir", str(remote), "show", "main:app.py").stdout == "print('existing code')\n"
    assert "git push -u devops HEAD:import-existing" in readme
    assert "--force" not in readme
