"""为最终公开发布目录生成可审计的多平台制品清单。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

INSTALLERS = {
    "PartyOps_1.4.5-rc.2_windows_amd64.exe": "windows/amd64",
    "PartyOps_1.4.5-rc.2_windows7_amd64.exe": "windows7/amd64",
    "PartyOps_1.4.5-rc.2_windows7_x86.exe": "windows7/x86",
    "PartyOps_1.4.5-rc.2_linux_amd64.deb": "linux-deb/amd64",
    "PartyOps_1.4.5-rc.2_linux_arm64.deb": "linux-deb/arm64",
    "PartyOps-1.4.5-0.rc.2.1.x86_64.rpm": "linux-rpm/amd64",
    "PartyOps-1.4.5-0.rc.2.1.aarch64.rpm": "linux-rpm/arm64",
    "PartyOps_1.4.5-rc.2_macos_x86_64.pkg": "macos/amd64",
    "PartyOps_1.4.5-rc.2_macos_arm64.pkg": "macos/arm64",
}

UNAVAILABLE_INSTALLERS: dict[str, str] = {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_key(private_key_path: Path, public_key_path: Path) -> tuple[Ed25519PrivateKey, str, str]:
    """载入正式密钥并只返回公钥与指纹，私钥内容不进入清单或日志。"""

    if not private_key_path.is_file() or private_key_path.is_symlink():
        raise ValueError("正式发布私钥必须是本机普通文件")
    data = private_key_path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data.strip(), validate=True))
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("正式发布私钥必须使用 Ed25519")
    trusted = public_key_path.read_text(encoding="ascii").strip()
    actual = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    if actual != trusted:
        raise ValueError("正式发布私钥与客户端内置信任公钥不匹配")
    fingerprint = hashlib.sha256(base64.b64decode(actual)).hexdigest()
    return key, actual, fingerprint


def _sign_payload(key: Ed25519PrivateKey, payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return base64.b64encode(key.sign(canonical)).decode("ascii")


def build_manifest(
    *,
    root: Path,
    output: Path,
    source_commit: str,
    tooling_commit: str,
    macos_source_commit: str,
    macos_workflow_commit: str,
    macos_build_run: str,
    generated_at: str,
    private_key_path: Path,
    public_key_path: Path,
    verified_platforms: tuple[str, ...] = (),
    native_verified_platforms: tuple[str, ...] = (),
    emulated_verified_platforms: tuple[str, ...] = (),
) -> dict[str, object]:
    timestamp = datetime.fromisoformat(generated_at)
    if timestamp.tzinfo is None:
        raise ValueError("清单生成时间必须包含时区")
    root = root.resolve()
    private_key_path = private_key_path.resolve()
    public_key_path = public_key_path.resolve()
    if private_key_path == root or root in private_key_path.parents:
        raise ValueError("正式发布私钥禁止放入公开制品目录")
    missing = sorted(name for name in INSTALLERS if not (root / name).is_file())
    if missing:
        raise FileNotFoundError(f"缺少当前发布矩阵安装包：{', '.join(missing)}")
    stale = sorted(
        path.name
        for path in root.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".exe", ".deb", ".rpm", ".pkg"}
        and path.name not in INSTALLERS
    )
    if stale:
        raise ValueError(f"发布目录含旧版或未知安装包：{', '.join(stale)}")

    key, public_key, signing_fingerprint = _verified_key(
        private_key_path, public_key_path
    )
    known_platforms = set(INSTALLERS.values())
    for label, platforms in {
        "verified": verified_platforms,
        "native verified": native_verified_platforms,
        "emulated verified": emulated_verified_platforms,
    }.items():
        if len(set(platforms)) != len(platforms) or any(item not in known_platforms for item in platforms):
            raise ValueError(f"{label} 平台必须是无重复的当前发布目标")

    assets = []
    for path in sorted(item for item in root.iterdir() if item.is_file()):
        if path.resolve() == output.resolve():
            continue
        asset: dict[str, object] = {
            "filename": path.name,
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }
        asset["signature"] = _sign_payload(key, asset)
        assets.append(asset)
    manifest: dict[str, object] = {
        "schema_version": 4,
        "product": "PartyOps",
        "version": "1.4.5-rc.2",
        "release_tag": "v1.4.5-rc.2",
        "source_commit": source_commit,
        "release_tooling_commit": tooling_commit,
        "supplemental_sources": [
            {
                "scope": ["macos/amd64", "macos/arm64"],
                "source_commit": macos_source_commit,
                "workflow_commit": macos_workflow_commit,
                "native_build_run": macos_build_run,
            }
        ],
        "generated_at": generated_at,
        "timezone": "Asia/Shanghai (UTC+8)",
        "release_type": "prerelease",
        "prerelease": True,
        "make_latest": False,
        "signed": True,
        "public_key": public_key,
        "signing_fingerprint_sha256": signing_fingerprint,
        "packaged_platforms": list(INSTALLERS.values()),
        "unavailable_platforms": list(UNAVAILABLE_INSTALLERS.values()),
        "verified_platforms": list(verified_platforms),
        "native_verified_platforms": list(native_verified_platforms),
        "emulated_verified_platforms": list(emulated_verified_platforms),
        "native_machine_validation": bool(native_verified_platforms),
        "limitations": [
            "Windows 7 与国产 Linux 尚未在对应真机运行验收",
            "Windows 安装器尚无商业代码签名",
            "ARM64 Linux 成品已通过 QEMU 动态门禁，但桌面 PID 归属仍需真实 ARM 内核复核",
            "macOS 双架构已在对应原生 Darwin 主机完成安装和 LaunchServices 门禁，但未使用 Developer ID、未公证且尚无用户设备交互验收",
        ],
        "assets": assets,
    }
    manifest["signature"] = _sign_payload(key, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--tooling-commit", required=True)
    parser.add_argument("--macos-source-commit", required=True)
    parser.add_argument("--macos-workflow-commit", required=True)
    parser.add_argument("--macos-build-run", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument(
        "--public-key",
        required=True,
        type=Path,
        help="客户端内置 Ed25519 信任公钥",
    )
    parser.add_argument("--verified-platform", action="append", default=[])
    parser.add_argument("--native-verified-platform", action="append", default=[])
    parser.add_argument("--emulated-verified-platform", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    payload = build_manifest(
        root=root,
        output=output,
        source_commit=args.source_commit,
        tooling_commit=args.tooling_commit,
        macos_source_commit=args.macos_source_commit,
        macos_workflow_commit=args.macos_workflow_commit,
        macos_build_run=args.macos_build_run,
        generated_at=args.generated_at,
        private_key_path=args.private_key.resolve(),
        public_key_path=args.public_key.resolve(),
        verified_platforms=tuple(args.verified_platform),
        native_verified_platforms=tuple(args.native_verified_platform),
        emulated_verified_platforms=tuple(args.emulated_verified_platform),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".incoming",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"最终发布清单已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
