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
