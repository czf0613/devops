"""GitHub-style native steps and file commands; credentials stay outside build env."""

import hashlib
import os
import re
from pathlib import Path

from devops_runner.environment import capabilities
from devops_runner.network import artifact_client


def expand(value, context):
    if isinstance(value, dict):
        return {k: expand(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, context) for v in value]
    if not isinstance(value, str):
        return value

    def resolve(match):
        key = match[1].strip()
        if not re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[\w-]+)+", key):
            raise ValueError("暂不支持此表达式")
        result = context
        for part in key.split("."):
            if not isinstance(result, dict) or part not in result:
                raise ValueError("表达式字段不存在")
            result = result[part]
        return str(result).lower() if isinstance(result, bool) else str(result)

    result = re.sub(r"\$\{\{\s*(.*?)\s*\}\}", resolve, value)
    if "${{" in result:
        raise ValueError("表达式格式无效")
    return result


def relative_path(workspace, value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or ":" in value or "\\" in value:
        raise ValueError("路径必须位于工作目录内")
    path = workspace / path
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("路径超出工作目录")
    return path


def file_values(path):
    if path.stat().st_size > 65536:
        raise ValueError("工作流文件命令超过容量限制")
    lines = iter(path.read_text(encoding="utf-8-sig").splitlines())
    values = {}
    for line in lines:
        if not line:
            continue
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, delimiter = line.split("<<", 1)
            chunks = []
            for content in lines:
                if content == delimiter:
                    break
                chunks.append(content)
            else:
                raise ValueError("文件命令缺少结束分隔符")
            values[key] = "\n".join(chunks)
        elif "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        else:
            raise ValueError("文件命令格式无效")
    return values


class WorkflowSteps:
    def __init__(self, execution):
        self.execution = execution
        self.executor = execution.executor
        self.payload = execution.payload
        caps = capabilities([])
        self.context = {
            **self.payload.get("context", {}),
            "steps": {},
            "runner": {
                "os": {"macos": "macOS", "windows": "Windows", "linux": "Linux"}[caps["os"]],
                "arch": caps["arch"].upper(),
                "temp": str(self.executor.root / "temp"),
            },
        }
        self.context.setdefault("github", {})["workspace"] = str(self.executor.workspace)
        self.environment = {}
        self.paths = []

    def client(self):
        return artifact_client(
            self.execution.agent.server,
            timeout=120,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + self.payload["token"]},
        )

    async def upload(self, options):
        name = str(options.get("name", "artifact"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", name):
            raise ValueError("产物名称无效")
        patterns = str(options["path"]).splitlines()
        for pattern in patterns:
            relative_path(self.executor.workspace, pattern)
        files = self.executor.collect(patterns)
        if not files:
            if options.get("if-no-files-found", "warn") == "error":
                raise RuntimeError("没有匹配的产物")
            await self.execution.log("没有匹配的产物文件。\n")
            return
        roots = []
        for pattern in patterns:
            prefix = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
            candidate = self.executor.workspace / prefix
            roots.append(candidate if candidate.is_dir() else candidate.parent)
        base = Path(os.path.commonpath(roots))
        async with self.client() as client:
            for _, path in files:
                if path.stat().st_size > self.payload["artifact_limit"]:
                    raise RuntimeError("产物超过容量限制")

                async def chunks(source=path):
                    with source.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            yield chunk

                response = await client.put(
                    self.execution.agent.server + f"/api/ci/artifacts/{self.execution.id}",
                    params={"name": f"{name}/{path.relative_to(base).as_posix()}"},
                    content=chunks(),
                )
                response.raise_for_status()
        await self.execution.log(f"已上传 {len(files)} 个产物文件。\n")

    async def download(self, options):
        wanted, found = str(options.get("name", "")), False
        base = relative_path(self.executor.workspace, str(options.get("path", ".")))
        async with self.client() as client:
            for artifact in self.payload.get("downloads", []):
                group, separator, tail = artifact["name"].partition("/")
                if wanted and group != wanted:
                    continue
                found = True
                relative = tail if wanted and separator else artifact["name"]
                target = relative_path(base, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                checksum = hashlib.sha256()
                size = 0
                async with client.stream(
                    "GET", self.execution.agent.server + f"/api/ci/artifacts/{artifact['id']}"
                ) as response:
                    response.raise_for_status()
                    with target.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.payload["artifact_limit"]:
                                raise RuntimeError("产物超过容量限制")
                            checksum.update(chunk)
                            stream.write(chunk)
                if checksum.hexdigest() != artifact["sha256"]:
                    raise RuntimeError("依赖产物校验失败")
        if wanted and not found:
            raise RuntimeError("指定产物不存在；下载仅允许来自 needs 中的任务")

    async def run(self, raw, index):
        values = {**self.payload["spec"]["env"], **self.environment}
        context = {**self.context, "env": {**os.environ, **values}}
        values = expand(values, context)
        step = expand(raw, {**context, "env": {**os.environ, **values}})
        uses, options = step.get("uses", ""), step.get("options", {})
        if uses.startswith("actions/checkout@"):
            await self.executor.checkout(
                self.execution.agent.server + self.payload["source_path"],
                self.payload["source_ref"],
                self.payload["sha"],
                self.payload["token"],
                depth=int(options.get("fetch-depth", 1)),
                lfs=options.get("lfs", False) in (True, "true"),
            )
            return 0
        if uses.startswith("actions/upload-artifact@"):
            await self.upload(options)
            return 0
        if uses.startswith("actions/download-artifact@"):
            await self.download(options)
            return 0
        if uses:
            raise RuntimeError("当前 worker 不支持此 Action")
        files = {}
        for key in ("ENV", "OUTPUT", "PATH", "STEP_SUMMARY"):
            path = self.executor.root / f"step-{index}-{key}"
            path.write_text("", encoding="utf-8")
            files[f"GITHUB_{key}"] = str(path)
        github = self.context["github"]
        variables = {"GITHUB_" + key.upper(): str(value) for key, value in github.items()}
        variables.update(
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_OS": self.context["runner"]["os"],
                "RUNNER_ARCH": self.context["runner"]["arch"],
                "RUNNER_TEMP": self.context["runner"]["temp"],
            }
        )
        Path(variables["RUNNER_TEMP"]).mkdir(exist_ok=True)
        if self.paths:
            values["PATH"] = os.pathsep.join(
                [*self.paths, values.get("PATH", os.environ.get("PATH", ""))]
            )
        code = await self.executor.execute(step, index, {**values, **variables, **files})
        exported = file_values(Path(files["GITHUB_ENV"]))
        if any(
            k.startswith(("GIT_", "GITHUB_", "RUNNER_", "DEVOPS_"))
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
            for k in exported
        ):
            raise ValueError("不能覆盖 CI 内部变量")
        self.environment.update(exported)
        path_file = Path(files["GITHUB_PATH"])
        if path_file.stat().st_size > 65536:
            raise ValueError("PATH 文件命令过大")
        self.paths = list(reversed(path_file.read_text().splitlines())) + self.paths
        if step.get("id"):
            self.context["steps"][step["id"]] = {
                "outputs": file_values(Path(files["GITHUB_OUTPUT"])),
                "outcome": "failure" if code else "success",
            }
        return code
