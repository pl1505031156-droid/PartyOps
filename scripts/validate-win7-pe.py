"""静态验证 Windows 7 Legacy 冻结目录的 PE 架构、子系统和导入 API。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pefile

MACHINES = {"amd64": 0x8664, "x86": 0x014C}

# 下列 DLL/API 最早随 Windows 8/10 提供。Win7 制品出现任一项都说明冻结运行时
# 使用了错误的 SDK/工具链；KB2533623 提供的 AddDllDirectory 等不在禁止列表中。
FORBIDDEN_DLL_PREFIXES = (
    "api-ms-win-core-path-",
    "api-ms-win-core-winrt-",
    "api-ms-win-core-realtime-",
)
FORBIDDEN_IMPORTS = {
    "kernel32.dll": {
        "GetCurrentPackageFamilyName",
        "GetCurrentPackageFullName",
        "GetCurrentPackageId",
        "GetProcessMitigationPolicy",
        "GetSystemTimePreciseAsFileTime",
        "SetProcessMitigationPolicy",
        "SetThreadDescription",
    },
    "shcore.dll": {"GetDpiForMonitor", "SetProcessDpiAwareness"},
    "user32.dll": {
        "EnableNonClientDpiScaling",
        "GetDpiForSystem",
        "GetDpiForWindow",
        "GetSystemMetricsForDpi",
        "SetProcessDpiAwarenessContext",
    },
}


def _decode(value: bytes | None) -> str:
    return (value or b"").decode("ascii", errors="replace")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_pe(
    path: Path, architecture: str, verified_ucrt_hashes: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    try:
        image = pefile.PE(str(path), fast_load=False)
    except pefile.PEFormatError as exc:
        return [f"{path.name}: PE 文件无效：{exc}"]
    try:
        machine = image.FILE_HEADER.Machine
        if machine != MACHINES[architecture]:
            errors.append(
                f"{path.name}: 架构为 0x{machine:04x}，期望 {architecture}"
            )
        subsystem = (
            image.OPTIONAL_HEADER.MajorSubsystemVersion,
            image.OPTIONAL_HEADER.MinorSubsystemVersion,
        )
        # Microsoft 官方 app-local UCRT 的 APISet 转发器按设计支持 Win7，
        # 但 PE 子系统字段仍为 10.0。只对已由嵌入来源清单逐文件校验的
        # 转发器放行；其他 EXE/DLL/PYD 仍必须明确以 Win7 6.1 为上限。
        is_verified_ucrt_forwarder = (
            path.name.lower().startswith("api-ms-win-")
            and _sha256(path) == verified_ucrt_hashes.get(path.name.lower())
        )
        if subsystem > (6, 1) and not is_verified_ucrt_forwarder:
            errors.append(
                f"{path.name}: PE 子系统 {subsystem[0]}.{subsystem[1]} 高于 Win7 6.1"
            )
        for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", []):
            dll = _decode(entry.dll).lower()
            if any(dll.startswith(prefix) for prefix in FORBIDDEN_DLL_PREFIXES):
                errors.append(f"{path.name}: 导入 Win7 不支持的 DLL {dll}")
            forbidden = FORBIDDEN_IMPORTS.get(dll, set())
            for imported in entry.imports:
                name = _decode(imported.name)
                if name in forbidden:
                    errors.append(f"{path.name}: 导入 Win7 不支持的 API {dll}!{name}")
    finally:
        image.close()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--architecture", choices=tuple(MACHINES), required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"Win7 PE 门禁失败：目录不存在：{root}", file=sys.stderr)
        return 2
    source_path = root / "ucrt-source.json"
    if not source_path.is_file():
        print("Win7 PE 门禁失败：缺少 UCRT 来源与哈希清单", file=sys.stderr)
        return 2
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        ucrt_hashes = {
            name.lower(): str(value).lower()
            for name, value in source["files"].items()
        }
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        print(f"Win7 PE 门禁失败：UCRT 来源清单无效：{exc}", file=sys.stderr)
        return 2
    expected_ucrt_arch = "x64" if args.architecture == "amd64" else "x86"
    if source.get("version") != "10.0.19041.0" or source.get(
        "architecture"
    ) != expected_ucrt_arch:
        print("Win7 PE 门禁失败：UCRT 版本或架构与候选不匹配", file=sys.stderr)
        return 2
    for directory in (root, root / "_internal"):
        for name, expected_hash in ucrt_hashes.items():
            candidate = directory / name
            if not candidate.is_file() or _sha256(candidate) != expected_hash:
                print(
                    f"Win7 PE 门禁失败：UCRT 文件缺失或哈希不匹配：{candidate}",
                    file=sys.stderr,
                )
                return 2

    vc_source_path = root / "vc-runtime-source.json"
    if not vc_source_path.is_file():
        print("Win7 PE 门禁失败：缺少 VC142 来源与哈希清单", file=sys.stderr)
        return 2
    try:
        vc_source = json.loads(vc_source_path.read_text(encoding="utf-8"))
        vc_hashes = {
            name.lower(): str(value).lower()
            for name, value in vc_source["files"].items()
        }
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        print(f"Win7 PE 门禁失败：VC142 来源清单无效：{exc}", file=sys.stderr)
        return 2
    if vc_source.get("version") != "14.29.30157.0" or vc_source.get(
        "architecture"
    ) != expected_ucrt_arch:
        print("Win7 PE 门禁失败：VC142 版本或架构与候选不匹配", file=sys.stderr)
        return 2
    for directory in (root, root / "_internal"):
        for name, expected_hash in vc_hashes.items():
            candidate = directory / name
            if not candidate.is_file() or _sha256(candidate) != expected_hash:
                print(
                    f"Win7 PE 门禁失败：VC142 文件缺失或哈希不匹配：{candidate}",
                    file=sys.stderr,
                )
                return 2

    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".exe", ".dll", ".pyd"}
    )
    if not files:
        print("Win7 PE 门禁失败：冻结目录没有 EXE/DLL/PYD", file=sys.stderr)
        return 2
    errors = [
        error
        for path in files
        for error in validate_pe(path, args.architecture, ucrt_hashes)
    ]
    if errors:
        print("Win7 PE 门禁失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print(f"Win7 {args.architecture} PE 门禁通过：已检查 {len(files)} 个二进制文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
