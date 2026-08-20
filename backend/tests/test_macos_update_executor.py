"""macOS 更新适配器的对抗式分支矩阵。"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from app import update_executor
from app.models import UpdatePackage, UpdateRun, UpdateStatus


RUN_ID = "a" * 32


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_macos_bundle_version_rejects_untrusted_metadata(tmp_path: Path) -> None:
    app = tmp_path / "PartyOps.app"
    assert update_executor._macos_bundle_version(app) == ""

    app.mkdir()
    info = app / "Contents" / "Info.plist"
    info.parent.mkdir()
    info.write_bytes(b"not-a-plist")
    assert update_executor._macos_bundle_version(app) == ""

    info.write_bytes(plistlib.dumps(["not", "a", "dictionary"]))
    assert update_executor._macos_bundle_version(app) == ""

    info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": " 1.4.3-rc.9 "}))
    assert update_executor._macos_bundle_version(app) == "1.4.3-rc.9"

    link = tmp_path / "Linked.app"
    link.symlink_to(app, target_is_directory=True)
    assert update_executor._macos_bundle_version(link) == ""


def test_macos_application_path_only_honors_test_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PARTYOPS_MACOS_APP_PATH", str(tmp_path / "ignored.app"))
    assert update_executor._macos_application_path() == Path("/Applications/PartyOps.app")

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    monkeypatch.setenv("PARTYOPS_MACOS_APP_PATH", "  ")
    assert update_executor._macos_application_path() == Path("/Applications/PartyOps.app")
    expected = tmp_path / "isolated.app"
    monkeypatch.setenv("PARTYOPS_MACOS_APP_PATH", str(expected))
    assert update_executor._macos_application_path() == expected.resolve()


def test_macos_trust_requires_codesign_and_gatekeeper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = tmp_path / "PartyOps.app"
    assert not update_executor._macos_application_is_trusted(app)
    app.mkdir()

    results = iter([completed(1)])
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(results))
    assert not update_executor._macos_application_is_trusted(app)

    results = iter([completed(), completed(1)])
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(results))
    assert not update_executor._macos_application_is_trusted(app)

    results = iter([completed(), completed()])
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(results))
    assert update_executor._macos_application_is_trusted(app)

    link = tmp_path / "PartyOps-link.app"
    link.symlink_to(app, target_is_directory=True)
    assert not update_executor._macos_application_is_trusted(link)


def test_macos_process_path_rejects_wrong_platform_and_libproc_failure(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    assert update_executor._macos_process_path(10) is None
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    assert update_executor._macos_process_path(0) is None

    import ctypes

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: (_ for _ in ()).throw(OSError()))
    assert update_executor._macos_process_path(10) is None


def test_stop_macos_runtime_validates_process_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = tmp_path / "PartyOps.app"
    expected = (app / "Contents" / "MacOS" / "partyops").resolve()
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    assert not update_executor._stop_macos_runtime(app, 18775)
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    assert not update_executor._stop_macos_runtime(app, 80)
    monkeypatch.setattr(update_executor.os, "getuid", lambda: 501, raising=False)

    def fake_run(command, **_kwargs):
        return completed(stdout="111 junk 222") if command[0].endswith("lsof") else completed()

    monkeypatch.setattr(update_executor, "_run", fake_run)
    monkeypatch.setattr(update_executor.os, "getpid", lambda: 222)
    monkeypatch.setattr(update_executor, "_macos_process_path", lambda _pid: tmp_path / "other")
    with pytest.raises(RuntimeError, match="身份不明"):
        update_executor._stop_macos_runtime(app, 18775)

    states = {111: [expected, None]}
    monkeypatch.setattr(
        update_executor,
        "_macos_process_path",
        lambda pid: states[pid].pop(0) if states.get(pid) else None,
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(update_executor.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert update_executor._stop_macos_runtime(app, 18775)
    assert killed and killed[0][0] == 111


def test_stop_macos_runtime_times_out_without_killing_unknown_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = tmp_path / "PartyOps.app"
    expected = (app / "Contents" / "MacOS" / "partyops").resolve()
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: completed(stdout="111")
        if command[0].endswith("lsof")
        else completed(),
    )
    monkeypatch.setattr(update_executor, "_macos_process_path", lambda _pid: expected)
    monkeypatch.setattr(update_executor.os, "kill", lambda *_a: None)
    ticks = iter([0.0, 0.0, 31.0])
    monkeypatch.setattr(update_executor.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(update_executor.time, "sleep", lambda *_a: None)
    with pytest.raises(RuntimeError, match="未能安全退出"):
        update_executor._stop_macos_runtime(app, 18775)


def test_macos_installer_restore_and_launch_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "update.pkg"
    package.write_bytes(b"pkg")
    app = tmp_path / "PartyOps.app"
    snapshot = tmp_path / "snapshot.app"
    failed = tmp_path / "failed.app"
    app.mkdir()
    snapshot.mkdir()

    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed())
    assert update_executor._run_macos_privileged_installer(package)
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(1))
    assert not update_executor._run_macos_privileged_installer(package)

    failed.mkdir()
    monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: True)
    assert not update_executor._restore_macos_application(snapshot, app, failed)
    failed.rmdir()

    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed())
    assert update_executor._restore_macos_application(snapshot, app, failed)

    trust = iter([True, False, False])
    monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: next(trust))
    runs = iter([completed(1), completed(1)])
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(runs))
    assert not update_executor._restore_macos_application(snapshot, app, failed)

    monkeypatch.setattr(update_executor, "_macos_application_path", lambda: app)
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed())
    assert update_executor._launch_macos_application()
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(1))
    assert not update_executor._launch_macos_application()


def test_macos_restore_recovers_official_app_after_invalid_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "PartyOps-previous.app"
    app = tmp_path / "PartyOps.app"
    failed = tmp_path / "PartyOps-failed.app"
    snapshot.mkdir()
    app.mkdir()
    results = iter([completed(1), completed()])
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(results))
    monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: True)
    assert not update_executor._restore_macos_application(snapshot, app, failed)


@pytest.mark.parametrize(
    ("platform_name", "run_id"),
    [("linux", RUN_ID), ("darwin", "bad")],
)
def test_launch_macos_update_rejects_invalid_context(
    monkeypatch: pytest.MonkeyPatch, platform_name: str, run_id: str
) -> None:
    monkeypatch.setattr(update_executor.sys, "platform", platform_name)
    assert not update_executor.launch_macos_update(run_id)


def test_launch_macos_update_missing_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "partyops"
    executable.write_bytes(b"app")
    updater = tmp_path / "partyops-updater"
    settings = SimpleNamespace(data_dir=tmp_path / "data")
    failures: list[dict] = []
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor.sys, "executable", str(executable))
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "_set_run", lambda _id, **kw: failures.append(kw))
    assert not update_executor.launch_macos_update(RUN_ID)
    assert failures[-1]["status"] == UpdateStatus.FAILED

    updater.write_bytes(b"helper")
    monkeypatch.setattr(update_executor.subprocess, "Popen", lambda *_a, **_k: object())
    assert update_executor.launch_macos_update(RUN_ID)

    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied")),
    )
    assert not update_executor.launch_macos_update(RUN_ID)
    assert "未能启动" in failures[-1]["message"]


def test_launch_linux_personal_update_branch_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(update_executor.os, "name", "nt")
    assert not update_executor.launch_linux_personal_update(RUN_ID)
    monkeypatch.setattr(update_executor.os, "name", "posix")
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    # 在 Windows 测试进程中只模拟 update_executor 的 POSIX 分支；路径对象
    # 仍使用宿主 WindowsPath，避免 pathlib 因全局 os.name 变化拒绝实例化。
    monkeypatch.setattr(update_executor, "Path", type(tmp_path))
    assert not update_executor.launch_linux_personal_update("bad/run")

    executable = tmp_path / "python"
    executable.write_bytes(b"python")
    monkeypatch.setattr(update_executor.sys, "executable", str(executable))
    monkeypatch.setattr(update_executor.sys, "frozen", False, raising=False)
    monkeypatch.setattr(update_executor.subprocess, "Popen", lambda *_a, **_k: object())
    assert update_executor.launch_linux_personal_update(RUN_ID)

    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    updater = tmp_path / "partyops-updater"
    updater.write_bytes(b"helper")
    assert update_executor.launch_linux_personal_update(RUN_ID)

    failures: list[dict] = []
    monkeypatch.setattr(update_executor, "_set_run", lambda _id, **kw: failures.append(kw))
    updater.unlink()
    assert not update_executor.launch_linux_personal_update(RUN_ID)
    assert failures[-1]["status"] == UpdateStatus.FAILED


class FakeSession:
    def __init__(self, run, package, backup):
        self.run = run
        self.package = package
        self.backup = backup

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, model, _identifier):
        return self.run if model is UpdateRun else self.package

    def scalar(self, _statement):
        return self.backup


def configure_macos_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    target_version: str = "1.4.3-rc.9",
    previous_version: str = "1.4.3-rc.8",
):
    updates = tmp_path / "updates"
    backups = tmp_path / "backups"
    data = tmp_path / "data"
    updates.mkdir()
    backups.mkdir()
    data.mkdir()
    package_path = updates / "update.partyops-update"
    package_path.write_bytes(b"signed-update")
    backup_path = backups / "pre-upgrade.zip"
    backup_path.write_bytes(b"backup")
    package = SimpleNamespace(
        id="package",
        filename=package_path.name,
        sha256=update_executor._hash(package_path),
        signature_valid=True,
    )
    run = SimpleNamespace(
        package_id=package.id,
        target_device_id=None,
        status=UpdateStatus.APPLYING,
        created_by="admin",
    )
    backup = SimpleNamespace(filename=backup_path.name)
    settings = SimpleNamespace(
        mode="personal",
        updates_dir=updates,
        backups_dir=backups,
        data_dir=data,
        port=18775,
    )
    app = tmp_path / "PartyOps.app"
    app.mkdir()
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    installed = {"value": False}
    calls: dict[str, list] = {"set": [], "remove": [], "restore": []}

    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: FakeSession(run, package, backup),
    )
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _p: tmp_path / "update.lock")
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _p: True)
    monkeypatch.setattr(update_executor, "_secure_update_backup_root", lambda _id: transaction)
    monkeypatch.setattr(
        update_executor,
        "_cache_verified_rollback_artifact",
        lambda source, target: shutil.copy2(source, target),
    )
    monkeypatch.setattr(
        update_executor,
        "_read_update_manifest",
        lambda _p: {"version": target_version, "format_version": 4},
    )
    monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _m: True)
    monkeypatch.setattr(update_executor, "_assert_update_not_downgrade", lambda _m: None)
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _m: "macos")
    monkeypatch.setattr(update_executor, "_macos_application_path", lambda: app)

    def bundle_version(path: Path) -> str:
        if path.name == "PartyOps-previous.app":
            return previous_version
        return target_version if installed["value"] else previous_version

    monkeypatch.setattr(update_executor, "_macos_bundle_version", bundle_version)
    monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: True)

    def select_artifact(_package, _manifest, _arch, target, _platform):
        target.write_bytes(b"pkg")
        return target

    monkeypatch.setattr(update_executor, "_select_artifact", select_artifact)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "arm64")
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(stdout="Developer ID Installer"))
    monkeypatch.setattr(update_executor.db_runtime, "dispose", lambda: None)
    monkeypatch.setattr(update_executor.db_runtime, "rebuild", lambda: None)
    monkeypatch.setattr(update_executor, "_stop_macos_runtime", lambda *_a: True)

    def install(_artifact):
        installed["value"] = True
        return True

    monkeypatch.setattr(update_executor, "_run_macos_privileged_installer", install)
    monkeypatch.setattr(update_executor, "_launch_macos_application", lambda: True)
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_a: True)
    monkeypatch.setattr(update_executor, "_set_run", lambda _id, **kw: calls["set"].append(kw))
    monkeypatch.setattr(
        update_executor,
        "_remove_secure_update_transaction",
        lambda path: calls["remove"].append(path),
    )
    monkeypatch.setattr(
        update_executor,
        "restore_database_from_upgrade_backup",
        lambda path: calls["restore"].append(path),
    )
    monkeypatch.setattr(update_executor, "_complete_personal_update_run", lambda *_a, **_k: None)
    monkeypatch.setattr(update_executor, "_record_restored_personal_update_run", lambda *_a, **_k: None)
    return SimpleNamespace(
        settings=settings,
        run=run,
        package=package,
        backup=backup,
        package_path=package_path,
        backup_path=backup_path,
        app=app,
        transaction=transaction,
        installed=installed,
        calls=calls,
    )


def test_execute_macos_update_success_and_same_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    assert update_executor.execute_macos_update(RUN_ID)
    assert state.installed["value"]
    assert state.calls["set"][-1]["status"] == UpdateStatus.COMPLETED

    other = tmp_path / "same"
    other.mkdir()
    state = configure_macos_transaction(
        monkeypatch,
        other,
        target_version="1.4.3-rc.9",
        previous_version="1.4.3-rc.9",
    )
    assert update_executor.execute_macos_update(RUN_ID)
    assert not state.installed["value"]


def test_execute_macos_update_precondition_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    assert not update_executor.execute_macos_update(RUN_ID)
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    assert not update_executor.execute_macos_update("bad/run")

    state.settings.mode = "client"
    assert not update_executor.execute_macos_update(RUN_ID)
    state.settings.mode = "host"
    state.run.target_device_id = "device"
    assert not update_executor.execute_macos_update(RUN_ID)
    state.run.target_device_id = None
    state.run.status = UpdateStatus.FAILED
    assert not update_executor.execute_macos_update(RUN_ID)
    state.run.status = UpdateStatus.APPLYING
    state.package.signature_valid = False
    assert not update_executor.execute_macos_update(RUN_ID)


def test_execute_macos_update_missing_inputs_and_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    state.package_path.unlink()
    assert not update_executor.execute_macos_update(RUN_ID)
    assert state.calls["set"][-1]["status"] == UpdateStatus.FAILED

    state.package_path.write_bytes(b"signed-update")
    state.package.sha256 = update_executor._hash(state.package_path)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _p: False)
    assert not update_executor.execute_macos_update(RUN_ID)
    assert "正在运行" in state.calls["set"][-1]["message"]


def test_execute_macos_update_rolls_back_after_installer_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_run_macos_privileged_installer", lambda _p: False)
    monkeypatch.setattr(update_executor, "_restore_macos_application", lambda *_a: True)
    assert not update_executor.execute_macos_update(RUN_ID)
    assert state.calls["restore"] == [state.backup_path]


@pytest.mark.parametrize(
    "failure",
    ["signature", "platform", "current-app", "pkg-signature", "gatekeeper", "snapshot"],
)
def test_execute_macos_update_fails_closed_before_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    if failure == "signature":
        monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _m: False)
    elif failure == "platform":
        monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _m: "linux-deb")
    elif failure == "current-app":
        monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: False)
    elif failure == "pkg-signature":
        monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(1))
    elif failure == "gatekeeper":
        results = iter([completed(stdout="Developer ID Installer"), completed(1)])
        monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(results))
    elif failure == "snapshot":
        def run(command, **_kwargs):
            if command[0].endswith("ditto"):
                return completed(1)
            return completed(stdout="Developer ID Installer")
        monkeypatch.setattr(update_executor, "_run", run)
    assert not update_executor.execute_macos_update(RUN_ID)
    assert state.calls["set"][-1]["status"] == UpdateStatus.FAILED


def configure_macos_device_transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    target_version: str = "1.4.3-rc.9",
    previous_version: str = "1.4.3-rc.8",
):
    package = tmp_path / "device.partyops-update"
    package.write_bytes(b"signed-device-update")
    app = tmp_path / "PartyOps.app"
    app.mkdir()
    transaction = tmp_path / "transaction"
    transaction.mkdir()
    installed = {"value": False}
    removed: list[Path] = []
    restored: list[tuple[Path, Path, Path]] = []

    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _p: tmp_path / "device.lock")
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _p: True)
    monkeypatch.setattr(update_executor, "_secure_update_backup_root", lambda _id: transaction)
    monkeypatch.setattr(
        update_executor,
        "_cache_verified_rollback_artifact",
        lambda source, target: shutil.copy2(source, target),
    )
    monkeypatch.setattr(
        update_executor,
        "_read_update_manifest",
        lambda _p: {"version": target_version, "format_version": 4},
    )
    monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _m: True)
    monkeypatch.setattr(update_executor, "_assert_update_not_downgrade", lambda _m: None)
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _m: "macos")
    monkeypatch.setattr(update_executor, "_macos_application_path", lambda: app)

    def bundle_version(path: Path) -> str:
        if path.name == "PartyOps-previous.app":
            return previous_version
        return target_version if installed["value"] else previous_version

    monkeypatch.setattr(update_executor, "_macos_bundle_version", bundle_version)
    monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: True)

    def select_artifact(_package, _manifest, _arch, target, _platform):
        target.write_bytes(b"pkg")
        return target

    monkeypatch.setattr(update_executor, "_select_artifact", select_artifact)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "arm64")
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(stdout="Developer ID Installer"))

    def install(_artifact):
        installed["value"] = True
        return True

    monkeypatch.setattr(update_executor, "_run_macos_privileged_installer", install)
    monkeypatch.setattr(
        update_executor,
        "_remove_secure_update_transaction",
        lambda path: removed.append(path),
    )

    def restore(snapshot, app_path, failed):
        restored.append((snapshot, app_path, failed))
        return True

    monkeypatch.setattr(update_executor, "_restore_macos_application", restore)
    return SimpleNamespace(
        package=package,
        app=app,
        transaction=transaction,
        installed=installed,
        removed=removed,
        restored=restored,
    )


def test_install_macos_device_package_rejects_invalid_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "device.partyops-update"
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    assert not update_executor.install_macos_device_package(package)
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    assert not update_executor.install_macos_device_package(package)
    package.write_bytes(b"update")
    link = tmp_path / "linked.partyops-update"
    link.symlink_to(package)
    assert not update_executor.install_macos_device_package(link)


def test_install_macos_device_package_lock_same_version_and_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_device_transaction(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _p: False)
    assert not update_executor.install_macos_device_package(state.package)

    same = tmp_path / "same"
    same.mkdir()
    state = configure_macos_device_transaction(
        monkeypatch,
        same,
        target_version="1.4.3-rc.9",
        previous_version="1.4.3-rc.9",
    )
    assert update_executor.install_macos_device_package(state.package)
    assert not state.installed["value"]
    assert state.removed == [state.transaction]

    full = tmp_path / "full"
    full.mkdir()
    state = configure_macos_device_transaction(monkeypatch, full)
    assert update_executor.install_macos_device_package(state.package)
    assert state.installed["value"]
    assert state.removed == [state.transaction]


@pytest.mark.parametrize(
    "failure",
    [
        "signature",
        "platform",
        "current-app",
        "pkg-signature",
        "pkg-identity",
        "gatekeeper",
        "snapshot-copy",
        "snapshot-version",
        "snapshot-trust",
        "installer",
        "installed-version",
        "installed-trust",
    ],
)
def test_install_macos_device_package_failure_and_rollback_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    state = configure_macos_device_transaction(monkeypatch, tmp_path)
    if failure == "signature":
        monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _m: False)
    elif failure == "platform":
        monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _m: "windows")
    elif failure == "current-app":
        monkeypatch.setattr(update_executor, "_macos_application_is_trusted", lambda _p: False)
    elif failure == "pkg-signature":
        monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(1))
    elif failure == "pkg-identity":
        monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(stdout="unsigned"))
    elif failure == "gatekeeper":
        results = iter([completed(stdout="Developer ID Installer"), completed(1)])
        monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(results))
    elif failure == "snapshot-copy":
        def run(command, **_kwargs):
            return completed(1) if command[0].endswith("ditto") else completed(stdout="Developer ID Installer")
        monkeypatch.setattr(update_executor, "_run", run)
    elif failure == "snapshot-version":
        original = update_executor._macos_bundle_version
        monkeypatch.setattr(
            update_executor,
            "_macos_bundle_version",
            lambda path: "wrong" if path.name == "PartyOps-previous.app" else original(path),
        )
    elif failure == "snapshot-trust":
        monkeypatch.setattr(
            update_executor,
            "_macos_application_is_trusted",
            lambda path: path.name != "PartyOps-previous.app",
        )
    elif failure == "installer":
        monkeypatch.setattr(update_executor, "_run_macos_privileged_installer", lambda _p: False)
    elif failure == "installed-version":
        monkeypatch.setattr(update_executor, "_macos_bundle_version", lambda _p: "1.4.3-rc.8")
    elif failure == "installed-trust":
        trust_calls = {"count": 0}

        def trust(path):
            trust_calls["count"] += 1
            return trust_calls["count"] < 3

        monkeypatch.setattr(update_executor, "_macos_application_is_trusted", trust)

    assert not update_executor.install_macos_device_package(state.package)
    if failure in {"installer", "installed-version", "installed-trust"}:
        assert state.restored
    else:
        assert state.removed == [state.transaction]


def test_macos_secure_update_paths_and_root_public_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(update_executor.os, "name", "posix")
    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(update_executor.Path, "home", classmethod(lambda cls: tmp_path))
    assert update_executor._update_lock_path(tmp_path / "data") == (
        tmp_path / "Library" / "Caches" / "PartyOps" / "update.lock"
    )
    with pytest.raises(RuntimeError, match="编号无效"):
        update_executor._secure_update_backup_root("bad/run")
    transaction = update_executor._secure_update_backup_root("mac-safe")
    assert transaction.is_dir()
    with pytest.raises(RuntimeError, match="已存在"):
        update_executor._secure_update_backup_root("mac-safe")

    # Windows 上不能实例化 PosixPath；下面只验证 Darwin 安装目录候选与
    # 生产配置隔离，POSIX root/权限分支由原生 macOS 工作流覆盖。
    monkeypatch.setattr(update_executor.os, "name", "nt")
    executable = tmp_path / "PartyOps.app" / "Contents" / "MacOS" / "partyops-updater"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"helper")
    public_key = executable.parent.parent / "Resources" / "update-public-key.txt"
    public_key.parent.mkdir()
    public_key.write_text("root-public-key\n", encoding="utf-8")
    public_key.chmod(0o600)
    settings = SimpleNamespace(environment="production", update_public_key="attacker")
    monkeypatch.setattr(update_executor.sys, "executable", str(executable))
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    assert update_executor._trusted_public_key() == "root-public-key"

    public_key.write_bytes(b"x" * 4097)
    assert update_executor._trusted_public_key() == ""
    public_key.unlink()
    assert update_executor._trusted_public_key() == ""


def test_linux_secure_update_transaction_uses_system_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "linux-update-transactions"
    native_path = type(tmp_path)

    def routed_path(value):
        if str(value) == "/var/cache/partyops/update-transactions":
            return cache
        return native_path(value)

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(update_executor.os, "name", "posix")
    monkeypatch.setattr(update_executor.sys, "platform", "linux")
    monkeypatch.setattr(update_executor, "Path", routed_path)
    transaction = update_executor._secure_update_backup_root("linux-safe")
    assert transaction == cache / "linux-safe"
    assert transaction.is_dir()


def test_discard_personal_rollback_rejects_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rollback = tmp_path / "rollback.pkg"
    metadata = tmp_path / "metadata.json"
    rollback.write_bytes(b"pkg")
    metadata.write_text("{}", encoding="utf-8")
    digest = update_executor._rollback_digest_path(rollback)
    digest.write_text("hash", encoding="utf-8")
    monkeypatch.setattr(
        update_executor, "_personal_native_rollback_paths", lambda _id: (rollback, metadata)
    )
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda p: p == digest)
    with pytest.raises(RuntimeError, match="不能是链接"):
        update_executor._discard_personal_native_rollback(RUN_ID)

    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _p: False)
    update_executor._discard_personal_native_rollback(RUN_ID)
    assert not rollback.exists() and not metadata.exists() and not digest.exists()


def test_windows_service_stop_polling_and_start_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"exe")
    monkeypatch.setattr(update_executor.sys, "executable", str(executable))
    monkeypatch.delenv("PARTYOPS_ENVIRONMENT", raising=False)
    calls = iter(
        [
            completed(stdout="STATE : 4 RUNNING"),
            completed(),
            completed(stdout="STATE : 4 RUNNING"),
            completed(stdout="STATE : 1 STOPPED"),
        ]
    )
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: next(calls))
    monkeypatch.setattr(update_executor.time, "sleep", lambda *_a: None)
    assert update_executor._stop_windows_host_service() == (True, True)

    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(1056))
    assert update_executor._start_windows_host_service_after_update()
    monkeypatch.setattr(update_executor, "_run", lambda *_a, **_k: completed(5))
    assert not update_executor._start_windows_host_service_after_update()


def test_execute_macos_update_database_precondition_and_input_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: FakeSession(None, state.package, state.backup),
    )
    assert not update_executor.execute_macos_update(RUN_ID)

    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: FakeSession(state.run, None, state.backup),
    )
    assert not update_executor.execute_macos_update(RUN_ID)

    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: FakeSession(state.run, state.package, None),
    )
    assert not update_executor.execute_macos_update(RUN_ID)

    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        lambda: FakeSession(state.run, state.package, state.backup),
    )
    state.package.sha256 = "0" * 64
    assert not update_executor.execute_macos_update(RUN_ID)


@pytest.mark.parametrize("failure", ["installed-version", "installed-trust", "launch", "health"])
def test_execute_macos_update_post_install_failure_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_restore_macos_application", lambda *_a: False)
    if failure == "installed-version":
        monkeypatch.setattr(update_executor, "_macos_bundle_version", lambda _p: "1.4.3-rc.8")
    elif failure == "installed-trust":
        calls = {"count": 0}

        def trust(_path):
            calls["count"] += 1
            return calls["count"] < 3

        monkeypatch.setattr(update_executor, "_macos_application_is_trusted", trust)
    elif failure == "launch":
        monkeypatch.setattr(update_executor, "_launch_macos_application", lambda: False)
    elif failure == "health":
        monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_a: False)
    assert not update_executor.execute_macos_update(RUN_ID)
    assert state.calls["set"][-1]["status"] == UpdateStatus.FAILED


def test_execute_macos_update_runtime_stop_recovery_and_rollback_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = configure_macos_transaction(monkeypatch, tmp_path)
    original_set = update_executor._set_run

    def fail_after_stop(_run_id, **values):
        if values.get("progress") == 45:
            raise RuntimeError("status store unavailable")
        return original_set(_run_id, **values)

    monkeypatch.setattr(update_executor, "_set_run", fail_after_stop)
    assert not update_executor.execute_macos_update(RUN_ID)

    other = tmp_path / "rollback-exception"
    other.mkdir()
    state = configure_macos_transaction(monkeypatch, other)
    monkeypatch.setattr(update_executor, "_run_macos_privileged_installer", lambda _p: False)
    monkeypatch.setattr(
        update_executor,
        "_restore_macos_application",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("rollback failed")),
    )
    assert not update_executor.execute_macos_update(RUN_ID)
    assert state.calls["set"][-1]["status"] == UpdateStatus.FAILED


@pytest.mark.parametrize(
    ("arguments", "success", "expected"),
    [
        (["--personal-run-id", RUN_ID], True, 0),
        (["--personal-run-id", RUN_ID], False, 1),
        (["--linux-personal-run-id", RUN_ID], True, 0),
        (["--linux-personal-run-id", RUN_ID], False, 1),
        (["--macos-run-id", RUN_ID], True, 0),
        (["--macos-run-id", RUN_ID], False, 1),
        (["--macos-install-package", "update.pkg"], True, 0),
        (["--macos-install-package", "update.pkg"], False, 1),
        (
            ["--linux-personal-transaction", RUN_ID],
            True,
            update_executor.LINUX_PERSONAL_TRANSACTION_FAILED,
        ),
        (
            [
                "--linux-personal-transaction",
                RUN_ID,
                "--personal-package",
                "update.deb",
                "--personal-backup",
                "backup.deb",
            ],
            True,
            73,
        ),
        (["--install-package", "update.partyops-update"], True, 0),
        (["--install-package", "update.partyops-update"], False, 1),
        (["--supervisor", "--once"], True, 31),
        (["--run-id", RUN_ID], True, 0),
        (["--run-id", RUN_ID], False, 1),
        (["--windows-system-service", "--once"], True, 41),
        (["--once"], True, 41),
    ],
)
def test_update_executor_cli_dispatch_matrix(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    success: bool,
    expected: int,
) -> None:
    """每个特权入口都必须精确路由并把失败转换成非零退出码。"""

    monkeypatch.setattr(sys, "argv", ["partyops-updater", *arguments])
    monkeypatch.setattr(
        update_executor, "execute_windows_personal_update", lambda _id: success
    )
    monkeypatch.setattr(
        update_executor, "execute_linux_personal_update", lambda _id: success
    )
    monkeypatch.setattr(update_executor, "execute_macos_update", lambda _id: success)
    monkeypatch.setattr(
        update_executor, "install_macos_device_package", lambda _path: success
    )
    monkeypatch.setattr(
        update_executor,
        "execute_linux_personal_root_transaction",
        lambda *_args: 73,
    )
    monkeypatch.setattr(update_executor, "install_device_package", lambda _path: success)
    monkeypatch.setattr(update_executor, "run_supervisor", lambda _once: 31)
    monkeypatch.setattr(update_executor, "execute_host_update", lambda _id: success)
    monkeypatch.setattr(update_executor, "run_daemon", lambda _once: 41)

    with pytest.raises(SystemExit) as stopped:
        update_executor.main()
    assert stopped.value.code == expected


def test_update_platform_and_native_version_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧清单兼容与三类原生包查询不能混用平台标识。"""

    monkeypatch.setattr(update_executor.os, "name", "nt")
    assert update_executor._manifest_platform_name({"format_version": 2}) == "windows"
    monkeypatch.setattr(update_executor.os, "name", "posix")
    assert update_executor._manifest_platform_name({"format_version": 2}) == "uos"

    monkeypatch.setattr(update_executor, "detect_platform_info", lambda: object())
    monkeypatch.setattr(update_executor, "update_platform_key", lambda _info: "")
    with pytest.raises(RuntimeError, match="无法匹配"):
        update_executor._manifest_platform_name({"format_version": 4})
    monkeypatch.setattr(update_executor, "update_platform_key", lambda _info: "linux-deb")
    assert update_executor._manifest_platform_name({"format_version": 4}) == "linux-deb"

    monkeypatch.setattr(update_executor, "_installed_rpm_version", lambda: "1.4.3~rc9")
    monkeypatch.setattr(update_executor, "_installed_package_version", lambda: "1.4.3~rc9")
    monkeypatch.setattr(
        update_executor, "_partyops_version_from_native", lambda value, kind: f"{kind}:{value}"
    )
    assert update_executor._linux_native_version("linux-rpm") == "rpm:1.4.3~rc9"
    assert update_executor._linux_native_version("linux-deb") == "deb:1.4.3~rc9"
    assert update_executor._linux_native_version("uos") == "deb:1.4.3~rc9"
    assert update_executor._linux_native_version("windows") == ""


def test_macos_process_path_native_result_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import ctypes

    class ProcPidPath:
        argtypes = None
        restype = None

        def __init__(self, payload: bytes):
            self.payload = payload

        def __call__(self, _pid, buffer, _size):
            if not self.payload:
                return 0
            ctypes.memmove(buffer, self.payload, len(self.payload))
            return len(self.payload)

    class LibProc:
        def __init__(self, payload: bytes):
            self.proc_pidpath = ProcPidPath(payload)

    monkeypatch.setattr(update_executor.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: LibProc(b""))
    assert update_executor._macos_process_path(42) is None

    executable = (tmp_path / "PartyOps.app" / "Contents" / "MacOS" / "partyops").resolve()
    monkeypatch.setattr(
        ctypes,
        "CDLL",
        lambda *_a, **_k: LibProc(os.fsencode(str(executable))),
    )
    assert update_executor._macos_process_path(42) == executable


class StateSession:
    def __init__(self, run=None, package=None, *, pending=None):
        self.run = run
        self.package = package
        self.pending = pending
        self.added: list[object] = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, model, _identifier):
        return self.run if model is UpdateRun else self.package

    def scalar(self, _statement):
        return self.pending

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1


def test_personal_update_state_commit_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = StateSession()
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: empty)
    update_executor._complete_personal_update_run(RUN_ID, message="done")
    assert empty.commits == 1

    run = SimpleNamespace(package_id="package")
    package = SimpleNamespace(status=None)
    populated = StateSession(run, package)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: populated)
    update_executor._complete_personal_update_run(RUN_ID, message="done")
    assert run.status == UpdateStatus.COMPLETED
    assert package.status == UpdateStatus.COMPLETED

    missing_run = StateSession(None, None)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: missing_run)
    update_executor._record_restored_personal_update_run(
        RUN_ID,
        package_id="package",
        created_by="admin",
        message="restored",
    )
    assert len(missing_run.added) == 1

    existing_run = SimpleNamespace()
    validated = SimpleNamespace(status=None)
    existing = StateSession(existing_run, validated)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: existing)
    update_executor._record_restored_personal_update_run(
        RUN_ID,
        package_id="package",
        created_by="admin",
        message="restored",
    )
    assert not existing.added
    assert validated.status == UpdateStatus.VALIDATED


def test_update_daemon_once_with_and_without_pending_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    pending = StateSession(pending=SimpleNamespace(id=RUN_ID))
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: pending)
    monkeypatch.setattr(update_executor, "execute_host_update", calls.append)
    assert update_executor.run_daemon(once=True) == 0
    assert calls == [RUN_ID]

    empty = StateSession(pending=None)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: empty)
    assert update_executor.run_daemon(once=True) == 0
    assert calls == [RUN_ID]

    monkeypatch.setattr(
        update_executor.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop daemon")),
    )
    with pytest.raises(RuntimeError, match="stop daemon"):
        update_executor.run_daemon(once=False)


def test_privileged_linux_environment_rejection_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """root 更新服务只接受固定系统配置与固定数据根。"""

    class PosixPathForTest(PurePosixPath):
        def resolve(self, strict=False):
            return self

    monkeypatch.setattr(update_executor, "Path", PosixPathForTest)
    monkeypatch.setattr(
        update_executor, "_trusted_system_environment_file", lambda _path: False
    )
    assert update_executor._candidate_host_environments() == []

    monkeypatch.setattr(
        update_executor, "_trusted_system_environment_file", lambda _path: True
    )
    configurations = iter(
        [
            {"PARTYOPS_MODE": "client"},
            {"PARTYOPS_MODE": "host", "PARTYOPS_DATA_DIR": "relative/data"},
            {"PARTYOPS_MODE": "host", "PARTYOPS_DATA_DIR": "/srv/partyops"},
            {
                "PARTYOPS_MODE": "host",
                "PARTYOPS_DATA_DIR": "/var/lib/partyops",
                "PARTYOPS_PORT": "18775",
                "UNTRUSTED_SECRET": "discard-me",
            },
        ]
    )
    monkeypatch.setattr(
        update_executor, "_read_environment", lambda _path: next(configurations)
    )
    assert update_executor._candidate_host_environments() == []
    assert update_executor._candidate_host_environments() == []
    assert update_executor._candidate_host_environments() == []
    accepted = update_executor._candidate_host_environments()
    assert accepted == [
        {
            "PARTYOPS_MODE": "host",
            "PARTYOPS_DATA_DIR": "/var/lib/partyops",
            "PARTYOPS_PORT": "18775",
            "PARTYOPS_ENVIRONMENT": "production",
            "PARTYOPS_STRICT_SQLITE": "true",
        }
    ]
