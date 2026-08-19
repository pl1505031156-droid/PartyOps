"""发布前对抗式审查新增事务的分支门禁。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import setup_wizard, update_executor
from app.enums import UpdateStatus


def _os_proxy(name: str) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(os), "name": name})


def test_personal_rollback_guards_and_deb_matrix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        update_executor, "PERSONAL_NATIVE_ROLLBACK_ROOT", tmp_path / "rollbacks"
    )
    with pytest.raises(RuntimeError, match="编号"):
        update_executor._personal_native_rollback_paths("../bad")
    artifact = tmp_path / "old.deb"
    artifact.write_bytes(b"old")
    with pytest.raises(RuntimeError, match="元数据"):
        update_executor._persist_personal_native_rollback(
            "run-1",
            artifact,
            platform_name="windows",
            previous_version="",
            target_version="",
        )
    monkeypatch.setattr(
        update_executor, "_is_link_or_reparse_point", lambda _path: True
    )
    with pytest.raises(RuntimeError, match="回滚目录"):
        update_executor._persist_personal_native_rollback(
            "run-1",
            artifact,
            platform_name="linux-deb",
            previous_version="1.4.2",
            target_version="1.4.3-rc.3",
        )

    monkeypatch.setattr(
        update_executor, "_is_link_or_reparse_point", lambda _path: False
    )
    update_executor._persist_personal_native_rollback(
        "run-1",
        artifact,
        platform_name="linux-deb",
        previous_version="1.4.2",
        target_version="1.4.3-rc.3",
    )
    rollback, metadata = update_executor._personal_native_rollback_paths("run-1")
    monkeypatch.setattr(
        update_executor,
        "_is_link_or_reparse_point",
        lambda path: path == metadata,
    )
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    assert not update_executor._rollback_linux_personal_package_locked("run-1")

    monkeypatch.setattr(
        update_executor, "_is_link_or_reparse_point", lambda _path: False
    )
    metadata.write_text("{}", encoding="utf-8")
    assert not update_executor._rollback_linux_personal_package_locked("run-1")
    metadata.write_text(
        json.dumps(
            {
                "format_version": 1,
                "platform": "linux-deb",
                "previous_version": "1.4.2",
                "target_version": "1.4.3-rc.3",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        update_executor,
        "_run_linux_package_manager",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    versions = iter(["1.4.3-rc.3", "1.4.2"])
    monkeypatch.setattr(
        update_executor, "_linux_native_version", lambda _platform: next(versions)
    )
    assert update_executor._rollback_linux_personal_package_locked("run-1")
    assert not rollback.exists() and not metadata.exists()

    monkeypatch.setattr(update_executor, "os", _os_proxy("nt"))
    assert not update_executor._rollback_linux_personal_package_locked("run-1")


def test_personal_run_database_completion_and_recreation(monkeypatch) -> None:
    package = SimpleNamespace(id="package", status=UpdateStatus.APPLYING)
    existing = SimpleNamespace(
        id="run", package_id=package.id, status=UpdateStatus.APPLYING
    )
    added: list[object] = []

    class Session:
        def __init__(self, run):
            self.run = run

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, identity):
            if model is update_executor.UpdateRun:
                return self.run if identity == "run" else None
            return package if identity == package.id else None

        def add(self, value):
            added.append(value)

        def commit(self):
            return None

    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: Session(existing)
    )
    update_executor._complete_personal_update_run("run", message="完成")
    assert existing.status == UpdateStatus.COMPLETED
    assert package.status == UpdateStatus.COMPLETED

    package.status = UpdateStatus.APPLYING
    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: Session(None)
    )
    update_executor._record_restored_personal_update_run(
        "run",
        package_id=package.id,
        created_by="admin",
        message="已回滚",
    )
    assert added and added[-1].status == UpdateStatus.ROLLED_BACK
    assert package.status == UpdateStatus.VALIDATED


def test_linux_personal_runtime_start_matrix(monkeypatch, tmp_path: Path) -> None:
    settings = SimpleNamespace(
        data_dir=tmp_path / "data",
        port=18775,
        agent_port=18776,
    )
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_executor.sys, "executable", str(tmp_path / "updater"))
    assert update_executor._restart_linux_personal_runtime() == (False, None)

    main = tmp_path / "partyops"
    main.write_bytes(b"main")
    process = SimpleNamespace(pid=1)
    spawned: list[dict[str, object]] = []
    monkeypatch.setenv("ROOT_ONLY_SECRET", "do-not-forward")
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda *_a, **kwargs: spawned.append(kwargs) or process,
    )
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_a, **_k: True)
    assert update_executor._restart_linux_personal_runtime() == (True, process)
    assert "ROOT_ONLY_SECRET" not in spawned[0]["env"]


@pytest.mark.parametrize(
    ("platform_name", "run_value", "signature"),
    [
        ("linux", None, True),
        ("linux", SimpleNamespace(target_device_id="device"), True),
        ("linux", SimpleNamespace(target_device_id=None), False),
    ],
)
def test_linux_personal_update_rejects_invalid_database_context(
    monkeypatch,
    tmp_path: Path,
    platform_name: str,
    run_value,
    signature: bool,
) -> None:
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    monkeypatch.setattr(update_executor.sys, "platform", platform_name)
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(mode="personal", updates_dir=tmp_path),
    )
    if run_value is not None:
        run_value.package_id = "package"
        run_value.status = UpdateStatus.APPLYING
    package = SimpleNamespace(signature_valid=signature)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, _identity):
            return run_value if model is update_executor.UpdateRun else package

    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: Session()
    )
    assert not update_executor.execute_linux_personal_update("run-1")


def test_linux_personal_update_authorization_and_health_rollback_matrix(
    monkeypatch,
    tmp_path: Path,
) -> None:
    updates_dir = tmp_path / "updates"
    backups_dir = tmp_path / "backups"
    updates_dir.mkdir()
    backups_dir.mkdir()
    package_path = updates_dir / "release.partyops-update"
    package_path.write_bytes(b"signed")
    backup_path = backups_dir / "pre.partyops-backup"
    backup_path.write_bytes(b"backup")
    run = SimpleNamespace(
        id="run-1",
        package_id="package",
        target_device_id=None,
        status=UpdateStatus.APPLYING,
        created_by="admin",
    )
    package = SimpleNamespace(
        id="package",
        filename=package_path.name,
        sha256=hashlib.sha256(package_path.read_bytes()).hexdigest(),
        version="1.4.3-rc.3",
        signature_valid=True,
    )
    backup = SimpleNamespace(filename=backup_path.name)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, identity):
            return run if model is update_executor.UpdateRun else package

        def scalar(self, _statement):
            return backup

    settings = SimpleNamespace(
        mode="personal",
        updates_dir=updates_dir,
        backups_dir=backups_dir,
        data_dir=tmp_path,
        port=18765,
    )
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(update_executor.sys, "frozen", False, raising=False)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: Session()
    )
    monkeypatch.setattr(update_executor.db_runtime, "dispose", lambda: None)
    states: list[dict[str, object]] = []
    monkeypatch.setattr(
        update_executor, "_set_run", lambda _run, **state: states.append(state)
    )
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            update_executor.LINUX_PERSONAL_TRANSACTION_FAILED,
            "",
            "",
        ),
    )
    assert not update_executor.execute_linux_personal_update(run.id)
    assert states[-1]["status"] == UpdateStatus.FAILED

    calls = 0

    def run_command(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            update_executor.LINUX_PERSONAL_TRANSACTION_ROLLED_BACK,
            "",
            "",
        )

    monkeypatch.setattr(update_executor, "_run", run_command)
    recorded: list[str] = []
    monkeypatch.setattr(
        update_executor,
        "_record_restored_personal_update_run",
        lambda run_id, **_kwargs: recorded.append(run_id),
    )
    assert not update_executor.execute_linux_personal_update(run.id)
    assert recorded == [run.id] and calls == 1


def test_linux_personal_launch_failure_and_frozen_command(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_executor.sys, "executable", str(tmp_path / "partyops"))
    states: list[dict[str, object]] = []
    monkeypatch.setattr(
        update_executor, "_set_run", lambda _run, **state: states.append(state)
    )
    assert not update_executor.launch_linux_personal_update("run-1")
    assert states[-1]["status"] == UpdateStatus.FAILED

    updater = tmp_path / "partyops-updater"
    updater.write_bytes(b"updater")
    commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **_kwargs: commands.append(command),
    )
    assert update_executor.launch_linux_personal_update("run-1")
    assert commands == [[str(updater), "--linux-personal-run-id", "run-1"]]


def test_host_switch_snapshot_validation_matrix(monkeypatch, tmp_path: Path) -> None:
    program_data = tmp_path / "ProgramData"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    with pytest.raises(ValueError, match="管理员权限"):
        setup_wizard._restore_windows_host_switch_privileged()

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    snapshot = setup_wizard._windows_host_switch_snapshot_path()
    with pytest.raises(ValueError, match="记录不可用"):
        setup_wizard._restore_windows_host_switch_privileged()
    snapshot.parent.mkdir(parents=True)

    def write(payload):
        snapshot.write_text(json.dumps(payload), encoding="utf-8")

    write({"format_version": 2, "services": {}})
    with pytest.raises(ValueError, match="格式无效"):
        setup_wizard._restore_windows_host_switch_privileged()
    write({"format_version": 1, "services": {}})
    with pytest.raises(ValueError, match="服务清单"):
        setup_wizard._restore_windows_host_switch_privileged()
    services = {
        "PartyOpsHost": {"start_type": 2, "delayed": False, "running": True},
        "PartyOpsUpdateService": {
            "start_type": 3,
            "delayed": False,
            "running": False,
        },
    }
    write({"format_version": 1, "previous_mode": 3, "services": services})
    with pytest.raises(ValueError, match="回滚内容"):
        setup_wizard._restore_windows_host_switch_privileged()
    write(
        {
            "format_version": 1,
            "previous_mode": json.dumps({"mode": "personal"}),
            "services": services,
        }
    )
    with pytest.raises(ValueError, match="不是主机模式"):
        setup_wizard._restore_windows_host_switch_privileged()
    invalid_state = dict(services)
    invalid_state["PartyOpsHost"] = []
    write(
        {
            "format_version": 1,
            "previous_mode": json.dumps({"mode": "host"}),
            "services": invalid_state,
        }
    )
    with pytest.raises(ValueError, match="服务回滚状态"):
        setup_wizard._restore_windows_host_switch_privileged()
    invalid_start = {name: dict(value) for name, value in services.items()}
    invalid_start["PartyOpsHost"]["start_type"] = 9
    write(
        {
            "format_version": 1,
            "previous_mode": json.dumps({"mode": "host"}),
            "services": invalid_start,
        }
    )
    with pytest.raises(ValueError, match="启动类型"):
        setup_wizard._restore_windows_host_switch_privileged()

    restored: list[tuple[str, object]] = []
    running: list[dict[str, bool]] = []
    services["PartyOpsHost"]["start_type"] = None
    write({"format_version": 1, "previous_mode": None, "services": services})
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_service_start_config",
        lambda service, config: restored.append((service, config)),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_services_after_mode_switch",
        lambda states: running.append(states),
    )
    setup_wizard._restore_windows_host_switch_privileged()
    assert restored[0] == ("PartyOpsHost", None)
    assert running and not snapshot.exists()


def test_admin_host_switch_helper_dispatch_matrix(monkeypatch) -> None:
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        setup_wizard,
        "_deactivate_windows_host_services_privileged",
        lambda: calls.append("disable"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_host_switch_privileged",
        lambda: calls.append("restore"),
    )
    setup_wizard._run_windows_host_switch_helper("--privileged-disable-host")
    setup_wizard._run_windows_host_switch_helper("--privileged-restore-host")
    with pytest.raises(ValueError, match="动作无效"):
        setup_wizard._run_windows_host_switch_helper("--invalid")
    assert calls == ["disable", "restore"]


def test_data_migration_control_files_restore_after_atomic_switch_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "attachments").mkdir()
    (source / "attachments" / "file.txt").write_text("content", encoding="utf-8")
    marker = target / ".partyops-data-root.json"
    marker.write_text("marker", encoding="utf-8")
    real_replace = setup_wizard.os.replace
    monkeypatch.setattr(
        setup_wizard.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("switch failed")),
    )
    with pytest.raises(OSError, match="switch failed"):
        setup_wizard.migrate_windows_data_dir(source, target)
    assert marker.read_text(encoding="utf-8") == "marker"
    monkeypatch.setattr(setup_wizard.os, "replace", real_replace)

    bad_target = tmp_path / "bad-target"
    bad_target.mkdir()
    (bad_target / "mode.json").mkdir()
    with pytest.raises(ValueError, match="控制文件"):
        setup_wizard.migrate_windows_data_dir(source, bad_target)


def test_personal_and_client_rollbacks_restore_previous_autostart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (config / "mode.json").write_text(
        json.dumps({"format_version": 1, "mode": "client"}), encoding="utf-8"
    )
    (config / "client.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(
        setup_wizard, "deactivate_windows_host_for_user_mode", lambda: False
    )
    monkeypatch.setattr(
        setup_wizard,
        "_write_data_root_marker",
        lambda *_args: (_ for _ in ()).throw(ValueError("marker denied")),
    )
    restored: list[Path] = []
    monkeypatch.setattr(
        setup_wizard, "install_client_autostart", lambda path: restored.append(path)
    )
    with pytest.raises(ValueError, match="marker denied"):
        setup_wizard.write_personal_config(data)
    assert restored == [config / "client.json"]

    monkeypatch.setattr(setup_wizard, "validate_config", lambda _config: None)
    with pytest.raises(ValueError, match="marker denied"):
        setup_wizard.write_client_config("https://host", "token", data, 600)
    assert restored[-1] == config / "client.json"


def test_windows_data_migration_atomically_locks_before_stopping_services(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    control = program_data / "PartyOps"
    control.mkdir(parents=True)
    old_data = tmp_path / "old-data"
    new_data = tmp_path / "new-data"
    old_data.mkdir()
    new_data.mkdir()
    (old_data / "attachments").mkdir()
    (control / "partyops.env").write_text(
        f"PARTYOPS_MODE=host\nPARTYOPS_DATA_DIR='{old_data}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(
        setup_wizard, "_validate_windows_data_dir", lambda _path: new_data
    )
    events: list[str] = []
    lock = program_data / "PartyOps-System" / "update.lock"
    monkeypatch.setattr(
        setup_wizard,
        "_acquire_windows_data_migration_lock",
        lambda: events.append("lock") or lock,
    )
    monkeypatch.setattr(
        setup_wizard,
        "_stop_windows_service_for_data_migration",
        lambda: events.append("stop") or {"PartyOpsHost": True},
    )
    monkeypatch.setattr(
        setup_wizard,
        "migrate_windows_data_dir",
        lambda _old, _new: events.append("migrate"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_release_windows_data_migration_lock",
        lambda _path: events.append("unlock"),
    )
    monkeypatch.setattr(setup_wizard, "_grant_windows_service_access", lambda _p: None)
    monkeypatch.setattr(
        setup_wizard, "_protect_windows_control_config", lambda _p: None
    )
    monkeypatch.setattr(
        setup_wizard, "_restore_windows_services_after_data_migration", lambda _s: None
    )
    setup_wizard.write_host_config("127.0.0.1", 18765, new_data, write_user_mode=False)
    assert events == ["lock", "stop", "migrate", "unlock"]


def test_windows_mode_switch_transaction_mismatch_finalize_and_recovery(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    transaction_id = "a" * 40
    setup_wizard._write_windows_host_switch_snapshot(
        transaction_id=transaction_id,
        previous_mode=json.dumps({"mode": "host"}),
        start_configs={
            "PartyOpsHost": (2, False),
            "PartyOpsUpdateService": (2, True),
        },
        running_states={"PartyOpsHost": True, "PartyOpsUpdateService": True},
    )
    snapshot = setup_wizard._windows_host_switch_snapshot_path()
    with pytest.raises(ValueError, match="MISMATCH"):
        setup_wizard._finalize_windows_host_switch_privileged("b" * 40)
    assert snapshot.is_file()
    setup_wizard._finalize_windows_host_switch_privileged(transaction_id)
    assert not snapshot.exists()

    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text("{}", encoding="utf-8")
    pending = setup_wizard._windows_host_switch_pending_path()
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_run_windows_host_switch_helper",
        lambda action, tx="": calls.append((action, tx)),
    )
    setup_wizard.recover_pending_windows_host_switch()
    assert calls == [("--privileged-restore-host", "")]


def test_user_mode_commits_same_privileged_switch_transaction(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    data = tmp_path / "personal"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    transaction_id = "c" * 40
    monkeypatch.setattr(
        setup_wizard,
        "deactivate_windows_host_for_user_mode",
        lambda: transaction_id,
    )
    committed: list[str | bool | None] = []
    monkeypatch.setattr(
        setup_wizard,
        "finalize_windows_host_switch",
        lambda tx: committed.append(tx),
    )
    monkeypatch.setattr(setup_wizard, "clear_windows_client_autostart", lambda: None)
    monkeypatch.setattr(
        setup_wizard, "install_windows_personal_autostart", lambda: None
    )
    setup_wizard.write_personal_config(data)
    assert committed == [transaction_id]


def test_windows_launcher_blocks_all_roles_until_pending_switch_recovers(
    monkeypatch, tmp_path: Path
) -> None:
    launcher_path = (
        Path(__file__).parents[2] / "packaging" / "windows" / "windows_launcher.py"
    )
    spec = importlib.util.spec_from_file_location(
        "partyops_windows_launcher_test", launcher_path
    )
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    program_data = tmp_path / "ProgramData"
    pending = program_data / "PartyOps" / "host-switch-pending.json"
    pending.parent.mkdir(parents=True)
    pending.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(launcher.sys, "argv", [str(tmp_path / "PartyOps.exe")])
    launched: list[tuple[Path, Path, list[str]]] = []
    monkeypatch.setattr(
        launcher,
        "launch_wizard_and_wait",
        lambda runtime, local, arguments: launched.append(
            (runtime, local, arguments)
        )
        or True,
    )
    assert launcher.main() == 1
    assert launched and launched[0][2] == []

    launched.clear()
    monkeypatch.setattr(
        launcher.sys,
        "argv",
        [str(tmp_path / "PartyOps.exe"), "--background"],
    )
    assert launcher.main() == 1
    assert launched == []
