"""在冻结前核对 PartyOps 各层版本，防止外壳与内部元数据不一致。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _expect(pattern: str, text: str, expected: str, label: str) -> None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    actual = match.group(1) if match else "<missing>"
    if actual != expected:
        raise ValueError(f"{label}版本不一致：期望 {expected}，实际 {actual}")


def _artifact_versions(text: str) -> set[str]:
    """同时识别通用文件名和 RPM 的候选版发布号。"""

    versions = set(
        re.findall(r"PartyOps_(\d+\.\d+\.\d+(?:-rc\.\d+)?)_", text)
    )
    for base, rc in re.findall(
        r"PartyOps-(\d+\.\d+\.\d+)-(?:0\.rc\.(\d+)\.\d+|\d+)[.'\"${]",
        text,
    ):
        versions.add(f"{base}-rc.{rc}" if rc else base)
    return versions


def verify(root: Path, expected: str) -> None:
    """验证应用仓库内的运行时、打包入口与发布生成器版本。"""

    pep440 = expected.replace("-rc.", "rc")
    _expect(
        r'^__version__\s*=\s*"([^"]+)"',
        (root / "backend/app/__init__.py").read_text(encoding="utf-8"),
        expected,
        "后端应用",
    )
    _expect(
        r'^AGENT_VERSION\s*=\s*"([^"]+)"',
        (root / "backend/app/client_agent.py").read_text(encoding="utf-8"),
        expected,
        "协同端",
    )
    _expect(
        r'^version\s*=\s*"([^"]+)"',
        (root / "backend/pyproject.toml").read_text(encoding="utf-8"),
        pep440,
        "Python 项目元数据",
    )
    lock_text = (root / "backend/uv.lock").read_text(encoding="utf-8")
    _expect(
        r'(?ms)^\[\[package\]\]\s*^name\s*=\s*"partyops"\s*^version\s*=\s*"([^"]+)"',
        lock_text,
        pep440,
        "Python 锁文件",
    )
    payload = json.loads(
        (root / "frontend/package.json").read_text(encoding="utf-8")
    )
    actual = payload.get("version")
    if actual != expected:
        raise ValueError(f"业务前端版本不一致：期望 {expected}，实际 {actual}")

    for relative, pattern, label, transformed in (
        (
            "packaging/windows/PartyOps.iss",
            r'^#define MyAppVersion "([^"]+)"',
            "Windows 安装器",
            expected,
        ),
        (
            "packaging/windows/build-windows.ps1",
            r'^\$releaseVersion\s*=\s*"([^"]+)"',
            "Windows 构建入口",
            expected,
        ),
        (
            "packaging/windows/build-windows7.ps1",
            r'^\$releaseVersion\s*=\s*"([^"]+)"',
            "Win7 构建入口",
            expected,
        ),
        (
            "packaging/uos/build-update-package.sh",
            r'^VERSION="([^"]+)"',
            "Linux 更新包入口",
            expected,
        ),
        (
            "scripts/generate-update-catalog.py",
            r'^VERSION\s*=\s*"([^"]+)"',
            "在线更新目录",
            expected,
        ),
        (
            "packaging/linux/build-native.sh",
            r'^DEB_VERSION="([^"]+)"',
            "DEB 构建入口",
            expected.replace("-rc.", "~rc."),
        ),
        (
            "packaging/uos/build-portable.sh",
            r'^APP_VERSION="([^"]+)"',
            "Linux 便携载荷",
            expected,
        ),
        (
            "packaging/linux/post-install-selftest.sh",
            r'^EXPECTED_VERSION="([^"]+)"',
            "Linux 安装后自检",
            expected,
        ),
        (
            "packaging/uos/one-click-install.sh",
            r'^PACKAGE_VERSION="\$\{PARTYOPS_PACKAGE_VERSION:-([^}]+)\}"',
            "Linux 一键安装包版本",
            expected.replace("-rc.", "~rc."),
        ),
        (
            "packaging/uos/target-acceptance.sh",
            r'^\s*PACKAGE_VERSION="\$\{PARTYOPS_PACKAGE_VERSION:-([^}]+)\}"',
            "Linux 目标机验收包版本",
            expected.replace("-rc.", "~rc."),
        ),
    ):
        _expect(
            pattern,
            (root / relative).read_text(encoding="utf-8"),
            transformed,
            label,
        )

    for relative, label in (
        ("scripts/build-platform-update-packages.py", "单平台更新矩阵"),
        ("scripts/generate-release-bundle-manifest.py", "安装包发布矩阵"),
    ):
        text = (root / relative).read_text(encoding="utf-8")
        versions = _artifact_versions(text)
        if versions != {expected}:
            actual = ", ".join(sorted(versions)) or "<missing>"
            raise ValueError(f"{label}版本不一致：期望 {expected}，实际 {actual}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()
    try:
        verify(args.root.resolve(), args.expected)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"版本一致性门禁失败：{exc}") from exc
    print(f"版本一致性门禁通过：{args.expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
