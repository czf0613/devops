"""Versioned, bounded YAML contract shared with the executor over WebSocket."""

import json
import re
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from devops_server.schemas import Input

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]


class Step(Input):
    name: str = Field(min_length=1, max_length=100)
    run: str = Field(default="", max_length=16000)
    shell: Literal["bash", "sh", "pwsh", "powershell"] | None = None
    uses: str = ""
    options: dict = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    working_directory: str = ""
    condition: Literal["success()", "always()", "failure()", "cancelled()"] = "success()"
    continue_on_error: bool = False
    id: str = ""


class Constraint(Input):
    field: Literal[
        "os_version",
        "os_edition",
        "kernel_version",
        "distribution.id",
        "distribution.version",
        "libc.name",
        "libc.version",
    ]
    operator: Literal["is", "=", ">=", "<=", ">", "<"]
    value: str = Field(min_length=1, max_length=100)


class Environment(Input):
    os: Literal["linux", "macos", "windows"] | None = None
    arch: Literal["arm64", "x64"] | None = None
    executor: Literal["native"] = "native"
    pool: Identifier = "default"
    labels: list[Identifier] = Field(default_factory=list, max_length=20)
    constraints: list[Constraint] = Field(default_factory=list, max_length=30)


class Job(Input):
    environment: Environment
    needs: list[Identifier] = Field(default_factory=list, max_length=20)
    steps: list[Step] = Field(min_length=1, max_length=50)
    timeout_minutes: int = Field(default=30, ge=1, le=240)
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    artifacts: list[str] = Field(default_factory=list, max_length=30)
    download_artifacts: list[Identifier] = Field(default_factory=list, max_length=20)
    runtime: int = 1
    display_name: str = ""
    matrix: dict = Field(default_factory=dict)
    matrix_group: str = ""
    fail_fast: bool = False

    @model_validator(mode="after")
    def safe_inputs(self):
        environments = [self.env, *(step.env for step in self.steps)]
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", k) or len(v) > 4096
            for values in environments
            for k, v in values.items()
        ):
            raise ValueError("环境变量名称或长度无效")
        if any(
            k.startswith(("GIT_", "DEVOPS_", "GITHUB_", "RUNNER_"))
            for values in environments
            for k in values
        ):
            raise ValueError("不能覆盖 CI 内部环境变量")
        for step in self.steps:
            if self.runtime < 2 and step.uses:
                raise ValueError("uses 仅用于 .github/workflows 工作流")
            if not bool(step.run) != bool(step.uses):
                raise ValueError("步骤必须且只能提供 run 或 uses")
            path = step.working_directory.replace("\\", "/")
            if path.startswith("/") or ".." in path.split("/") or ":" in path:
                raise ValueError("working-directory 必须位于工作目录内")
        for path in self.artifacts:
            if (
                not path
                or len(path) > 300
                or path.startswith(("/", "\\"))
                or ".." in path.replace("\\", "/").split("/")
                or ":" in path
                or "\\" in path
            ):
                raise ValueError("产物使用工作目录内的相对路径或 glob")
        if not set(self.download_artifacts) <= set(self.needs):
            raise ValueError("只能下载 needs 指定 Job 的产物")
        return self


class Workflow(Input):
    version: Literal[1]
    name: str = Field(default="CI", min_length=1, max_length=100)
    on: list[Literal["push", "pull_request", "manual"]] = Field(min_length=1, max_length=3)
    branches: list[str] = Field(default_factory=list, max_length=30)
    jobs: dict[Identifier, Job] = Field(min_length=1, max_length=20)
    triggers: dict = Field(default_factory=dict)
    inputs: dict = Field(default_factory=dict)
    format: str = "legacy"

    @model_validator(mode="after")
    def dag(self):
        visited, active = set(), set()

        def visit(name):
            if name in active or name not in self.jobs:
                raise ValueError("Job 依赖不存在或形成循环")
            if name in visited:
                return
            active.add(name)
            for dependency in self.jobs[name].needs:
                visit(dependency)
            active.remove(name)
            visited.add(name)

        for name in self.jobs:
            visit(name)
        return self


def load_yaml(raw: str):
    if len(raw.encode()) > 128 * 1024:
        raise ValueError("CI 配置不能超过 128 KiB")
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_duplicate_keys = False
    # No aliases/anchors: prevent cyclic or exponentially expanded documents.
    try:
        depth = 0
        for token in yaml.scan(raw):
            kind = type(token).__name__
            if kind in {"AliasToken", "AnchorToken", "TagToken"}:
                raise ValueError("CI 配置不支持 YAML 引用或自定义标签")
            if kind in {
                "BlockMappingStartToken",
                "BlockSequenceStartToken",
                "FlowMappingStartToken",
                "FlowSequenceStartToken",
            }:
                depth += 1
            elif kind in {"BlockEndToken", "FlowMappingEndToken", "FlowSequenceEndToken"}:
                depth -= 1
            if depth > 20:
                raise ValueError("CI 配置嵌套过深")
        data = yaml.load(raw)
        if len(json.dumps(data)) > 256 * 1024:
            raise ValueError("CI 配置过大")
    except (YAMLError, RecursionError, TypeError) as exc:
        raise ValueError("CI 配置格式无效") from exc
    return data


def parse_workflow(raw: str, *, github=False, inputs=None) -> Workflow:
    data = load_yaml(raw)
    if github:
        from devops_server.ci_github import normalize

        data = normalize(data, inputs=inputs)
    return Workflow.model_validate(data)
