"""发布侧更新包自校验器回归。"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-partyops-update.py"
SPEC = importlib.util.spec_from_file_location("partyops_update_validator", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def _create_package(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_value = base64.b64encode(public_bytes).decode("ascii")
    public_path = tmp_path / "update-public-key.txt"
    public_path.write_text(public_value, encoding="utf-8")
    payloads = {
        "partyops_1.3.3_amd64.deb": b"amd64-package",
        "partyops_1.3.3_arm64.deb": b"arm64-package",
    }
    manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "1.3.3",
        "public_key": public_value,
        "architecture_artifacts": {
            "amd64": "partyops_1.3.3_amd64.deb",
            "arm64": "partyops_1.3.3_arm64.deb",
        },
        "artifacts": {
            name: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in payloads.items()
        },
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(
        private_key.sign(canonical)
    ).decode("ascii")
    package = tmp_path / "partyops_1.3.3.partyops-update"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    return package, public_path


def test_validator_accepts_signed_dual_architecture_package(tmp_path: Path) -> None:
    package, public_path = _create_package(tmp_path)
    assert validator.validate_package(package, public_path, "1.3.3") == []


def test_validator_rejects_tampered_artifact(tmp_path: Path) -> None:
    package, public_path = _create_package(tmp_path)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("partyops_1.3.3_amd64.deb", b"tampered")
    errors = validator.validate_package(package, public_path, "1.3.3")
    assert any("重复文件名" in error for error in errors)


def test_validator_rejects_untrusted_key_unsafe_path_and_unreadable_package(tmp_path: Path) -> None:
    package, public_path = _create_package(tmp_path)
    public_path.write_text("not-base64", encoding="utf-8")
    assert any("发布公钥不可用" in error for error in validator.validate_package(package, public_path, "1.3.3"))

    package, public_path = _create_package(tmp_path / "unsafe")
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("../escape.txt", b"unsafe")
    assert any("非法路径" in error for error in validator.validate_package(package, public_path, "1.3.3"))

    broken = tmp_path / "broken.partyops-update"
    broken.write_bytes(b"not-a-zip")
    assert any("无法读取" in error for error in validator.validate_package(broken, public_path, "1.3.3"))


def test_validator_reports_manifest_and_mapping_errors(tmp_path: Path) -> None:
    package, public_path = _create_package(tmp_path)
    trusted = public_path.read_text(encoding="utf-8").strip()
    invalid_manifest = {
        "format": "wrong",
        "format_version": 1,
        "version": "0.0.0",
        "public_key": "different",
        "signature": "invalid",
        "artifacts": [],
        "architecture_artifacts": [],
    }
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(invalid_manifest))
    errors = validator.validate_package(package, public_path, "1.3.3")
    assert {"更新包格式标识错误", "更新包清单版本必须为 2", "更新包版本与预期版本不一致", "更新包公钥与安装包信任公钥不一致", "更新包制品清单结构错误"} <= set(errors)
    assert any("Ed25519" in error for error in errors)

    mapped_manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "1.3.3",
        "public_key": trusted,
        "signature": "invalid",
        "architecture_artifacts": {"amd64": "wrong.bin", "arm64": "wrong.bin"},
        "artifacts": {},
        "platform_artifacts": "invalid",
    }
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(mapped_manifest))
        archive.writestr("unregistered.deb", b"deb")
        archive.writestr("unregistered.exe", b"exe")
    errors = validator.validate_package(package, public_path, "1.3.3")
    assert any("amd64 制品文件名" in error for error in errors)
    assert any("arm64 制品文件名" in error for error in errors)
    assert "平台制品映射结构错误" in errors
    assert any("未登记制品" in error for error in errors)
    assert any("未登记 Windows 制品" in error for error in errors)


def test_validator_reports_artifact_integrity_and_platform_mapping_errors(tmp_path: Path) -> None:
    package, public_path = _create_package(tmp_path)
    trusted = public_path.read_text(encoding="utf-8").strip()
    amd64_name = "partyops_1.3.3_amd64.deb"
    arm64_name = "partyops_1.3.3_arm64.deb"
    payload = b"package"
    manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "1.3.3",
        "public_key": trusted,
        "signature": "invalid",
        "architecture_artifacts": {"amd64": amd64_name, "arm64": arm64_name},
        "artifacts": {
            amd64_name: {"size": 999, "sha256": "0" * 64},
        },
        "platform_artifacts": {
            "uos": {"amd64": "different.deb"},
            "windows": {"amd64": "wrong.exe"},
        },
    }
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(amd64_name, payload)
        archive.writestr(arm64_name, payload)
    errors = validator.validate_package(package, public_path, "1.3.3")
    assert any("大小与清单不一致" in error for error in errors)
    assert any("SHA-256 与清单不一致" in error for error in errors)
    assert any("缺少 arm64 制品记录" in error for error in errors)
    assert "UOS 平台映射与旧双架构映射不一致" in errors
    assert "Windows x64 制品映射错误" in errors
