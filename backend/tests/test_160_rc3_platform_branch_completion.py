"""rc.3 跨平台配置与协同地址的剩余失败分支。"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import client_agent, setup_wizard
from app.client_agent import AgentCommandError


def _os_proxy(name: str) -> SimpleNamespace:
    proxy = SimpleNamespace(**vars(os))
    proxy.name = name
    if not hasattr(proxy, "getuid"):
        proxy.getuid = lambda: 1000
    return proxy


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


def test_setup_platform_roots_and_desktop_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard.sys, "platform", "win32")
    assert setup_wizard.config_root() == tmp_path / "local" / "PartyOps"
    assert setup_wizard.personal_default_data_dir().name == "PartyOps-个人数据"

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    assert "Application Support" in str(setup_wizard.config_root())
    assert setup_wizard.installer_default_data_dir().name == "HostData"
    assert setup_wizard.personal_default_data_dir().name == "PersonalData"

    monkeypatch.setattr(setup_wizard.sys, "platform", "plan9")
    assert (
        setup_wizard._publish_desktop_tool_url("wizard", "http://127.0.0.1:99") is None
    )

    marker = tmp_path / "wizard.url"
    marker.write_text("http://127.0.0.1:88\n", encoding="utf-8")
    setup_wizard._clear_desktop_tool_url(marker, "http://127.0.0.1:99")
    assert marker.exists()
    setup_wizard._clear_desktop_tool_url(None, "http://127.0.0.1:99")


def test_macos_launch_agent_activation_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(setup_wizard.stdlib_platform, "system", lambda: "Darwin")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    path = tmp_path / "cn.partyops.personal.plist"
    path.write_text("plist", encoding="utf-8")

    calls: list[list[str]] = []

    def bootstrap_failed(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=7 if "bootstrap" in command else 0,
            stdout="",
            stderr="bootstrap denied",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", bootstrap_failed)
    with pytest.raises(ValueError, match="bootstrap denied"):
        setup_wizard._activate_macos_launch_agent(path, "cn.partyops.personal")

    def kickstart_failed(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=8 if "kickstart" in command else 0,
            stdout="",
            stderr="kickstart denied",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", kickstart_failed)
    with pytest.raises(ValueError, match="kickstart denied"):
        setup_wizard._activate_macos_launch_agent(path, "cn.partyops.personal")

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: (
            calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    setup_wizard._activate_macos_launch_agent(path, "cn.partyops.personal")
    assert any("kickstart" in command for command in calls)


def test_macos_launch_agent_write_remove_and_runtime_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="模式无效"):
        setup_wizard._write_macos_launch_agent(
            mode="bad",
            program_arguments=[],
            log_path=tmp_path / "log",
        )
    with pytest.raises(FileNotFoundError):
        setup_wizard._write_macos_launch_agent(
            mode="personal",
            program_arguments=[str(tmp_path / "missing")],
            log_path=tmp_path / "log",
        )
    with pytest.raises(ValueError, match="标识不受支持"):
        setup_wizard._macos_launch_agent_path("bad")
    with pytest.raises(ValueError, match="模式无效"):
        setup_wizard._remove_macos_launch_agent("bad")

    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(setup_wizard.stdlib_platform, "system", lambda: "Darwin")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PARTYOPS_MACOS_LAUNCH_AGENTS_DIR", str(tmp_path))
    agent = tmp_path / "cn.partyops.personal.plist"
    agent.write_text("plist", encoding="utf-8")
    commands: list[list[str]] = []
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command)
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    setup_wizard._remove_macos_launch_agent("personal")
    assert not agent.exists() and commands

    for url in ("file:///tmp/x", "http://127.0.0.1"):
        with pytest.raises(ValueError, match="地址无效"):
            setup_wizard.configured_runtime_status(url)
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="CA 尚未就绪"):
        setup_wizard.configured_runtime_status("https://host:18765")

    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"[]"),
    )
    with pytest.raises(ValueError, match="返回格式无效"):
        setup_wizard.configured_runtime_status("http://host:18765")
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b'{"configured":false}'),
    )
    assert setup_wizard.configured_runtime_status("http://host:18765") is False


def test_client_network_migration_all_endpoint_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for value in ("bad", "http://localhost:18765", "http://0.0.0.0:18765"):
        with pytest.raises(AgentCommandError):
            client_agent._validated_migration_url(value, field="主机地址")
    assert (
        client_agent._validated_migration_url(
            "https://192.168.1.8:18765/", field="主机"
        )
        == "https://192.168.1.8:18765"
    )

    assert client_agent._select_migration_endpoints(
        {}, "http://new", "http://agent"
    ) == (
        "http://new",
        "http://agent",
    )
    invalid = {"network_migration": {"expires_at": "invalid"}}
    assert client_agent._select_migration_endpoints(
        invalid, "http://new", "http://agent"
    ) == (
        "http://new",
        "http://agent",
    )
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    migration = {
        "network_migration": {
            "expires_at": future,
            "previous_host_url": "http://old",
            "previous_agent_url": "http://old-agent",
        }
    }
    monkeypatch.setattr(client_agent, "host_reachable", lambda url: url == "http://new")
    assert client_agent._select_migration_endpoints(
        migration, "http://new", "http://agent"
    ) == (
        "http://new",
        "http://agent",
    )
    assert migration["network_migration"]["state"] == "new_address_ready"

    monkeypatch.setattr(client_agent, "host_reachable", lambda url: url == "http://old")
    assert client_agent._select_migration_endpoints(
        migration, "http://new", "http://agent"
    ) == (
        "http://old",
        "http://old-agent",
    )
    assert migration["network_migration"]["state"] == "using_previous_address"

    monkeypatch.setattr(client_agent, "host_reachable", lambda _url: False)
    assert client_agent._select_migration_endpoints(
        migration, "http://new", "http://agent"
    ) == (
        "http://new",
        "http://agent",
    )
    assert migration["network_migration"]["state"] == "new_address_unreachable"

    config = {"host_url": "http://old", "agent_url": "http://old-agent"}
    path = tmp_path / "client.json"
    result = client_agent.apply_network_migration_command(
        {
            "host_url": "http://192.168.1.9:18765",
            "agent_url": "http://192.168.1.9:18766",
            "transaction_id": "tx-1",
            "expires_at": future,
        },
        config,
        path,
    )
    assert result["ok"] is True
    assert (
        json.loads(path.read_text(encoding="utf-8"))["network_migration"]["state"]
        == "pending_restart"
    )
    with pytest.raises(AgentCommandError, match="缺少编号"):
        client_agent.apply_network_migration_command(
            {"host_url": "http://192.168.1.9", "expires_at": future},
            config,
            path,
        )


def test_client_path_and_browser_handoff_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    destination = tmp_path / "client-browser.url"
    with pytest.raises(ValueError, match="当前协同配置目录"):
        client_agent.write_browser_launch_url(
            config_path,
            tmp_path / "other.url",
            "https://host",
        )
    with pytest.raises(ValueError, match="有效"):
        client_agent.write_browser_launch_url(config_path, destination, "file:///tmp/x")
    with pytest.raises(ValueError, match="控制字符"):
        client_agent.write_browser_launch_url(
            config_path,
            destination,
            "https://host/\nattack",
        )

    monkeypatch.setattr(
        client_agent.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("busy")),
    )
    with pytest.raises(OSError, match="busy"):
        client_agent.write_browser_launch_url(config_path, destination, "https://host")
    assert not list(tmp_path.glob(".*.tmp"))

    shared = tmp_path / "shared"
    shared.mkdir()
    file_path = shared / "a.txt"
    file_path.write_text("ok", encoding="utf-8")
    config = {
        "device_id": "device",
        "shared_roots": [
            {
                "local_path": str(shared),
                "remote_key": "root",
                "approval_status": "approved",
            }
        ],
    }
    for key in ("bad", "other:root:a.txt", "device:root:../a.txt", "device:root:"):
        with pytest.raises(AgentCommandError):
            client_agent._resolve_shared_path(config, key)
    with pytest.raises(AgentCommandError, match="尚未在主机获批"):
        client_agent._resolve_shared_path(
            {"device_id": "device", "shared_roots": []},
            "device:root:a.txt",
        )
    with pytest.raises(AgentCommandError, match="移动或删除"):
        client_agent._resolve_shared_path(config, "device:root:missing.txt")
    with pytest.raises(AgentCommandError, match="类型不受支持"):
        client_agent._resolve_shared_path(config, "device:root:.")
    assert (
        client_agent._resolve_shared_path(
            config,
            "device:root:.",
            allow_directory=True,
        )
        == shared
    )

    with pytest.raises(AgentCommandError, match="文件名无效"):
        client_agent._non_overwriting_target(tmp_path, "\x00")
    first = tmp_path / "report.txt"
    first.write_text("1", encoding="utf-8")
    second = client_agent._non_overwriting_target(tmp_path, "report.txt")
    assert second.name == "report (1).txt"


def test_setup_spawn_platform_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[dict[str, object]] = []
    process = SimpleNamespace()
    monkeypatch.setattr(setup_wizard, "_rotate_bounded_log", lambda *_args: None)
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "Popen",
        lambda _command, **options: captured.append(options) or process,
    )
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    assert setup_wizard._spawn(["partyops"], tmp_path / "win.log") is process
    assert captured[-1]["creationflags"] == subprocess.CREATE_NO_WINDOW
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    setup_wizard._spawn(["partyops"], tmp_path / "linux.log")
    assert captured[-1]["start_new_session"] is True


def test_device_config_success_and_rollback_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    backup = tmp_path / "backup"
    shared = tmp_path / "shared"
    shared.mkdir()
    personal_data = tmp_path / "old-personal"
    personal_data.mkdir()
    config.mkdir()
    (config / "personal.env").write_text(
        f"PARTYOPS_DATA_DIR={personal_data}\nPARTYOPS_PORT=18775\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(
        setup_wizard, "device_metadata", lambda: {"platform": "windows"}
    )
    monkeypatch.setattr(setup_wizard, "validate_config", lambda _values: None)
    monkeypatch.setattr(
        setup_wizard, "deactivate_windows_host_for_user_mode", lambda: True
    )
    monkeypatch.setattr(
        setup_wizard, "_stop_personal_process_for_data_migration", lambda *_args: True
    )
    monkeypatch.setattr(setup_wizard, "clear_windows_personal_autostart", lambda: None)
    monkeypatch.setattr(
        setup_wizard, "finalize_windows_host_switch", lambda _state: None
    )
    installed: list[Path] = []
    monkeypatch.setattr(
        setup_wizard, "install_internal_ca", lambda path: installed.append(path)
    )
    path = setup_wizard.write_device_config(
        "https://192.168.1.8:18765/",
        {
            "device_token": "token",
            "device_id": "device-1",
            "agent_url": "https://192.168.1.8:18766",
            "_private_key_pem": "private",
            "certificate_pem": "certificate",
            "ca_certificate_pem": "ca",
        },
        backup,
        device_name=" 协同机 ",
        shared_dir=shared,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["shared_roots"] and payload["device_name"] == "协同机"
    assert payload["host_url"] == "https://192.168.1.8:18765"
    assert installed and (config / "pki" / "device.key").read_text() == "private"

    for enrollment, interval in (
        ({}, 600),
        ({"device_token": "x", "device_id": "d"}, 59),
    ):
        with pytest.raises(ValueError):
            setup_wizard.write_device_config(
                "http://host",
                enrollment,
                backup,
                device_name="device",
                interval_seconds=interval,
            )

    not_directory = tmp_path / "not-directory"
    not_directory.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="真实文件夹"):
        setup_wizard.write_device_config(
            "http://host",
            {"device_token": "x", "device_id": "d"},
            backup,
            device_name="device",
            shared_dir=not_directory,
        )

    (config / "mode.json").write_text('{"mode":"personal"}', encoding="utf-8")
    restarted: list[str] = []
    monkeypatch.setattr(
        setup_wizard,
        "_restart_previous_personal_process",
        lambda _env: restarted.append("process"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "install_windows_personal_autostart",
        lambda: restarted.append("autostart"),
    )
    monkeypatch.setattr(
        setup_wizard,
        "restore_windows_host_after_failed_switch",
        lambda _state: (_ for _ in ()).throw(OSError("host restore failed")),
    )
    monkeypatch.setattr(
        setup_wizard,
        "write_mode_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )
    with pytest.raises(ValueError, match="MODE_SWITCH_ROLLBACK_FAILED"):
        setup_wizard.write_device_config(
            "http://host",
            {"device_token": "x", "device_id": "d"},
            tmp_path / "failed-backup",
            device_name="device",
        )
    assert restarted == ["process", "autostart"]


@pytest.mark.parametrize("platform_name", ["darwin", "linux", "win32"])
def test_personal_config_platform_autostart_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_name: str,
) -> None:
    config = tmp_path / platform_name / "config"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(setup_wizard.sys, "platform", platform_name)
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(
        setup_wizard, "deactivate_windows_host_for_user_mode", lambda: None
    )
    monkeypatch.setattr(setup_wizard, "clear_windows_client_autostart", lambda: None)
    monkeypatch.setattr(
        setup_wizard, "finalize_windows_host_switch", lambda _state: None
    )
    calls: list[str] = []
    monkeypatch.setattr(
        setup_wizard, "_remove_macos_launch_agent", lambda mode: calls.append(mode)
    )
    monkeypatch.setattr(
        setup_wizard, "install_host_autostart", lambda _path: calls.append("host")
    )
    monkeypatch.setattr(
        setup_wizard,
        "install_windows_personal_autostart",
        lambda: calls.append("windows"),
    )
    path = setup_wizard.write_personal_config(tmp_path / platform_name / "data")
    assert path.is_file()
    if platform_name == "darwin":
        assert calls == ["client", "host"]
    elif platform_name == "linux":
        assert calls == ["host"]
    else:
        assert calls == ["windows"]


def test_client_autostart_macos_and_unsupported_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "client.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    monkeypatch.setattr(
        setup_wizard, "_executable", lambda _name: tmp_path / "launcher"
    )
    with pytest.raises(ValueError, match="缺少备份目录"):
        setup_wizard.install_client_autostart(config)

    config.write_text(
        json.dumps({"backup_dir": str(tmp_path / "backup")}), encoding="utf-8"
    )
    removed: list[str] = []
    monkeypatch.setattr(
        setup_wizard,
        "_write_macos_launch_agent",
        lambda **_kwargs: tmp_path / "agent.plist",
    )
    monkeypatch.setattr(
        setup_wizard,
        "_remove_macos_launch_agent",
        lambda mode: removed.append(mode),
    )
    assert setup_wizard.install_client_autostart(config).name == "agent.plist"
    assert removed == ["host", "personal"]
    monkeypatch.setattr(setup_wizard.sys, "platform", "freebsd")
    assert setup_wizard.install_client_autostart(config) is None
