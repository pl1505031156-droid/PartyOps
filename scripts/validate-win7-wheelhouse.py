"""验证 Windows 7 Legacy wheelhouse、安全回移证据与架构闭包。"""

from __future__ import annotations

import argparse
import email
import json
import re
import sys
import zipfile
from pathlib import Path

AI_PACKAGES = {"numpy", "onnxruntime", "tokenizers"}
REQUIRED_AI_PACKAGES = {"numpy", "onnxruntime", "tokenizers"}
REQUIRED_BUILD_PACKAGES = {"pyinstaller", "pywin32", "pefile"}


def canonicalize_name(name: str) -> str:
    """使用 PEP 503 规则规范化包名，保持验证器可由纯 CPython 3.8 启动。"""

    return re.sub(r"[-_.]+", "-", name).lower()


def read_wheels(wheelhouse: Path, architecture: str) -> dict[str, tuple[str, Path]]:
    wheels: dict[str, tuple[str, Path]] = {}
    expected_platform = "win_amd64" if architecture == "amd64" else "win32"
    for path in sorted(wheelhouse.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            ]
            if len(metadata_names) != 1:
                raise ValueError(f"{path.name} 的 METADATA 数量异常")
            metadata = email.message_from_bytes(archive.read(metadata_names[0]))
            wheel_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/WHEEL") and name.count("/") == 1
            ]
            if len(wheel_names) != 1:
                raise ValueError(f"{path.name} 的 WHEEL 元数据数量异常")
            wheel_metadata = email.message_from_bytes(archive.read(wheel_names[0]))
        tags = wheel_metadata.get_all("Tag", [])
        platforms = {tag.rsplit("-", 1)[-1] for tag in tags if "-" in tag}
        if not platforms:
            raise ValueError(f"{path.name} 缺少有效 Tag")
        if "any" not in platforms and expected_platform not in platforms:
            raise ValueError(
                f"{path.name} 与 {architecture} 不兼容；只允许 {expected_platform} 或 any"
            )
        raw_name = metadata.get("Name")
        raw_version = metadata.get("Version")
        if not raw_name or not raw_version:
            raise ValueError(f"{path.name} 缺少 Name 或 Version")
        name = canonicalize_name(raw_name)
        if name in wheels:
            raise ValueError(
                f"Legacy 离线目录包含重复包：{raw_name}；文件："
                f"{wheels[name][1].name}、{path.name}。禁止覆盖合并旧 wheelhouse。"
            )
        wheels[name] = (raw_version, path)
    if not wheels:
        raise ValueError("wheelhouse 为空")
    return wheels


def validate_evidence(
    config_path: Path, evidence_root: Path, wheels: dict[str, tuple[str, Path]]
) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for raw_name, component in config.get("components", {}).items():
        name = canonicalize_name(raw_name)
        wheel = wheels.get(name)
        required_version = str(component["required_build_version"])
        if wheel is None:
            raise ValueError(f"缺少安全回移轮子：{raw_name}=={required_version}")
        if wheel[0] != required_version:
            raise ValueError(
                f"{raw_name} 必须使用安全回移版本 {required_version}，实际为 {wheel[0]}"
            )
        evidence_path = evidence_root / f"{name}.json"
        if not evidence_path.is_file():
            raise ValueError(f"缺少 {raw_name} 安全回移证据：{evidence_path}")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence.get("component") != raw_name:
            raise ValueError(f"{evidence_path.name} component 不匹配")
        if evidence.get("version") != required_version:
            raise ValueError(f"{evidence_path.name} version 不匹配")
        for field in component.get("required_evidence", []):
            value = evidence.get(field)
            if value in (None, "", [], {}):
                raise ValueError(f"{evidence_path.name} 缺少有效字段：{field}")
        for field in ("sbom", "vex", "vulnerability_reproduction", "post_fix_test"):
            referenced = evidence_root / str(evidence[field])
            if not referenced.is_file() or referenced.stat().st_size == 0:
                raise ValueError(f"{raw_name} 证据文件不存在或为空：{referenced}")
        test_result = json.loads(
            (evidence_root / str(evidence["post_fix_test"])).read_text(encoding="utf-8")
        )
        if test_result.get("passed") is not True or test_result.get("high_severity_open") != 0:
            raise ValueError(f"{raw_name} 安全回移复现测试未通过或仍有高危漏洞")


def validate_capabilities(
    wheels: dict[str, tuple[str, Path]], architecture: str
) -> None:
    names = set(wheels)
    missing_build = sorted(REQUIRED_BUILD_PACKAGES - names)
    if missing_build:
        raise ValueError(f"Win7 冻结工具链不完整：{', '.join(missing_build)}")
    if architecture == "x86":
        unexpected = sorted(names & AI_PACKAGES)
        if unexpected:
            raise ValueError(f"Win7 x86 禁止携带语义/LLM 运行时：{', '.join(unexpected)}")
        return
    missing = sorted(REQUIRED_AI_PACKAGES - names)
    if missing:
        raise ValueError(f"Win7 x64 智能运行时不完整：{', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--architecture", choices=("amd64", "x86"), required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("backend/legacy/security-backports.json"),
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        wheels = read_wheels(args.wheelhouse.resolve(), args.architecture)
        validate_evidence(args.config.resolve(), args.evidence_root.resolve(), wheels)
        validate_capabilities(wheels, args.architecture)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"Win7 {args.architecture} 发布门禁失败：{exc}", file=sys.stderr)
        return 2
    print(
        f"Win7 {args.architecture} wheelhouse 与安全回移证据通过：{len(wheels)} 个包。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
