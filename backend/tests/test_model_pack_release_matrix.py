"""离线模型包清单、文件完整性和分能力激活回归。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import model_packs
from app.enums import ModelPackStatus
from app.problems import ProblemException


def _manifest(files: dict[str, bytes] | None = None) -> tuple[dict, dict[str, bytes]]:
    payloads = files or {
        "embedding/model.onnx": b"model",
        "embedding/tokenizer.json": b"tokenizer",
        "LICENSE": b"MIT",
    }
    manifest = {
        "format": "partyops-modelpack",
        "format_version": 1,
        "name": "中文向量",
        "version": "1.0.0",
        "estimated_memory_mb": 512,
        "components": {
            "embedding": {
                "model_file": "embedding/model.onnx",
                "tokenizer_file": "embedding/tokenizer.json",
                "pooling": "cls",
                "max_length": 512,
                "dimension": 512,
            }
        },
        "license_files": ["LICENSE"],
        "files": {
            name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for name, value in payloads.items()
        },
        "signature": "trusted-in-test",
    }
    return manifest, payloads


def _archive(tmp_path: Path, manifest: dict, files: dict[str, bytes], name="model.partyops-modelpack"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for filename, value in files.items():
            archive.writestr(filename, value)
    return path


def test_normalized_architecture_and_invalid_signature(monkeypatch) -> None:
    monkeypatch.setattr(model_packs.platform, "machine", lambda: "AARCH64")
    assert model_packs.normalized_architecture() == "arm64"
    monkeypatch.setattr(model_packs.platform, "machine", lambda: "riscv64")
    assert model_packs.normalized_architecture() == "riscv64"
    monkeypatch.setattr(model_packs.platform, "machine", lambda: "")
    assert model_packs.normalized_architecture() == "unknown"

    settings = model_packs.get_settings()
    monkeypatch.setattr(settings, "model_pack_public_key", "not-base64")
    monkeypatch.setattr(settings, "update_public_key", "")
    assert model_packs._manifest_signature_valid({"signature": "not-base64"}) is False


def test_manifest_rejects_invalid_metadata_components_and_paths(monkeypatch, tmp_path) -> None:
    manifest, files = _manifest()
    path = _archive(tmp_path, manifest, files)
    monkeypatch.setattr(model_packs, "_manifest_signature_valid", lambda _manifest: True)
    with zipfile.ZipFile(path) as archive:
        accepted, signed = model_packs._validate_manifest(manifest, archive)
    assert accepted == manifest["files"] and signed is True

    cases = [
        ({**manifest, "format_version": 3}, "MODEL_PACK_FORMAT_INVALID"),
        ({**manifest, "format_version": "bad"}, "MODEL_PACK_FORMAT_INVALID"),
        ({**manifest, "files": []}, "MODEL_PACK_MANIFEST_INVALID"),
        ({**manifest, "estimated_memory_mb": "many"}, "MODEL_PACK_MEMORY_INVALID"),
        ({**manifest, "estimated_memory_mb": -1}, "MODEL_PACK_MEMORY_INVALID"),
        ({**manifest, "components": {}}, "MODEL_PACK_COMPONENT_MISSING"),
        ({**manifest, "license_files": []}, "MODEL_PACK_LICENSE_MISSING"),
        ({**manifest, "license_files": ["missing.txt"]}, "MODEL_PACK_FILE_MISSING"),
    ]
    invalid_embedding = dict(manifest["components"]["embedding"])
    invalid_embedding["pooling"] = "max"
    cases.append(({**manifest, "components": {"embedding": invalid_embedding}}, "MODEL_PACK_POOLING_INVALID"))
    invalid_embedding = dict(manifest["components"]["embedding"])
    invalid_embedding["dimension"] = "wide"
    cases.append(({**manifest, "components": {"embedding": invalid_embedding}}, "MODEL_PACK_EMBEDDING_INVALID"))
    invalid_embedding = dict(manifest["components"]["embedding"])
    invalid_embedding["max_length"] = 0
    cases.append(({**manifest, "components": {"embedding": invalid_embedding}}, "MODEL_PACK_EMBEDDING_INVALID"))

    with zipfile.ZipFile(path) as archive:
        for value, code in cases:
            with pytest.raises(ProblemException) as error:
                model_packs._validate_manifest(value, archive)
            assert error.value.code == code

    manifest_v2 = {
        **manifest,
        "format_version": 2,
        "platforms": ["windows", "linux"],
        "architectures": ["amd64", "arm64"],
        "runtime": "onnxruntime",
        "resource_profile": {
            "min_memory_mb": 2048,
            "recommended_memory_mb": 4096,
            "disk_mb": 512,
            "threads": 2,
            "context_tokens": 512,
            "measured_peak_memory_mb": 768,
        },
    }
    with zipfile.ZipFile(path) as archive:
        accepted, signed = model_packs._validate_manifest(manifest_v2, archive)
    assert accepted == manifest["files"] and signed is True
    with zipfile.ZipFile(path) as archive, pytest.raises(ProblemException) as missing_profile:
        model_packs._validate_manifest({**manifest, "format_version": 2}, archive)
    assert missing_profile.value.code == "MODEL_PACK_RESOURCE_PROFILE_MISSING"
    invalid_profile = json.loads(json.dumps(manifest_v2))
    invalid_profile["resource_profile"]["recommended_memory_mb"] = 1024
    with zipfile.ZipFile(path) as archive, pytest.raises(ProblemException) as bad_profile:
        model_packs._validate_manifest(invalid_profile, archive)
    assert bad_profile.value.code == "MODEL_PACK_RESOURCE_PROFILE_INVALID"

    assert model_packs._safe_member("models/model.gguf").parts == ("models", "model.gguf")
    for unsafe in (
        "../model",
        "/absolute",
        "folder\\model",
        "folder//model",
        "model:stream",
        "con.txt",
        "model. ",
    ):
        with pytest.raises(ProblemException):
            model_packs._safe_member(unsafe)


def test_manifest_rejects_links_too_many_files_hashes_and_unsigned(monkeypatch, tmp_path) -> None:
    manifest, files = _manifest()
    monkeypatch.setattr(model_packs, "_manifest_signature_valid", lambda _manifest: True)

    link_path = tmp_path / "link.pack"
    with zipfile.ZipFile(link_path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        for filename, value in files.items():
            info = zipfile.ZipInfo(filename)
            if filename == "LICENSE":
                info.external_attr = 0o120777 << 16
            archive.writestr(info, value)
    with zipfile.ZipFile(link_path) as archive, pytest.raises(ProblemException) as link:
        model_packs._validate_manifest(manifest, archive)
    assert link.value.code == "MODEL_PACK_SPECIAL_FILE_DENIED"

    extra_path = tmp_path / "extra.pack"
    with zipfile.ZipFile(extra_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for filename, value in files.items():
            archive.writestr(filename, value)
        archive.writestr("unregistered.dll", b"payload")
    with zipfile.ZipFile(extra_path) as archive, pytest.raises(ProblemException) as extra:
        model_packs._validate_manifest(manifest, archive)
    assert extra.value.code == "MODEL_PACK_EXTRA_FILES"

    collision_path = tmp_path / "collision.pack"
    with zipfile.ZipFile(collision_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for filename, value in files.items():
            archive.writestr(filename, value)
        archive.writestr("Extra", b"one")
        archive.writestr("extra", b"two")
    with zipfile.ZipFile(collision_path) as archive, pytest.raises(ProblemException) as collision:
        model_packs._validate_manifest(manifest, archive)
    assert collision.value.code == "MODEL_PACK_DUPLICATE_MEMBER"

    normal_path = _archive(tmp_path, manifest, files, "normal.pack")
    monkeypatch.setattr(model_packs, "MAX_MEMBERS", 1)
    with zipfile.ZipFile(normal_path) as archive, pytest.raises(ProblemException) as many:
        model_packs._validate_manifest(manifest, archive)
    assert many.value.code == "MODEL_PACK_TOO_MANY_FILES"

    monkeypatch.setattr(model_packs, "MAX_MEMBERS", 64)
    monkeypatch.setattr(model_packs, "MAX_UNPACKED_BYTES", 1)
    with zipfile.ZipFile(normal_path) as archive, pytest.raises(ProblemException) as large:
        model_packs._validate_manifest(manifest, archive)
    assert large.value.code == "MODEL_PACK_TOO_LARGE"

    monkeypatch.setattr(model_packs, "MAX_UNPACKED_BYTES", 6 * 1024**3)
    broken = json.loads(json.dumps(manifest))
    broken["files"]["LICENSE"]["sha256"] = "0" * 64
    with zipfile.ZipFile(normal_path) as archive, pytest.raises(ProblemException) as digest:
        model_packs._validate_manifest(broken, archive)
    assert digest.value.code == "MODEL_PACK_HASH_MISMATCH"

    for field_value in ("many", -1):
        invalid_size = json.loads(json.dumps(manifest))
        invalid_size["files"]["LICENSE"]["size"] = field_value
        with zipfile.ZipFile(normal_path) as archive, pytest.raises(ProblemException) as metadata:
            model_packs._validate_manifest(invalid_size, archive)
        assert metadata.value.code == "MODEL_PACK_MANIFEST_INVALID"
    invalid_hash = json.loads(json.dumps(manifest))
    invalid_hash["files"]["LICENSE"]["sha256"] = "not-a-sha256"
    with zipfile.ZipFile(normal_path) as archive, pytest.raises(ProblemException) as metadata:
        model_packs._validate_manifest(invalid_hash, archive)
    assert metadata.value.code == "MODEL_PACK_MANIFEST_INVALID"

    monkeypatch.setattr(model_packs, "_manifest_signature_valid", lambda _manifest: False)
    with zipfile.ZipFile(normal_path) as archive, pytest.raises(ProblemException) as unsigned:
        model_packs._validate_manifest(manifest, archive)
    assert unsigned.value.code == "MODEL_PACK_SIGNATURE_INVALID"


def test_version_install_duplicate_bad_archive_and_path_boundary(monkeypatch, tmp_path) -> None:
    assert model_packs._numeric_version(" 1.4.1-rc.1 ") == (1, 4, 1)
    with pytest.raises(ProblemException) as version:
        model_packs._numeric_version("latest")
    assert version.value.code == "MODEL_RUNTIME_VERSION_INVALID"

    source = tmp_path / "model.pack"
    source.write_bytes(b"payload")
    monkeypatch.setattr(model_packs, "MAX_MODEL_PACK_BYTES", 1)
    with pytest.raises(ProblemException) as too_large:
        model_packs.install_model_pack(source, source.name, SimpleNamespace(id="admin"), SimpleNamespace())
    assert too_large.value.code == "MODEL_PACK_TOO_LARGE"

    monkeypatch.setattr(model_packs, "MAX_MODEL_PACK_BYTES", 100)
    duplicate_db = SimpleNamespace(
        scalar=lambda _statement: SimpleNamespace(version="1.0.0")
    )
    with pytest.raises(ProblemException) as duplicate:
        model_packs.install_model_pack(source, source.name, SimpleNamespace(id="admin"), duplicate_db)
    assert duplicate.value.code == "MODEL_PACK_ALREADY_INSTALLED"

    invalid = tmp_path / "invalid.pack"
    invalid.write_bytes(b"not zip")
    empty_db = SimpleNamespace(scalar=lambda _statement: None)
    with pytest.raises(ProblemException) as damaged:
        model_packs.install_model_pack(invalid, invalid.name, SimpleNamespace(id="admin"), empty_db)
    assert damaged.value.code == "MODEL_PACK_INVALID"

    bounded = tmp_path / "bounded.pack"
    with zipfile.ZipFile(bounded, "w") as archive:
        archive.writestr("manifest.json", "{}")
    monkeypatch.setattr(model_packs, "MAX_MODEL_PACK_BYTES", 1024 * 1024)
    monkeypatch.setattr(model_packs, "MAX_MODEL_MANIFEST_BYTES", 1)
    with pytest.raises(ProblemException) as manifest_large:
        model_packs.install_model_pack(
            bounded,
            bounded.name,
            SimpleNamespace(id="admin"),
            empty_db,
        )
    assert manifest_large.value.code == "MODEL_PACK_MANIFEST_INVALID"

    bad_pack = SimpleNamespace(install_key="../outside")
    with pytest.raises(ProblemException):
        model_packs.model_pack_root(bad_pack)


def test_verify_pack_cache_damage_and_cleanup(monkeypatch, tmp_path) -> None:
    models_dir = tmp_path / "models"
    packages = models_dir / "packages"
    root = models_dir / "safe-key"
    root.mkdir(parents=True)
    packages.mkdir()
    payload = b"verified"
    model_file = root / "model.onnx"
    model_file.write_bytes(payload)
    package_file = packages / "source.pack"
    package_file.write_bytes(b"pack")
    monkeypatch.setattr(model_packs, "get_settings", lambda: SimpleNamespace(models_dir=models_dir))
    pack = SimpleNamespace(
        id="pack-1",
        install_key="safe-key",
        filename="source.pack",
        manifest={"files": {"model.onnx": {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}},
    )
    model_packs._verification_cache.clear()
    assert model_packs.verify_installed_pack(pack) is True
    assert model_packs.verify_installed_pack(pack) is True

    model_file.write_bytes(b"changed-size")
    assert model_packs.verify_installed_pack(pack) is False
    model_file.write_bytes(b"tampered")
    pack.manifest["files"]["model.onnx"]["size"] = len(b"tampered")
    assert model_packs.verify_installed_pack(pack) is False
    pack.manifest["files"] = []
    assert model_packs.verify_installed_pack(pack) is False

    pack.manifest = {"files": {"model.onnx": {"size": len(b"tampered"), "sha256": hashlib.sha256(b"tampered").hexdigest()}}}
    model_packs.remove_installed_pack_files(pack)
    assert not root.exists() and not package_file.exists()


class _ActivationDb:
    def __init__(self, scalar_values, objects=None):
        self.values = iter(scalar_values)
        self.objects = objects or {}
        self.added = []
        self.deleted = []
        self.flushes = 0

    def scalar(self, _statement):
        return next(self.values, None)

    def get(self, _model, object_id):
        return self.objects.get(object_id)

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def flush(self):
        self.flushes += 1


def test_active_activate_switch_and_deactivate_capabilities(monkeypatch) -> None:
    legacy = SimpleNamespace(
        id="legacy", capabilities=[], manifest={"components": {"embedding": {}}}
    )
    assert model_packs.active_model_pack(_ActivationDb([None, legacy]), "embedding") is legacy
    assert model_packs.active_model_pack(_ActivationDb([]), "invalid") is None

    pack = SimpleNamespace(
        id="new",
        capabilities=["embedding"],
        min_runtime_version="1.4.1",
        status=ModelPackStatus.INSTALLED,
        activated_at=None,
    )
    with pytest.raises(ProblemException) as invalid:
        model_packs.activate_model_pack(_ActivationDb([]), pack, "vision", "admin")
    assert invalid.value.code == "MODEL_CAPABILITY_INVALID"
    with pytest.raises(ProblemException) as missing:
        model_packs.activate_model_pack(_ActivationDb([]), pack, "llm", "admin")
    assert missing.value.code == "MODEL_CAPABILITY_MISSING"

    settings = model_packs.get_settings()
    monkeypatch.setattr(settings, "app_version", "1.4.1")
    pack.min_runtime_version = "2.0.0"
    with pytest.raises(ProblemException) as old_runtime:
        model_packs.activate_model_pack(_ActivationDb([]), pack, "embedding", "admin")
    assert old_runtime.value.code == "MODEL_RUNTIME_TOO_OLD"

    pack.min_runtime_version = "1.4.1"
    monkeypatch.setattr(model_packs, "verify_installed_pack", lambda _pack: False)
    corrupt_db = _ActivationDb([])
    with pytest.raises(ProblemException) as corrupt:
        model_packs.activate_model_pack(corrupt_db, pack, "embedding", "admin")
    assert corrupt.value.code == "MODEL_PACK_CORRUPT" and pack.status == ModelPackStatus.CORRUPT

    previous = SimpleNamespace(id="old", status=ModelPackStatus.ACTIVE, activated_at=object())
    activation = SimpleNamespace(model_pack_id="old", activated_by="old-user", activated_at=None)
    monkeypatch.setattr(model_packs, "verify_installed_pack", lambda _pack: True)
    switch_db = _ActivationDb([activation, None], {"old": previous})
    activated = model_packs.activate_model_pack(switch_db, pack, "embedding", "admin")
    assert activated is pack and activation.model_pack_id == "new"
    assert previous.status == ModelPackStatus.INSTALLED and previous.activated_at is None

    active = SimpleNamespace(model_pack_id="new")
    deactivate_db = _ActivationDb([active, None], {"new": pack})
    assert model_packs.deactivate_model_capability(deactivate_db, "embedding") is pack
    assert active in deactivate_db.deleted and pack.status == ModelPackStatus.INSTALLED
    assert model_packs.deactivate_model_capability(_ActivationDb([None]), "llm") is None
