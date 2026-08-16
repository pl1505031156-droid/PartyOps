"""Windows 个人模式专业更新的授权、幂等、回滚与失败边界。"""

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import client_agent, update_executor
from app.enums import UpdateStatus
from app.models import UpdatePackage, UpdateRun


def _os_proxy(name: str) -> SimpleNamespace:
    proxy = SimpleNamespace(**vars(os))
    proxy.name = name
    return proxy


def _settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "数据"
    updates_dir = data_dir / "updates"
    updates_dir.mkdir(parents=True)
    return SimpleNamespace(
        mode="personal",
        data_dir=data_dir,
        updates_dir=updates_dir,
        app_version="1.4.3-rc.2",
    )


def _records(filename: str = "release.partyops-update") -> tuple[SimpleNamespace, SimpleNamespace]:
    run = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        package_id="package-1",
        target_device_id=None,
        status=UpdateStatus.UPLOADED,
        progress=0,
        message="",
        completed_at=None,
    )
    package = SimpleNamespace(
        id="package-1",
        filename=filename,
        sha256="expected",
        status=UpdateStatus.VALIDATED,
    )
    return run, package


def _session_factory(run: object | None, package: object | None, commits: list[bool] | None = None):
    class Session:
        def get(self, model, identity):
            if model is UpdateRun and identity == getattr(run, "id", None):
                return run
            if model is UpdatePackage and identity == getattr(package, "id", None):
                return package
            return None

        def commit(self):
            if commits is not None:
                commits.append(True)

    @contextmanager
    def factory():
        yield Session()

    return factory


def _prepare_personal_environment(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    run, package = _records()
    source = settings.updates_dir / package.filename
    source.write_bytes(b"signed-update")
    states: list[dict[str, object]] = []
    removed: list[Path | None] = []
    monkeypatch.setattr(update_executor, "os", _os_proxy("nt"))
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        _session_factory(run, package),
    )
    monkeypatch.setattr(update_executor, "_hash", lambda _path: "expected")
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _root: tmp_path / "system" / "update.lock")

    def acquire(path: Path) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("locked", encoding="utf-8")
        return True

    monkeypatch.setattr(update_executor, "_acquire_update_lock", acquire)
    monkeypatch.setattr(update_executor, "_windows_installer_cache", lambda: tmp_path / "installer-cache")
    monkeypatch.setattr(update_executor, "_verify_cached_rollback_artifact", lambda _path: True)

    def backup_root(_run_id: str) -> Path:
        path = tmp_path / "transaction"
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(update_executor, "_secure_update_backup_root", backup_root)
    monkeypatch.setattr(
        update_executor,
        "_remove_secure_update_transaction",
        lambda path: removed.append(path),
    )
    monkeypatch.setattr(update_executor, "_read_update_manifest", lambda _path: {"version": "1.4.3-rc.3"})
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: "windows")
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")

    def select(_package, _manifest, _architecture, target: Path, _platform):
        target.write_bytes(b"installer")
        return target

    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(update_executor, "_cache_verified_rollback_artifact", lambda *_args: None)
    monkeypatch.setattr(update_executor, "_set_run", lambda _run_id, **state: states.append(state))
    return settings, run, package, states, removed


@pytest.mark.parametrize(
    ("mode", "run_value", "target_device", "package_value", "status"),
    [
        ("host", True, None, True, UpdateStatus.VALIDATED),
        ("personal", False, None, True, UpdateStatus.VALIDATED),
        ("personal", True, "device-1", True, UpdateStatus.VALIDATED),
        ("personal", True, None, False, UpdateStatus.VALIDATED),
        ("personal", True, None, True, UpdateStatus.FAILED),
    ],
)
def test_personal_update_rejects_wrong_mode_or_database_state(
    monkeypatch,
    tmp_path: Path,
    mode: str,
    run_value: bool,
    target_device: str | None,
    package_value: bool,
    status: UpdateStatus,
) -> None:
    settings = _settings(tmp_path)
    settings.mode = mode
    run, package = _records()
    run.target_device_id = target_device
    package.status = status
    monkeypatch.setattr(update_executor, "os", _os_proxy("nt"))
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        _session_factory(run if run_value else None, package if package_value else None),
    )
    assert not update_executor.execute_windows_personal_update(run.id)


def test_personal_update_rejects_missing_package_hash_and_live_lock(monkeypatch, tmp_path: Path) -> None:
    settings, run, package, states, _removed = _prepare_personal_environment(monkeypatch, tmp_path)
    source = settings.updates_dir / package.filename
    source.unlink()
    assert not update_executor.execute_windows_personal_update(run.id)
    assert states[-1]["message"] == "更新包文件缺失或哈希不一致"

    source.write_bytes(b"tampered")
    monkeypatch.setattr(update_executor, "_hash", lambda _path: "wrong")
    assert not update_executor.execute_windows_personal_update(run.id)
    monkeypatch.setattr(update_executor, "_hash", lambda _path: "expected")
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: False)
    assert not update_executor.execute_windows_personal_update(run.id)


def test_personal_update_completes_and_marks_database(monkeypatch, tmp_path: Path) -> None:
    _settings_value, run, package, states, removed = _prepare_personal_environment(monkeypatch, tmp_path)
    commits: list[bool] = []
    monkeypatch.setattr(
        update_executor.db_runtime,
        "session_factory",
        _session_factory(run, package, commits),
    )
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _path: False)
    monkeypatch.setattr(update_executor, "_run_windows_installer", lambda _path: True)
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda version, timeout: version == "1.4.3-rc.3" and timeout == 120)

    assert update_executor.execute_windows_personal_update(run.id)
    assert run.status == UpdateStatus.COMPLETED
    assert package.status == UpdateStatus.COMPLETED
    assert commits
    assert removed and removed[-1] is not None
    assert states[-1]["progress"] == 80


@pytest.mark.parametrize("failure", ["cache-link", "rollback-cache", "stage-hash", "platform"])
def test_personal_update_stops_before_mutation_and_discards_transaction(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:
    _settings_value, run, _package, states, removed = _prepare_personal_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _path: failure == "cache-link")
    if failure == "rollback-cache":
        monkeypatch.setattr(update_executor, "_verify_cached_rollback_artifact", lambda _path: False)
    elif failure == "stage-hash":
        calls = iter(["expected", "different"])
        monkeypatch.setattr(update_executor, "_hash", lambda _path: next(calls))
    elif failure == "platform":
        monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: "linux-deb")

    assert not update_executor.execute_windows_personal_update(run.id)
    assert states[-1]["status"] == UpdateStatus.FAILED
    assert "安全边界内停止" in str(states[-1]["message"])
    assert removed


def test_personal_update_rolls_back_after_installer_or_health_failure(monkeypatch, tmp_path: Path) -> None:
    _settings_value, run, _package, states, removed = _prepare_personal_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _path: False)
    installs = iter([False, True])
    monkeypatch.setattr(update_executor, "_run_windows_installer", lambda _path: next(installs))
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda version, timeout: version == "1.4.3-rc.2" and timeout == 90)

    assert not update_executor.execute_windows_personal_update(run.id)
    assert states[-1]["status"] == UpdateStatus.ROLLED_BACK
    assert "已恢复上一版本" in str(states[-1]["message"])
    assert removed

    # 新版安装成功但健康检查失败时，同样必须进入旧安装器回滚，而不是误报完成。
    states.clear()
    removed.clear()
    installs = iter([True, True])
    health = iter([False, True])
    monkeypatch.setattr(update_executor, "_run_windows_installer", lambda _path: next(installs))
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_args: next(health))
    assert not update_executor.execute_windows_personal_update(run.id)
    assert states[-1]["status"] == UpdateStatus.ROLLED_BACK


def test_personal_update_keeps_failed_transaction_for_diagnosis(monkeypatch, tmp_path: Path) -> None:
    _settings_value, run, _package, states, removed = _prepare_personal_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_is_link_or_reparse_point", lambda _path: False)

    def fail_installer(_path: Path) -> bool:
        raise OSError("installer unavailable")

    monkeypatch.setattr(update_executor, "_run_windows_installer", fail_installer)
    assert not update_executor.execute_windows_personal_update(run.id)
    assert states[-1]["status"] == UpdateStatus.FAILED
    assert not removed


def test_launch_personal_update_validates_id_executable_and_uac_result(monkeypatch, tmp_path: Path) -> None:
    valid_id = "11111111-1111-4111-8111-111111111111"
    states: list[dict[str, object]] = []
    monkeypatch.setattr(update_executor, "_set_run", lambda _run_id, **state: states.append(state))
    monkeypatch.setattr(update_executor, "os", _os_proxy("posix"))
    assert not update_executor.launch_windows_personal_update(valid_id)

    monkeypatch.setattr(update_executor, "os", _os_proxy("nt"))
    assert not update_executor.launch_windows_personal_update("../invalid")
    runtime = tmp_path / "PartyOps.exe"
    runtime.write_bytes(b"runtime")
    monkeypatch.setattr(update_executor.sys, "executable", str(runtime))
    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    assert not update_executor.launch_windows_personal_update(valid_id)
    assert states[-1]["status"] == UpdateStatus.FAILED

    updater = tmp_path / "PartyOpsUpdater.exe"
    updater.write_bytes(b"updater")
    closed: list[int] = []

    class Shell32:
        result = True

        def ShellExecuteExW(self, pointer):
            pointer._obj.hProcess = 42
            return self.result

    shell32 = Shell32()
    kernel32 = SimpleNamespace(CloseHandle=lambda handle: closed.append(int(handle)))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=shell32, kernel32=kernel32))
    assert update_executor.launch_windows_personal_update(valid_id)
    assert closed == [42]

    shell32.result = False
    assert not update_executor.launch_windows_personal_update(valid_id)
    assert states[-1]["status"] == UpdateStatus.FAILED

    def policy_error() -> OSError:
        error = OSError("管理员用策略规则限制了访问")
        error.winerror = 786
        return error

    monkeypatch.setattr(ctypes, "WinError", policy_error)
    assert not update_executor.launch_windows_personal_update(valid_id)
    assert "ADMIN_POLICY_BLOCKED" in str(states[-1]["message"])


def test_launch_personal_update_uses_python_module_in_source_checkout(monkeypatch, tmp_path: Path) -> None:
    valid_id = "22222222-2222-4222-8222-222222222222"
    python = tmp_path / "python.exe"
    python.write_bytes(b"python")
    monkeypatch.setattr(update_executor, "os", _os_proxy("nt"))
    monkeypatch.setattr(update_executor.sys, "executable", str(python))
    monkeypatch.setattr(update_executor.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(
            shell32=SimpleNamespace(ShellExecuteExW=lambda _pointer: True),
            kernel32=SimpleNamespace(CloseHandle=lambda _handle: None),
        ),
    )
    assert update_executor.launch_windows_personal_update(valid_id)


def test_agent_windows_uac_update_waits_for_verified_helper_result(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "PartyOpsUpdater.exe"
    package = tmp_path / "release.partyops-update"
    helper.write_bytes(b"helper")
    package.write_bytes(b"package")
    waits: list[int] = []
    closed: list[int] = []

    class Shell32:
        result = True
        process = 42

        def ShellExecuteExW(self, pointer):
            pointer._obj.hProcess = self.process
            return self.result

    class Kernel32:
        wait_result = 0
        exit_query = True
        exit_code = 0

        def WaitForSingleObject(self, _process, timeout):
            waits.append(timeout)
            return self.wait_result

        def GetExitCodeProcess(self, _process, pointer):
            pointer._obj.value = self.exit_code
            return self.exit_query

        def CloseHandle(self, process):
            closed.append(int(process))

    shell = Shell32()
    kernel = Kernel32()
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(shell32=shell, kernel32=kernel),
    )
    assert client_agent._run_windows_elevated_update(helper, package, timeout_seconds=0)
    assert waits == [1000]
    assert closed == [42]

    kernel.exit_code = 5
    assert not client_agent._run_windows_elevated_update(helper, package, timeout_seconds=2)
    assert waits[-1] == 2000
    kernel.exit_code = 0
    kernel.exit_query = False
    assert not client_agent._run_windows_elevated_update(helper, package)
    kernel.exit_query = True
    kernel.wait_result = 258
    with pytest.raises(client_agent.AgentCommandError) as timed_out:
        client_agent._run_windows_elevated_update(helper, package)
    assert timed_out.value.code == "UPDATE_STATE_UNKNOWN"
    assert not timed_out.value.retryable
    kernel.wait_result = 0
    shell.process = 0
    assert not client_agent._run_windows_elevated_update(helper, package)
    shell.process = 42
    shell.result = False
    assert not client_agent._run_windows_elevated_update(helper, package)

    def policy_error() -> OSError:
        error = OSError("管理员用策略规则限制了访问")
        error.winerror = 786
        return error

    monkeypatch.setattr(ctypes, "WinError", policy_error)
    with pytest.raises(client_agent.AgentCommandError) as policy_blocked:
        client_agent._run_windows_elevated_update(helper, package)
    assert policy_blocked.value.code == "ADMIN_POLICY_BLOCKED"
