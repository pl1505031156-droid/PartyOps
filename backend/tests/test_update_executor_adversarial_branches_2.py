"""更新执行器的高风险失败分支，防止门禁只覆盖成功路径。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import __version__ as APP_VERSION
from app import update_executor
from app.enums import UpdateStatus


def _os_proxy(name: str) -> SimpleNamespace:
    proxy = SimpleNamespace(**vars(os))
    proxy.name = name
    return proxy


def _settings(data_dir: Path) -> SimpleNamespace:
    data_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        data_dir=data_dir,
        database_path=data_dir / "partyops.db",
        attachments_dir=data_dir / "attachments",
        archives_dir=data_dir / "archives",
        update_public_key="",
        tls_enabled=False,
        tls_client_ca_file=None,
        port=18765,
        app_version="1.4.3-rc.2",
    )


def _health_payload(**changes: object) -> bytes:
    payload: dict[str, object] = {
        "status": "ok",
        "mode": "host",
        "app_version": APP_VERSION,
        "sqlite": {"safe_version": True, "fts5": True},
    }
    payload.update(changes)
    return json.dumps(payload).encode()


class _Response:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


def _write_manifest_archive(
    path: Path,
    manifest: dict | str,
    entries: dict[str, bytes],
) -> None:
    raw = manifest if isinstance(manifest, str) else json.dumps(manifest)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", raw)
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_privileged_manifest_reader_rejects_ambiguous_and_oversized_archives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "release.partyops-update"
    artifact = "PartyOps_1.4.3-rc.3_linux_amd64.deb"
    payload = b"deb"
    manifest = {
        "version": "1.4.3-rc.3",
        "artifacts": {
            artifact: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    _write_manifest_archive(package, manifest, {artifact: payload})
    assert update_executor._read_update_manifest(package)["version"] == "1.4.3-rc.3"

    with zipfile.ZipFile(package, "w") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(artifact, payload)
    with pytest.raises(RuntimeError, match="重复文件"):
        update_executor._read_update_manifest(package)

    duplicate_json = (
        '{"version":"1.4.3-rc.2","version":"1.4.3-rc.3",'
        '"artifacts":{}}'
    )
    _write_manifest_archive(package, duplicate_json, {})
    with pytest.raises(ValueError, match="重复字段"):
        update_executor._read_update_manifest(package)

    _write_manifest_archive(package, manifest, {artifact: payload, "extra.txt": b"x"})
    with pytest.raises(RuntimeError, match="未登记"):
        update_executor._read_update_manifest(package)

    _write_manifest_archive(package, manifest, {artifact: payload})
    monkeypatch.setattr(update_executor, "MAX_UPDATE_MANIFEST_BYTES", 1)
    with pytest.raises(RuntimeError, match="清单缺失或体积异常"):
        update_executor._read_update_manifest(package)


def test_privileged_manifest_reader_rejects_nonregular_and_bad_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "release.partyops-update"
    artifact = "PartyOps_1.4.3-rc.3_linux_amd64.deb"
    payload = b"deb"
    manifest = {
        "version": "1.4.3-rc.3",
        "artifacts": {
            artifact: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        link = zipfile.ZipInfo(artifact)
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, b"target")
    with pytest.raises(RuntimeError, match="非普通文件"):
        update_executor._read_update_manifest(package)

    bad_hash = json.loads(json.dumps(manifest))
    bad_hash["artifacts"][artifact]["sha256"] = "invalid"
    _write_manifest_archive(package, bad_hash, {artifact: payload})
    with pytest.raises(RuntimeError, match="元数据"):
        update_executor._read_update_manifest(package)

    _write_manifest_archive(package, manifest, {artifact: payload})
    monkeypatch.setattr(update_executor, "MAX_UPDATE_EXPANDED_BYTES", 1)
    with pytest.raises(RuntimeError, match="展开体积"):
        update_executor._read_update_manifest(package)


def test_secure_transaction_and_managed_tree_reject_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "data")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="任务编号"):
        update_executor._secure_update_backup_root("../escape")

    real_base = tmp_path / "real-base"
    real_base.mkdir()
    transaction_base = settings.data_dir / "upgrade-backups"
    transaction_base.symlink_to(real_base, target_is_directory=True)
    with pytest.raises(RuntimeError, match="事务根目录"):
        update_executor._secure_update_backup_root("run-link")
    transaction_base.unlink()

    transaction_base.mkdir()
    (transaction_base / "run-existing").mkdir()
    with pytest.raises(RuntimeError, match="已存在"):
        update_executor._secure_update_backup_root("run-existing")

    chmod_calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(Path, "chmod", lambda path, mode: chmod_calls.append((path, mode)))
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    created = update_executor._secure_update_backup_root("run-new")
    assert created.is_dir()
    assert chmod_calls == [(transaction_base, 0o700)]

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(RuntimeError, match="超出"):
        update_executor._snapshot_managed_tree(outside, tmp_path / "copy", settings.data_dir)
    update_executor._snapshot_managed_tree(
        settings.data_dir / "absent",
        tmp_path / "absent-copy",
        settings.data_dir,
    )

    source = settings.data_dir / "attachments"
    source.mkdir()
    (source / "safe.txt").write_text("safe", encoding="utf-8")
    linked_file = source / "linked.txt"
    linked_file.symlink_to(tmp_path / "target.txt")
    with pytest.raises(RuntimeError, match="包含链接"):
        update_executor._assert_managed_tree_has_no_links(source)
    linked_file.unlink()

    source_link = settings.data_dir / "source-link"
    source_link.symlink_to(source, target_is_directory=True)
    with pytest.raises(RuntimeError, match="数据源"):
        update_executor._snapshot_managed_tree(
            source_link,
            tmp_path / "linked-copy",
            settings.data_dir,
        )


def test_snapshot_space_cleanup_and_cached_artifact_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "data")
    settings.database_path.write_bytes(b"db")
    settings.attachments_dir.mkdir()
    (settings.attachments_dir / "a.bin").write_bytes(b"abc")
    assert update_executor._managed_tree_size(tmp_path / "missing") == 0
    assert update_executor._managed_tree_size(settings.attachments_dir) == 3

    monkeypatch.setattr(
        update_executor.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1),
    )
    with pytest.raises(RuntimeError, match="DISK_FULL"):
        update_executor._ensure_update_snapshot_space(tmp_path, settings)

    assert update_executor._remove_secure_update_transaction(None)
    assert update_executor._remove_secure_update_transaction(tmp_path / "absent")
    invalid = tmp_path / "bad.name"
    invalid.mkdir()
    assert not update_executor._remove_secure_update_transaction(invalid)

    transaction = tmp_path / "valid-run"
    transaction.mkdir()
    monkeypatch.setattr(
        update_executor.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert not update_executor._remove_secure_update_transaction(transaction)

    artifact = tmp_path / "current.exe"
    assert not update_executor._verify_cached_rollback_artifact(artifact)
    artifact.write_bytes(b"installer")
    digest_path = update_executor._rollback_digest_path(artifact)
    digest_path.write_text("not-a-digest", encoding="ascii")
    assert not update_executor._verify_cached_rollback_artifact(artifact)
    digest_path.write_text("0" * 64, encoding="ascii")
    assert not update_executor._verify_cached_rollback_artifact(artifact)
    digest_path.write_text(hashlib.sha256(b"installer").hexdigest(), encoding="ascii")
    assert update_executor._verify_cached_rollback_artifact(artifact)


def test_cache_interruption_removes_incoming_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.exe"
    source.write_bytes(b"trusted")
    target = tmp_path / "cache" / "current.exe"
    monkeypatch.setattr(
        update_executor,
        "_atomic_replace",
        lambda *_args: (_ for _ in ()).throw(OSError("power loss")),
    )
    with pytest.raises(OSError, match="power loss"):
        update_executor._cache_verified_rollback_artifact(source, target)
    assert not list(target.parent.glob("*.incoming"))
    assert not target.exists()


def test_lock_paths_and_lock_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    assert update_executor._update_lock_path(data_dir) == Path("/var/cache/partyops/update.lock")

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    lock = data_dir / ".update.lock"
    original_write = update_executor.os.write
    monkeypatch.setattr(
        update_executor.os,
        "write",
        lambda *_args: (_ for _ in ()).throw(OSError("disk error")),
    )
    assert not update_executor._acquire_update_lock(lock)
    assert not lock.exists()
    monkeypatch.setattr(update_executor.os, "write", original_write)


def test_manifest_signature_architecture_and_platform_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "data")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    assert not update_executor._verify_manifest_signature({})

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    settings.update_public_key = base64.b64encode(public_key).decode()
    manifest: dict[str, object] = {"format_version": 3, "version": "1.4.3-rc.3"}
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest["signature"] = base64.b64encode(private_key.sign(canonical)).decode()
    assert update_executor._verify_manifest_signature(manifest)
    manifest["version"] = "1.4.3-rc.4"
    assert not update_executor._verify_manifest_signature(manifest)

    monkeypatch.setattr(update_executor.platform, "machine", lambda: "mips64")
    with pytest.raises(RuntimeError, match="架构"):
        update_executor._architecture()
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "x86_64")
    assert update_executor._architecture() == "amd64"

    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    assert update_executor._manifest_platform_name({"format_version": 2}) == "uos"
    monkeypatch.setattr(update_executor, "update_platform_key", lambda _info: "")
    with pytest.raises(RuntimeError, match="无法匹配"):
        update_executor._manifest_platform_name({"format_version": 3})
    with pytest.raises(RuntimeError, match="格式版本"):
        update_executor._manifest_platform_name({"format_version": "3"})
    assert not update_executor._manifest_has_windows_artifact(
        {"format_version": True, "platform_artifacts": {}},
    )


def test_artifact_selection_rejects_manifest_zip_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 将已安装版本固定为上一候选版，避免测试结果随当前构建版本漂移，
    # 并真实覆盖“必须先桥接到最低兼容版本”的拒绝分支。
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(app_version="1.4.3-rc.4"),
    )
    package = tmp_path / "release.partyops-update"
    artifact_name = "PartyOps_1.4.3-rc.6_linux_amd64.deb"
    payload = b"deb-payload"
    monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _manifest: True)

    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(artifact_name, payload)
    base_manifest = {
        "version": "1.4.3-rc.6",
        "platform_artifacts": {"linux-deb": {"amd64": artifact_name}},
        "artifacts": {
            artifact_name: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    with pytest.raises(RuntimeError, match="签名"):
        monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _manifest: False)
        update_executor._select_artifact(
            package, base_manifest, "amd64", tmp_path / "out.deb", "linux-deb"
        )
    monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _manifest: True)

    for manifest, architecture, platform_name, message in (
        (
            {"version": "1.4.3-rc.6", "platform_artifacts": []},
            "amd64",
            "linux-deb",
            "不包含",
        ),
        (
            {
                "version": "1.4.3-rc.6",
                "platform_artifacts": {"linux-deb": {"x86": "partyops-x86.deb"}},
                "artifacts": {"partyops-x86.deb": {}},
            },
            "x86",
            "linux-deb",
            "没有允许",
        ),
        (
            {**base_manifest, "artifacts": {}},
            "amd64",
            "linux-deb",
            "清单不一致",
        ),
    ):
        with pytest.raises(RuntimeError, match=message):
            update_executor._select_artifact(
                package,
                manifest,
                architecture,
                tmp_path / f"{architecture}.out",
                platform_name,
            )

    wrong_size = json.loads(json.dumps(base_manifest))
    wrong_size["artifacts"][artifact_name]["size"] = 1
    with pytest.raises(RuntimeError, match="大小"):
        update_executor._select_artifact(
            package, wrong_size, "amd64", tmp_path / "size.deb", "linux-deb"
        )
    wrong_hash = json.loads(json.dumps(base_manifest))
    wrong_hash["artifacts"][artifact_name]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="哈希"):
        update_executor._select_artifact(
            package, wrong_hash, "amd64", tmp_path / "hash.deb", "linux-deb"
        )

    duplicate = tmp_path / "duplicate.partyops-update"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr(artifact_name, payload)
            archive.writestr(artifact_name, payload)
    with pytest.raises(RuntimeError, match="重复"):
        update_executor._select_artifact(
            duplicate,
            base_manifest,
            "amd64",
            tmp_path / "duplicate.deb",
            "linux-deb",
        )

    special = tmp_path / "special.partyops-update"
    info = zipfile.ZipInfo(artifact_name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(special, "w") as archive:
        archive.writestr(info, payload)
    with pytest.raises(RuntimeError, match="普通文件"):
        update_executor._select_artifact(
            special,
            base_manifest,
            "amd64",
            tmp_path / "special.deb",
            "linux-deb",
        )

    downgrade = json.loads(json.dumps(base_manifest))
    downgrade["version"] = "1.4.3-rc.2"
    with pytest.raises(RuntimeError, match="UPDATE_DOWNGRADE_DENIED"):
        update_executor._select_artifact(
            package,
            downgrade,
            "amd64",
            tmp_path / "downgrade.deb",
            "linux-deb",
        )

    bridge = json.loads(json.dumps(base_manifest))
    bridge["version"] = "1.4.3-rc.6"
    bridge["min_version"] = "1.4.3-rc.6"
    with pytest.raises(RuntimeError, match="UPDATE_BRIDGE_REQUIRED"):
        update_executor._select_artifact(
            package, bridge, "amd64", tmp_path / "bridge.deb", "linux-deb"
        )
    invalid_minimum = json.loads(json.dumps(base_manifest))
    invalid_minimum["min_version"] = "1.4.3-rc.7"
    with pytest.raises(RuntimeError, match="高于目标版本"):
        update_executor._select_artifact(
            package,
            invalid_minimum,
            "amd64",
            tmp_path / "invalid-minimum.deb",
            "linux-deb",
        )


def test_health_schema_wait_and_windows_artifact_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "data")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)

    for response in (
        _Response(_health_payload(), status=503),
        _Response(_health_payload(sqlite=[])),
        _Response(_health_payload(app_version="not-a-version")),
    ):
        monkeypatch.setattr(
            update_executor.urllib.request,
            "urlopen",
            lambda *_args, _response=response, **_kwargs: _response,
        )
        assert not update_executor._health_check("1.4.3-rc.3")

    monkeypatch.setattr(
        update_executor.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(_health_payload()),
    )
    # PEP 440 的 rc 两种写法应等价；期望值必须跟随当前候选版。
    assert update_executor._health_check(APP_VERSION.replace("-rc.", "rc"))

    monkeypatch.setattr(update_executor, "_health_check", lambda _version: False)
    clock = iter([0.0, 0.2, 1.1])
    monkeypatch.setattr(update_executor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(update_executor.time, "sleep", lambda _seconds: None)
    assert not update_executor._wait_for_health("1.4.3-rc.3", 1.0)

    monkeypatch.setattr(update_executor, "detect_platform_info", lambda: object())
    monkeypatch.setattr(update_executor, "update_platform_key", lambda _info: "linux-deb")
    manifest = {
        "format_version": 3,
        "platform_artifacts": {"windows": {"amd64": "PartyOps.exe"}},
    }
    assert not update_executor._manifest_has_windows_artifact(manifest)


def test_set_run_missing_and_terminal_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        def __init__(self, run):
            self.run = run
            self.commits = 0

        def get(self, _model, _run_id):
            return self.run

        def commit(self):
            self.commits += 1

    @contextmanager
    def session_factory(session):
        yield session

    missing = Session(None)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: session_factory(missing),
    )
    update_executor._set_run(
        "missing", status=UpdateStatus.APPLYING, progress=999, message="x"
    )
    assert missing.commits == 0

    run = SimpleNamespace(status=None, progress=0, message="", completed_at=None)
    present = Session(run)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: session_factory(present),
    )
    update_executor._set_run(
        "run", status=UpdateStatus.FAILED, progress=-1, message="x" * 2100
    )
    assert run.progress == 0
    assert len(run.message) == 2000
    assert run.completed_at is not None
    assert present.commits == 1

    run.completed_at = None
    update_executor._set_run(
        "run", status=UpdateStatus.APPLYING, progress=25, message="applying"
    )
    assert run.completed_at is None
    assert run.progress == 25


def test_release_version_floor_native_mapping_and_posix_trust_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        environment="production",
        update_public_key="",
        app_version="1.4.3-rc.2",
    )
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    update_executor._assert_update_not_downgrade(
        {
            "version": "1.4.3-rc.3",
            "min_version": "1.4.3-rc.2",
        }
    )
    assert (
        update_executor._partyops_version_from_native(
            "1:1.4.3~rc.3+dist1", "deb"
        )
        == "1.4.3-rc.3"
    )
    assert update_executor._partyops_version_from_native("raw", "other") == "raw"

    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    assert update_executor._trusted_public_key() == ""


def test_privileged_supervisor_rejects_untrusted_config_and_inherited_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ConfigPath:
        def __init__(self, mode: int, uid: int = 0, attributes: int = 0) -> None:
            self.metadata = SimpleNamespace(
                st_mode=stat.S_IFREG | mode,
                st_uid=uid,
                st_file_attributes=attributes,
            )

        def lstat(self):
            return self.metadata

    assert update_executor._trusted_system_environment_file(ConfigPath(0o600))  # type: ignore[arg-type]
    assert not update_executor._trusted_system_environment_file(ConfigPath(0o622))  # type: ignore[arg-type]
    assert not update_executor._trusted_system_environment_file(ConfigPath(0o600, uid=1000))  # type: ignore[arg-type]
    assert not update_executor._trusted_system_environment_file(
        ConfigPath(0o600, attributes=0x400)  # type: ignore[arg-type]
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    captured: dict[str, object] = {}
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    monkeypatch.setenv("PARTYOPS_UPDATE_PUBLIC_KEY", "attacker-key")
    monkeypatch.setenv("UNRELATED_SAFE_VALUE", "kept")
    monkeypatch.setattr(
        update_executor,
        "_candidate_host_environments",
        lambda: [{"PARTYOPS_DATA_DIR": str(data_dir), "PARTYOPS_MODE": "host"}],
    )
    monkeypatch.setattr(update_executor, "_pending_run_id", lambda _path: "run-safe")
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command, **kwargs),
    )
    assert update_executor.run_supervisor(once=True) == 0
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert "PARTYOPS_UPDATE_PUBLIC_KEY" not in environment
    assert environment["UNRELATED_SAFE_VALUE"] == "kept"
    assert environment["PARTYOPS_DATA_DIR"] == str(data_dir)
