"""Host facts, collected without executing repository code or reading user configuration."""

import os
import platform
import re
import struct
import subprocess
from importlib.metadata import version


def libc():
    try:
        value = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except ValueError, OSError, AttributeError:
        value = ""
    if value.startswith("glibc "):
        return {"name": "glibc", "version": value.split()[-1]}
    name, release = platform.libc_ver()
    if name in ("glibc", "musl"):
        return {"name": name, "version": release}
    try:
        result = subprocess.run(
            ["ldd", "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        output = result.stdout + result.stderr
        if "musl" in output.lower():
            match = re.search(r"Version\s+(\d+(?:\.\d+)+)", output, re.I)
            return {"name": "musl", "version": match.group(1) if match else ""}
    except OSError, subprocess.SubprocessError:
        pass
    return {"name": "unknown", "version": ""}


def capabilities(labels):
    operating = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = platform.machine().lower()
    architecture = {"aarch64": "arm64", "arm64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(
        machine
    )
    if not operating or not architecture or struct.calcsize("P") != 8:
        raise ValueError("构建机仅支持 macOS / Windows / Linux 的 x64 或 arm64 64 位环境")
    facts = {
        "os_version": "",
        "kernel_version": platform.release(),
        "os_edition": "",
        "distribution": {"id": "", "name": "", "version": ""},
        "libc": {"name": "unknown", "version": ""},
    }
    if operating == "macos":
        facts["os_version"] = platform.mac_ver()[0]
        facts["os_edition"] = "macOS"
    elif operating == "windows":
        release, build, _, _ = platform.win32_ver()
        facts["os_version"] = build
        facts["os_edition"] = release
        # Windows 11 and Server also use NT 10.0; product type is essential.
        import sys

        native = sys.getwindowsversion()
        server = native.product_type != 1
        facts["os_version"] = f"{native.major}.{native.minor}.{native.build}"
        facts["os_edition"] = (
            "Windows Server"
            if server
            else ("Windows 11" if native.build >= 22000 else "Windows 10")
        )
    else:
        try:
            distribution = platform.freedesktop_os_release()
        except OSError:
            distribution = {}
        facts["distribution"] = {
            "id": distribution.get("ID", ""),
            "name": distribution.get("NAME", ""),
            "version": distribution.get("VERSION_ID", ""),
        }
        facts["os_version"] = distribution.get("VERSION_ID", "")
        facts["os_edition"] = distribution.get("PRETTY_NAME", "Linux")
        facts["libc"] = libc()
    return {
        "os": operating,
        "arch": architecture,
        "executors": ["native"],
        "labels": labels,
        "version": version("devops-runner"),
        "runtime": 2,
        **facts,
    }
