"""离线验证 PartyOps 更新包的签名、平台矩阵、路径与内容哈希。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version


V3_SUFFIXES = {
    ("windows", "amd64"): "_windows_amd64.exe",
    ("windows7", "amd64"): "_windows7_amd64.exe",
    ("windows7", "x86"): "_windows7_x86.exe",
    ("linux-deb", "amd64"): "_linux_amd64.deb",
    ("linux-deb", "arm64"): "_linux_arm64.deb",
    ("linux-rpm", "amd64"): ".x86_64.rpm",
    ("linux-rpm", "arm64"): ".aarch64.rpm",
}
MAX_UPDATE_MEMBERS = 16
MAX_UPDATE_MANIFEST_BYTES = 1024 * 1024
MAX_UPDATE_ARTIFACT_BYTES = 4 * 1024**3
MAX_UPDATE_EXPANDED_BYTES = 16 * 1024**3
MIN_SCHEMA_REVISION = "0019"
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


def _release_version(value: object) -> Version:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-?rc\.?\d+)?", raw, flags=re.IGNORECASE):
        raise InvalidVersion(raw)
    parsed = Version(raw)
    if parsed.epoch != 0 or parsed.local is not None or len(parsed.release) != 3:
        raise InvalidVersion(raw)
    return parsed


def _safe_member(name: str) -> str | None:
    path = PurePosixPath(name)
    segments = name.split("/")
    if (
        not name
        or "\\" in name
        or len(name) > 512
        or path.is_absolute()
        or any(
            not segment
            or segment in {".", ".."}
            or segment.endswith((" ", "."))
            or ":" in segment
            or any(ord(character) < 32 for character in segment)
            or segment.rstrip(" .").split(".", 1)[0].casefold()
            in _WINDOWS_RESERVED_NAMES
            for segment in segments
        )
    ):
        return None
    return unicodedata.normalize("NFC", "/".join(segments)).casefold()


def _entry_error(info: zipfile.ZipInfo) -> str | None:
    mode = (info.external_attr >> 16) & 0o170000
    if (
        info.is_dir()
        or mode not in {0, stat.S_IFREG}
        or info.flag_bits & 0x1
        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    ):
        return f"更新包包含特殊、加密或非标准压缩成员：{info.filename}"
    if (
        info.file_size > 100 * 1024**2
        and info.file_size > max(info.compress_size, 1) * 1000
    ):
        return f"更新包成员压缩比异常：{info.filename}"
    return None


def _validate_artifact(
    archive: zipfile.ZipFile,
    artifacts: dict,
    filename: str,
    errors: list[str],
) -> None:
    record = artifacts.get(filename)
    if not isinstance(record, dict):
        errors.append(f"清单缺少制品记录：{filename}")
        return
    try:
        info = archive.getinfo(filename)
    except KeyError:
        errors.append(f"更新包缺少制品：{filename}")
        return
    try:
        expected_size = int(record.get("size", -1))
    except (TypeError, ValueError):
        errors.append(f"{filename} 大小字段无效")
        return
    if (
        expected_size < 0
        or expected_size > MAX_UPDATE_ARTIFACT_BYTES
        or expected_size != info.file_size
    ):
        errors.append(f"{filename} 大小与清单不一致")
        return
    digest = hashlib.sha256()
    try:
        with archive.open(info) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except zipfile.BadZipFile:
        errors.append(f"{filename} ZIP CRC 校验失败")
        return
    expected_hash = str(record.get("sha256", "")).lower()
    if len(expected_hash) != 64 or any(character not in "0123456789abcdef" for character in expected_hash):
        errors.append(f"{filename} SHA-256 格式无效")
    elif digest.hexdigest() != expected_hash:
        errors.append(f"{filename} SHA-256 与清单不一致")


def validate_package(
    package_path: Path,
    public_key_path: Path,
    expected_version: str,
    required_architectures: tuple[str, ...] = ("amd64", "arm64"),
) -> list[str]:
    errors: list[str] = []
    try:
        trusted_public_key = public_key_path.read_text(encoding="utf-8").strip()
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(trusted_public_key, validate=True)
        )
    except (OSError, ValueError) as exc:
        return [f"发布公钥不可用：{exc}"]

    try:
        _release_version(expected_version)
    except InvalidVersion:
        return ["预期版本格式无效"]

    try:
        with zipfile.ZipFile(package_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_UPDATE_MEMBERS:
                return ["更新包文件数量超过 16 个"]
            names = [info.filename for info in infos]
            collision_keys = [_safe_member(name) for name in names]
            if any(key is None for key in collision_keys):
                unsafe_index = next(
                    index for index, key in enumerate(collision_keys) if key is None
                )
                return [f"更新包包含非法路径：{names[unsafe_index]}"]
            if len(names) != len(set(names)) or len(collision_keys) != len(
                set(collision_keys)
            ):
                return ["更新包包含重复文件名"]
            for info in infos:
                entry_error = _entry_error(info)
                if entry_error:
                    return [entry_error]
            manifest_infos = [info for info in infos if info.filename == "manifest.json"]
            if len(manifest_infos) != 1:
                return ["更新包必须包含唯一的 manifest.json"]
            if manifest_infos[0].file_size > MAX_UPDATE_MANIFEST_BYTES:
                return ["更新包 manifest.json 超过 1 MiB"]
            manifest = json.loads(archive.read(manifest_infos[0]).decode("utf-8"))
            if not isinstance(manifest, dict):
                return ["更新包清单必须是 JSON 对象"]
            if manifest.get("format") != "partyops-update":
                errors.append("更新包格式标识错误")
            raw_format_version = manifest.get("format_version", 0)
            format_version = raw_format_version if type(raw_format_version) is int else 0
            if format_version not in (2, 3, 4):
                errors.append("更新包清单版本必须为 2、3 或 4")
            if str(manifest.get("version", "")) != expected_version:
                errors.append("更新包版本与预期版本不一致")
            try:
                if _release_version(manifest.get("version")) != _release_version(
                    expected_version
                ):
                    errors.append("更新包标准版本与预期版本不一致")
            except InvalidVersion:
                errors.append("更新包版本格式无效")
            try:
                minimum_version = _release_version(manifest.get("min_version"))
                target_version = _release_version(manifest.get("version"))
                if minimum_version > target_version:
                    errors.append("更新包最低桥接版本高于目标版本")
            except InvalidVersion:
                errors.append("更新包最低桥接版本格式无效")
            schema_revision = str(manifest.get("schema_revision", ""))
            if len(schema_revision) != 4 or not schema_revision.isdigit():
                errors.append("更新包数据库模式版本无效")
            elif schema_revision < MIN_SCHEMA_REVISION:
                errors.append("更新包数据库模式版本低于当前发布基线")
            release_notes = manifest.get("release_notes")
            if (
                not isinstance(release_notes, list)
                or not release_notes
                or len(release_notes) > 50
                or any(
                    not isinstance(note, str)
                    or not note.strip()
                    or len(note) > 500
                    for note in release_notes
                )
            ):
                errors.append("更新包发布说明无效")
            if manifest.get("public_key") != trusted_public_key:
                errors.append("更新包公钥与安装包信任公钥不一致")

            signature = str(manifest.get("signature", ""))
            unsigned = dict(manifest)
            unsigned.pop("signature", None)
            canonical = json.dumps(
                unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            signature_valid = True
            try:
                public_key.verify(base64.b64decode(signature, validate=True), canonical)
            except (InvalidSignature, ValueError, TypeError):
                errors.append("更新包 Ed25519 发布签名无效")
                signature_valid = False

            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict):
                errors.append("更新包制品清单结构错误")
                return errors
            referenced: set[str] = set()
            if format_version == 4:
                mappings = manifest.get("platform_artifacts")
                platform_name = str(manifest.get("target_platform", ""))
                architecture = str(manifest.get("target_architecture", ""))
                platform_mapping = (
                    mappings.get(platform_name) if isinstance(mappings, dict) else None
                )
                suffix = V3_SUFFIXES.get((platform_name, architecture))
                filename = (
                    str(platform_mapping.get(architecture, ""))
                    if isinstance(platform_mapping, dict)
                    else ""
                )
                if (
                    manifest.get("package_role") != "platform-update"
                    or suffix is None
                    or not isinstance(mappings, dict)
                    or set(mappings) != {platform_name}
                    or not isinstance(platform_mapping, dict)
                    or set(platform_mapping) != {architecture}
                    or not filename.endswith(suffix)
                    or set(artifacts) != {filename}
                ):
                    errors.append("format v4 轻量更新包目标或制品映射无效")
                else:
                    referenced.add(filename)
            elif format_version == 3:
                mappings = manifest.get("platform_artifacts")
                if not isinstance(mappings, dict):
                    errors.append("format v3 缺少平台制品映射")
                    return errors
                expected_platforms = {platform for platform, _architecture in V3_SUFFIXES}
                if set(mappings) != expected_platforms:
                    errors.append("format v3 平台映射键不完整或包含额外平台")
                for (platform_name, architecture), suffix in V3_SUFFIXES.items():
                    platform_mapping = mappings.get(platform_name)
                    expected_architectures = {
                        item_architecture
                        for item_platform, item_architecture in V3_SUFFIXES
                        if item_platform == platform_name
                    }
                    if not isinstance(platform_mapping, dict) or set(
                        platform_mapping
                    ) != expected_architectures:
                        errors.append(f"{platform_name} 架构映射不完整或包含额外架构")
                    filename = (
                        str(platform_mapping.get(architecture, ""))
                        if isinstance(platform_mapping, dict)
                        else ""
                    )
                    if not filename.endswith(suffix):
                        errors.append(f"{platform_name}/{architecture} 制品文件名或映射错误")
                        continue
                    referenced.add(filename)
            else:
                architecture_artifacts = manifest.get("architecture_artifacts")
                if not isinstance(architecture_artifacts, dict):
                    errors.append("format v2 缺少双架构映射")
                    return errors
                if set(architecture_artifacts) != set(required_architectures):
                    errors.append("format v2 双架构映射不完整或包含额外架构")
                for architecture in required_architectures:
                    filename = str(architecture_artifacts.get(architecture, ""))
                    if not filename.endswith(f"_{architecture}.deb"):
                        errors.append(f"{architecture} 制品文件名或映射错误")
                        continue
                    referenced.add(filename)
                    if not isinstance(artifacts.get(filename), dict):
                        errors.append(f"清单缺少 {architecture} 制品记录")
                        continue

                # v2 后期版本允许在旧双架构 DEB 映射旁附加 Windows x64
                # 制品。rc.3 仍完整校验该兼容字段，避免旧客户端拿到未签名
                # 或未登记的安装器；v3 则由上面的固定七制品矩阵负责。
                legacy_platforms = manifest.get("platform_artifacts")
                if legacy_platforms is not None:
                    if not isinstance(legacy_platforms, dict):
                        errors.append("平台制品映射结构错误")
                    else:
                        uos_mapping = legacy_platforms.get("uos")
                        if uos_mapping != architecture_artifacts:
                            errors.append("UOS 平台映射与旧双架构映射不一致")
                        windows_mapping = legacy_platforms.get("windows")
                        windows_name = (
                            str(windows_mapping.get("amd64", ""))
                            if isinstance(windows_mapping, dict)
                            else ""
                        )
                        if not windows_name.endswith("_windows_amd64.exe"):
                            errors.append("Windows x64 制品映射错误")
                        else:
                            referenced.add(windows_name)
                            if not isinstance(artifacts.get(windows_name), dict):
                                errors.append("清单缺少 Windows x64 制品记录")

            missing_references = referenced - set(artifacts)
            if missing_references:
                errors.append(f"清单引用缺失：{sorted(missing_references)[0]}")
            allowed_files = {"manifest.json", "RELEASE-NOTES.txt", *artifacts}
            unregistered = set(names) - allowed_files
            if unregistered:
                windows_unregistered = sorted(
                    name for name in unregistered if name.endswith(".exe")
                )
                other_unregistered = sorted(unregistered - set(windows_unregistered))
                if other_unregistered:
                    errors.append(f"更新包包含未登记制品：{other_unregistered[0]}")
                if windows_unregistered:
                    errors.append(
                        f"更新包包含未登记 Windows 制品：{windows_unregistered[0]}"
                    )
            # 签名无效时只返回上面的廉价结构诊断，不读取大型制品内容。
            if not signature_valid:
                return errors
            expanded_size = 0
            for filename, record in artifacts.items():
                if _safe_member(str(filename)) is None:
                    errors.append(f"制品清单包含非法路径：{filename}")
                    continue
                if isinstance(record, dict):
                    try:
                        expanded_size += max(0, int(record.get("size", -1)))
                    except (TypeError, ValueError):
                        pass
                _validate_artifact(archive, artifacts, str(filename), errors)
            if expanded_size > MAX_UPDATE_EXPANDED_BYTES:
                errors.append("更新包制品总展开体积超过 16 GiB")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        errors.append(f"更新包无法读取：{exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    errors = validate_package(
        args.package.resolve(), args.public_key.resolve(), args.expected_version
    )
    if errors:
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        return 2
    print(f"PartyOps {args.expected_version} 更新包签名、平台矩阵和哈希验证通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
