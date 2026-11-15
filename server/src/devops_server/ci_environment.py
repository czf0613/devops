"""GitHub runs-on labels and explicit host version constraints.

Version constraints select a build host, not the minimum runtime of its output.
Unknown facts never satisfy a version constraint.
"""

import re


def version_tuple(value):
    match = re.match(r"\d+(?:\.\d+)*", value or "")
    return tuple(int(p) for p in match.group().split(".")) if match else ()


def version_matches(actual, expected, operator):
    left, right = version_tuple(actual), version_tuple(expected)
    if not left or not right:
        return False
    if operator == "=":
        return left[: len(right)] == right
    length = max(len(left), len(right))
    left += (0,) * (length - len(left))
    right += (0,) * (length - len(right))
    return {">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right}[
        operator
    ]


def runs_on(value):
    pool = "default"
    if isinstance(value, dict):
        if set(value) - {"group", "labels"}:
            raise ValueError("runs-on 只支持 group 和 labels")
        pool, value = value.get("group", "default"), value.get("labels", ["self-hosted"])
    labels = [value] if isinstance(value, str) else value
    if not isinstance(labels, list) or not labels or len(labels) > 20:
        raise ValueError("runs-on 需要构建机标签或资源池")
    env = {
        "os": None,
        "arch": None,
        "pool": pool,
        "executor": "native",
        "labels": [],
        "constraints": [],
    }

    def set_field(field, value):
        if env[field] is not None and env[field] != value:
            raise ValueError("runs-on 含有互相冲突的系统或架构")
        env[field] = value

    def constraint(field, operator, value):
        env["constraints"].append({"field": field, "operator": operator, "value": value})

    for raw in labels:
        if not isinstance(raw, str) or len(raw) > 100:
            raise ValueError("构建机标签无效")
        label = raw.lower()
        if label == "self-hosted":
            continue
        if label in ("x86", "i386", "i686", "arm", "arm32"):
            raise ValueError("CI 不支持 32 位构建机")
        if label in ("x64", "arm64"):
            set_field("arch", label)
        elif label in ("linux", "macos", "windows"):
            set_field("os", label)
        elif label in ("macos-latest", "windows-latest", "ubuntu-latest"):
            set_field("os", "linux" if label.startswith("ubuntu") else label.split("-")[0])
            if label.startswith("ubuntu"):
                constraint("distribution.id", "is", "ubuntu")
            if label.startswith("windows"):
                constraint("os_version", ">=", "10.0.17763")
        elif match := re.fullmatch(r"macos-(\d+(?:\.\d+)*)(?:-(intel|arm64))?", label):
            set_field("os", "macos")
            constraint("os_version", "=", match[1])
            if match[2]:
                set_field("arch", "x64" if match[2] == "intel" else "arm64")
        elif match := re.fullmatch(r"ubuntu-(\d+\.\d+)(-arm)?", label):
            set_field("os", "linux")
            constraint("distribution.id", "is", "ubuntu")
            constraint("distribution.version", "=", match[1])
            set_field("arch", "arm64" if match[2] else "x64")
        elif label in ("windows-10", "windows-11", "windows-2019", "windows-2022", "windows-2025"):
            set_field("os", "windows")
            if label in ("windows-10", "windows-11"):
                constraint("os_edition", "is", label.replace("-", " ").title())
            else:
                constraint("os_edition", "is", "Windows Server")
                constraint(
                    "os_version",
                    "=",
                    {
                        "windows-2019": "10.0.17763",
                        "windows-2022": "10.0.20348",
                        "windows-2025": "10.0.26100",
                    }[label],
                )
        elif match := re.fullmatch(
            r"(macos|windows|kernel|glibc|musl)(>=|<=|=|>|<)(\d+(?:\.\d+)*)", label
        ):
            field, op, release = match.groups()
            if field in ("glibc", "musl"):
                set_field("os", "linux")
                constraint("libc.name", "is", field)
                field = "libc.version"
            elif field == "kernel":
                set_field("os", "linux")
                field = "kernel_version"
            else:
                set_field("os", field)
                field = "os_version"
            constraint(field, op, release)
        elif label in ("glibc", "musl"):
            set_field("os", "linux")
            constraint("libc.name", "is", label)
        else:
            env["labels"].append(raw)
    return env


def matches(caps, constraints):
    for constraint in constraints:
        actual = caps
        for field in constraint["field"].split("."):
            actual = actual.get(field, "") if isinstance(actual, dict) else ""
        if constraint["operator"] == "is":
            if actual != constraint["value"]:
                return False
        elif not version_matches(actual, constraint["value"], constraint["operator"]):
            return False
    return True
