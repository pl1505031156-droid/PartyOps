"""离线验证 PartyOps 更新包的签名、架构制品和内容哈希。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not (
        not name
        or "\\" in name
        or "\x00" in name
        or len(name) > 512
        or path.is_absolute()
        or ".." in path.parts
    )


def validate_package(
    package_path: Path,
    public_key_path: Path,
    expected_version: str,
    required_architectures: tuple[str, ...] = ("amd64", "arm64"),
) -> list[str]:
    errors: list[str] = []
    try:
        trusted_public_key = public_key_path.read_text(encoding="utf-8").strip()
        public_key_bytes = base64.b64decode(trusted_public_key, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    except (OSError, ValueError) as exc:
        return [f"发布公钥不可用：{exc}"]

    try:
        with zipfile.ZipFile(package_path) as archive:
            names = archive.namelist()
            unsafe = [name for name in names if not _safe_member(name)]
            if unsafe:
                return [f"更新包包含非法路径：{unsafe[0]}"]
            if len(names) != len(set(names)):
                return ["更新包包含重复文件名"]
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

            if manifest.get("format") != "partyops-update":
                errors.append("更新包格式标识错误")
            if manifest.get("format_version") != 2:
                errors.append("更新包清单版本必须为 2")
            if str(manifest.get("version", "")) != expected_version:
                errors.append("更新包版本与预期版本不一致")
            if manifest.get("public_key") != trusted_public_key:
                errors.append("更新包公钥与安装包信任公钥不一致")

            signature = str(manifest.get("signature", ""))
            unsigned = dict(manifest)
            unsigned.pop("signature", None)
            canonical = json.dumps(
                unsigned,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                public_key.verify(base64.b64decode(signature, validate=True), canonical)
            except (InvalidSignature, ValueError, TypeError):
                errors.append("更新包 Ed25519 发布签名无效")

            artifacts = manifest.get("artifacts")
            architecture_artifacts = manifest.get("architecture_artifacts")
            if not isinstance(artifacts, dict) or not isinstance(
                architecture_artifacts, dict
            ):
                errors.append("更新包制品清单结构错误")
                return errors

            referenced: set[str] = set()
            for architecture in required_architectures:
                filename = str(architecture_artifacts.get(architecture, ""))
                if not filename.endswith(f"_{architecture}.deb"):
                    errors.append(f"{architecture} 制品文件名或架构映射错误")
                    continue
                referenced.add(filename)
                record = artifacts.get(filename)
                if not isinstance(record, dict):
                    errors.append(f"清单缺少 {architecture} 制品记录")
                    continue
                try:
                    info = archive.getinfo(filename)
                except KeyError:
                    errors.append(f"更新包缺少 {filename}")
                    continue
                if int(record.get("size", -1)) != info.file_size:
                    errors.append(f"{filename} 大小与清单不一致")
                digest = hashlib.sha256()
                with archive.open(info) as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != str(record.get("sha256", "")).lower():
                    errors.append(f"{filename} SHA-256 与清单不一致")

            platform_artifacts = manifest.get("platform_artifacts")
            if platform_artifacts is not None:
                if not isinstance(platform_artifacts, dict):
                    errors.append("平台制品映射结构错误")
                else:
                    uos = platform_artifacts.get("uos")
                    windows = platform_artifacts.get("windows")
                    if uos != architecture_artifacts:
                        errors.append("UOS 平台映射与旧双架构映射不一致")
                    windows_name = windows.get("amd64", "") if isinstance(windows, dict) else ""
                    if not str(windows_name).endswith("_windows_amd64.exe"):
                        errors.append("Windows x64 制品映射错误")
                    else:
                        referenced.add(str(windows_name))
                        record = artifacts.get(windows_name)
                        try:
                            info = archive.getinfo(windows_name)
                        except KeyError:
                            errors.append(f"更新包缺少 {windows_name}")
                        else:
                            if not isinstance(record, dict):
                                errors.append("清单缺少 Windows x64 制品记录")
                            else:
                                digest = hashlib.sha256()
                                with archive.open(info) as source:
                                    while chunk := source.read(1024 * 1024):
                                        digest.update(chunk)
                                if int(record.get("size", -1)) != info.file_size:
                                    errors.append(f"{windows_name} 大小与清单不一致")
                                if digest.hexdigest() != str(record.get("sha256", "")).lower():
                                    errors.append(f"{windows_name} SHA-256 与清单不一致")

            extra_debs = {
                name for name in names if name.endswith(".deb")
            } - referenced
            if extra_debs:
                errors.append(f"更新包包含未登记制品：{sorted(extra_debs)[0]}")
            extra_installers = {name for name in names if name.endswith(".exe")} - referenced
            if extra_installers:
                errors.append(f"更新包包含未登记 Windows 制品：{sorted(extra_installers)[0]}")
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
        args.package.resolve(),
        args.public_key.resolve(),
        args.expected_version,
    )
    if errors:
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        return 2
    print(
        f"PartyOps {args.expected_version} 更新包签名、双架构制品和哈希验证通过。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
