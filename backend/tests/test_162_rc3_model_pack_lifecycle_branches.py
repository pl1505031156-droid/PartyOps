"""本地模型包安全卸载、回滚与激活查询的分支回归。"""

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


class _DB:
    def __init__(self, *, scalars=None, gets=None) -> None:
        self.scalar_values = list(scalars or [])
        self.gets = gets or {}
        self.deleted = []
        self.flushes = 0

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def get(self, _model, identifier):
        return self.gets.get(identifier)

    def delete(self, value) -> None:
        self.deleted.append(value)

    def flush(self) -> None:
        self.flushes += 1


def _settings(root: Path) -> SimpleNamespace:
    root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(models_dir=root, app_version="1.4.5-rc.3")


def _pack(**overrides) -> SimpleNamespace:
    values = {
        "id": "pack-1",
        "install_key": "runtime-1",
        "filename": "needle.partyops-modelpack",
        "manifest": {"files": {}},
        "capabilities": ["intent_router"],
        "status": ModelPackStatus.INSTALLED,
        "activated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_staged_removal_rollback_finish_and_path_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    monkeypatch.setattr(model_packs, "get_settings", lambda: _settings(models))
    with pytest.raises(ProblemException) as raised:
        model_packs.stage_model_pack_removal(_pack(filename="../outside.pack"))
    assert raised.value.code == "MODEL_PACK_PATH_INVALID"

    pack = _pack()
    runtime = models / pack.install_key
    runtime.mkdir()
    (runtime / "runtime.dll").write_bytes(b"runtime")
    package = models / "packages" / pack.filename
    package.parent.mkdir(parents=True)
    package.write_bytes(b"package")
    stage, moves = model_packs.stage_model_pack_removal(pack)
    assert len(moves) == 2 and stage.is_dir()
    assert not runtime.exists() and not package.exists()
    model_packs.rollback_staged_model_pack_removal(stage, moves)
    assert runtime.is_dir() and package.is_file() and not stage.exists()

    stage, moves = model_packs.stage_model_pack_removal(pack)
    moves[0][0].mkdir(parents=True)
    model_packs.rollback_staged_model_pack_removal(stage, moves)
    assert moves[0][0].is_dir()
    assert model_packs.finish_staged_model_pack_removal(models / "missing") is True

    removable = models / ".uninstall-staging" / "finish"
    removable.mkdir(parents=True)
    assert model_packs.finish_staged_model_pack_removal(removable) is False


def test_cleanup_staging_counts_only_unremovable_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    monkeypatch.setattr(model_packs, "get_settings", lambda: _settings(models))
    assert model_packs.cleanup_model_pack_uninstall_staging() == 0
    parent = models / ".uninstall-staging"
    removable_dir = parent / "old-dir"
    removable_dir.mkdir(parents=True)
    (removable_dir / "x").write_text("x", encoding="utf-8")
    removable_file = parent / "old-file"
    removable_file.write_text("x", encoding="utf-8")
    assert model_packs.cleanup_model_pack_uninstall_staging() == 0
    assert not parent.exists()

    blocked = parent / "blocked"
    blocked.mkdir(parents=True)
    original = model_packs.shutil.rmtree

    def fail_blocked(path: Path) -> None:
        if Path(path).name == "blocked":
            raise OSError("busy")
        original(path)

    monkeypatch.setattr(model_packs.shutil, "rmtree", fail_blocked)
    assert model_packs.cleanup_model_pack_uninstall_staging() == 1
    assert blocked.exists()


def test_remove_verify_cache_hash_and_missing_file_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    monkeypatch.setattr(model_packs, "get_settings", lambda: _settings(models))
    pack = _pack()
    model_packs.remove_installed_pack_files(pack)

    runtime = models / pack.install_key
    runtime.mkdir()
    payload = b"needle-runtime"
    runtime_file = runtime / "runtime.dll"
    runtime_file.write_bytes(payload)
    package = models / "packages" / pack.filename
    package.parent.mkdir(parents=True)
    package.write_bytes(b"package")
    pack.manifest = {
        "files": {
            "runtime.dll": {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        }
    }
    assert model_packs.verify_installed_pack(pack) is True
    assert model_packs.verify_installed_pack(pack) is True
    runtime_file.write_bytes(b"wrong")
    assert model_packs.verify_installed_pack(pack) is False
    runtime_file.write_bytes(b"x" * len(payload))
    assert model_packs.verify_installed_pack(pack) is False
    runtime_file.unlink()
    assert model_packs.verify_installed_pack(pack) is False
    pack.manifest = {"files": []}
    assert model_packs.verify_installed_pack(pack) is False

    runtime.mkdir(exist_ok=True)
    model_packs.remove_installed_pack_files(pack)
    assert not runtime.exists() and not package.exists()


def test_active_and_deactivate_capability_fallbacks() -> None:
    assert model_packs.active_model_pack(_DB(), "unknown") is None
    activation = SimpleNamespace(model_pack_id="pack-1")
    direct = _pack(capabilities=["intent_router"])
    assert (
        model_packs.active_model_pack(
            _DB(scalars=[activation], gets={"pack-1": direct}),
            "intent_router",
        )
        is direct
    )
    legacy = _pack(
        capabilities=[],
        manifest={"components": {"embedding": {"model_file": "model.onnx"}}},
    )
    assert (
        model_packs.active_model_pack(_DB(scalars=[None, legacy]), "embedding")
        is legacy
    )
    assert model_packs.active_model_pack(_DB(scalars=[None, None]), "llm") is None

    assert model_packs.deactivate_model_capability(_DB(), "intent_router") is None
    activation = SimpleNamespace(model_pack_id="pack-1")
    pack = _pack(status=ModelPackStatus.ACTIVE, activated_at="now")
    db = _DB(scalars=[activation, None], gets={"pack-1": pack})
    assert model_packs.deactivate_model_capability(db, "intent_router") is pack
    assert db.deleted == [activation]
    assert pack.status == ModelPackStatus.INSTALLED and pack.activated_at is None

    still_active = SimpleNamespace(id="other")
    pack = _pack(status=ModelPackStatus.ACTIVE, activated_at="now")
    db = _DB(scalars=[activation, still_active], gets={"pack-1": pack})
    model_packs.deactivate_model_capability(db, "intent_router")
    assert pack.status == ModelPackStatus.ACTIVE


def _intent_manifest(payloads: dict[str, bytes]) -> dict:
    return {
        "format": "partyops-modelpack",
        "format_version": 2,
        "name": "Needle Intent",
        "version": "2.0.3",
        "estimated_memory_mb": 128,
        "components": {"intent_router": {"runtime_file": "runtime.dll"}},
        "platforms": ["windows"],
        "architectures": ["amd64"],
        "runtime": "needle-native",
        "resource_profile": {
            "min_memory_mb": 256,
            "recommended_memory_mb": 512,
            "disk_mb": 8,
            "threads": 1,
            "context_tokens": 512,
            "measured_peak_memory_mb": 64,
        },
        "license_files": ["LICENSE"],
        "files": {
            name: {
                "size": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for name, value in payloads.items()
        },
        "signature": "test",
    }


def _archive(
    path: Path, manifest: dict, payloads: dict[str, bytes], *, directory=False
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        if directory:
            archive.writestr("folder/", b"")
        for name, value in payloads.items():
            archive.writestr(name, value)
    return path


def test_intent_manifest_optional_weights_directories_and_rejections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads = {"runtime.dll": b"runtime", "LICENSE": b"Apache-2.0"}
    manifest = _intent_manifest(payloads)
    monkeypatch.setattr(model_packs, "_manifest_signature_valid", lambda _value: True)
    path = _archive(tmp_path / "intent.pack", manifest, payloads, directory=True)
    with zipfile.ZipFile(path) as archive:
        files, signed = model_packs._validate_manifest(manifest, archive)
    assert signed is True and files == manifest["files"]

    for invalid_model in ("", 1):
        broken = json.loads(json.dumps(manifest))
        broken["components"]["intent_router"]["model_file"] = invalid_model
        with (
            zipfile.ZipFile(path) as archive,
            pytest.raises(ProblemException) as raised,
        ):
            model_packs._validate_manifest(broken, archive)
        assert raised.value.code == "MODEL_PACK_INTENT_INVALID"

    broken = json.loads(json.dumps(manifest))
    broken["resource_profile"]["min_memory_mb"] = 1
    with zipfile.ZipFile(path) as archive, pytest.raises(ProblemException) as raised:
        model_packs._validate_manifest(broken, archive)
    assert raised.value.code == "MODEL_PACK_RESOURCE_PROFILE_INVALID"

    broken = json.loads(json.dumps(manifest))
    broken["files"]["manifest.json"] = {
        "size": 1,
        "sha256": "0" * 64,
    }
    with zipfile.ZipFile(path) as archive, pytest.raises(ProblemException) as raised:
        model_packs._validate_manifest(broken, archive)
    assert raised.value.code == "MODEL_PACK_MANIFEST_INVALID"

    broken = json.loads(json.dumps(manifest))
    broken["files"]["runtime.dll"] = "bad"
    with zipfile.ZipFile(path) as archive, pytest.raises(ProblemException) as raised:
        model_packs._validate_manifest(broken, archive)
    assert raised.value.code == "MODEL_PACK_FILE_MISSING"

    compressed_payloads = {"runtime.dll": b"0" * (10 * 1024**2), "LICENSE": b"A"}
    compressed = _intent_manifest(compressed_payloads)
    compressed_path = _archive(
        tmp_path / "compressed.pack",
        compressed,
        compressed_payloads,
    )
    with (
        zipfile.ZipFile(compressed_path) as archive,
        pytest.raises(ProblemException) as raised,
    ):
        model_packs._validate_manifest(compressed, archive)
    assert raised.value.code == "MODEL_PACK_RATIO_INVALID"


def test_remaining_manifest_install_and_staging_edges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    settings = _settings(models)
    monkeypatch.setattr(model_packs, "get_settings", lambda: settings)
    monkeypatch.setattr(model_packs, "_manifest_signature_valid", lambda _value: True)
    payloads = {
        "runtime.dll": b"runtime",
        "weights.cact": b"weights",
        "LICENSE": b"Apache-2.0",
    }
    manifest = _intent_manifest(payloads)
    manifest["components"]["intent_router"]["model_file"] = "weights.cact"
    path = _archive(tmp_path / "weights.pack", manifest, payloads)
    with zipfile.ZipFile(path) as archive:
        files, _signed = model_packs._validate_manifest(manifest, archive)
    assert "weights.cact" in files

    directory_manifest = _intent_manifest({"runtime.dll": b"runtime", "LICENSE": b"A"})
    directory_manifest["files"]["folder/"] = {
        "size": 0,
        "sha256": hashlib.sha256(b"").hexdigest(),
    }
    directory_path = _archive(
        tmp_path / "directory.pack",
        directory_manifest,
        {"runtime.dll": b"runtime", "LICENSE": b"A"},
        directory=True,
    )
    with (
        zipfile.ZipFile(directory_path) as archive,
        pytest.raises(ProblemException) as raised,
    ):
        model_packs._validate_manifest(directory_manifest, archive)
    assert raised.value.code == "MODEL_PACK_FILE_MISSING"

    invalid = tmp_path / "manifest-list.pack"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("manifest.json", "[]")
    with pytest.raises(ProblemException) as raised:
        model_packs.install_model_pack(
            invalid,
            invalid.name,
            SimpleNamespace(id="admin"),
            _DB(scalars=[None]),
        )
    assert raised.value.code == "MODEL_PACK_MANIFEST_INVALID"

    valid = tmp_path / "disk-full.pack"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("manifest.json", "{}")
    monkeypatch.setattr(model_packs, "_validate_manifest", lambda *_args: ({}, True))
    monkeypatch.setattr(
        model_packs.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )
    with pytest.raises(ProblemException) as raised:
        model_packs.install_model_pack(
            valid,
            valid.name,
            SimpleNamespace(id="admin"),
            _DB(scalars=[None]),
        )
    assert raised.value.code == "MODEL_PACK_DISK_FULL"

    model_packs.remove_installed_pack_files(_pack(filename="../outside.pack"))
    empty_stage, moves = model_packs.stage_model_pack_removal(
        _pack(id="missing-pack", install_key="missing-runtime", filename="missing.pack")
    )
    assert moves == []
    model_packs.rollback_staged_model_pack_removal(tmp_path / "not-created", [])
    model_packs.finish_staged_model_pack_removal(empty_stage)

    activation = SimpleNamespace(model_pack_id="missing")
    fallback = _pack(capabilities=["llm"])
    assert (
        model_packs.active_model_pack(
            _DB(scalars=[activation, fallback], gets={}),
            "llm",
        )
        is fallback
    )


def test_install_failure_cleans_runtime_and_package_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    settings = _settings(models)
    monkeypatch.setattr(model_packs, "get_settings", lambda: settings)
    monkeypatch.setattr(model_packs, "_manifest_signature_valid", lambda _value: True)
    monkeypatch.setattr(
        model_packs.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=10 * 1024**3),
    )
    payloads = {"runtime.dll": b"runtime", "LICENSE": b"Apache-2.0"}
    package = _archive(
        tmp_path / "replace-failure.pack",
        _intent_manifest(payloads),
        payloads,
    )
    real_replace = model_packs.os.replace
    replace_count = 0

    def fail_on_package_move(source, destination) -> None:
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("simulated package move failure")
        real_replace(source, destination)

    monkeypatch.setattr(model_packs.os, "replace", fail_on_package_move)
    with pytest.raises(OSError, match="package move failure"):
        model_packs.install_model_pack(
            package,
            package.name,
            SimpleNamespace(id="admin"),
            _DB(scalars=[None]),
        )
    assert not any(
        path.is_dir() for path in models.iterdir() if path.name != "packages"
    )


def test_cleanup_staging_quarantines_link_like_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    models = tmp_path / "models"
    settings = _settings(models)
    stage_parent = models / ".uninstall-staging"
    stage_parent.mkdir(parents=True)
    unsafe_child = stage_parent / "unsafe-link"
    unsafe_child.write_text("keep", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(model_packs, "get_settings", lambda: settings)
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda value: value == unsafe_child or original_is_symlink(value),
    )

    assert model_packs.cleanup_model_pack_uninstall_staging() == 1
    assert unsafe_child.exists()
