"""Normalize supported GitHub workflow syntax into the immutable executor contract."""

import itertools
import re

from devops_server.ci_environment import runs_on


class WorkflowError(ValueError):
    """A safe diagnostic: never contains scripts, values, or credential material."""


def fields(value, allowed, section):
    if not isinstance(value, dict) or set(value) - set(allowed.split()):
        raise WorkflowError(f"{section} 含有暂不支持的配置项，请查看 CI 兼容性说明")


def strings(value, section):
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise WorkflowError(f"{section} 需要字符串或字符串列表")
    return values


def replace(value, context):
    if isinstance(value, dict):
        return {k: replace(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [replace(v, context) for v in value]
    if not isinstance(value, str):
        return value

    def resolve(match):
        key = match[1].strip()
        if key.startswith(("matrix.", "inputs.")):
            if key not in context:
                raise WorkflowError("表达式引用了未定义的 matrix 或 inputs 字段")
            return context[key]
        return match[0]

    pattern = r"\$\{\{\s*([^{}]+?)\s*\}\}"
    if match := re.fullmatch(pattern, value):
        return resolve(match)
    return re.sub(pattern, lambda m: str(resolve(m)), value)


def env(value):
    if not isinstance(value, dict):
        raise WorkflowError("env 需要键值对象")
    if any(not isinstance(v, (str, int, float, bool)) for v in value.values()):
        raise WorkflowError("env 值必须是字符串、数字或布尔值")
    return {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in value.items()}


def matrix_rows(strategy):
    fields(strategy, "matrix fail-fast", "strategy")
    if not isinstance(strategy.get("fail-fast", True), bool):
        raise WorkflowError("strategy.fail-fast 需要布尔值")
    matrix = strategy.get("matrix", {})
    if not isinstance(matrix, dict):
        raise WorkflowError("matrix 需要静态键值对象")
    axes = {k: v for k, v in matrix.items() if k not in ("include", "exclude")}
    if any(not isinstance(v, list) or not v for v in axes.values()):
        raise WorkflowError("matrix 每个维度需要非空列表")
    count = 1
    for values in axes.values():
        count *= len(values)
    if count > 20:
        raise WorkflowError("矩阵展开后不能超过 20 个任务")
    rows = (
        [dict(zip(axes, values, strict=True)) for values in itertools.product(*axes.values())]
        if axes
        else []
    )
    for exclusion in matrix.get("exclude", []):
        if not isinstance(exclusion, dict):
            raise WorkflowError("matrix.exclude 格式无效")
        rows = [r for r in rows if not all(r.get(k) == v for k, v in exclusion.items())]
    originals = [dict(row) for row in rows]
    for addition in matrix.get("include", []):
        if not isinstance(addition, dict):
            raise WorkflowError("matrix.include 格式无效")
        matched = False
        for row, original in zip(rows, originals, strict=False):
            if all(k not in original or original[k] == v for k, v in addition.items()):
                row.update(addition)
                matched = True
        if not matched:
            rows.append(dict(addition))
    return rows if matrix else [{}]


def normalize(data, *, inputs=None):
    fields(data, "name on env defaults jobs permissions", "工作流")
    # permissions never grants GitHub credentials. Only read/none has a local equivalent.
    permissions = data.get("permissions", {})
    if permissions not in ("read-all", {}) and (
        not isinstance(permissions, dict)
        or any(v not in ("read", "none") for v in permissions.values())
    ):
        raise WorkflowError("当前 CI 只提供本仓库只读凭据，不支持 permissions 写权限")
    triggers = data.get("on")
    if isinstance(triggers, (str, list)):
        triggers = dict.fromkeys(strings(triggers, "on"), {})
    fields(triggers, "push pull_request workflow_dispatch", "on")
    triggers = {k: v or {} for k, v in triggers.items()}
    definitions = {}
    for event, options in triggers.items():
        fields(
            options,
            "inputs" if event == "workflow_dispatch" else "branches branches-ignore",
            f"on.{event}",
        )
        if "branches" in options and "branches-ignore" in options:
            raise WorkflowError("同一事件不能同时使用 branches 和 branches-ignore")
        for key in ("branches", "branches-ignore"):
            if key in options:
                options[key] = strings(options[key], f"on.{event}.{key}")
        if event == "workflow_dispatch":
            definitions = options.get("inputs", {})
    if not isinstance(definitions, dict) or len(definitions) > 10:
        raise WorkflowError("workflow_dispatch.inputs 格式无效")
    values = dict(inputs or {})
    if values.keys() - definitions.keys():
        raise WorkflowError("手动运行包含未定义的 inputs")
    for key, definition in definitions.items():
        fields(definition, "description type required default options", "workflow_dispatch.inputs")
        kind = definition.get("type", "string")
        if kind not in ("string", "boolean", "number", "choice"):
            raise WorkflowError("手动输入类型暂不支持")
        value = values.get(key, definition.get("default", False if kind == "boolean" else ""))
        if inputs is not None and definition.get("required") and value == "":
            raise WorkflowError("请填写必填的工作流输入")
        if kind == "boolean" and not isinstance(value, bool):
            raise WorkflowError("工作流布尔输入无效")
        if kind == "choice" and value != "" and value not in definition.get("options", []):
            raise WorkflowError("工作流选项输入无效")
        if (
            kind == "number"
            and value != ""
            and (isinstance(value, bool) or not isinstance(value, (int, float)))
        ):
            raise WorkflowError("工作流数字输入无效")
        values[key] = value
    context = {f"inputs.{k}": v for k, v in values.items()}
    defaults = data.get("defaults", {})
    fields(defaults, "run", "defaults")
    fields(defaults.get("run", {}), "shell working-directory", "defaults.run")
    if not isinstance(data.get("jobs"), dict) or not data["jobs"]:
        raise WorkflowError("工作流必须包含 jobs")
    expanded, names = {}, {}
    for name, job in data["jobs"].items():
        fields(
            job, "name runs-on needs steps timeout-minutes env defaults strategy permissions", "job"
        )
        if job.get("permissions", {}) not in ({}, "read-all"):
            raise WorkflowError("请在工作流层设置只读 permissions")
        variants = matrix_rows(job.get("strategy", {}))
        names[name] = []
        for index, row in enumerate(variants):
            identity = name if len(variants) == 1 else f"{name}-{index + 1}"
            if identity in expanded or (identity in data["jobs"] and identity != name):
                raise WorkflowError("矩阵展开后的任务名称冲突")
            names[name].append(identity)
            spec = replace(job, {**context, **{f"matrix.{k}": v for k, v in row.items()}})
            job_defaults = spec.get("defaults", {})
            fields(job_defaults, "run", "job.defaults")
            fields(job_defaults.get("run", {}), "shell working-directory", "job.defaults.run")
            run_defaults = {**defaults.get("run", {}), **job_defaults.get("run", {})}
            steps = []
            for step in spec.get("steps", []):
                fields(
                    step,
                    "id name run uses with shell env working-directory if continue-on-error",
                    "step",
                )
                condition = step.get("if", "success()")
                if not isinstance(condition, str):
                    raise WorkflowError("step.if 格式无效")
                condition = condition.removeprefix("${{").removesuffix("}}").strip()
                if condition not in ("success()", "always()", "failure()", "cancelled()"):
                    raise WorkflowError(
                        "step.if 当前支持 success() / always() / failure() / cancelled()"
                    )
                uses, options = step.get("uses", ""), step.get("with", {})
                if uses:
                    match = re.fullmatch(
                        r"actions/(checkout|upload-artifact|download-artifact)@"
                        r"(v\d+(?:\.\d+)*|[a-f0-9]{40})",
                        uses,
                    )
                    if not match:
                        raise WorkflowError(
                            "暂不支持此 uses Action；当前支持 actions/checkout、"
                            "upload-artifact、download-artifact"
                        )
                    supported = {
                        "checkout": "fetch-depth lfs persist-credentials",
                        "upload-artifact": "name path if-no-files-found",
                        "download-artifact": "name path",
                    }[match[1]]
                    fields(options, supported, "step.with")
                    if match[1] == "upload-artifact" and not options.get("path"):
                        raise WorkflowError("upload-artifact 需要 with.path")
                    if match[1] == "checkout" and options.get("persist-credentials") is True:
                        raise WorkflowError("不能将源码凭据保存到构建步骤")
                elif options:
                    raise WorkflowError("run 步骤不接受 with")
                steps.append(
                    {
                        "id": step.get("id", ""),
                        "name": step.get(
                            "name", uses or str(step.get("run", "")).split("\n")[0][:100]
                        ),
                        "run": step.get("run", ""),
                        "uses": uses,
                        "options": options,
                        "shell": step.get("shell", run_defaults.get("shell")),
                        "working_directory": step.get(
                            "working-directory", run_defaults.get("working-directory", "")
                        ),
                        "env": env(step.get("env", {})),
                        "condition": condition,
                        "continue_on_error": step.get("continue-on-error", False),
                    }
                )
            needs = strings(spec.get("needs", []), "needs")
            expanded[identity] = {
                "environment": runs_on(spec.get("runs-on")),
                "needs": needs,
                "steps": steps,
                "timeout_minutes": spec.get("timeout-minutes", 30),
                "env": {**env(replace(data.get("env", {}), context)), **env(spec.get("env", {}))},
                "display_name": str(spec.get("name", name))
                + (f" ({', '.join(str(v) for v in row.values())})" if row else ""),
                "matrix": row,
                "matrix_group": name if "matrix" in job.get("strategy", {}) else "",
                "fail_fast": job.get("strategy", {}).get("fail-fast", True),
                "runtime": 2,
            }
    for spec in expanded.values():
        if any(dependency not in names for dependency in spec["needs"]):
            raise WorkflowError("needs 引用了不存在的任务")
        spec["needs"] = [variant for dependency in spec["needs"] for variant in names[dependency]]
        spec["download_artifacts"] = (
            spec["needs"]
            if any(s["uses"].startswith("actions/download-artifact@") for s in spec["steps"])
            else []
        )
    return {
        "version": 1,
        "format": "github",
        "name": data.get("name", "CI"),
        "on": ["manual" if e == "workflow_dispatch" else e for e in triggers],
        "triggers": triggers,
        "inputs": values,
        "jobs": expanded,
    }
