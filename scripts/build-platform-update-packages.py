"""为已通过独立门禁的平台生成只包含本机安装器的签名更新包。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


_VALIDATOR_PATH = Path(__file__).with_name("validate-partyops-update.py")
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "partyops_update_validator", _VALIDATOR_PATH
)
if _VALIDATOR_SPEC is None or _VALIDATOR_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("无法加载 PartyOps 更新包验证器")
_VALIDATOR = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR)
validate_package = _VALIDATOR.validate_package


VERSION = "1.4.3-rc.5"
PLATFORMS = {
    ("windows", "amd64"): "PartyOps_1.4.3-rc.5_windows_amd64.exe",
    ("windows7", "amd64"): "PartyOps_1.4.3-rc.5_windows7_amd64.exe",
    ("windows7", "x86"): "PartyOps_1.4.3-rc.5_windows7_x86.exe",
    ("linux-deb", "amd64"): "PartyOps_1.4.3-rc.5_linux_amd64.deb",
    ("linux-deb", "arm64"): "PartyOps_1.4.3-rc.5_linux_arm64.deb",
    ("linux-rpm", "amd64"): "PartyOps-1.4.3-0.rc.5.1.x86_64.rpm",
    ("linux-rpm", "arm64"): "PartyOps-1.4.3-0.rc.5.1.aarch64.rpm",
}
RELEASE_NOTES = [
    "新增系统内检查、后台下载和一键原位升级，失败自动回滚且不丢失业务数据",
    "在线更新按当前系统与架构精确下载，不再下载其他平台安装器",
    "修复 PartyOps 协议注册表拒绝访问并提供事务回滚诊断",
    "修复数据库启动异常导致 CHILD_EXITED、乱码与管理员创建 10061",
    "新增 Windows 个人模式与可选彻底卸载；提供 Win7 SP1 x64/x86 Legacy 安装器，因未真机验证仅建议受控局域网使用",
    "新增麒麟、UOS、deepin 的 DEB 与 openEuler RPM 双架构原生包",
    "安装后自动核对文件、前端、SQLite/FTS5、OCR、智能运行时与健康端点",
    "Windows 自定义固定磁盘目录不再被父目录通用 ACL 误拦截，安装器自动保护最终程序目录并避免旧安装包缓存",
]


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_targets(values: list[str] | None) -> tuple[tuple[str, str], ...]:
    """解析显式发布目标；默认仍要求完整七平台，防止无意漏发。"""

    if not values:
        return tuple(PLATFORMS)
    selected: list[tuple[str, str]] = []
    for value in values:
        parts = value.split("/", 1)
        target = (parts[0], parts[1]) if len(parts) == 2 else ("", "")
        if target not in PLATFORMS:
            raise ValueError(f"未知发布目标：{value}")
        if target in selected:
            raise ValueError(f"发布目标重复：{value}")
        selected.append(target)
    return tuple(selected)


def _private_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data.strip(), validate=True))
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("更新签名必须使用 Ed25519 私钥")
    return key


def _public_text(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _signed_manifest(
    key: Ed25519PrivateKey,
    platform_name: str,
    architecture: str,
    artifact: Path,
) -> bytes:
    public_key = _public_text(key)
    manifest = {
        "format": "partyops-update",
        "format_version": 4,
        "package_role": "platform-update",
        "version": VERSION,
        # rc.2 的旧公钥无法验证轮换后的签名；rc.3 是新的应用内更新信任基线。
        "min_version": "1.4.3-rc.3",
        "schema_revision": "0019",
        "release_title": "多系统适配与专业级应用内升级",
        "target_platform": platform_name,
        "target_architecture": architecture,
        "platform_artifacts": {platform_name: {architecture: artifact.name}},
        "artifacts": {
            artifact.name: {
                "sha256": _hash(artifact),
                "size": artifact.stat().st_size,
            }
        },
        "release_notes": RELEASE_NOTES,
        "public_key": public_key,
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(key.sign(canonical)).decode("ascii")
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 8, 15, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_package(
    *,
    key: Ed25519PrivateKey,
    public_key_path: Path,
    artifact: Path,
    output: Path,
    platform_name: str,
    architecture: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".incoming",
            delete=False,
        ) as raw:
            temporary = Path(raw.name)
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            archive.writestr(
                _zip_info("manifest.json"),
                _signed_manifest(key, platform_name, architecture, artifact),
            )
            archive.writestr(
                _zip_info("RELEASE-NOTES.txt"),
                (
                    f"党建智办 PartyOps {VERSION} 应用内更新包\n"
                    f"目标：{platform_name}/{architecture}\n"
                    "签名校验通过后执行原位升级；失败自动回滚程序并保留数据。\n"
                ).encode("utf-8"),
            )
            with artifact.open("rb") as source, archive.open(_zip_info(artifact.name), "w") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
        errors = validate_package(temporary, public_key_path, VERSION)
        if errors:
            raise ValueError("；".join(errors))
        os.replace(temporary, output)
        temporary = None
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{_hash(output)}  {output.name}\n",
            encoding="ascii",
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--target",
        action="append",
        metavar="平台/架构",
        help="仅生成已通过独立门禁的目标；可重复，例如 linux-deb/arm64。",
    )
    args = parser.parse_args()

    key = _private_key(args.private_key.resolve())
    trusted_public = args.public_key.read_text(encoding="ascii").strip()
    if _public_text(key) != trusted_public:
        raise SystemExit("发布私钥与安装包信任公钥不匹配，拒绝生成更新包。")
    artifacts_dir = args.artifacts_dir.resolve()
    output_dir = args.output_dir.resolve()
    try:
        targets = resolve_targets(args.target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for platform_name, architecture in targets:
        filename = PLATFORMS[(platform_name, architecture)]
        artifact = artifacts_dir / filename
        if not artifact.is_file():
            raise SystemExit(f"缺少已通过独立门禁的制品：{filename}")
        output = output_dir / f"partyops_{VERSION}_{platform_name}_{architecture}.partyops-update"
        build_package(
            key=key,
            public_key_path=args.public_key.resolve(),
            artifact=artifact,
            output=output,
            platform_name=platform_name,
            architecture=architecture,
        )
        print(f"已生成单平台签名更新包：{output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
