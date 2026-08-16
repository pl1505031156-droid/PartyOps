"""专业更新器的跨平台失败路径与守护进程分支矩阵。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import update_executor


def _archive(
    path: Path, manifest: object | None, entries: dict[str, bytes] | None = None
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
        for name, payload in (entries or {}).items():
            archive.writestr(name, payload)


def test_privileged_manifest_rejects_every_incomplete_metadata_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "release.partyops-update"

    _archive(package, None)
    with pytest.raises(RuntimeError, match="文件数量"):
        update_executor._read_update_manifest(package)

    _archive(package, [])
    with pytest.raises(RuntimeError, match="清单结构"):
        update_executor._read_update_manifest(package)

    for manifest in (
        {"version": 3, "artifacts": {}},
        {"version": "", "artifacts": {}},
        {"version": "1.4.3-rc.3", "artifacts": []},
    ):
        _archive(package, manifest)
        with pytest.raises(RuntimeError, match="字段不完整"):
            update_executor._read_update_manifest(package)

    artifact = "PartyOps_1.4.3-rc.3_linux_amd64.deb"
    payload = b"deb"
    for record, message in (
        ("invalid", "结构"),
        ({"size": [], "sha256": "0" * 64}, "大小"),
        ({"size": len(payload), "sha256": "short"}, "元数据"),
    ):
        manifest = {"version": "1.4.3-rc.3", "artifacts": {artifact: record}}
        _archive(package, manifest, {artifact: payload})
        with pytest.raises(RuntimeError, match=message):
            update_executor._read_update_manifest(package)

    missing = {
        "version": "1.4.3-rc.3",
        "artifacts": {
            artifact: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    _archive(package, missing)
    with pytest.raises(RuntimeError, match="元数据"):
        update_executor._read_update_manifest(package)

    _archive(package, missing, {artifact: payload})
    monkeypatch.setattr(update_executor, "MAX_UPDATE_EXPANDED_BYTES", 1)
    with pytest.raises(RuntimeError, match="展开体积"):
        update_executor._read_update_manifest(package)


def test_process_runner_and_installed_package_adapters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(
            command, 0, "install ok installed\t1.4.3-rc.3\n", ""
        )

    monkeypatch.setattr(update_executor.subprocess, "run", run)
    update_executor._run(["plain"])
    update_executor._run(["with-env"], environment={"PARTYOPS_IN_APP_UPDATE": "1"})
    assert calls[0]["env"] is None
    assert calls[1]["env"]["PARTYOPS_IN_APP_UPDATE"] == "1"  # type: ignore[index]

    assert update_executor._installed_package_version() == "1.4.3-rc.3"
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", ""),
    )
    assert update_executor._installed_package_version() == ""
    assert update_executor._installed_rpm_version() == ""
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "unknown\t1.0", ""
        ),
    )
    assert update_executor._installed_package_version() == ""
    assert update_executor._installed_rpm_version() == "unknown\t1.0"

    monkeypatch.setattr(update_executor.shutil, "which", lambda _name: None)
    assert not update_executor._install_rpm(tmp_path / "package.rpm")
    monkeypatch.setattr(
        update_executor.shutil,
        "which",
        lambda name: "/usr/bin/dnf" if name == "dnf" else None,
    )
    actions: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run_linux_package_manager",
        lambda command, **_kwargs: (
            actions.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    assert update_executor._install_rpm(tmp_path / "package.rpm")
    assert update_executor._install_rpm(tmp_path / "package.rpm", allow_downgrade=True)
    assert actions[0][2] == "install"
    assert actions[1][2] == "downgrade"


def test_version_and_restore_guards_cover_safe_and_unsafe_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(app_version="bad-version")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="版本号无效"):
        update_executor._assert_update_not_downgrade({"version": "1.4.3-rc.3"})
    settings.app_version = "1.4.3-rc.3"
    with pytest.raises(RuntimeError, match="最低兼容版本号"):
        update_executor._assert_update_not_downgrade(
            {"version": "1.4.3-rc.4", "min_version": "not-a-version"}
        )
    update_executor._assert_update_not_downgrade({"version": "1.4.3-rc.3"})

    data_root = tmp_path / "data"
    destination = data_root / "attachments"
    backup = tmp_path / "backup"
    data_root.mkdir()
    with pytest.raises(RuntimeError, match="超出"):
        update_executor._restore_managed_tree(backup, tmp_path / "outside", data_root)
    update_executor._restore_managed_tree(backup, destination, data_root)
    backup.mkdir()
    (backup / "kept.txt").write_text("new", encoding="utf-8")
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    update_executor._restore_managed_tree(backup, destination, data_root)
    assert (destination / "kept.txt").read_text(encoding="utf-8") == "new"

    linked = data_root / "linked"
    linked.symlink_to(destination, target_is_directory=True)
    with pytest.raises(RuntimeError, match="链接"):
        update_executor._restore_managed_tree(backup, linked, data_root)


@pytest.mark.parametrize(
    (
        "platform_name",
        "installed",
        "dpkg_ready",
        "first_code",
        "second_code",
        "expected",
    ),
    [
        ("windows", "old", True, 0, 0, True),
        ("windows7", "1.4.3-rc.3", True, 0, 0, True),
        ("linux-rpm", "old", True, 0, 0, True),
        ("linux-rpm", "1.4.3-rc.3", True, 0, 0, True),
        ("linux-deb", "old", False, 0, 0, False),
        ("linux-deb", "old", True, 1, 0, False),
        ("linux-deb", "old", True, 0, 0, True),
        ("linux-deb", "old", True, 0, 1, False),
    ],
)
def test_device_package_installs_exact_local_platform_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_name: str,
    installed: str,
    dpkg_ready: bool,
    first_code: int,
    second_code: int,
    expected: bool,
) -> None:
    package = (
        tmp_path
        / f"{platform_name}-{installed}-{first_code}-{second_code}.partyops-update"
    )
    package.write_bytes(b"signed")
    transaction_count = 0

    def transaction(_run_id: str) -> Path:
        nonlocal transaction_count
        transaction_count += 1
        path = tmp_path / f"transaction-{transaction_count}"
        path.mkdir()
        return path

    manifest = {"version": "1.4.3-rc.3"}
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor, "_secure_update_backup_root", transaction)
    monkeypatch.setattr(
        update_executor, "_read_update_manifest", lambda _path: manifest
    )
    monkeypatch.setattr(
        update_executor, "_manifest_platform_name", lambda _manifest: platform_name
    )

    def select(_package, _manifest, _architecture, target: Path, _platform):
        target.write_bytes(b"installer")
        return target

    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(
        update_executor, "_remove_secure_update_transaction", lambda _path: True
    )
    deb_versions = iter([installed, "1.4.3~rc.3"])
    rpm_versions = iter([installed, "1.4.3-0.rc.3.1"])
    monkeypatch.setattr(
        update_executor,
        "_installed_package_version",
        lambda: next(deb_versions, installed),
    )
    monkeypatch.setattr(
        update_executor,
        "_installed_rpm_version",
        lambda: next(rpm_versions, installed),
    )
    monkeypatch.setattr(update_executor, "_run_windows_installer", lambda _path: True)
    monkeypatch.setattr(update_executor, "_install_rpm", lambda _path: True)
    monkeypatch.setattr(
        update_executor, "_verify_cached_rollback_artifact", lambda _path: True
    )
    monkeypatch.setattr(
        update_executor, "_cache_verified_rollback_artifact", lambda *_args: None
    )
    monkeypatch.setattr(
        update_executor,
        "_create_installed_package_snapshot",
        lambda target: target.write_bytes(b"rollback-deb"),
    )
    monkeypatch.setattr(update_executor, "_ensure_dpkg_ready", lambda: dpkg_ready)
    codes = iter([first_code, second_code])
    monkeypatch.setattr(
        update_executor,
        "_run_linux_package_manager",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, next(codes, 0), "", ""
        ),
    )
    assert update_executor.install_device_package(package) is expected


def test_update_daemon_supervisor_and_pending_database_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BrokenFactory:
        def __enter__(self):
            raise OperationalError("SELECT", {}, sqlite3.OperationalError("busy"))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: BrokenFactory()
    )
    assert update_executor.run_daemon(once=True) == 0

    class Session:
        def __init__(self, run):
            self.run = run

        def scalar(self, _statement):
            return self.run

    @contextmanager
    def factory(run):
        yield Session(run)

    executed: list[str] = []
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: factory(SimpleNamespace(id="run-1")),
    )
    monkeypatch.setattr(
        update_executor, "execute_host_update", lambda run_id: executed.append(run_id)
    )
    assert update_executor.run_daemon(once=True) == 0
    assert executed == ["run-1"]

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert update_executor._pending_run_id(data_dir) is None
    database = data_dir / "partyops.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE update_runs (id TEXT, target_device_id TEXT, status TEXT, created_at TEXT)"
        )
        connection.execute(
            "INSERT INTO update_runs VALUES ('pending-1', NULL, 'APPLYING', '2026-08-15')"
        )
    assert update_executor._pending_run_id(data_dir) == "pending-1"
    database.write_bytes(b"not-sqlite")
    assert update_executor._pending_run_id(data_dir) is None

    lock = tmp_path / "supervisor.lock"
    lock.write_text("active", encoding="utf-8")
    monkeypatch.setattr(
        update_executor,
        "_candidate_host_environments",
        lambda: [{"PARTYOPS_DATA_DIR": str(data_dir)}],
    )
    monkeypatch.setattr(
        update_executor, "_pending_run_id", lambda _path: "run-supervisor"
    )
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _path: lock)
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: False)
    assert update_executor.run_supervisor(once=True) == 0

    spawned: list[list[str]] = []
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: True)
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **_kwargs: spawned.append(command),
    )
    assert update_executor.run_supervisor(once=True) == 0
    assert spawned and "--run-id" in spawned[0]


def test_linux_personal_update_coordinator_keeps_root_out_of_user_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "updates" / "release.partyops-update"
    package_path.parent.mkdir()
    package_path.write_bytes(b"signed-update")
    run = SimpleNamespace(
        id="run-linux-personal",
        package_id="package-1",
        target_device_id=None,
        status=update_executor.UpdateStatus.APPLYING,
        created_by="admin",
    )
    package = SimpleNamespace(
        id="package-1",
        filename=package_path.name,
        sha256=hashlib.sha256(package_path.read_bytes()).hexdigest(),
        version="1.4.3-rc.3",
        signature_valid=True,
    )

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, identity):
            return (
                run
                if identity == run.id
                else package
                if identity == package.id
                else None
            )

        def scalar(self, _statement):
            return SimpleNamespace(filename="pre-upgrade.partyops-backup")

    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: Session()
    )
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "pre-upgrade.partyops-backup").write_bytes(b"backup")
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(
            mode="personal",
            data_dir=tmp_path,
            updates_dir=package_path.parent,
            backups_dir=backups_dir,
            port=18775,
        ),
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: (
            commands.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    monkeypatch.setattr(update_executor.db_runtime, "dispose", lambda: None)
    completed: list[str] = []
    monkeypatch.setattr(
        update_executor,
        "_complete_personal_update_run",
        lambda run_id, **_kwargs: completed.append(run_id),
    )
    monkeypatch.setattr(update_executor, "_set_run", lambda *_a, **_k: None)
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(**{**vars(os), "name": "posix"}),
    )

    assert update_executor.execute_linux_personal_update(run.id)
    assert len(commands) == 1 and commands[0][0] == "pkexec"
    assert "--linux-personal-transaction" in commands[0]
    assert "--rollback-personal-package" not in commands[0]
    assert "--discard-personal-rollback" not in commands[0]
    assert str(package_path) in commands[0]
    assert str(backups_dir / "pre-upgrade.partyops-backup") in commands[0]
    assert completed == [run.id]


def test_linux_personal_launcher_uses_independent_user_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(**{**vars(os), "name": "posix"}),
    )
    spawned: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **kwargs: spawned.append((command, kwargs)),
    )
    assert update_executor.launch_linux_personal_update("run-1")
    assert "--linux-personal-run-id" in spawned[0][0]
    assert spawned[0][1]["start_new_session"] is True
    assert not update_executor.launch_linux_personal_update("bad/run")


def test_personal_native_rollback_cache_is_verified_and_consumed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rollback_root = tmp_path / "root-cache"
    monkeypatch.setattr(update_executor, "PERSONAL_NATIVE_ROLLBACK_ROOT", rollback_root)
    monkeypatch.setattr(update_executor, "RPM_PACKAGE_CACHE", tmp_path / "current.rpm")
    artifact = tmp_path / "previous.rpm"
    artifact.write_bytes(b"previous-version")
    update_executor._persist_personal_native_rollback(
        "run-rollback",
        artifact,
        platform_name="linux-rpm",
        previous_version="1.4.2",
        target_version="1.4.3-rc.3",
    )
    cached, metadata = update_executor._personal_native_rollback_paths("run-rollback")
    assert update_executor._verify_cached_rollback_artifact(cached)
    assert metadata.is_file()
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(**{**vars(os), "name": "posix"}),
    )
    monkeypatch.setattr(update_executor, "_install_rpm", lambda *_a, **_k: True)
    versions = iter(["1.4.3-rc.3", "1.4.2"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    assert update_executor._rollback_linux_personal_package_locked("run-rollback")
    assert not cached.exists() and not metadata.exists()


def test_linux_personal_root_transaction_holds_lock_through_health_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = tmp_path / "personal"
    updates = data / "updates"
    backups = data / "backups"
    updates.mkdir(parents=True)
    backups.mkdir()
    package = updates / "release.partyops-update"
    backup = backups / "pre-upgrade.partyops-backup"
    package.write_bytes(b"signed")
    backup.write_bytes(b"backup")
    lock = tmp_path / "system-cache" / "update.lock"
    rollback_artifact = tmp_path / "previous.rpm"
    rollback_artifact.write_bytes(b"previous")
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(
            data_dir=data,
            updates_dir=updates,
            backups_dir=backups,
        ),
    )
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _path: lock)
    monkeypatch.setattr(
        update_executor, "PERSONAL_NATIVE_ROLLBACK_ROOT", tmp_path / "rollbacks"
    )
    monkeypatch.setattr(update_executor, "RPM_PACKAGE_CACHE", tmp_path / "current.rpm")
    monkeypatch.setattr(
        update_executor, "_pkexec_desktop_uid", lambda: data.stat().st_uid
    )
    monkeypatch.setattr(update_executor, "verify_backup", lambda _path: None)
    manifest = {"version": "1.4.3-rc.3"}
    monkeypatch.setattr(
        update_executor, "_read_update_manifest", lambda _path: manifest
    )
    monkeypatch.setattr(
        update_executor, "_verify_manifest_signature", lambda _value: True
    )
    monkeypatch.setattr(
        update_executor, "_assert_update_not_downgrade", lambda _value: None
    )
    monkeypatch.setattr(
        update_executor, "_manifest_platform_name", lambda _value: "linux-rpm"
    )
    events: list[str] = []

    def install(_path, **kwargs):
        assert lock.is_file() and kwargs["_lock_already_held"] is True
        update_executor._persist_personal_native_rollback(
            "run-root",
            rollback_artifact,
            platform_name="linux-rpm",
            previous_version="1.4.2",
            target_version="1.4.3-rc.3",
        )
        events.append("install")
        return True

    monkeypatch.setattr(update_executor, "install_device_package", install)
    versions = iter(["1.4.2", "1.4.3-rc.3", "1.4.3-rc.3", "1.4.2"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    health_results = iter([(False, None), (True, None)])

    def restart(_uid):
        assert lock.is_file()
        events.append("health")
        return next(health_results)

    monkeypatch.setattr(update_executor, "_restart_linux_personal_runtime", restart)

    def install_rollback(_path, **_kwargs):
        assert lock.is_file()
        events.append("rollback-package")
        return True

    monkeypatch.setattr(update_executor, "_install_rpm", install_rollback)
    monkeypatch.setattr(
        update_executor,
        "_restore_personal_database_as_user",
        lambda _path, _uid: (
            events.append("rollback-database")
            if lock.is_file()
            else (_ for _ in ()).throw(AssertionError("数据库回滚时锁已释放"))
        ),
    )
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(**{**vars(os), "name": "posix", "geteuid": lambda: 0}),
    )

    result = update_executor.execute_linux_personal_root_transaction(
        "run-root", package, backup
    )
    assert result == update_executor.LINUX_PERSONAL_TRANSACTION_ROLLED_BACK
    assert not lock.exists()
    assert events == [
        "install",
        "health",
        "rollback-package",
        "rollback-database",
        "health",
    ]


def test_personal_rollback_rejects_a_different_current_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        update_executor, "PERSONAL_NATIVE_ROLLBACK_ROOT", tmp_path / "rollbacks"
    )
    artifact = tmp_path / "previous.rpm"
    artifact.write_bytes(b"previous")
    update_executor._persist_personal_native_rollback(
        "run-version",
        artifact,
        platform_name="linux-rpm",
        previous_version="1.4.2",
        target_version="1.4.3-rc.3",
    )
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(**{**vars(os), "name": "posix"}),
    )
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: "1.4.4"
    )
    monkeypatch.setattr(
        update_executor,
        "_install_rpm",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("不同事务版本不能进入包回滚")
        ),
    )
    assert not update_executor._rollback_linux_personal_package_locked("run-version")


def test_linux_personal_polkit_action_is_one_shot_and_argument_scoped() -> None:
    root = Path(__file__).parents[2]
    policy = (root / "packaging" / "uos" / "cn.partyops.update.policy").read_text(
        encoding="utf-8"
    )
    executor = (root / "backend" / "app" / "update_executor.py").read_text(
        encoding="utf-8"
    )
    assert "auth_admin_keep" not in policy
    assert "<allow_active>auth_admin</allow_active>" in policy
    assert "--linux-personal-transaction" in policy
    assert "org.freedesktop.policykit.exec.argv1" in policy
    assert 'parser.add_argument("--rollback-personal-package"' not in executor
    assert 'parser.add_argument("--discard-personal-rollback"' not in executor


@pytest.mark.parametrize(
    ("arguments", "target", "result"),
    [
        (["update", "--personal-run-id", "personal"], "personal", True),
        (
            ["update", "--linux-personal-run-id", "linux-personal"],
            "linux-personal",
            True,
        ),
        (
            [
                "update",
                "--linux-personal-transaction",
                "run-1",
                "--personal-package",
                "package.partyops-update",
                "--personal-backup",
                "backup.partyops-backup",
            ],
            "linux-transaction",
            10,
        ),
        (["update", "--install-package", "package.partyops-update"], "device", False),
        (["update", "--supervisor", "--once"], "supervisor", 7),
        (["update", "--run-id", "host"], "host", True),
        (["update", "--once"], "daemon", 9),
    ],
)
def test_update_executor_cli_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    target: str,
    result: bool | int,
) -> None:
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(
        update_executor, "execute_windows_personal_update", lambda _run: bool(result)
    )
    monkeypatch.setattr(
        update_executor, "execute_linux_personal_update", lambda _run: bool(result)
    )
    monkeypatch.setattr(
        update_executor,
        "execute_linux_personal_root_transaction",
        lambda *_args: int(result),
    )
    monkeypatch.setattr(
        update_executor, "install_device_package", lambda _path: bool(result)
    )
    monkeypatch.setattr(update_executor, "run_supervisor", lambda _once: int(result))
    monkeypatch.setattr(
        update_executor, "execute_host_update", lambda _run: bool(result)
    )
    monkeypatch.setattr(update_executor, "run_daemon", lambda _once: int(result))
    expected_code = (
        int(result)
        if target in {"supervisor", "daemon", "linux-transaction"}
        else 0
        if bool(result)
        else 1
    )
    with pytest.raises(SystemExit) as raised:
        update_executor.main()
    assert raised.value.code == expected_code
