"""rc.3 首次配置向导的跨平台失败矩阵与恢复分支。"""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import sys
import urllib.error
import winreg
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import setup_wizard
from app.windows_host_status import HEALTH_TIMEOUT, SERVICE_MISSING, SERVICE_STOPPED


def _os_proxy(name: str) -> SimpleNamespace:
    proxy = SimpleNamespace(**vars(os))
    proxy.name = name
    return proxy


class _HealthResponse:
    def __init__(self, payload: bytes = b'{"status":"ok"}', status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def test_installer_defaults_private_write_and_host_selection(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    marker = program_data / "PartyOps" / "install-data-dir.txt"
    marker.parent.mkdir(parents=True)
    selected = tmp_path / "业务 数据"
    marker.write_text(str(selected), encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    assert setup_wizard.installer_default_data_dir() == selected
    assert setup_wizard.initial_personal_data_dir() == selected

    marker.write_text("relative/path", encoding="utf-8")
    assert setup_wizard.installer_default_data_dir() == program_data / "PartyOps-Data"
    marker.unlink()
    assert setup_wizard.installer_default_data_dir() == program_data / "PartyOps-Data"

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    assert setup_wizard.installer_default_data_dir().name == "PartyOps-数据"
    private = tmp_path / "config" / "private.json"
    chmod_calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        Path,
        "chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    setup_wizard._write_private(private, "{}")
    assert private.read_text(encoding="utf-8") == "{}"
    assert chmod_calls and chmod_calls[0][1] == 0o600

    monkeypatch.setattr(
        setup_wizard, "discover_lan_addresses", lambda: ["192.168.10.8"]
    )
    setup_wizard.validate_host_config_selection("192.168.10.8", 18765)
    with pytest.raises(ValueError, match="局域网地址"):
        setup_wizard.validate_host_config_selection("192.168.10.9", 18765)
    for port in (1023, 65535):
        with pytest.raises(ValueError, match="主机端口"):
            setup_wizard.validate_host_config_selection("127.0.0.1", port)


def test_personal_mode_never_requests_service_or_admin(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    data_dir = tmp_path / "个人 数据"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(
        setup_wizard, "_validate_personal_data_dir", lambda path: path.resolve()
    )
    monkeypatch.setattr(setup_wizard, "clear_windows_client_autostart", lambda: None)
    monkeypatch.setattr(
        setup_wizard, "install_windows_personal_autostart", lambda: None
    )
    path = setup_wizard.write_personal_config(data_dir, 18775)
    content = path.read_text(encoding="utf-8")
    assert "PARTYOPS_MODE=personal" in content
    assert "PARTYOPS_BIND_HOST=127.0.0.1" in content
    assert "PARTYOPS_TLS_ENABLED=false" in content
    assert (
        json.loads((config / "mode.json").read_text(encoding="utf-8"))["mode"]
        == "personal"
    )
    marker = json.loads(
        (data_dir / ".partyops-data-root.json").read_text(encoding="utf-8")
    )
    assert marker["product"] == "PartyOps" and marker["scopes"] == ["personal"]

    environment = {
        "PARTYOPS_PORT": "18775",
        "PARTYOPS_DATA_DIR": str(data_dir),
    }
    monkeypatch.setattr(
        setup_wizard, "load_host_environment", lambda _path: environment
    )
    monkeypatch.setattr(
        setup_wizard.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("closed")),
    )
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        setup_wizard, "_executable", lambda _name: tmp_path / "PartyOps.exe"
    )
    monkeypatch.setattr(
        setup_wizard, "_spawn", lambda command, *_args: spawned.append(command)
    )
    waits: list[dict[str, object]] = []
    monkeypatch.setattr(
        setup_wizard,
        "wait_for_host_health",
        lambda host, port, **kwargs: waits.append(kwargs) or f"http://{host}:{port}",
    )
    assert setup_wizard.launch_personal(path) == "http://127.0.0.1:18775"
    assert spawned == [[str(tmp_path / "PartyOps.exe")]]
    assert waits == [
        {
            "timeout": 180.0,
            "data_dir": data_dir,
            "service_managed": False,
            "process": None,
        }
    ]

    page = setup_wizard.render_page("csrf")
    assert "个人使用" in page and "无需管理员授权" in page
    assert 'data-role="personal"' in page and 'name="mode" value="personal"' in page


def test_data_root_marker_merges_legacy_roles_and_rejects_forgery(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "共享数据"
    data_dir.mkdir()
    setup_wizard._write_data_root_marker(data_dir, "host")
    setup_wizard._write_data_root_marker(data_dir, "client")
    marker_path = data_dir / ".partyops-data-root.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["scopes"] == ["client", "host"]

    marker_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "product": "PartyOps",
                "app_id": "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A",
                "scope": "personal",
            }
        ),
        encoding="utf-8",
    )
    setup_wizard._write_data_root_marker(data_dir, "client")
    assert json.loads(marker_path.read_text(encoding="utf-8"))["scopes"] == [
        "client",
        "personal",
    ]

    invalid_payloads = [
        "{损坏",
        json.dumps({"format_version": 2, "product": "External", "scopes": ["host"]}),
        json.dumps(
            {
                "format_version": 2,
                "product": "PartyOps",
                "app_id": "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A",
                "scopes": [],
            }
        ),
        json.dumps(
            {
                "format_version": 9,
                "product": "PartyOps",
                "app_id": "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A",
                "scopes": ["host"],
            }
        ),
        json.dumps(
            {
                "format_version": 2,
                "product": "PartyOps",
                "app_id": "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A",
                "scopes": ["external"],
            }
        ),
    ]
    for payload in invalid_payloads:
        marker_path.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match="标记损坏"):
            setup_wizard._write_data_root_marker(data_dir, "personal")
    with pytest.raises(ValueError, match="范围无效"):
        setup_wizard._write_data_root_marker(data_dir, "external")


def test_windows_service_start_type_snapshot_restore_and_verification(
    monkeypatch,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *_args, **_kwargs: Key())
    values = {"Start": (2, winreg.REG_DWORD), "DelayedAutoStart": (1, winreg.REG_DWORD)}

    def query_value(_key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name]

    monkeypatch.setattr(winreg, "QueryValueEx", query_value)
    assert setup_wizard._windows_service_start_config("PartyOpsHost") == (2, True)
    values.pop("DelayedAutoStart")
    assert setup_wizard._windows_service_start_config("PartyOpsHost") == (2, False)
    monkeypatch.setattr(
        winreg,
        "OpenKey",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert setup_wizard._windows_service_start_config("Missing") is None

    completed = lambda code=0: subprocess.CompletedProcess([], code, "", "")
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed())
    for config in ((2, True), (2, False), (3, False), (4, False)):
        monkeypatch.setattr(
            setup_wizard,
            "_windows_service_start_config",
            lambda _service, expected=config: expected,
        )
        setup_wizard._restore_windows_service_start_config("PartyOpsHost", config)
    with pytest.raises(ValueError, match="未知启动类型"):
        setup_wizard._restore_windows_service_start_config("PartyOpsHost", (5, False))
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed(5))
    with pytest.raises(ValueError, match="原启动类型"):
        setup_wizard._restore_windows_service_start_config("PartyOpsHost", (3, False))
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed())
    monkeypatch.setattr(
        setup_wizard, "_windows_service_start_config", lambda _service: (4, False)
    )
    with pytest.raises(ValueError, match="回读不一致"):
        setup_wizard._restore_windows_service_start_config("PartyOpsHost", (3, False))


def test_privileged_host_deactivation_is_transactional(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    monkeypatch.setattr(
        setup_wizard,
        "_windows_service_start_config",
        lambda service: (2, service == "PartyOpsHost"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_stop_windows_service_for_data_migration",
        lambda: {"PartyOpsHost": True, "PartyOpsUpdateService": False},
    )
    completed = lambda code=0: subprocess.CompletedProcess([], code, "", "")
    commands: list[list[str]] = []

    def success(command, **_kwargs):
        commands.append(command)
        return completed()

    monkeypatch.setattr(setup_wizard.subprocess, "run", success)
    setup_wizard._deactivate_windows_host_services_privileged()
    mode_path = tmp_path / "ProgramData" / "PartyOps" / "mode.json"
    assert json.loads(mode_path.read_text(encoding="utf-8"))["mode"] == "personal"
    assert any(command[0] == "netsh.exe" for command in commands)

    mode_path.write_text('{"format_version":1,"mode":"host"}', encoding="utf-8")
    restored_configs: list[tuple[str, tuple[int, bool] | None]] = []
    restored_states: list[dict[str, bool]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_service_start_config",
        lambda service, config: restored_configs.append((service, config)),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_services_after_data_migration",
        lambda states: restored_states.append(states),
    )
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed(5))
    with pytest.raises(ValueError, match="无法停用"):
        setup_wizard._deactivate_windows_host_services_privileged()
    assert json.loads(mode_path.read_text(encoding="utf-8"))["mode"] == "host"
    assert len(restored_configs) == 2 and restored_states

    mode_path.unlink()
    calls = 0

    def firewall_failure(command, **_kwargs):
        nonlocal calls
        calls += 1
        return completed(5 if command[0] == "netsh.exe" else 0)

    monkeypatch.setattr(setup_wizard.subprocess, "run", firewall_failure)
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_service_start_config",
        lambda *_args: (_ for _ in ()).throw(ValueError("restore denied")),
    )
    setup_wizard._deactivate_windows_host_services_privileged()
    assert json.loads(mode_path.read_text(encoding="utf-8"))["mode"] == "personal"
    assert calls == 5
    warning = mode_path.parent / "mode-switch-warning.log"
    assert "FIREWALL_RULE_CLEANUP_DEFERRED" in warning.read_text(encoding="utf-8")

    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: False)
    with pytest.raises(ValueError, match="管理员权限"):
        setup_wizard._deactivate_windows_host_services_privileged()


def test_host_switch_snapshot_restores_exact_services_and_mode(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    mode_path = program_data / "PartyOps" / "mode.json"
    mode_path.parent.mkdir(parents=True)
    original_mode = '{"format_version":1,"mode":"host"}'
    mode_path.write_text(original_mode, encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    configs = {
        "PartyOpsHost": (2, True),
        "PartyOpsUpdateService": (3, False),
    }
    monkeypatch.setattr(
        setup_wizard, "_windows_service_start_config", lambda service: configs[service]
    )
    running = {"PartyOpsHost": True, "PartyOpsUpdateService": False}
    monkeypatch.setattr(
        setup_wizard, "_windows_service_running", lambda service: running[service]
    )
    monkeypatch.setattr(
        setup_wizard, "_stop_windows_service_for_data_migration", lambda: running
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""),
    )
    setup_wizard._deactivate_windows_host_services_privileged()
    snapshot = setup_wizard._windows_host_switch_snapshot_path()
    assert snapshot.is_file()
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["services"]["PartyOpsHost"] == {
        "start_type": 2,
        "delayed": True,
        "running": True,
    }

    restored_configs: list[tuple[str, tuple[int, bool] | None]] = []
    restored_running: list[dict[str, bool]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_service_start_config",
        lambda service, config: restored_configs.append((service, config)),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_services_after_mode_switch",
        lambda states: restored_running.append(states),
    )
    setup_wizard._restore_windows_host_switch_privileged()
    assert mode_path.read_text(encoding="utf-8") == original_mode
    assert restored_configs == list(configs.items())
    assert restored_running == [running]
    assert not snapshot.exists()


def test_host_switch_rollback_keeps_snapshot_when_service_cannot_restart(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    mode_path = program_data / "PartyOps" / "mode.json"
    mode_path.parent.mkdir(parents=True)
    mode_path.write_text('{"mode":"personal"}', encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    snapshot = setup_wizard._windows_host_switch_snapshot_path()
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps(
            {
                "format_version": 1,
                "transaction_id": "tx_restore_0123456789abcdef01234567",
                "previous_mode": json.dumps({"mode": "host"}),
                "services": {
                    "PartyOpsHost": {
                        "start_type": 2,
                        "delayed": False,
                        "running": True,
                    },
                    "PartyOpsUpdateService": {
                        "start_type": 3,
                        "delayed": False,
                        "running": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_wizard, "_restore_windows_service_start_config", lambda *_args: None
    )
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_services_after_mode_switch",
        lambda *_args: (_ for _ in ()).throw(
            ValueError("[MODE_SWITCH_ROLLBACK_FAILED] 服务仍为停止状态")
        ),
    )
    with pytest.raises(ValueError, match="MODE_SWITCH_ROLLBACK_FAILED"):
        setup_wizard._restore_windows_host_switch_privileged(
            "tx_restore_0123456789abcdef01234567"
        )
    assert snapshot.is_file()
    assert "host" in mode_path.read_text(encoding="utf-8")


def test_strict_mode_switch_service_restore_checks_running_state(monkeypatch) -> None:
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "_windows_service_running", lambda _name: False)
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 5, "", "策略拒绝"
        ),
    )
    times = iter([0.0, 10.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="MODE_SWITCH_ROLLBACK_FAILED"):
        setup_wizard._restore_windows_services_after_mode_switch(
            {"PartyOpsHost": True, "PartyOpsUpdateService": False}, timeout=5
        )


def test_strict_mode_switch_service_restore_error_matrix(monkeypatch) -> None:
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    setup_wizard._restore_windows_services_after_mode_switch(
        {"PartyOpsHost": True}
    )

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    calls: list[list[str]] = []

    def should_not_start(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_wizard.subprocess, "run", should_not_start)
    setup_wizard._restore_windows_services_after_mode_switch({})
    assert calls == []

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("SCM 不可用")),
    )
    with pytest.raises(ValueError, match="SCM 不可用"):
        setup_wizard._restore_windows_services_after_mode_switch(
            {"PartyOpsHost": True}
        )

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(setup_wizard, "_windows_service_running", lambda _name: True)
    setup_wizard._restore_windows_services_after_mode_switch({"PartyOpsHost": True})

    monkeypatch.setattr(
        setup_wizard,
        "_windows_service_running",
        lambda _name: (_ for _ in ()).throw(OSError("无法回读状态")),
    )
    with pytest.raises(ValueError, match="无法回读状态"):
        setup_wizard._restore_windows_services_after_mode_switch(
            {"PartyOpsHost": True}
        )

    states = iter([False, True])
    monkeypatch.setattr(
        setup_wizard, "_windows_service_running", lambda _name: next(states)
    )
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    setup_wizard._restore_windows_services_after_mode_switch(
        {"PartyOpsHost": True}, timeout=5
    )


def test_host_switch_rollback_wraps_unexpected_restore_error(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    snapshot = setup_wizard._windows_host_switch_snapshot_path()
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    transaction_id = "tx_unexpected_0123456789abcdef0123"
    snapshot.write_text(
        json.dumps(
            {
                "format_version": 1,
                "transaction_id": transaction_id,
                "previous_mode": None,
                "services": {
                    "PartyOpsHost": {
                        "start_type": 2,
                        "delayed": False,
                        "running": True,
                    },
                    "PartyOpsUpdateService": {
                        "start_type": 3,
                        "delayed": False,
                        "running": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_service_start_config",
        lambda *_args: (_ for _ in ()).throw(OSError("注册表拒绝")),
    )
    with pytest.raises(ValueError, match="MODE_SWITCH_ROLLBACK_FAILED.*注册表拒绝"):
        setup_wizard._restore_windows_host_switch_privileged(transaction_id)
    assert snapshot.is_file()


def test_bounded_log_and_desktop_marker_branch_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "launcher.log"
    setup_wizard._rotate_bounded_log(log_path, max_bytes=4, backups=4)
    log_path.write_bytes(b"abc")
    setup_wizard._rotate_bounded_log(log_path, max_bytes=4, backups=4)
    assert log_path.read_bytes() == b"abc"

    log_path.write_bytes(b"12345")
    log_path.with_name("launcher.log.1").write_bytes(b"one")
    log_path.with_name("launcher.log.3").write_bytes(b"three")
    log_path.with_name("launcher.log.4").write_bytes(b"oldest")
    setup_wizard._rotate_bounded_log(log_path, max_bytes=4, backups=4)
    assert log_path.with_name("launcher.log.1").read_bytes() == b"12345"
    assert log_path.with_name("launcher.log.2").read_bytes() == b"one"
    assert log_path.with_name("launcher.log.4").read_bytes() == b"three"

    with monkeypatch.context() as context:
        context.setattr(
            Path, "is_file", lambda _self: (_ for _ in ()).throw(OSError("ACL"))
        )
        setup_wizard._rotate_bounded_log(log_path, max_bytes=4, backups=4)

    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    assert setup_wizard._publish_desktop_tool_url("wizard", "http://127.0.0.1:9") is None
    setup_wizard._clear_desktop_tool_url(None, "http://127.0.0.1:9")

    marker = tmp_path / "wizard.url"
    marker.write_text("http://127.0.0.1:10\n", encoding="utf-8")
    setup_wizard._clear_desktop_tool_url(marker, "http://127.0.0.1:9")
    assert marker.is_file()
    setup_wizard._clear_desktop_tool_url(marker, "http://127.0.0.1:10")
    assert not marker.exists()
    setup_wizard._clear_desktop_tool_url(marker, "http://127.0.0.1:10")


def test_windows_acl_precheck_cross_platform_and_precreation_branches(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    setup_wizard.assert_windows_service_data_path_security(
        tmp_path, verify_target=True
    )
    assert setup_wizard._windows_service_running("PartyOpsHost") is False

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setitem(sys.modules, "win32security", SimpleNamespace())
    monkeypatch.setattr(
        setup_wizard,
        "_assert_path_components_have_no_reparse_points",
        lambda _path: None,
    )
    setup_wizard.assert_windows_service_data_path_security(
        tmp_path, verify_target=False
    )


def test_user_mode_failure_invokes_privileged_host_restore(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    data = tmp_path / "personal"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    calls: list[str] = []
    monkeypatch.setattr(
        setup_wizard,
        "_validate_personal_data_dir",
        lambda path: calls.append("validate") or path.resolve(),
    )
    monkeypatch.setattr(
        setup_wizard,
        "deactivate_windows_host_for_user_mode",
        lambda: calls.append("deactivate") or True,
    )
    monkeypatch.setattr(
        setup_wizard,
        "restore_windows_host_after_failed_switch",
        lambda deactivated: calls.append(f"restore:{deactivated}"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_write_data_root_marker",
        lambda *_a: (_ for _ in ()).throw(ValueError("marker denied")),
    )
    with pytest.raises(ValueError, match="marker denied"):
        setup_wizard.write_personal_config(data)
    assert calls[:2] == ["validate", "deactivate"]
    assert calls[-1] == "restore:True"


def test_host_config_write_failure_restores_control_files(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    host_config = config / "partyops.env"
    mode_config = config / "mode.json"
    marker = data / ".partyops-data-root.json"
    old_host = "PARTYOPS_MODE=host\nPARTYOPS_DATA_DIR=old\n"
    old_mode = '{"format_version":1,"mode":"host"}'
    old_marker = '{"format_version":2,"product":"PartyOps","app_id":"1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A","scopes":["host"]}'
    host_config.write_text(old_host, encoding="utf-8")
    mode_config.write_text(old_mode, encoding="utf-8")
    marker.write_text(old_marker, encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: [])
    monkeypatch.setattr(
        setup_wizard,
        "_write_data_root_marker",
        lambda *_args: (_ for _ in ()).throw(ValueError("marker denied")),
    )
    with pytest.raises(ValueError, match="marker denied"):
        setup_wizard.write_host_config("127.0.0.1", 18765, data)
    assert host_config.read_text(encoding="utf-8") == old_host
    assert mode_config.read_text(encoding="utf-8") == old_mode
    assert marker.read_text(encoding="utf-8") == old_marker


def test_data_migration_preserves_valid_target_control_marker(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with sqlite3.connect(source / "partyops.db") as database:
        database.execute("CREATE TABLE fixture(value TEXT)")
        database.execute("INSERT INTO fixture VALUES ('kept')")
    marker = target / ".partyops-data-root.json"
    marker_content = '{"format_version":2,"product":"PartyOps","app_id":"1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A","scopes":["personal"]}'
    marker.write_text(marker_content, encoding="utf-8")
    setup_wizard.migrate_windows_data_dir(source, target)
    assert marker.read_text(encoding="utf-8") == marker_content
    with sqlite3.connect(target / "partyops.db") as database:
        assert database.execute("SELECT value FROM fixture").fetchone() == ("kept",)


def test_personal_data_directory_migrates_consistently_and_preserves_source(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    old_data = tmp_path / "旧个人数据"
    new_data = tmp_path / "新个人数据"
    old_data.mkdir()
    database = sqlite3.connect(old_data / "partyops.db")
    database.execute("CREATE TABLE fixture(value TEXT)")
    database.execute("INSERT INTO fixture VALUES ('保留')")
    database.commit()
    database.close()
    (old_data / "attachments").mkdir()
    (old_data / "attachments" / "材料.txt").write_text("材料", encoding="utf-8")
    personal_env = config / "personal.env"
    personal_env.write_text(
        "PARTYOPS_MODE=personal\n"
        "PARTYOPS_PORT=18775\n"
        f"PARTYOPS_DATA_DIR={shlex.quote(str(old_data))}\n"
        "PARTYOPS_BOOTSTRAP_TOKEN=existing-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(
        setup_wizard, "_validate_personal_data_dir", lambda path: path.resolve()
    )
    stopped: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_stop_personal_process_for_data_migration",
        lambda path, port: stopped.append((path, port)) or True,
    )
    monkeypatch.setattr(setup_wizard, "clear_windows_client_autostart", lambda: None)
    monkeypatch.setattr(
        setup_wizard, "install_windows_personal_autostart", lambda: None
    )

    setup_wizard.write_personal_config(new_data, 18775)

    assert stopped == [(old_data.resolve(), 18775)]
    assert old_data.is_dir() and (old_data / "partyops.db").is_file()
    assert (new_data / "attachments" / "材料.txt").read_text(encoding="utf-8") == "材料"
    migrated = sqlite3.connect(new_data / "partyops.db")
    try:
        assert migrated.execute("SELECT value FROM fixture").fetchone() == ("保留",)
    finally:
        migrated.close()
    assert setup_wizard.load_host_environment(personal_env)["PARTYOPS_DATA_DIR"] == str(
        new_data.resolve()
    )


def test_personal_process_marker_refuses_unknown_listener_and_cleans_stale_marker(
    monkeypatch, tmp_path: Path
) -> None:
    """迁移只终止可证明归属的进程；未知监听者必须阻断而不是猜测。"""

    data_dir = tmp_path / "个人数据"
    data_dir.mkdir()
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)

    monkeypatch.setattr(
        setup_wizard.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )
    assert not setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)

    class Listener:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        setup_wizard.socket, "create_connection", lambda *_args, **_kwargs: Listener()
    )
    with pytest.raises(ValueError, match="缺少受控进程标记"):
        setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)

    marker = setup_wizard._personal_process_marker(data_dir)
    marker.write_text("{损坏", encoding="utf-8")
    with pytest.raises(ValueError, match="进程标记损坏"):
        setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)

    marker.write_text(
        json.dumps({"pid": 42, "executable": str(tmp_path / "external.exe")}),
        encoding="utf-8",
    )
    assert not setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)
    assert not marker.exists()


def test_personal_process_stop_is_pid_safe_and_bounded(
    monkeypatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "个人数据"
    data_dir.mkdir()
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"MZ")
    marker = setup_wizard._personal_process_marker(data_dir)
    payload = {"format_version": 1, "pid": 42, "executable": str(executable.resolve())}
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)

    marker.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard, "_process_executable_matches", lambda *_args: True
    )
    monkeypatch.setattr(
        setup_wizard.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert not setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)
    assert not marker.exists()

    marker.write_text(json.dumps(payload), encoding="utf-8")
    matches = iter((True, False, False, False))
    monkeypatch.setattr(
        setup_wizard, "_process_executable_matches", lambda *_args: next(matches)
    )
    signals: list[int] = []
    monkeypatch.setattr(setup_wizard.os, "kill", lambda _pid, sig: signals.append(sig))
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    assert setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)
    assert signals == [setup_wizard.signal.SIGTERM]
    assert not marker.exists()

    marker.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard, "_process_executable_matches", lambda *_args: True
    )
    ticks = iter((0.0, 21.0))
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(ticks))
    signals.clear()
    with pytest.raises(ValueError, match="未能安全停止"):
        setup_wizard._stop_personal_process_for_data_migration(data_dir, 18775)
    assert len(signals) == 2
    assert marker.exists()


def test_personal_process_record_and_executable_guards(
    monkeypatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "个人数据"
    data_dir.mkdir()
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)

    setup_wizard._record_personal_process(data_dir, None)
    setup_wizard._record_personal_process(data_dir, SimpleNamespace(pid=0))
    assert not setup_wizard._personal_process_marker(data_dir).exists()

    setup_wizard._record_personal_process(data_dir, SimpleNamespace(pid=31415))
    marker = json.loads(
        setup_wizard._personal_process_marker(data_dir).read_text(encoding="utf-8")
    )
    assert marker["pid"] == 31415
    assert marker["executable"] == str(executable.resolve())

    assert not setup_wizard._process_executable_matches(0, executable)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    assert not setup_wizard._process_executable_matches(999_999_999, executable)


def test_default_paths_marker_link_and_managed_tree_guards(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert (
        setup_wizard.personal_default_data_dir()
        == tmp_path / "Local" / "PartyOps-个人数据"
    )
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert (
        setup_wizard.personal_default_data_dir()
        == tmp_path / "xdg" / "partyops-personal"
    )

    missing = tmp_path / "missing"
    setup_wizard._assert_managed_data_tree_has_no_reparse_points(missing)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    marker = data_dir / ".partyops-data-root.json"
    target = tmp_path / "external-marker.json"
    target.write_text("{}", encoding="utf-8")
    marker.symlink_to(target)
    with pytest.raises(ValueError, match="标记是链接"):
        setup_wizard._write_data_root_marker(data_dir, "host")
    marker.unlink()

    nested = data_dir / "attachments"
    nested.mkdir()
    link = nested / "outside"
    outside = tmp_path / "outside"
    outside.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接或目录联接"):
        setup_wizard._assert_managed_data_tree_has_no_reparse_points(data_dir)


def test_windows_service_state_probe_stop_restore_and_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")

    completed = lambda code=0, stdout="", stderr="": subprocess.CompletedProcess(
        [], code, stdout, stderr
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: completed(0, "STATE              : 4  RUNNING"),
    )
    assert setup_wizard._windows_service_running("PartyOpsHost")
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: completed(0, "STATE              : 1  STOPPED"),
    )
    assert not setup_wizard._windows_service_running("PartyOpsHost")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    assert not setup_wizard._windows_service_running("PartyOpsHost")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")

    monkeypatch.setattr(
        setup_wizard,
        "_windows_service_running",
        lambda service: service == "PartyOpsUpdateService",
    )
    commands: list[list[str]] = []

    def stop_then_query(command, **_kwargs):
        commands.append(command)
        if command[1] == "query" and command[2] == "PartyOpsUpdateService":
            return completed(1060, stderr="does not exist")
        if command[1] == "query":
            return completed(0, "STATE : 1 STOPPED")
        return completed()

    monkeypatch.setattr(setup_wizard.subprocess, "run", stop_then_query)
    states = setup_wizard._stop_windows_service_for_data_migration(timeout=1)
    assert states == {"PartyOpsUpdateService": True, "PartyOpsHost": False}
    started: list[str] = []
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: started.append(command[-1]) or completed(),
    )
    setup_wizard._restore_windows_services_after_data_migration(states)
    assert started == ["PartyOpsUpdateService"]

    restored: list[dict[str, bool]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_restore_windows_services_after_data_migration",
        lambda value: restored.append(value),
    )
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed())
    with pytest.raises(ValueError, match="无法停止"):
        setup_wizard._stop_windows_service_for_data_migration(timeout=0)
    assert restored


def test_windows_host_role_detection_and_uac_result_mapping(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    root = program_data / "PartyOps"
    root.mkdir(parents=True)
    mode_path = root / "mode.json"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))

    assert not setup_wizard._windows_system_host_role_active()
    (root / "partyops.env").write_text("PARTYOPS_MODE=host", encoding="utf-8")
    assert setup_wizard._windows_system_host_role_active()
    mode_path.write_text('{"mode":"personal"}', encoding="utf-8")
    assert not setup_wizard._windows_system_host_role_active()
    mode_path.write_text('{"mode":"host"}', encoding="utf-8")
    assert setup_wizard._windows_system_host_role_active()
    mode_path.write_text("{损坏", encoding="utf-8")
    assert setup_wizard._windows_system_host_role_active()

    calls: list[str] = []
    monkeypatch.setattr(setup_wizard, "_windows_system_host_role_active", lambda: False)
    monkeypatch.setattr(
        setup_wizard,
        "_deactivate_windows_host_services_privileged",
        lambda *_args: calls.append("deactivate"),
    )
    setup_wizard.deactivate_windows_host_for_user_mode()
    assert calls == []

    monkeypatch.setattr(setup_wizard, "_windows_system_host_role_active", lambda: True)
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    setup_wizard.deactivate_windows_host_for_user_mode()
    assert calls == ["deactivate"]

    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: False)
    monkeypatch.setattr(
        setup_wizard, "_executable", lambda _name: tmp_path / "wizard.exe"
    )
    completed = lambda code, stderr="": subprocess.CompletedProcess(
        [], code, "", stderr
    )
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed(0))
    setup_wizard.deactivate_windows_host_for_user_mode()
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: completed(786, "管理员用策略规则限制了访问"),
    )
    with pytest.raises(setup_wizard.HostStartupError) as policy:
        setup_wizard.deactivate_windows_host_for_user_mode()
    assert policy.value.code == setup_wizard.ADMIN_POLICY_BLOCKED
    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed(5))
    with pytest.raises(ValueError, match="管理员确认"):
        setup_wizard.deactivate_windows_host_for_user_mode()


def test_sqlite_copy_and_data_migration_early_failures(
    monkeypatch, tmp_path: Path
) -> None:
    setup_wizard._verify_sqlite_copy(tmp_path / "missing.db")

    class BadConnection:
        def execute(self, _sql):
            return SimpleNamespace(fetchone=lambda: ("corrupt",))

        def close(self):
            return None

    monkeypatch.setattr(
        setup_wizard.sqlite3, "connect", lambda *_a, **_k: BadConnection()
    )
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="完整性检查"):
        setup_wizard._verify_sqlite_copy(bad)

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    setup_wizard.migrate_windows_data_dir(source, source)
    setup_wizard.migrate_windows_data_dir(tmp_path / "absent", target)
    nested = source / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="不能互相嵌套"):
        setup_wizard.migrate_windows_data_dir(source, nested)
    (target / "foreign.txt").write_text("external", encoding="utf-8")
    with pytest.raises(ValueError, match="不是空目录"):
        setup_wizard.migrate_windows_data_dir(source, target)
    (target / "foreign.txt").unlink()
    setup_wizard.migrate_windows_data_dir(source, target)


def test_personal_config_migration_and_write_failures_restore_previous_state(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    old_data = tmp_path / "old"
    new_data = tmp_path / "new"
    old_data.mkdir()
    previous = (
        "PARTYOPS_MODE=personal\nPARTYOPS_PORT=18775\n"
        f"PARTYOPS_DATA_DIR={shlex.quote(str(old_data))}\n"
        "PARTYOPS_BOOTSTRAP_TOKEN=preserved\n"
    )
    personal_env = config / "personal.env"
    personal_env.write_text(previous, encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(
        setup_wizard, "_validate_personal_data_dir", lambda path: path.resolve()
    )
    monkeypatch.setattr(
        setup_wizard, "deactivate_windows_host_for_user_mode", lambda: None
    )
    monkeypatch.setattr(
        setup_wizard, "_stop_personal_process_for_data_migration", lambda *_a: True
    )
    restarted: list[dict[str, str]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_restart_previous_personal_process",
        lambda env: restarted.append(env),
    )
    monkeypatch.setattr(
        setup_wizard,
        "migrate_windows_data_dir",
        lambda *_a: (_ for _ in ()).throw(ValueError("copy failed")),
    )
    with pytest.raises(ValueError, match="copy failed"):
        setup_wizard.write_personal_config(new_data)
    assert restarted and personal_env.read_text(encoding="utf-8") == previous

    restarted.clear()
    monkeypatch.setattr(setup_wizard, "migrate_windows_data_dir", lambda *_a: None)
    monkeypatch.setattr(
        setup_wizard,
        "_write_data_root_marker",
        lambda *_a: (_ for _ in ()).throw(ValueError("marker denied")),
    )
    with pytest.raises(ValueError, match="marker denied"):
        setup_wizard.write_personal_config(new_data)
    assert restarted and personal_env.read_text(encoding="utf-8") == previous

    personal_env.unlink()
    restarted.clear()
    with pytest.raises(ValueError, match="marker denied"):
        setup_wizard.write_personal_config(new_data)
    assert not personal_env.exists() and restarted == []


def test_windows_admin_probe_and_owned_user_autostarts(
    monkeypatch, tmp_path: Path
) -> None:
    import ctypes

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    assert not setup_wizard.windows_is_admin()
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1)),
    )
    assert setup_wizard.windows_is_admin()
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(shell32=SimpleNamespace()),
    )
    assert not setup_wizard.windows_is_admin()

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    deleted: list[str] = []
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(winreg, "OpenKey", lambda *_a, **_k: Key())
    monkeypatch.setattr(winreg, "CreateKeyEx", lambda *_a, **_k: Key())
    monkeypatch.setattr(winreg, "DeleteValue", lambda _key, name: deleted.append(name))
    monkeypatch.setattr(
        winreg,
        "SetValueEx",
        lambda _key, name, _reserved, _kind, value: written.append((name, value)),
    )
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    launcher = tmp_path / "PartyOpsLauncher.exe"
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: launcher)
    setup_wizard.clear_windows_client_autostart()
    setup_wizard.clear_windows_personal_autostart()
    setup_wizard.install_windows_personal_autostart()
    assert deleted == ["PartyOpsAgent", "PartyOpsPersonal"]
    assert written == [("PartyOpsPersonal", f'"{launcher}" --background')]

    monkeypatch.setattr(
        winreg,
        "OpenKey",
        lambda *_a, **_k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    setup_wizard.clear_windows_client_autostart()
    setup_wizard.clear_windows_personal_autostart()


def test_windows_data_directory_permission_space_and_system_guards(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("USERPROFILE", r"D:\Profiles\Fixture")
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files x86"))
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=4 * 1024**3, used=0, free=4 * 1024**3),
    )

    class BlankPath:
        def __str__(self) -> str:
            return ""

    default = setup_wizard._validate_windows_data_dir(BlankPath())  # type: ignore[arg-type]
    assert default == (tmp_path / "ProgramData" / "PartyOps-Data").resolve()
    with pytest.raises(ValueError, match="系统共享目录"):
        setup_wizard._validate_windows_data_dir(tmp_path / "ProgramData")
    with pytest.raises(ValueError, match="系统目录"):
        setup_wizard._validate_windows_data_dir(tmp_path / "Windows" / "PartyOps")

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接或目录联接"):
        setup_wizard._validate_windows_data_dir(linked_parent / "PartyOps")

    low_space = tmp_path / "Low Space"
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1024, used=1024, free=0),
    )
    with pytest.raises(ValueError, match="空间不足"):
        setup_wizard._validate_windows_data_dir(low_space)

    denied = tmp_path / "Denied"
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=4 * 1024**3, used=0, free=4 * 1024**3),
    )
    original_write = Path.write_bytes

    def reject_probe(path: Path, data: bytes) -> int:
        if path.name.startswith(".partyops-write-test-"):
            raise PermissionError("denied")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", reject_probe)
    with pytest.raises(ValueError, match="不可写"):
        setup_wizard._validate_windows_data_dir(denied)


def test_personal_data_directory_supports_custom_paths_and_rejects_unsafe_media(
    monkeypatch, tmp_path: Path
) -> None:
    import ctypes

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files x86"))
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=4 * 1024**3, used=0, free=4 * 1024**3),
    )

    class BlankPath:
        def __str__(self) -> str:
            return ""

    default = tmp_path / "个人默认 数据"
    monkeypatch.setattr(setup_wizard, "personal_default_data_dir", lambda: default)
    assert setup_wizard._validate_personal_data_dir(BlankPath()) == default.resolve()  # type: ignore[arg-type]
    assert setup_wizard._validate_personal_data_dir(tmp_path / "中文 空格").is_dir()

    with pytest.raises(ValueError, match="网络共享"):
        setup_wizard._validate_personal_data_dir(Path(r"\\server\share\PartyOps"))
    with pytest.raises(ValueError, match="磁盘根目录"):
        setup_wizard._validate_personal_data_dir(Path(tmp_path.anchor))
    with pytest.raises(ValueError, match="系统或程序目录"):
        setup_wizard._validate_personal_data_dir(tmp_path / "Windows" / "PartyOps")

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(kernel32=SimpleNamespace(GetDriveTypeW=lambda _root: 2)),
    )
    with pytest.raises(ValueError, match="固定磁盘"):
        setup_wizard._validate_personal_data_dir(tmp_path / "removable")

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1024, used=1024, free=0),
    )
    with pytest.raises(ValueError, match="空间不足"):
        setup_wizard._validate_personal_data_dir(tmp_path / "low-space")

    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=4 * 1024**3, used=0, free=4 * 1024**3),
    )
    original_write = Path.write_bytes

    def reject_probe(path: Path, data: bytes) -> int:
        if path.name.startswith(".partyops-write-test-"):
            raise PermissionError("denied")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", reject_probe)
    with pytest.raises(ValueError, match="不可写"):
        setup_wizard._validate_personal_data_dir(tmp_path / "denied")


def test_windows_service_acl_stop_and_autostart_errors(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    completed = lambda code=0, out="", err="": subprocess.CompletedProcess(
        [], code, out, err
    )

    monkeypatch.setattr(setup_wizard.subprocess, "run", lambda *_a, **_k: completed(1))
    with pytest.raises(ValueError, match="保护所选主机数据目录"):
        setup_wizard._grant_windows_service_access(tmp_path)
    with pytest.raises(setup_wizard.HostStartupError) as caught:
        setup_wizard._enable_windows_host_service_autostart()
    assert caught.value.code == SERVICE_MISSING

    monkeypatch.setattr(
        setup_wizard, "_windows_service_running", lambda _service: False
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: (
            completed(1060, "服务未安装") if command[1] == "query" else completed()
        ),
    )
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    setup_wizard._stop_windows_service_for_data_migration(timeout=5)

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: (
            completed(0, "STATE : 1  STOPPED") if command[1] == "query" else completed()
        ),
    )
    setup_wizard._stop_windows_service_for_data_migration(timeout=5)

    times = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        setup_wizard.subprocess, "run", lambda *_a, **_k: completed(0, "START_PENDING")
    )
    with pytest.raises(ValueError, match="无法停止"):
        setup_wizard._stop_windows_service_for_data_migration(timeout=5)


def test_windows_data_and_control_acls_are_separated(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    completed = lambda: subprocess.CompletedProcess([], 0, "", "")
    commands: list[list[str]] = []
    security_checks: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        setup_wizard,
        "assert_windows_service_data_path_security",
        lambda path, *, verify_target: security_checks.append((path, verify_target)),
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or completed(),
    )
    data_dir = tmp_path / "业务 数据"
    data_dir.mkdir()
    (data_dir / "partyops.db").write_bytes(b"sqlite-payload")
    setup_wizard._grant_windows_service_access(data_dir)
    assert any("/setowner" in command for command in commands)
    data_acl = next(command for command in commands if "/inheritance:r" in command)
    assert "*S-1-5-18:(OI)(CI)F" in data_acl
    assert "*S-1-5-32-544:(OI)(CI)F" in data_acl
    assert "/T" not in data_acl
    reset_acl = next(command for command in commands if "/reset" in command)
    assert str(data_dir / "*") in reset_acl
    assert "/T" in reset_acl and "/L" in reset_acl
    assert all("*S-1-5-32-545" not in command for command in commands)
    assert security_checks[-1] == (data_dir, True)

    commands.clear()
    control = tmp_path / "control"
    control.mkdir()
    config = control / "partyops.env"
    config.write_text("PARTYOPS_MODE=host\n", encoding="utf-8")
    (control / "mode.json").write_text("{}", encoding="utf-8")
    setup_wizard._protect_windows_control_config(config)
    assert any("*S-1-5-32-545:(RX)" in command for command in commands)
    assert sum("*S-1-5-32-545:R" in command for command in commands) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows 数据目录 ACL 回归")
def test_windows_data_acl_upgrade_accepts_writable_parent_and_keeps_files_readable(
    monkeypatch, tmp_path: Path
) -> None:
    """旧版目录可继承宽松父 ACL，但收敛后文件必须可读且目标不可被普通用户写。"""

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    parent = tmp_path / "用户可写 父目录"
    data_dir = parent / "党建智办 数据"
    nested = data_dir / "attachments"
    nested.mkdir(parents=True)
    database = data_dir / "partyops.db"
    attachment = nested / "中文 文件.txt"
    database.write_bytes(b"sqlite-regression")
    attachment.write_text("PartyOps", encoding="utf-8")
    parent_acl = subprocess.run(
        [
            "icacls.exe",
            str(parent),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-11:(OI)(CI)M",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert parent_acl.returncode == 0, parent_acl.stderr
    inherited = subprocess.run(
        ["icacls.exe", str(data_dir / "*"), "/reset", "/T", "/L", "/Q"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inherited.returncode == 0, inherited.stderr

    setup_wizard.normalize_windows_service_data_path_security(data_dir)
    setup_wizard.assert_windows_service_data_path_security(
        data_dir,
        verify_target=True,
    )
    assert database.read_bytes() == b"sqlite-regression"
    assert attachment.read_text(encoding="utf-8") == "PartyOps"


def test_windows_service_data_acl_rejects_untrusted_writer(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    target = tmp_path / "secure-data"
    target.mkdir()

    class Dacl:
        def __init__(self, aces):
            self.aces = aces

        def GetAceCount(self):
            return len(self.aces)

        def GetAce(self, index):
            return self.aces[index]

    class Descriptor:
        def __init__(self, aces):
            self.aces = aces

        def GetSecurityDescriptorOwner(self):
            return "S-1-5-32-544"

        def GetSecurityDescriptorDacl(self):
            return Dacl(self.aces)

    fake_win32 = SimpleNamespace(
        OWNER_SECURITY_INFORMATION=1,
        DACL_SECURITY_INFORMATION=4,
        ACCESS_ALLOWED_ACE_TYPE=0,
        ACCESS_ALLOWED_OBJECT_ACE_TYPE=5,
        ConvertSidToStringSid=lambda sid: sid,
    )
    aces = [((0, 0), 0x40000000, "S-1-5-11")]
    fake_win32.GetFileSecurity = lambda *_args: Descriptor(aces)
    monkeypatch.setitem(sys.modules, "win32security", fake_win32)
    with pytest.raises(PermissionError, match="非受信主体"):
        setup_wizard.assert_windows_service_data_path_security(
            target,
            verify_target=True,
        )

    # 仅继承到子项、并不作用于当前目录的 ACE 不构成当前路径替换权限。
    aces[:] = [((0, 0x08), 0x10000000, "S-1-5-11")]
    setup_wizard.assert_windows_service_data_path_security(
        target,
        verify_target=True,
    )


def test_sqlite_and_data_migration_guard_matrix(monkeypatch, tmp_path: Path) -> None:
    setup_wizard._verify_sqlite_copy(tmp_path / "missing.db")

    class BadConnection:
        def execute(self, _sql: str):
            return SimpleNamespace(fetchone=lambda: ("corrupt",))

        def close(self) -> None:
            return None

    database = tmp_path / "bad.db"
    database.write_bytes(b"not-empty")
    monkeypatch.setattr(
        setup_wizard.sqlite3, "connect", lambda *_a, **_k: BadConnection()
    )
    with pytest.raises(ValueError, match="完整性"):
        setup_wizard._verify_sqlite_copy(database)

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    setup_wizard.migrate_windows_data_dir(source, source)
    setup_wizard.migrate_windows_data_dir(tmp_path / "absent", target)
    with pytest.raises(ValueError, match="不能互相嵌套"):
        setup_wizard.migrate_windows_data_dir(source, source / "nested")

    (target / "foreign.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="不是空目录"):
        setup_wizard.migrate_windows_data_dir(source, target)
    (target / "foreign.txt").unlink()
    setup_wizard.migrate_windows_data_dir(source, target)

    (source / "launcher.log").write_text("log", encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard.shutil,
        "copy2",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("copy failed")),
    )
    with pytest.raises(OSError, match="copy failed"):
        setup_wizard.migrate_windows_data_dir(source, target)
    assert target.is_dir()
    assert not list(tmp_path.glob(".target.partyops-migrating-*"))


def test_host_config_system_mode_migrates_and_preserves_selected_path(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    config_path = program_data / "PartyOps" / "partyops.env"
    config_path.parent.mkdir(parents=True)
    old_data = tmp_path / "旧 数据"
    new_data = tmp_path / "新 数据"
    config_path.write_text(f"PARTYOPS_DATA_DIR='{old_data}'\n", encoding="utf-8")
    update_key = tmp_path / "runtime" / "update-public-key.txt"
    update_key.parent.mkdir()
    update_key.write_text("public-key", encoding="utf-8")
    calls: list[object] = []

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: ["192.168.1.8"])
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: update_key.parent)
    monkeypatch.setattr(setup_wizard, "config_root", lambda: tmp_path / "LocalConfig")
    monkeypatch.setattr(
        setup_wizard, "_validate_windows_data_dir", lambda path: path.resolve()
    )
    monkeypatch.setattr(
        setup_wizard,
        "_stop_windows_service_for_data_migration",
        lambda: calls.append("stop"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "migrate_windows_data_dir",
        lambda old, new: calls.append((old, new)),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_grant_windows_service_access",
        lambda path: calls.append(("acl", path)),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_protect_windows_control_config",
        lambda path: calls.append(("control-acl", path)),
    )

    written = setup_wizard.write_host_config("192.168.1.8", 18765, new_data)
    assert written == config_path
    assert calls[0] == "stop"
    assert (old_data.resolve(), new_data.resolve()) in calls
    content = written.read_text(encoding="utf-8")
    assert "PARTYOPS_BIND_HOST=0.0.0.0" in content
    assert "PARTYOPS_UPDATE_PUBLIC_KEY=public-key" in content
    assert (
        json.loads((config_path.parent / "mode.json").read_text(encoding="utf-8"))[
            "mode"
        ]
        == "host"
    )


def test_configure_host_direct_and_elevation_failure_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    program_data = tmp_path / "ProgramData"
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "config_root", lambda: tmp_path / "config")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: [])
    monkeypatch.setattr(setup_wizard, "_validate_windows_data_dir", lambda path: path)
    monkeypatch.setattr(
        setup_wizard, "_executable", lambda _name: tmp_path / "PartyOpsWizard.exe"
    )
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: False)

    powershell_calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_k: (
            powershell_calls.append(command)
            or subprocess.CompletedProcess([], 1, "", "")
        ),
    )
    with pytest.raises(ValueError, match="授权未完成"):
        setup_wizard.configure_host_config("127.0.0.1", 18765, tmp_path / "数据")
    elevation_script = powershell_calls[0][4]
    assert "$ErrorActionPreference='Stop'" in elevation_script
    assert "NativeErrorCode" in elevation_script
    assert "$null -eq $process" in elevation_script

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [],
            786,
            "",
            "管理员用策略规则限制了对 PartyOpsWizard.exe 的访问",
        ),
    )
    with pytest.raises(setup_wizard.HostStartupError) as policy_error:
        setup_wizard.configure_host_config("127.0.0.1", 18765, tmp_path / "数据")
    assert policy_error.value.code == "ADMIN_POLICY_BLOCKED"
    assert "个人使用" in str(policy_error.value)
    assert setup_wizard._windows_policy_blocked("[WinError 786] policy rule")
    assert not setup_wizard._windows_policy_blocked("用户取消了授权")

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""),
    )
    with pytest.raises(ValueError, match="配置文件未生成"):
        setup_wizard.configure_host_config("127.0.0.1", 18765, tmp_path / "数据")

    expected = program_data / "PartyOps" / "partyops.env"
    expected.parent.mkdir(parents=True)
    expected.write_text("PARTYOPS_MODE=host\n", encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "config_root", lambda: tmp_path / "Local")
    monkeypatch.setattr(setup_wizard, "clear_windows_client_autostart", lambda: None)
    assert (
        setup_wizard.configure_host_config("127.0.0.1", 18765, tmp_path / "数据")
        == expected
    )

    direct = tmp_path / "direct.env"
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    monkeypatch.setattr(setup_wizard, "write_host_config", lambda *_a, **_k: direct)
    monkeypatch.setattr(
        setup_wizard, "_enable_windows_host_service_autostart", lambda: None
    )
    assert (
        setup_wizard.configure_host_config("127.0.0.1", 18765, tmp_path / "数据")
        == direct
    )


def test_configure_host_failure_restores_personal_after_firewall_policy_denial(
    monkeypatch, tmp_path: Path
) -> None:
    """完整覆盖 personal→host→失败→个人恢复，防火墙拒绝不能制造二次回滚失败。"""

    config = tmp_path / "Local" / "PartyOps"
    config.mkdir(parents=True)
    personal_data = tmp_path / "个人数据"
    personal_data.mkdir()
    personal_config = config / "personal.env"
    personal_config.write_text(
        "PARTYOPS_MODE=personal\n"
        "PARTYOPS_PORT=18775\n"
        f"PARTYOPS_DATA_DIR={shlex.quote(str(personal_data))}\n",
        encoding="utf-8",
    )
    program_data = tmp_path / "ProgramData"
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    monkeypatch.setattr(setup_wizard, "_validate_windows_data_dir", lambda path: path)
    monkeypatch.setattr(
        setup_wizard, "_stop_personal_process_for_data_migration", lambda *_a: True
    )
    host_config = program_data / "PartyOps" / "partyops.env"

    def write_host(*_args):
        host_config.parent.mkdir(parents=True, exist_ok=True)
        host_config.write_text("PARTYOPS_MODE=host\n", encoding="utf-8")
        (host_config.parent / "mode.json").write_text(
            '{"format_version":1,"mode":"host"}', encoding="utf-8"
        )
        return host_config

    monkeypatch.setattr(setup_wizard, "write_host_config", write_host)
    monkeypatch.setattr(
        setup_wizard,
        "_enable_windows_host_service_autostart",
        lambda: (_ for _ in ()).throw(ValueError("更新服务启动类型设置失败")),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_windows_system_host_role_active",
        lambda: True,
    )
    monkeypatch.setattr(
        setup_wizard,
        "_windows_service_start_config",
        lambda service: (2, service == "PartyOpsHost"),
    )
    monkeypatch.setattr(
        setup_wizard, "_windows_service_running", lambda _service: False
    )
    monkeypatch.setattr(
        setup_wizard,
        "_stop_windows_service_for_data_migration",
        lambda: {"PartyOpsHost": False, "PartyOpsUpdateService": False},
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 5 if command[0] == "netsh.exe" else 0, "", "策略拒绝"
        ),
    )
    restarted: list[dict[str, str]] = []
    autostarts: list[str] = []
    user_modes: list[str] = []
    committed: list[str | bool | None] = []
    monkeypatch.setattr(
        setup_wizard,
        "_restart_previous_personal_process",
        lambda environment: restarted.append(environment),
    )
    monkeypatch.setattr(
        setup_wizard,
        "install_windows_personal_autostart",
        lambda: autostarts.append("personal"),
    )
    original_write_mode = setup_wizard.write_mode_config
    monkeypatch.setattr(
        setup_wizard,
        "write_mode_config",
        lambda mode, **kwargs: (
            user_modes.append(mode),
            original_write_mode(mode, **kwargs),
        )[1],
    )
    original_deactivate = setup_wizard._deactivate_windows_host_services_privileged

    def deactivate() -> str:
        transaction_id = "A" * 32
        original_deactivate(transaction_id)
        return transaction_id

    monkeypatch.setattr(setup_wizard, "deactivate_windows_host_for_user_mode", deactivate)
    monkeypatch.setattr(
        setup_wizard,
        "finalize_windows_host_switch",
        lambda transaction: committed.append(transaction),
    )

    with pytest.raises(ValueError, match="更新服务启动类型设置失败") as failure:
        setup_wizard.configure_host_config("127.0.0.1", 18765, tmp_path / "主机数据")

    assert "MODE_SWITCH_ROLLBACK_FAILED" not in str(failure.value)
    assert restarted and autostarts == ["personal"] and user_modes == ["personal"]
    assert committed == ["A" * 32]
    warning = program_data / "PartyOps" / "mode-switch-warning.log"
    assert "FIREWALL_RULE_CLEANUP_DEFERRED" in warning.read_text(encoding="utf-8")


def test_device_config_credentials_paths_and_pki(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config_dir)
    monkeypatch.setattr(
        setup_wizard, "device_metadata", lambda: {"architecture": "amd64"}
    )
    monkeypatch.setattr(
        setup_wizard, "validate_config", lambda _config: ("host", "token", tmp_path)
    )
    monkeypatch.setattr(
        setup_wizard, "install_internal_ca", lambda path: installed.append(path)
    )
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    installed: list[Path] = []

    with pytest.raises(ValueError, match="完整设备凭据"):
        setup_wizard.write_device_config(
            "http://host", {}, tmp_path / "backup", device_name="终端"
        )
    with pytest.raises(ValueError, match="灾备拉取间隔"):
        setup_wizard.write_device_config(
            "http://host",
            {"device_token": "token", "device_id": "device"},
            tmp_path / "backup",
            device_name="终端",
            interval_seconds=1,
        )
    shared_file = tmp_path / "file.txt"
    shared_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="真实文件夹"):
        setup_wizard.write_device_config(
            "http://host",
            {"device_token": "token", "device_id": "device"},
            tmp_path / "backup",
            device_name="终端",
            shared_dir=shared_file,
        )

    shared = tmp_path / "共享 文件"
    shared.mkdir()
    enrollment = {
        "device_token": "token",
        "device_id": "device",
        "agent_url": "https://host:18766",
        "_private_key_pem": "KEY",
        "certificate_pem": "CERT",
        "ca_certificate_pem": "CA",
    }
    path = setup_wizard.write_device_config(
        "https://host:18765/",
        enrollment,
        tmp_path / "backup",
        device_name=" 协同终端 ",
        shared_dir=shared,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["host_url"] == "https://host:18765"
    assert payload["shared_roots"][0]["local_path"] == str(shared.resolve())
    assert payload["key_file"].endswith("device.key")
    assert installed == [config_dir / "pki" / "ca.pem"]


def test_environment_executable_autostart_and_ca_platform_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    frontend = runtime / "frontend"
    frontend.mkdir(parents=True)
    executable = runtime / "partyops-client"
    executable.write_text("client", encoding="utf-8")
    environment = tmp_path / "partyops.env"
    environment.write_text(
        "# note\nBROKEN\nPARTYOPS_PORT=18765\nEMPTY=\n", encoding="utf-8"
    )
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: runtime)
    values = setup_wizard.load_host_environment(environment)
    assert values["PARTYOPS_PORT"] == "18765"
    assert values["EMPTY"] == ""
    assert values["PARTYOPS_FRONTEND_DIST"] == str(frontend)

    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    assert setup_wizard._executable("partyops-client") == executable
    assert setup_wizard.install_host_autostart(environment) is None
    start = runtime / "start.sh"
    start.write_text("#!/bin/sh", encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard, "config_root", lambda: tmp_path / "cfg" / "partyops"
    )
    assert (
        setup_wizard.install_host_autostart(environment).name == "partyops-host.desktop"
    )
    assert (
        setup_wizard.install_client_autostart(environment).name
        == "partyops-client.desktop"
    )

    monkeypatch.setattr(setup_wizard.sys, "platform", "win32")
    launcher = runtime / "PartyOpsLauncher.exe"
    launcher.write_bytes(b"launcher")
    registry: dict[str, str] = {}

    class RegistryKey:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_SET_VALUE=1,
        REG_SZ=1,
        CreateKeyEx=lambda *_args: RegistryKey(),
        SetValueEx=lambda _key, name, _reserved, _kind, value: registry.__setitem__(
            name, value
        ),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    assert setup_wizard.install_client_autostart(environment) == environment
    assert registry["PartyOpsAgent"] == f'"{launcher}" --background'

    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    assert setup_wizard.install_host_autostart(environment) is None
    assert setup_wizard.install_client_autostart(environment) is None
    setup_wizard.install_internal_ca(tmp_path / "missing.pem")

    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    ca = tmp_path / "ca.pem"
    ca.write_text("CA", encoding="utf-8")
    helper = runtime / "install-internal-ca.sh"
    helper.write_text("#!/bin/sh", encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "denied"),
    )
    with pytest.raises(ValueError, match="管理员授权"):
        setup_wizard.install_internal_ca(ca)
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("pkexec missing")),
    )
    with pytest.raises(ValueError, match="PolicyKit"):
        setup_wizard.install_internal_ca(ca)


@pytest.mark.parametrize(
    ("output", "returncode", "expected"),
    [
        ("service does not exist", 1060, "missing"),
        ("STATE : STOPPED", 0, "stopped"),
        ("STATE : RUNNING", 0, "running"),
        ("STATE : START_PENDING", 0, "pending"),
        ("STATE : STOP_PENDING", 0, "stopping"),
        ("unknown", 1, "unknown"),
    ],
)
def test_query_windows_service_state_matrix(
    monkeypatch, output: str, returncode: int, expected: str
) -> None:
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], returncode, output, ""),
    )
    state, detail = setup_wizard._query_windows_host_service()
    assert state == expected
    assert detail == output


def test_start_service_and_health_timeout_diagnostics(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        setup_wizard, "_query_windows_host_service", lambda: ("running", "ok")
    )
    setup_wizard._start_windows_host_service()

    monkeypatch.setattr(
        setup_wizard, "_query_windows_host_service", lambda: ("missing", "missing")
    )
    with pytest.raises(setup_wizard.HostStartupError) as missing:
        setup_wizard._start_windows_host_service()
    assert missing.value.code == SERVICE_MISSING

    states = iter([("stopped", "initial"), ("stopped", "before"), ("running", "ready")])
    monkeypatch.setattr(
        setup_wizard, "_query_windows_host_service", lambda: next(states)
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "started", ""),
    )
    setup_wizard._start_windows_host_service(timeout=5)

    times = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        setup_wizard, "_query_windows_host_service", lambda: ("pending", "pending")
    )
    with pytest.raises(setup_wizard.HostStartupError) as stopped:
        setup_wizard._start_windows_host_service(timeout=5)
    assert stopped.value.code == SERVICE_STOPPED

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    times = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("refused")),
    )
    monkeypatch.setattr(
        setup_wizard, "_query_windows_host_service", lambda: ("missing", "SCM missing")
    )
    with pytest.raises(setup_wizard.HostStartupError) as health:
        setup_wizard.wait_for_host_health(
            "192.168.1.8", 18765, timeout=5, data_dir=tmp_path
        )
    assert health.value.code == SERVICE_MISSING
    assert "SCM missing" in health.value.detail

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    times = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    old_statuses = [
        {"updated_at": "old", "code": HEALTH_TIMEOUT, "detail": "旧诊断"}
    ]
    current_status = {
        "updated_at": "current",
        "code": HEALTH_TIMEOUT,
        "detail": "状态文件诊断",
    }
    monkeypatch.setattr(
        setup_wizard,
        "read_service_status",
        lambda _path: old_statuses.pop(0) if old_statuses else current_status,
    )
    with pytest.raises(setup_wizard.HostStartupError) as status_override:
        setup_wizard.wait_for_host_health(
            "192.168.1.8", 18765, timeout=5, data_dir=tmp_path
        )
    assert status_override.value.code == HEALTH_TIMEOUT
    assert status_override.value.detail == "状态文件诊断"


def test_health_wait_rejects_terminal_child_and_accepts_tls_with_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        setup_wizard,
        "read_service_status",
        lambda _path, states=iter(
            [
                {"updated_at": "old", "code": "CHILD_EXITED", "detail": "旧诊断"},
                {
                    "updated_at": "current",
                    "code": "CHILD_EXITED",
                    "detail": "数据库迁移失败",
                },
            ]
        ): next(states),
    )
    with pytest.raises(setup_wizard.HostStartupError) as terminal:
        setup_wizard.wait_for_host_health(
            "192.168.1.8",
            18765,
            timeout=5,
            data_dir=tmp_path,
        )
    assert terminal.value.code == "CHILD_EXITED"
    assert terminal.value.detail == "数据库迁移失败"

    from app import __version__

    ca = tmp_path / "secrets" / "pki" / "ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("test-ca", encoding="utf-8")
    payload = json.dumps(
        {
            "status": "ok",
            "mode": "host",
            "app_version": __version__,
            "sqlite": {"safe_version": True, "fts5": True},
        }
    ).encode("utf-8")
    monkeypatch.setattr(setup_wizard, "read_service_status", lambda _path: {})
    monkeypatch.setattr(
        setup_wizard.ssl, "create_default_context", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HealthResponse(payload),
    )
    monkeypatch.setattr(
        setup_wizard,
        "write_service_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("readonly")),
    )
    progress: list[str] = []
    assert (
        setup_wizard.wait_for_host_health(
            "192.168.1.8",
            18765,
            tls=True,
            timeout=5,
            data_dir=tmp_path,
            progress=progress.append,
        )
        == "https://192.168.1.8:18765"
    )
    assert progress == ["health_check", "ready"]


def test_host_resolution_and_admin_bootstrap_error_matrix(monkeypatch) -> None:
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    for value in (
        "ftp://host",
        "http://user:pass@host",
        "http://host/path",
        "http://8.8.8.8",
    ):
        with pytest.raises(ValueError):
            setup_wizard.resolve_host_url(value)

    attempts: list[str] = []

    def open_health(request, **_kwargs):
        attempts.append(request.full_url)
        if request.full_url.startswith("http://"):
            raise urllib.error.URLError("http refused")
        return _HealthResponse()

    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", open_health)
    resolved, payload = setup_wizard.resolve_host_url("http://192.168.1.8:18765")
    assert resolved == "https://192.168.1.8:18765"
    assert payload["status"] == "ok"
    assert len(attempts) == 2

    monkeypatch.setattr(
        setup_wizard.urllib.request, "urlopen", lambda *_a, **_k: _HealthResponse(b"[]")
    )
    with pytest.raises(ValueError, match="无法连接主机"):
        setup_wizard.resolve_host_url("https://192.168.1.8:18765")

    invalid_admins = [
        {"display_name": "A", "username": "admin", "password": "12345678"},
        {"display_name": "管理员", "username": "a!", "password": "12345678"},
        {"display_name": "管理员", "username": "admin", "password": "short"},
    ]
    for payload in invalid_admins:
        with pytest.raises(ValueError):
            setup_wizard.bootstrap_first_admin("http://127.0.0.1:18765", **payload)
    with pytest.raises(ValueError, match="地址无效"):
        setup_wizard.bootstrap_first_admin(
            "http://127.0.0.1",
            display_name="管理员",
            username="admin",
            password="12345678",
        )

    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_a, **_k: _HealthResponse(status=200),
    )
    with pytest.raises(ValueError, match="未确认"):
        setup_wizard.bootstrap_first_admin(
            "http://127.0.0.1:18765",
            display_name="管理员",
            username="admin",
            password="12345678",
        )


def test_folder_picker_render_and_wait_for_ca_matrix(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(
        setup_wizard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "kdialog" else None,
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], 0, str(tmp_path / "共享目录") + "\n", ""
        ),
    )
    assert setup_wizard._choose_system_folder() == tmp_path / "共享目录"
    page = setup_wizard.render_shared_root_manager(
        "csrf",
        [
            {
                "root_id": "r1",
                "name": "<共享>",
                "local_path": "D:/资料",
                "approval_status": "pending",
            }
        ],
        message="完成",
        error="提醒",
    )
    assert "&lt;共享&gt;" in page and "完成" in page and "提醒" in page
    assert "尚未添加共享目录" in setup_wizard.render_shared_root_manager("csrf", [])

    ca = tmp_path / "ca.pem"
    ca.write_text("CA", encoding="utf-8")
    copied = tmp_path / "config"
    installed: list[Path] = []
    monkeypatch.setattr(setup_wizard, "config_root", lambda: copied)
    monkeypatch.setattr(
        setup_wizard, "install_internal_ca", lambda path: installed.append(path)
    )
    setup_wizard._wait_and_install_ca(ca)
    assert installed == [copied / "pki" / "ca.pem"]
    assert (copied / "pki" / "ca.pem").read_text(encoding="utf-8") == "CA"
