"""系统更新执行器的签名、架构、幂等和路径安全回归。"""

from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
import json
import os
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import update_executor
from app.enums import UpdateStatus
from app.models import UpdatePackage, UpdateRun


def _signed_manifest(
    private_key: Ed25519PrivateKey,
    filename: str,
    payload: bytes,
    *,
    version: str = "1.3.3",
) -> dict:
    manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": version,
        "architecture_artifacts": {"amd64": filename},
        "artifacts": {
            filename: {
                "size": len(payload),
                "sha256": update_executor.hashlib.sha256(payload).hexdigest(),
            }
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
    return manifest


def _public_key(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _write_update(path: Path, manifest: dict, filename: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False),
        )
        archive.writestr(filename, payload)


@pytest.mark.parametrize(
    "name",
    ["../partyops_amd64.deb", "/partyops_amd64.deb", "..\\partyops_amd64.deb", "bad\x00name"],
)
def test_safe_member_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(RuntimeError, match="非法路径"):
        update_executor._safe_member(name)


def test_manifest_signature_accepts_only_untampered_payload(monkeypatch) -> None:
    private_key = Ed25519PrivateKey.generate()
    manifest = _signed_manifest(
        private_key,
        "partyops_1.3.3_amd64.deb",
        b"deb-payload",
    )
    monkeypatch.setattr(
        update_executor,
        "_trusted_public_key",
        lambda: _public_key(private_key),
    )
    assert update_executor._verify_manifest_signature(manifest) is True
    manifest["version"] = "9.9.9"
    assert update_executor._verify_manifest_signature(manifest) is False


def test_select_artifact_checks_signature_size_hash_and_architecture(
    monkeypatch,
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    filename = "partyops_1.3.3_amd64.deb"
    payload = b"verified-debian-package"
    manifest = _signed_manifest(private_key, filename, payload)
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(app_version="1.3.2"),
    )
    package = tmp_path / "partyops_1.3.3.partyops-update"
    _write_update(package, manifest, filename, payload)
    monkeypatch.setattr(
        update_executor,
        "_trusted_public_key",
        lambda: _public_key(private_key),
    )

    target = tmp_path / "selected.deb"
    assert update_executor._select_artifact(
        package, manifest, "amd64", target
    ) == target
    assert target.read_bytes() == payload

    with pytest.raises(RuntimeError, match="不包含 arm64"):
        update_executor._select_artifact(
            package, manifest, "arm64", tmp_path / "arm64.deb"
        )

    tampered = dict(manifest)
    tampered["artifacts"] = {
        filename: {"size": len(payload), "sha256": "0" * 64}
    }
    with pytest.raises(RuntimeError, match="发布签名无效"):
        update_executor._select_artifact(
            package, tampered, "amd64", tmp_path / "tampered.deb"
        )


def test_select_windows_platform_artifact_keeps_legacy_uos_mapping(
    monkeypatch,
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    deb_name = "partyops_1.4.0_amd64.deb"
    exe_name = "PartyOps_1.4.0_windows_amd64.exe"
    payloads = {deb_name: b"uos-runtime", exe_name: b"windows-runtime"}
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(app_version="1.3.9"),
    )
    manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "1.4.0",
        "architecture_artifacts": {"amd64": deb_name},
        "platform_artifacts": {
            "uos": {"amd64": deb_name},
            "windows": {"amd64": exe_name},
        },
        "artifacts": {
            name: {
                "size": len(payload),
                "sha256": update_executor.hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in payloads.items()
        },
    }
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(private_key.sign(canonical)).decode("ascii")
    package = tmp_path / "partyops_1.4.0.partyops-update"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    monkeypatch.setattr(update_executor, "_trusted_public_key", lambda: _public_key(private_key))
    selected = update_executor._select_artifact(
        package,
        manifest,
        "amd64",
        tmp_path / "selected.exe",
        "windows",
    )
    assert selected.read_bytes() == payloads[exe_name]
    assert manifest["architecture_artifacts"]["amd64"] == deb_name


def test_same_version_device_update_still_verifies_package(
    monkeypatch,
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    filename = "partyops_1.3.3_amd64.deb"
    payload = b"same-version-deb"
    manifest = _signed_manifest(private_key, filename, payload)
    package = tmp_path / "partyops_1.3.3.partyops-update"
    _write_update(package, manifest, filename, payload)
    transfers = tmp_path / "transfers"
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(transfers_dir=transfers, app_version="1.3.3"),
    )
    # v2 清单没有平台维度；显式模拟 Linux Agent，避免 Windows 测试机
    # 将该历史 DEB 清单按 Windows 制品解释。
    monkeypatch.setattr(update_executor, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: "uos")
    monkeypatch.setattr(update_executor, "_installed_package_version", lambda: "1.3.3")
    monkeypatch.setattr(
        update_executor,
        "_trusted_public_key",
        lambda: _public_key(private_key),
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: commands.append(command),
    )

    assert update_executor.install_device_package(package) is True
    assert commands == []
    # 高权限更新器不再在普通用户可写的 transfers 中解压可执行制品。
    assert not transfers.exists()
    assert not list((tmp_path / "upgrade-backups").glob("device-*"))

    manifest["signature"] = base64.b64encode(b"invalid").decode("ascii")
    _write_update(package, manifest, filename, payload)
    assert update_executor.install_device_package(package) is False


def test_device_update_handles_invalid_zip_and_dpkg_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    transfers = tmp_path / "transfers"
    settings = SimpleNamespace(transfers_dir=transfers)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    broken = tmp_path / "broken.partyops-update"
    broken.write_bytes(b"not-a-zip")
    assert update_executor.install_device_package(broken) is False

    private_key = Ed25519PrivateKey.generate()
    filename = "partyops_1.3.3_amd64.deb"
    payload = b"upgrade-deb"
    manifest = _signed_manifest(private_key, filename, payload, version="1.3.4")
    package = tmp_path / "upgrade.partyops-update"
    _write_update(package, manifest, filename, payload)
    monkeypatch.setattr(
        update_executor,
        "_trusted_public_key",
        lambda: _public_key(private_key),
    )
    monkeypatch.setattr(update_executor, "_installed_package_version", lambda: "1.3.3")
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "failed"),
    )
    assert update_executor.install_device_package(package) is False


def test_dpkg_preflight_repairs_pending_configuration(monkeypatch) -> None:
    """安装前先收敛 dpkg 半配置状态，失败时不得继续覆盖程序。"""

    commands: list[list[str]] = []

    def successful(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(update_executor, "_run", successful)
    assert update_executor._ensure_dpkg_ready() is True
    assert commands == [["dpkg", "--audit"]]

    commands.clear()

    def partyops_pending(command, **_kwargs):
        commands.append(command)
        if command[:2] == ["dpkg", "--audit"]:
            return subprocess.CompletedProcess(
                command, 0, " partyops 软件包尚未完成配置\n", ""
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(update_executor, "_run", partyops_pending)
    assert update_executor._ensure_dpkg_ready() is True
    assert commands == [
        ["dpkg", "--audit"],
        ["dpkg", "--audit", "partyops"],
        ["dpkg", "--configure", "partyops"],
    ]

    commands.clear()

    def unrelated_pending(command, **_kwargs):
        commands.append(command)
        output = (
            " partyops 软件包尚未完成配置\n other-package 软件包尚未完成配置\n"
            if command == ["dpkg", "--audit"]
            else " partyops 软件包尚未完成配置\n"
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(update_executor, "_run", unrelated_pending)
    assert update_executor._ensure_dpkg_ready() is False
    assert commands == [["dpkg", "--audit"], ["dpkg", "--audit", "partyops"]]

    def partyops_audit_failure(command, **_kwargs):
        if command == ["dpkg", "--audit"]:
            return subprocess.CompletedProcess(command, 0, " partyops 待配置\n", "")
        return subprocess.CompletedProcess(command, 2, "", "查询失败")

    monkeypatch.setattr(update_executor, "_run", partyops_audit_failure)
    assert update_executor._ensure_dpkg_ready() is False

    def empty_partyops_audit(command, **_kwargs):
        output = " partyops 待配置\n" if command == ["dpkg", "--audit"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(update_executor, "_run", empty_partyops_audit)
    assert update_executor._ensure_dpkg_ready() is False

    def configure_failure(command, **_kwargs):
        if command[:2] == ["dpkg", "--audit"]:
            return subprocess.CompletedProcess(command, 0, " partyops 待配置\n", "")
        return subprocess.CompletedProcess(command, 1, "", "配置失败")

    monkeypatch.setattr(update_executor, "_run", configure_failure)
    assert update_executor._ensure_dpkg_ready() is False

    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "broken"),
    )
    assert update_executor._ensure_dpkg_ready() is False


def test_restore_tree_cannot_escape_data_root(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "data.txt").write_text("ok", encoding="utf-8")
    data_root = tmp_path / "data"
    destination = data_root / "attachments"
    update_executor._restore_managed_tree(backup, destination, data_root)
    assert (destination / "data.txt").read_text(encoding="utf-8") == "ok"
    with pytest.raises(RuntimeError, match="超出"):
        update_executor._restore_managed_tree(
            backup,
            tmp_path / "outside",
            data_root,
        )


def test_read_environment_and_pending_run_are_fault_tolerant(tmp_path: Path) -> None:
    environment = tmp_path / "partyops.env"
    environment.write_text(
        "\n".join(
            [
                "# 注释",
                "PARTYOPS_MODE=host",
                f'PARTYOPS_DATA_DIR="{tmp_path / "业务数据"}"',
                "PARTYOPS_EMPTY=   ",
                "UNRELATED=value",
                "PARTYOPS_BROKEN='unterminated",
            ]
        ),
        encoding="utf-8",
    )
    values = update_executor._read_environment(environment)
    assert values["PARTYOPS_MODE"] == "host"
    assert values["PARTYOPS_DATA_DIR"].endswith("业务数据")
    assert values["PARTYOPS_EMPTY"] == ""
    assert "UNRELATED" not in values
    assert "PARTYOPS_BROKEN" not in values

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert update_executor._pending_run_id(data_dir) is None
    database = data_dir / "partyops.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE update_runs (id TEXT, target_device_id TEXT, status TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO update_runs VALUES ('run-2', NULL, 'APPLYING', '2026-08-03T10:01:00')"
        )
        connection.execute(
            "INSERT INTO update_runs VALUES ('run-1', NULL, 'APPLYING', '2026-08-03T10:00:00')"
        )
    assert update_executor._pending_run_id(data_dir) == "run-1"


def test_update_lock_rejects_live_owner_and_recovers_stale_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """活跃升级不得并发，异常退出留下的锁必须能够自动恢复。"""

    lock_path = tmp_path / ".update.lock"
    monkeypatch.setattr(update_executor, "_system_boot_id", lambda: "boot-current")
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "boot_id": "boot-current"}),
        encoding="utf-8",
    )
    assert update_executor._acquire_update_lock(lock_path) is False

    lock_path.write_text(
        json.dumps({"pid": 999_999_999, "boot_id": "boot-current"}),
        encoding="utf-8",
    )
    assert update_executor._acquire_update_lock(lock_path) is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["boot_id"] == "boot-current"


def test_legacy_update_lock_uses_grace_period(monkeypatch, tmp_path: Path) -> None:
    """兼容旧版空锁：短时间内保护，确认陈旧后再自动接管。"""

    lock_path = tmp_path / ".update.lock"
    lock_path.touch()
    monkeypatch.setattr(update_executor.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        update_executor,
        "LEGACY_UPDATE_LOCK_GRACE_SECONDS",
        300,
    )
    os.utime(lock_path, (900.0, 900.0))
    assert update_executor._update_lock_is_stale(lock_path) is False
    os.utime(lock_path, (600.0, 600.0))
    assert update_executor._update_lock_is_stale(lock_path) is True


def test_execute_host_update_success_and_failure_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    updates = tmp_path / "updates"
    updates.mkdir()
    package_path = updates / "release.partyops-update"
    fixture_name = "partyops_1.3.3_amd64.deb"
    fixture_payload = b"fixture"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": "1.3.3",
                    "artifacts": {
                        fixture_name: {
                            "size": len(fixture_payload),
                            "sha256": hashlib.sha256(fixture_payload).hexdigest(),
                        }
                    },
                }
            ),
        )
        archive.writestr(fixture_name, fixture_payload)
    package = SimpleNamespace(
        id="package-1",
        filename=package_path.name,
        sha256=update_executor._hash(package_path),
        status=UpdateStatus.APPLYING,
    )
    run = SimpleNamespace(
        id="run-1",
        package_id=package.id,
        target_device_id=None,
        status=UpdateStatus.APPLYING,
        progress=0,
        message="",
        completed_at=None,
    )

    class FakeSession:
        def get(self, model, identifier):
            if model is UpdateRun and identifier == run.id:
                return run
            if model is UpdatePackage and identifier == package.id:
                return package
            return None

        def commit(self) -> None:
            return None

    @contextmanager
    def session_factory():
        yield FakeSession()

    data_dir = tmp_path / "data"
    settings = SimpleNamespace(
        data_dir=data_dir,
        updates_dir=updates,
        database_path=data_dir / "partyops.db",
        attachments_dir=data_dir / "attachments",
        archives_dir=data_dir / "archives",
        transfers_dir=data_dir / "transfers",
    )
    settings.transfers_dir.mkdir(parents=True)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", session_factory)
    progress_states: list[tuple[UpdateStatus, int, str]] = []

    def record_run(_run_id, *, status, progress, message):
        progress_states.append((status, progress, message))

    monkeypatch.setattr(update_executor, "_set_run", record_run)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: "uos")
    monkeypatch.setattr(update_executor, "_health_check", lambda *_args: True)
    monkeypatch.setattr(update_executor, "_queue_device_updates", lambda *_args: 0)
    commands: list[list[str]] = []

    def successful_command(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(update_executor, "_run", successful_command)

    def snapshot(destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"rollback")

    monkeypatch.setattr(update_executor, "_create_installed_package_snapshot", snapshot)

    def select_artifact(_package, _manifest, _arch, target: Path) -> Path:
        target.write_bytes(b"deb")
        return target

    monkeypatch.setattr(update_executor, "_select_artifact", select_artifact)
    assert update_executor.execute_host_update(run.id) is True
    assert run.status == UpdateStatus.COMPLETED
    assert package.status == UpdateStatus.COMPLETED
    assert not (data_dir / ".update.lock").exists()

    run.status = UpdateStatus.APPLYING
    package.status = UpdateStatus.APPLYING
    monkeypatch.setattr(
        update_executor,
        "_select_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("拒绝制品")),
    )
    commands_before_preflight_failure = list(commands)
    assert update_executor.execute_host_update(run.id) is False
    # 制品在停服和 dpkg 修改程序之前即被拒绝，此时不得伪装成“已回滚”，
    # 更不得为了回滚一个从未发生的变更而触碰正在运行的旧版本。
    assert progress_states[-1][0] == UpdateStatus.FAILED
    assert "原版本保持不变" in progress_states[-1][2]
    new_commands = commands[len(commands_before_preflight_failure) :]
    assert new_commands == [["dpkg", "--audit"]]
    assert not any(command[:2] == ["systemctl", "stop"] for command in new_commands)
    assert not any(command[:2] == ["dpkg", "--unpack"] for command in new_commands)
    assert not (data_dir / ".update.lock").exists()

    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: "windows")
    monkeypatch.setattr(update_executor, "_manifest_has_windows_artifact", lambda _manifest: False)
    commands_before_wrong_platform = list(commands)
    assert update_executor.execute_host_update(run.id) is False
    assert "不包含当前 Windows" in progress_states[-1][2]
    assert commands == commands_before_wrong_platform
