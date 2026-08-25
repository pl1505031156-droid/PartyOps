"""rc.3 最终事务新增失败分支的覆盖率门禁。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from app import setup_wizard, update_executor
from app.enums import UpdateStatus


def _os_proxy(name: str, **overrides: object) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(os), "name": name, **overrides})


def _personal_settings(tmp_path: Path) -> SimpleNamespace:
    data = tmp_path / "personal"
    updates = data / "updates"
    backups = data / "backups"
    updates.mkdir(parents=True)
    backups.mkdir()
    return SimpleNamespace(
        mode="personal",
        data_dir=data,
        updates_dir=updates,
        backups_dir=backups,
        database_path=data / "partyops.db",
        port=18775,
        agent_port=18776,
    )


@pytest.mark.parametrize(
    ("raw", "valid"),
    [("", False), ("abc", False), ("0", False), ("1000", True)],
)
def test_pkexec_uid_validation_matrix(monkeypatch, raw: str, valid: bool) -> None:
    monkeypatch.setenv("PKEXEC_UID", raw)
    if valid:
        assert update_executor._pkexec_desktop_uid() == 1000
    else:
        with pytest.raises(RuntimeError, match="桌面账号"):
            update_executor._pkexec_desktop_uid()


def test_personal_transaction_path_boundary_matrix(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    valid = root / "valid.pkg"
    valid.write_bytes(b"pkg")
    outside = tmp_path / "outside.pkg"
    outside.write_bytes(b"pkg")
    with pytest.raises(RuntimeError, match="路径无效"):
        update_executor._personal_transaction_file(Path("relative"), root, "包")
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _p: True)
    with pytest.raises(RuntimeError, match="路径无效"):
        update_executor._personal_transaction_file(valid, root, "包")
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _p: False)
    with pytest.raises(RuntimeError, match="不属于"):
        update_executor._personal_transaction_file(outside, root, "包")
    with pytest.raises((FileNotFoundError, RuntimeError)):
        update_executor._personal_transaction_file(root / "missing", root, "包")
    assert (
        update_executor._personal_transaction_file(valid, root, "包") == valid.resolve()
    )


@pytest.mark.parametrize("uid", [0, -1])
def test_restart_linux_personal_rejects_invalid_desktop_uid(
    monkeypatch, tmp_path: Path, uid: int
) -> None:
    settings = _personal_settings(tmp_path)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.sys, "frozen", False, raising=False)
    assert update_executor._restart_linux_personal_runtime(uid) == (False, None)


def test_restart_linux_personal_runtime_missing_account_runuser_and_success(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _personal_settings(tmp_path)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        update_executor,
        "os",
        _os_proxy("posix", geteuid=lambda: 0, fchown=lambda *_args: None),
    )
    account = SimpleNamespace(pw_name="desktop", pw_dir=str(tmp_path), pw_gid=1000)
    fake_pwd = SimpleNamespace(getpwuid=lambda _uid: account)
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)

    fake_pwd.getpwuid = lambda _uid: (_ for _ in ()).throw(KeyError("missing"))
    assert update_executor._restart_linux_personal_runtime(1000) == (False, None)
    fake_pwd.getpwuid = lambda _uid: account
    monkeypatch.setattr(update_executor.shutil, "which", lambda _name: None)
    assert update_executor._restart_linux_personal_runtime(1000) == (False, None)

    runuser = tmp_path / "runuser"
    runuser.write_bytes(b"exe")
    monkeypatch.setattr(update_executor.shutil, "which", lambda _name: str(runuser))
    spawned: list[tuple[list[str], dict[str, object]]] = []
    process = SimpleNamespace(pid=123)
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **kwargs: spawned.append((command, kwargs)) or process,
    )
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_args: True)
    assert update_executor._restart_linux_personal_runtime(1000) == (True, process)
    assert spawned[0][0][:4] == [str(runuser), "-u", "desktop", "--"]
    assert spawned[0][1]["env"]["HOME"] == str(tmp_path)
    assert "PKEXEC_UID" not in spawned[0][1]["env"]

    spawned.clear()
    assert update_executor._restart_linux_personal_runtime(None) == (True, process)
    assert "-u" not in spawned[0][0]


def test_restart_linux_personal_runtime_missing_executable(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _personal_settings(tmp_path)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        update_executor.sys, "executable", str(tmp_path / "missing-updater")
    )
    assert update_executor._restart_linux_personal_runtime() == (False, None)


def test_restore_personal_database_owner_matrix(monkeypatch, tmp_path: Path) -> None:
    settings = _personal_settings(tmp_path)
    backup = settings.backups_dir / "backup.partyops-backup"
    backup.write_bytes(b"backup")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    ownership: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(
        update_executor,
        "os",
        _os_proxy(
            "posix", chown=lambda path, uid, gid: ownership.append((path, uid, gid))
        ),
    )

    def restore(_backup: Path) -> None:
        settings.database_path.write_bytes(b"db")

    monkeypatch.setattr(
        update_executor, "restore_database_from_upgrade_backup", restore
    )
    account = SimpleNamespace(pw_gid=2000)
    monkeypatch.setitem(
        sys.modules, "pwd", SimpleNamespace(getpwuid=lambda _uid: account)
    )
    update_executor._restore_personal_database_as_user(backup, 1000)
    assert ownership[-1][1:] == (1000, 2000)

    settings.database_path.chmod(0o640)
    update_executor._restore_personal_database_as_user(backup, 1001)
    assert ownership[-1][1] == 1001


@pytest.mark.parametrize(
    ("os_name", "platform", "geteuid", "run_id"),
    [
        ("nt", "linux", 0, "run-1"),
        ("posix", "win32", 0, "run-1"),
        ("posix", "linux", 1, "run-1"),
        ("posix", "linux", 0, "bad/run"),
    ],
)
def test_linux_personal_root_transaction_platform_guards(
    monkeypatch,
    tmp_path: Path,
    os_name: str,
    platform: str,
    geteuid: int,
    run_id: str,
) -> None:
    monkeypatch.setattr(
        update_executor, "os", _os_proxy(os_name, geteuid=lambda: geteuid)
    )
    monkeypatch.setattr(update_executor.sys, "platform", platform)
    assert (
        update_executor.execute_linux_personal_root_transaction(
            run_id, tmp_path / "pkg", tmp_path / "backup"
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )


def _configure_root_transaction(
    monkeypatch, tmp_path: Path
) -> tuple[SimpleNamespace, Path, Path, list[str]]:
    settings = _personal_settings(tmp_path)
    package = settings.updates_dir / "release.partyops-update"
    backup = settings.backups_dir / "pre.partyops-backup"
    package.write_bytes(b"signed")
    backup.write_bytes(b"backup")
    events: list[str] = []
    lock = tmp_path / "system" / "update.lock"
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _path: lock)
    monkeypatch.setattr(
        update_executor,
        "_acquire_update_lock",
        lambda path: (
            path.parent.mkdir(parents=True, exist_ok=True)
            or path.write_text("lock", encoding="utf-8")
            or True
        ),
    )
    monkeypatch.setattr(
        update_executor, "_pkexec_desktop_uid", lambda: settings.data_dir.stat().st_uid
    )
    monkeypatch.setattr(update_executor, "verify_backup", lambda _path: None)
    monkeypatch.setattr(
        update_executor,
        "_read_update_manifest",
        lambda _path: {"version": "1.4.3-rc.3"},
    )
    monkeypatch.setattr(
        update_executor, "_verify_manifest_signature", lambda _manifest: True
    )
    monkeypatch.setattr(
        update_executor, "_assert_update_not_downgrade", lambda _manifest: None
    )
    monkeypatch.setattr(
        update_executor, "_manifest_platform_name", lambda _manifest: "linux-rpm"
    )
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix", geteuid=lambda: 0))
    return settings, package, backup, events


def test_linux_personal_root_transaction_lock_owner_signature_and_platform_failures(
    monkeypatch, tmp_path: Path
) -> None:
    settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: False)
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-lock", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )

    _configure_root_transaction(monkeypatch, tmp_path / "owner")
    monkeypatch.setattr(update_executor, "_pkexec_desktop_uid", lambda: 999999)
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-owner", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )

    settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path / "signature"
    )
    monkeypatch.setattr(
        update_executor, "_verify_manifest_signature", lambda _manifest: False
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-signature", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )

    settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path / "platform"
    )
    monkeypatch.setattr(
        update_executor, "_manifest_platform_name", lambda _manifest: "windows"
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-platform", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )


class _Process:
    def __init__(self, *, timeout: bool = False) -> None:
        self.timeout = timeout
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout: int):
        if self.timeout and timeout == 20:
            raise subprocess.TimeoutExpired("partyops", timeout)
        return 0

    def kill(self):
        self.killed = True


def test_linux_personal_root_transaction_repeated_version_health_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: "1.4.3-rc.3"
    )
    process = _Process(timeout=True)
    outcomes = iter([(True, None), (False, process)])
    monkeypatch.setattr(
        update_executor, "_restart_linux_personal_runtime", lambda _uid: next(outcomes)
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-repeat-1", package, backup
        )
        == 0
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-repeat-2", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )
    assert process.terminated and process.killed


@pytest.mark.parametrize(
    ("installed", "current", "expected"),
    [
        (False, "1.4.2", update_executor.LINUX_PERSONAL_TRANSACTION_ROLLED_BACK),
        (False, "broken", update_executor.LINUX_PERSONAL_TRANSACTION_FAILED),
    ],
)
def test_linux_personal_root_transaction_install_failure_result_matrix(
    monkeypatch,
    tmp_path: Path,
    installed: bool,
    current: str,
    expected: int,
) -> None:
    _settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path
    )
    versions = iter(["1.4.2", current])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    monkeypatch.setattr(
        update_executor, "install_device_package", lambda *_args, **_kwargs: installed
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-install", package, backup
        )
        == expected
    )


def test_linux_personal_root_transaction_success_cleanup_warning_and_rollback_failures(
    monkeypatch, tmp_path: Path
) -> None:
    _settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path
    )
    versions = iter(["1.4.2", "1.4.3-rc.3", "1.4.2"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    monkeypatch.setattr(
        update_executor, "install_device_package", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        update_executor, "_restart_linux_personal_runtime", lambda _uid: (True, None)
    )
    monkeypatch.setattr(
        update_executor,
        "_discard_personal_native_rollback",
        lambda _run: (_ for _ in ()).throw(OSError("busy")),
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-success", package, backup
        )
        == 0
    )

    _settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path / "rollback"
    )
    versions = iter(["1.4.2", "1.4.3-rc.3", "1.4.3-rc.3"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    monkeypatch.setattr(
        update_executor, "install_device_package", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        update_executor, "_restart_linux_personal_runtime", lambda _uid: (False, None)
    )
    monkeypatch.setattr(
        update_executor, "_rollback_linux_personal_package_locked", lambda _run: False
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-rollback", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )


def test_linux_personal_root_transaction_remaining_health_and_exception_paths(
    monkeypatch, tmp_path: Path
) -> None:
    _settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path / "same-no-process"
    )
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: "1.4.3-rc.3"
    )
    monkeypatch.setattr(
        update_executor, "_restart_linux_personal_runtime", lambda _uid: (False, None)
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-same-fail", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )

    _settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path / "restored-unhealthy"
    )
    versions = iter(["1.4.2", "1.4.3-rc.3", "1.4.2"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    monkeypatch.setattr(
        update_executor, "install_device_package", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        update_executor,
        "_restart_linux_personal_runtime",
        lambda _uid: (False, None),
    )
    monkeypatch.setattr(
        update_executor, "_rollback_linux_personal_package_locked", lambda _run: True
    )
    monkeypatch.setattr(
        update_executor, "_restore_personal_database_as_user", lambda *_args: None
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-restored-unhealthy", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_FAILED
    )

    _settings, package, backup, _events = _configure_root_transaction(
        monkeypatch, tmp_path / "exception-rollback"
    )
    versions = iter(["1.4.2", "broken", "1.4.3-rc.3"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    monkeypatch.setattr(
        update_executor, "install_device_package", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        update_executor, "_rollback_linux_personal_package_locked", lambda _run: True
    )
    monkeypatch.setattr(
        update_executor, "_restore_personal_database_as_user", lambda *_args: None
    )
    monkeypatch.setattr(
        update_executor,
        "_restart_linux_personal_runtime",
        lambda _uid: (True, None),
    )
    assert (
        update_executor.execute_linux_personal_root_transaction(
            "run-exception-rollback", package, backup
        )
        == update_executor.LINUX_PERSONAL_TRANSACTION_ROLLED_BACK
    )


class _PersonalUpdateSession:
    def __init__(self, run: object, package: object, backup: object = None) -> None:
        self.run = run
        self.package = package
        self.backup = backup

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, model, _identity):
        return self.run if model is update_executor.UpdateRun else self.package

    def scalar(self, _statement):
        return self.backup


def _configure_user_personal_update(
    monkeypatch, tmp_path: Path
) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    settings = _personal_settings(tmp_path)
    package_path = settings.updates_dir / "release.partyops-update"
    package_path.write_bytes(b"signed")
    backup_path = settings.backups_dir / "pre.partyops-backup"
    backup_path.write_bytes(b"backup")
    run = SimpleNamespace(
        id="run-user",
        package_id="package",
        target_device_id=None,
        status=UpdateStatus.APPLYING,
        created_by="admin",
    )
    package = SimpleNamespace(
        id="package",
        filename=package_path.name,
        sha256=update_executor._hash(package_path),
        signature_valid=True,
    )
    backup = SimpleNamespace(filename=backup_path.name)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: _PersonalUpdateSession(run, package, backup),
    )
    monkeypatch.setattr(update_executor.db_runtime, "dispose", lambda: None)
    monkeypatch.setattr(update_executor, "_set_run", lambda *_args, **_kwargs: None)
    return settings, run, package, backup


@pytest.mark.parametrize(
    ("os_name", "platform", "run_id"),
    [
        ("nt", "linux", "run-1"),
        ("posix", "win32", "run-1"),
        ("posix", "linux", "bad/run"),
    ],
)
def test_linux_personal_user_coordinator_platform_guards(
    monkeypatch, os_name: str, platform: str, run_id: str
) -> None:
    monkeypatch.setattr(update_executor, "os", _os_proxy(os_name))
    monkeypatch.setattr(update_executor.sys, "platform", platform)
    assert not update_executor.execute_linux_personal_update(run_id)


def test_linux_personal_user_coordinator_context_and_run_validation_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    settings, run, package, backup = _configure_user_personal_update(
        monkeypatch, tmp_path
    )
    settings.mode = "host"
    assert not update_executor.execute_linux_personal_update(run.id)
    settings.mode = "personal"

    cases = [
        (None, package),
        (SimpleNamespace(**{**vars(run), "target_device_id": "device"}), package),
        (SimpleNamespace(**{**vars(run), "status": UpdateStatus.FAILED}), package),
        (run, None),
        (run, SimpleNamespace(**{**vars(package), "signature_valid": False})),
    ]
    for invalid_run, invalid_package in cases:
        monkeypatch.setattr(
            update_executor.db_runtime,
            "session_factory",
            lambda r=invalid_run, p=invalid_package: _PersonalUpdateSession(
                r, p, backup
            ),
        )
        assert not update_executor.execute_linux_personal_update(run.id)


def test_linux_personal_user_coordinator_package_backup_and_result_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    settings, run, package, backup = _configure_user_personal_update(
        monkeypatch, tmp_path
    )
    package_path = settings.updates_dir / package.filename
    package_path.unlink()
    assert not update_executor.execute_linux_personal_update(run.id)


    package_path.write_bytes(b"tampered")
    assert not update_executor.execute_linux_personal_update(run.id)
    package.sha256 = update_executor._hash(package_path)

    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: _PersonalUpdateSession(run, package, None),
    )
    assert not update_executor.execute_linux_personal_update(run.id)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: _PersonalUpdateSession(run, package, backup),
    )
    (settings.backups_dir / backup.filename).unlink()
    assert not update_executor.execute_linux_personal_update(run.id)
    (settings.backups_dir / backup.filename).write_bytes(b"backup")

    completed: list[str] = []
    rolled_back: list[str] = []
    monkeypatch.setattr(
        update_executor,
        "_complete_personal_update_run",
        lambda run_id, **_kwargs: completed.append(run_id),
    )
    monkeypatch.setattr(
        update_executor,
        "_record_restored_personal_update_run",
        lambda run_id, **_kwargs: rolled_back.append(run_id),
    )
    results = iter(
        [
            update_executor.LINUX_PERSONAL_TRANSACTION_COMPLETED,
            update_executor.LINUX_PERSONAL_TRANSACTION_ROLLED_BACK,
            update_executor.LINUX_PERSONAL_TRANSACTION_FAILED,
        ]
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: (
            commands.append(command)
            or subprocess.CompletedProcess(command, next(results), "", "")
        ),
    )
    monkeypatch.setattr(update_executor.sys, "frozen", False, raising=False)
    assert update_executor.execute_linux_personal_update(run.id)
    assert not update_executor.execute_linux_personal_update(run.id)
    assert not update_executor.execute_linux_personal_update(run.id)
    assert completed == [run.id] and rolled_back == [run.id]
    assert "-m" in commands[0]

    frozen_updater = tmp_path / "partyops-updater"
    frozen_updater.write_bytes(b"exe")
    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_executor.sys, "executable", str(frozen_updater))
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("pkexec missing")),
    )
    assert not update_executor.execute_linux_personal_update(run.id)


def test_windows_data_migration_lock_failure_and_release_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "update.lock"
    monkeypatch.setattr(
        setup_wizard,
        "_windows_privileged_update_lock_path",
        lambda: lock_path,
    )
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: False)
    with pytest.raises(ValueError, match="更新事务"):
        setup_wizard._acquire_windows_data_migration_lock()

    setup_wizard._release_windows_data_migration_lock(None)
    lock_path.write_text("locked", encoding="utf-8")
    setup_wizard._release_windows_data_migration_lock(lock_path)
    assert not lock_path.exists()

    class _BusyLock:
        def unlink(self, **_kwargs):
            raise OSError("busy")

    with pytest.raises(ValueError, match="事务锁未能安全释放"):
        setup_wizard._release_windows_data_migration_lock(_BusyLock())


def test_windows_service_start_config_early_and_restore_validation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    assert setup_wizard._windows_service_start_config("PartyOpsHost") is None
    setup_wizard._restore_windows_service_start_config("PartyOpsHost", (2, False))

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    assert setup_wizard._windows_service_start_config("PartyOpsHost") is None
    setup_wizard._restore_windows_service_start_config("PartyOpsHost", None)
    setup_wizard._restore_windows_service_start_config("PartyOpsHost", (2, False))

    monkeypatch.delenv("PARTYOPS_ENVIRONMENT", raising=False)
    with pytest.raises(ValueError, match="未知启动类型"):
        setup_wizard._restore_windows_service_start_config(
            "PartyOpsHost", (99, False)
        )


@pytest.mark.parametrize("transaction_id", [123, "bad/id"])
def test_windows_host_switch_snapshot_rejects_invalid_transaction_id(
    monkeypatch, tmp_path: Path, transaction_id: object
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        __import__("json").dumps(
            {
                "format_version": 1,
                "transaction_id": transaction_id,
                "previous_mode": None,
                "services": {
                    "PartyOpsHost": {"start_type": 3, "delayed": False},
                    "PartyOpsUpdateService": {
                        "start_type": 3,
                        "delayed": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_wizard, "_windows_host_switch_snapshot_path", lambda: snapshot
    )
    with pytest.raises(ValueError, match="事务编号"):
        setup_wizard._validated_windows_host_switch_snapshot()


def test_windows_host_switch_finalize_and_restore_dispatch_matrix(monkeypatch) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    with pytest.raises(ValueError, match="管理员权限"):
        setup_wizard._finalize_windows_host_switch_privileged("t" * 32)

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_host_switch_privileged",
        lambda transaction_id="": calls.append(("restore", transaction_id)),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_finalize_windows_host_switch_privileged",
        lambda transaction_id: calls.append(("finalize", transaction_id)),
    )
    setup_wizard._run_windows_host_switch_helper(
        "--privileged-restore-host", "r" * 32
    )
    setup_wizard._run_windows_host_switch_helper("--privileged-restore-host")
    setup_wizard._run_windows_host_switch_helper(
        "--privileged-finalize-host-switch", "f" * 32
    )
    with pytest.raises(ValueError, match="动作无效"):
        setup_wizard._run_windows_host_switch_helper("--invalid")
    assert calls == [
        ("restore", "r" * 32),
        ("restore", ""),
        ("finalize", "f" * 32),
    ]

    calls.clear()
    setup_wizard.finalize_windows_host_switch(None)
    setup_wizard.finalize_windows_host_switch(True)
    setup_wizard.finalize_windows_host_switch("f" * 32)
    setup_wizard.restore_windows_host_after_failed_switch(None)
    setup_wizard.restore_windows_host_after_failed_switch("r" * 32)
    assert calls == [("finalize", "f" * 32), ("restore", "r" * 32)]


@pytest.mark.parametrize(
    ("flag", "function_name"),
    [
        ("--privileged-disable-host", "_deactivate_windows_host_services_privileged"),
        ("--privileged-restore-host", "_restore_windows_host_switch_privileged"),
        (
            "--privileged-finalize-host-switch",
            "_finalize_windows_host_switch_privileged",
        ),
    ],
)
def test_windows_privileged_mode_switch_cli_authorization_and_dispatch(
    monkeypatch, flag: str, function_name: str
) -> None:
    transaction_id = "z" * 32
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    monkeypatch.setattr(sys, "argv", ["PartyOpsWizard", flag])
    with pytest.raises(SystemExit) as denied:
        setup_wizard.main()
    assert denied.value.code != 0

    calls: list[str] = []
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(
        setup_wizard,
        function_name,
        lambda value: calls.append(value),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "PartyOpsWizard",
            flag,
            "--mode-switch-transaction",
            transaction_id,
        ],
    )
    with pytest.raises(SystemExit) as completed:
        setup_wizard.main()
    assert completed.value.code == 0
    assert calls == [transaction_id]


def test_windows_data_dir_environment_absence_and_service_acl_empty_tree(
    monkeypatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "Data" / "PartyOps"
    for name in ("PROGRAMDATA", "USERPROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        setup_wizard, "_assert_path_components_have_no_reparse_points", lambda _p: None
    )
    monkeypatch.setattr(
        setup_wizard,
        "assert_windows_service_data_path_security",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=4 * 1024**3),
    )
    assert setup_wizard._validate_windows_data_dir(data_dir) == data_dir.resolve()

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.delenv("PARTYOPS_ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        setup_wizard, "_assert_managed_data_tree_has_no_reparse_points", lambda _p: None
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    setup_wizard._grant_windows_service_access(data_dir)
    (data_dir / "partyops.db").write_bytes(b"db")
    setup_wizard._grant_windows_service_access(data_dir)


def test_windows_cross_account_empty_data_dir_is_adopted_without_weakening_acl(
    monkeypatch, tmp_path: Path
) -> None:
    """日常账号预建的 D/E 盘空目录可由 UAC 管理员安全接管。"""

    data_dir = tmp_path / "D盘 党建文档"
    data_dir.mkdir()

    class Descriptor:
        def GetSecurityDescriptorOwner(self):
            return "S-1-5-21-foreign-owner"

        def GetSecurityDescriptorDacl(self):
            return object()

    fake_security = SimpleNamespace(
        OWNER_SECURITY_INFORMATION=1,
        DACL_SECURITY_INFORMATION=2,
        GetFileSecurity=lambda *_args: Descriptor(),
        ConvertSidToStringSid=lambda value: value,
        LookupAccountName=lambda *_args: ("S-1-5-21-current-admin", "", 1),
    )
    monkeypatch.setitem(sys.modules, "win32security", fake_security)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(
        setup_wizard, "_assert_managed_data_tree_has_no_reparse_points", lambda _p: None
    )
    assert setup_wizard._assert_windows_service_data_root_adoptable(data_dir) is True

    (data_dir / "unknown.bin").write_bytes(b"untrusted")
    with pytest.raises(PermissionError, match="目录不为空"):
        setup_wizard._assert_windows_service_data_root_adoptable(data_dir)


def test_windows_data_dir_owner_falls_back_to_takeown_then_rechecks(
    monkeypatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "D盘 数据"
    data_dir.mkdir()
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs):
        calls.append(command)
        first_owner_attempt = len(calls) == 1 and command[0] == "icacls.exe"
        return subprocess.CompletedProcess(command, 5 if first_owner_attempt else 0, "", "")

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(
        setup_wizard, "_assert_managed_data_tree_has_no_reparse_points", lambda _p: None
    )
    monkeypatch.setattr(
        setup_wizard, "_assert_windows_service_data_root_adoptable", lambda _p: True
    )
    monkeypatch.setattr(
        setup_wizard, "assert_windows_service_data_path_security", lambda *_a, **_k: None
    )
    monkeypatch.setattr(setup_wizard.subprocess, "run", run)

    setup_wizard._grant_windows_service_access(data_dir)
    assert calls[0][0] == "icacls.exe"
    assert calls[1][:4] == ["takeown.exe", "/F", str(data_dir), "/A"]
    assert calls[2] == calls[0]


def test_personal_data_dir_fixed_drive_and_mode_launch_branches(
    monkeypatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "Personal"
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.delenv("PARTYOPS_ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        setup_wizard, "_assert_path_components_have_no_reparse_points", lambda _p: None
    )
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=4 * 1024**3),
    )
    assert setup_wizard._validate_personal_data_dir(data_dir) == data_dir.resolve()

    config = tmp_path / "personal.env"
    config.write_text("ignored", encoding="utf-8")
    personal_env = {
        "PARTYOPS_PORT": "18775",
        "PARTYOPS_DATA_DIR": str(data_dir),
    }
    monkeypatch.setattr(setup_wizard, "load_host_environment", lambda _p: personal_env)

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        setup_wizard.socket,
        "create_connection",
        lambda *_args, **_kwargs: _Connection(),
    )
    monkeypatch.setattr(
        setup_wizard,
        "wait_for_host_health",
        lambda *_args, **_kwargs: "http://127.0.0.1:18775",
    )
    monkeypatch.setattr(setup_wizard, "_personal_process_is_owned", lambda _path: True)
    assert setup_wizard.launch_personal(config) == "http://127.0.0.1:18775"

    host_env = {
        "PARTYOPS_HOST": "127.0.0.1",
        "PARTYOPS_PORT": "18765",
        "PARTYOPS_TLS_ENABLED": "false",
        "PARTYOPS_DATA_DIR": str(data_dir),
    }
    spawned: list[dict[str, str]] = []
    monkeypatch.setattr(setup_wizard, "load_host_environment", lambda _p: host_env)
    monkeypatch.setattr(setup_wizard, "install_host_autostart", lambda _p: None)
    monkeypatch.setattr(
        setup_wizard,
        "_spawn",
        lambda _command, _log, env: spawned.append(env),
    )
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: tmp_path / "partyops")
    monkeypatch.setattr(
        setup_wizard,
        "wait_for_host_health",
        lambda *_args, **_kwargs: "http://127.0.0.1:18765",
    )
    monkeypatch.setattr(setup_wizard, "_wait_and_install_ca", lambda _p: None)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    assert setup_wizard.launch_host(config) == "http://127.0.0.1:18765"
    assert spawned == [host_env]


def test_windows_deactivate_generated_and_invalid_transaction_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    with pytest.raises(ValueError, match="事务编号无效"):
        setup_wizard._deactivate_windows_host_services_privileged("bad/id")

    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setattr(setup_wizard.secrets, "token_urlsafe", lambda _n: "t" * 43)
    monkeypatch.setattr(
        setup_wizard, "_windows_service_start_config", lambda _service: None
    )
    monkeypatch.setattr(
        setup_wizard, "_windows_service_running", lambda _service: False
    )
    snapshots: list[str] = []
    monkeypatch.setattr(
        setup_wizard,
        "_write_windows_host_switch_snapshot",
        lambda **kwargs: snapshots.append(str(kwargs["transaction_id"])),
    )
    monkeypatch.setattr(
        setup_wizard, "_stop_windows_service_for_data_migration", lambda: {}
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(setup_wizard, "_write_private", lambda *_args, **_kwargs: None)
    setup_wizard._deactivate_windows_host_services_privileged()
    assert snapshots == ["t" * 43]


def test_process_executable_windows_open_and_query_failures(monkeypatch) -> None:
    class _Kernel:
        def __init__(self) -> None:
            self.handle = 0
            self.closed: list[int] = []

        def OpenProcess(self, *_args):
            return self.handle

        def QueryFullProcessImageNameW(self, *_args):
            return False

        def CloseHandle(self, handle):
            self.closed.append(handle)

    class _Buffer:
        value = ""

    kernel = _Kernel()
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(kernel32=kernel),
        wintypes=SimpleNamespace(DWORD=lambda value: SimpleNamespace(value=value)),
        create_unicode_buffer=lambda _size: _Buffer(),
        byref=lambda value: value,
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    assert not setup_wizard._process_executable_matches(10, Path("PartyOps.exe"))
    kernel.handle = 42
    assert not setup_wizard._process_executable_matches(10, Path("PartyOps.exe"))
    assert kernel.closed == [42]


def test_record_wizard_failure_posix_permissions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(setup_wizard, "config_root", lambda: tmp_path)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    modes: list[int] = []
    monkeypatch.setattr(Path, "chmod", lambda _path, mode: modes.append(mode))
    diagnostic_id = setup_wizard._record_wizard_failure(ValueError("test"))
    assert len(diagnostic_id) == 12
    assert modes == [0o600]
