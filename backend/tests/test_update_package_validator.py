"""发布侧更新包自校验器回归。"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import stat
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate-partyops-update.py"
SPEC = importlib.util.spec_from_file_location("partyops_update_validator", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

BUILDER_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-platform-update-packages.py"
BUILDER_SPEC = importlib.util.spec_from_file_location("partyops_update_builder", BUILDER_SCRIPT)
assert BUILDER_SPEC and BUILDER_SPEC.loader
builder = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(builder)


def _write_signed_package(
    package: Path,
    public_path: Path,
    manifest: dict,
    payloads: dict[str, bytes],
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_value = base64.b64encode(public_bytes).decode("ascii")
    public_path.write_text(public_value, encoding="utf-8")
    manifest["public_key"] = public_value
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(private_key.sign(canonical)).decode(
        "ascii"
    )
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, payload in payloads.items():
            archive.writestr(name, payload)


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
        "min_version": "1.3.0",
        "schema_revision": "0019",
        "release_notes": ["测试更新包"],
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
    with pytest.warns(UserWarning, match="Duplicate name"):
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


def test_validator_rejects_archive_ambiguity_extra_files_and_skips_unsigned_payload(
    monkeypatch, tmp_path: Path
) -> None:
    package, public_path = _create_package(tmp_path / "extra")
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("unregistered.dll", b"extra")
    assert any(
        "未登记制品" in error
        for error in validator.validate_package(package, public_path, "1.3.3")
    )

    package, public_path = _create_package(tmp_path / "collision")
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("PARTYOPS_1.3.3_AMD64.DEB", b"collision")
    assert any(
        "重复文件名" in error
        for error in validator.validate_package(package, public_path, "1.3.3")
    )

    package, public_path = _create_package(tmp_path / "special")
    with zipfile.ZipFile(package, "a") as archive:
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"target")
    assert any(
        "特殊" in error
        for error in validator.validate_package(package, public_path, "1.3.3")
    )

    package, public_path = _create_package(tmp_path / "unsigned")
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        payloads = {
            name: archive.read(name)
            for name in manifest["artifacts"]
        }
    manifest["signature"] = "invalid"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, payload in payloads.items():
            archive.writestr(name, payload)

    def unexpected_hash():
        raise AssertionError("签名失败后不应读取并哈希制品")

    monkeypatch.setattr(validator.hashlib, "sha256", unexpected_hash)
    unsigned_errors = validator.validate_package(package, public_path, "1.3.3")
    assert any("Ed25519" in error for error in unsigned_errors)


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
    assert {"更新包格式标识错误", "更新包清单版本必须为 2、3 或 4", "更新包版本与预期版本不一致", "更新包公钥与安装包信任公钥不一致", "更新包制品清单结构错误"} <= set(errors)
    assert any("Ed25519" in error for error in errors)

    mapped_manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "1.3.3",
        "min_version": "1.3.0",
        "schema_revision": "0019",
        "release_notes": ["映射错误回归"],
        "public_key": trusted,
        "signature": "invalid",
        "architecture_artifacts": {"amd64": "wrong.bin", "arm64": "wrong.bin"},
        "artifacts": {},
        "platform_artifacts": "invalid",
    }
    _write_signed_package(
        package,
        public_path,
        mapped_manifest,
        {"unregistered.deb": b"deb", "unregistered.exe": b"exe"},
    )
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
        "min_version": "1.3.0",
        "schema_revision": "0019",
        "release_notes": ["完整性错误回归"],
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
    _write_signed_package(
        package,
        public_path,
        manifest,
        {amd64_name: payload, arm64_name: payload},
    )
    errors = validator.validate_package(package, public_path, "1.3.3")
    assert any("大小与清单不一致" in error for error in errors)
    assert any("缺少 arm64 制品记录" in error for error in errors)
    assert "UOS 平台映射与旧双架构映射不一致" in errors
    assert "Windows x64 制品映射错误" in errors

    hash_manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "1.3.3",
        "min_version": "1.3.0",
        "schema_revision": "0019",
        "release_notes": ["哈希错误回归"],
        "architecture_artifacts": {"amd64": amd64_name, "arm64": arm64_name},
        "artifacts": {
            amd64_name: {"size": len(payload), "sha256": "0" * 64},
            arm64_name: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        },
    }
    _write_signed_package(
        package,
        public_path,
        hash_manifest,
        {amd64_name: payload, arm64_name: payload},
    )
    hash_errors = validator.validate_package(package, public_path, "1.3.3")
    assert any("SHA-256 与清单不一致" in error for error in hash_errors)


def test_format_v4_contains_only_target_platform_artifact(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path = tmp_path / "public.txt"
    public_path.write_text(
        base64.b64encode(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
        encoding="ascii",
    )
    artifact = tmp_path / "PartyOps_1.4.3-rc.3_linux_arm64.deb"
    artifact.write_bytes(b"arm64-deb")
    package = tmp_path / "partyops_1.4.3-rc.3_linux-deb_arm64.partyops-update"
    builder.build_package(
        key=private_key,
        public_key_path=public_path,
        artifact=artifact,
        output=package,
        platform_name="linux-deb",
        architecture="arm64",
    )
    assert validator.validate_package(package, public_path, "1.4.3-rc.3") == []
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "RELEASE-NOTES.txt",
            artifact.name,
        }
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format_version"] == 4
    assert manifest["target_platform"] == "linux-deb"
    assert manifest["target_architecture"] == "arm64"


def test_platform_update_target_parser_is_explicit_and_strict() -> None:
    assert builder.resolve_targets(None) == tuple(builder.PLATFORMS)
    assert builder.resolve_targets(["linux-rpm/arm64"]) == (("linux-rpm", "arm64"),)
    for values in (["unknown/amd64"], ["windows/amd64", "windows/amd64"]):
        with pytest.raises(ValueError):
            builder.resolve_targets(values)
