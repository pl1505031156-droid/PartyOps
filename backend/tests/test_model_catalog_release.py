"""签名模型目录的成功与篡改拒绝门禁。"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate-model-catalog.py"


def _module():
    spec = importlib.util.spec_from_file_location("partyops_model_catalog", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pack(path: Path, key: Ed25519PrivateKey, *, payload: bytes = b"gguf", written: bytes | None = None) -> None:
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")
    manifest = {
        "format": "partyops-modelpack",
        "format_version": 2,
        "name": "测试模型",
        "version": "1.0.0",
        "model_id": "test-model",
        "architecture": "universal",
        "architectures": ["universal"],
        "platforms": ["windows", "linux", "macos"],
        "runtime": "llama.cpp-test",
        "resource_profile": {"min_memory_mb": 2048},
        "min_runtime_version": "1.4.5-rc.2",
        "license_name": "MIT",
        "model_source": "https://example.invalid/official",
        "components": {"llm": {"model_file": "models/llm/model.gguf"}},
        "files": {
            "models/llm/model.gguf": {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
        "public_key": public,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["signature"] = base64.b64encode(key.sign(canonical)).decode("ascii")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("models/llm/model.gguf", payload if written is None else written)


def test_model_catalog_generation_and_member_tamper_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public_path = tmp_path / "public.txt"
    public_path.write_text(base64.b64encode(public_raw).decode("ascii") + "\n", encoding="ascii")
    pack = tmp_path / "sample.partyops-modelpack"
    _pack(pack, key)
    output = tmp_path / "catalog.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--pack",
            str(pack),
            "--private-key",
            str(private_path),
            "--public-key",
            str(public_path),
            "--asset-url",
            f"{pack.name}=https://downloads.example.invalid/downloads/{pack.name}",
            "--release-version",
            "1.4.5-rc.2",
            "--generated-at",
            "2026-08-24T18:15:00+08:00",
            "--output",
            str(output),
        ],
    )
    module.main()
    catalog = json.loads(output.read_text(encoding="utf-8"))
    signature = base64.b64decode(catalog.pop("signature"), validate=True)
    Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, module._canonical(catalog))
    assert catalog["models"][0]["sha256"] == hashlib.sha256(pack.read_bytes()).hexdigest()
    assert catalog["models"][0]["download_url"].startswith("https://")

    tampered = tmp_path / "tampered.partyops-modelpack"
    _pack(tampered, key, payload=b"gguf", written=b"evil")
    with pytest.raises(ValueError, match="成员校验失败"):
        module._read_pack(tampered, public_raw)


def test_model_catalog_rejects_non_download_or_credentialed_urls() -> None:
    module = _module()
    filename = "sample.partyops-modelpack"
    expected = f"https://downloads.example.invalid/downloads/{filename}"
    assert module._validated_asset_url(expected, filename) == expected
    for invalid in (
        f"http://downloads.example.invalid/downloads/{filename}",
        f"https://user:secret@downloads.example.invalid/downloads/{filename}",
        f"https://downloads.example.invalid/files/{filename}",
        "https://downloads.example.invalid/downloads/wrong.partyops-modelpack",
        f"https://downloads.example.invalid/downloads/{filename}?token=secret",
    ):
        with pytest.raises(ValueError, match="/downloads/"):
            module._validated_asset_url(invalid, filename)
