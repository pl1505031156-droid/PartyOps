"""跨平台运行环境探测与旧 1.4.x 平台字段兼容。"""

from __future__ import annotations

import platform
import sys
from pathlib import Path


CORE_CAPABILITIES = (
    "host",
    "collaboration",
    "database",
    "files",
    "archives",
    "backup",
    "ocr",
)
AI_CAPABILITIES = ("semantic_rerank", "local_llm")


def normalize_architecture(value: str | None = None) -> str:
    """把操作系统架构名称收敛为发布清单使用的名称。"""

    raw = (value or platform.machine()).strip().lower()
    return {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86": "x86",
        "i386": "x86",
        "i486": "x86",
        "i586": "x86",
        "i686": "x86",
    }.get(raw, raw[:16])


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    """读取 freedesktop os-release；损坏或缺失时返回空字典。"""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip().upper()
        if not key.replace("_", "").isalnum():
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value[:160]
    return values


def _linux_package_format(values: dict[str, str]) -> str:
    tokens = {
        item
        for item in " ".join(
            (values.get("ID", ""), values.get("ID_LIKE", ""))
        ).lower().replace(",", " ").split()
        if item
    }
    if tokens & {"openeuler", "rhel", "fedora", "centos", "suse", "opensuse"}:
        return "rpm"
    if tokens & {"debian", "ubuntu", "deepin", "uos", "kylin", "neokylin"}:
        return "deb"
    return ""

def detect_platform_info(*, os_release_path: Path = Path("/etc/os-release")) -> dict[str, object]:
    """返回心跳、安装器和更新选包共享的稳定平台契约。"""

    architecture = normalize_architecture()
    if sys.platform == "win32":
        release, version, _csd, _ptype = platform.win32_ver()
        is_windows7 = release == "7" or version.startswith("6.1")
        distribution = "windows7" if is_windows7 else "windows"
        runtime_profile = (
            "legacy-core" if is_windows7 and architecture == "x86"
            else "legacy-full" if is_windows7
            else "full"
        )
        capabilities = list(CORE_CAPABILITIES)
        if runtime_profile != "legacy-core":
            capabilities.extend(AI_CAPABILITIES)
        return {
            "platform_family": "windows",
            "distribution": distribution,
            "distribution_version": release or version,
            "package_format": "exe",
            "architecture": architecture,
            "runtime_profile": runtime_profile,
            "capabilities": capabilities,
            "platform": "windows",
        }
    if sys.platform.startswith("linux"):
        values = read_os_release(os_release_path)
        distribution = values.get("ID", "linux").strip().lower() or "linux"
        return {
            "platform_family": "linux",
            "distribution": distribution[:40],
            "distribution_version": values.get("VERSION_ID", "")[:40],
            "package_format": _linux_package_format(values),
            "architecture": architecture,
            "runtime_profile": "full",
            "capabilities": [*CORE_CAPABILITIES, *AI_CAPABILITIES],
            # 1.4.x 旧服务只认识 windows/uos；精确发行版由新字段承载。
            "platform": "uos",
        }
    return {
        "platform_family": sys.platform[:24],
        "distribution": sys.platform[:40],
        "distribution_version": "",
        "package_format": "",
        "architecture": architecture,
        "runtime_profile": "unsupported",
        "capabilities": [],
        "platform": sys.platform[:40],
    }


def update_platform_key(info: dict[str, object]) -> str:
    """把平台信息映射为更新清单 format v3 的制品键。"""

    family = str(info.get("platform_family", "")).lower()
    distribution = str(info.get("distribution", "")).lower()
    package_format = str(info.get("package_format", "")).lower()
    if family == "windows":
        return "windows7" if distribution == "windows7" else "windows"
    if family == "linux" and package_format in {"deb", "rpm"}:
        return f"linux-{package_format}"
    return ""
