"""协同 Agent 更新、通知、命令分派与单次运行发布回归。"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import client_agent


class _Response:
    status = 200

    def __init__(self, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.offset = 0
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self.offset
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_remote_index_content_encoding_and_scan_state_guards(tmp_path: Path) -> None:
    root = tmp_path / "共享"
    root.mkdir()
    utf8 = root / "说明.txt"
    utf8.write_text("党建协同正文", encoding="utf-8")
    item, signature = client_agent._remote_index_item(root, utf8, "", True)
    assert item["extracted_text"] == "党建协同正文"
    assert item["content_changed"] is True and signature
    unchanged, _ = client_agent._remote_index_item(root, utf8, signature, False)
    assert unchanged["content_changed"] is False and unchanged["extracted_text"] == ""

    gb = root / "旧编码.txt"
    gb.write_bytes("党建旧编码".encode("gb18030"))
    extracted, _ = client_agent._remote_index_item(root, gb, "", True)
    assert extracted["extracted_text"] == "党建旧编码"
    child = root / "子目录"
    child.mkdir()
    directory, _ = client_agent._remote_index_item(root, child, "", True)
    assert directory["is_directory"] is True and directory["size_bytes"] == 0

    state = tmp_path / "state.json"
    assert client_agent._load_scan_state(state) == {}
    state.write_text("[]", encoding="utf-8")
    assert client_agent._load_scan_state(state) == {}
    state.write_text("{broken", encoding="utf-8")
    assert client_agent._load_scan_state(state) == {}


def test_apply_update_windows_download_helper_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "PartyOpsAgent.exe"
    executable.write_bytes(b"agent")
    helper = tmp_path / "PartyOpsUpdater.exe"
    helper.write_bytes(b"updater")
    monkeypatch.setattr(client_agent.sys, "executable", str(executable))
    monkeypatch.setattr(client_agent.os, "name", "nt")
    package = b"signed-update-package"
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_args: _Response(package))
    commands: list[list[str]] = []
    monkeypatch.setattr(
        client_agent.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    config = {"updates_dir": str(tmp_path / "updates")}
    assert client_agent.apply_update_command(
        "https://host", "token", {"package": "partyops_1.4.2.partyops-update"}, config
    )["ok"] is True
    target = tmp_path / "updates" / "partyops_1.4.2.partyops-update"
    assert target.read_bytes() == package
    assert "-Verb RunAs" in commands[0][4]

    monkeypatch.setattr(
        client_agent.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "denied"),
    )
    failed = client_agent.apply_update_command(
        "https://host", "token", {"package": "partyops_1.4.2.partyops-update"}, config
    )
    assert failed["error_code"] == "UPDATE_INSTALL_FAILED"
    assert client_agent.apply_update_command(
        "https://host", "token", {"package": "../bad.exe"}, config
    )["error_code"] == "UPDATE_PACKAGE_INVALID"

    helper.unlink()
    missing = client_agent.apply_update_command(
        "https://host", "token", {"package": "partyops_1.4.2.partyops-update"}, config
    )
    assert missing["error_code"] == "UPDATE_HELPER_MISSING"
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(client_agent.AgentCommandError) as interrupted:
        client_agent.apply_update_command(
            "https://host", "token", {"package": "partyops_1.4.2.partyops-update"}, config
        )
    assert interrupted.value.code == "NETWORK_INTERRUPTED" and interrupted.value.retryable


def test_device_command_dispatch_ack_retry_and_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    acknowledgements: list[tuple] = []
    monkeypatch.setattr(
        client_agent,
        "ack_device_command",
        lambda host, token, command_id, result: acknowledgements.append((command_id, result)) or True,
    )
    handlers = {
        "upload_file": "upload_transfer",
        "upload_bundle": "upload_bundle_transfer",
        "download_file": "download_transfer",
        "apply_update": "apply_update_command",
    }
    for command_type, attribute in handlers.items():
        monkeypatch.setattr(
            client_agent,
            attribute,
            lambda *_args, command_type=command_type, **_kwargs: {"ok": True, "message": command_type},
        )
        assert client_agent.process_device_command(
            "https://host",
            "token",
            {"id": f"command-{command_type}", "type": command_type, "payload": {}},
            {},
        )
    monkeypatch.setattr(
        client_agent,
        "rotate_device_certificate",
        lambda *_args, **_kwargs: {"ok": True, "message": "rotated"},
    )
    assert client_agent.process_device_command(
        "https://host",
        "token",
        {"id": "rotate", "type": "rotate_certificate", "payload": {}},
        {},
        tmp_path / "client.json",
    )
    assert client_agent.process_device_command(
        "https://host",
        "token",
        {"id": "unsupported", "type": "unknown", "payload": {}},
        {},
    )
    assert acknowledgements[-1][1]["error_code"] == "COMMAND_UNSUPPORTED"
    assert not client_agent.process_device_command(
        "https://host", "token", {"id": "", "type": "upload_file", "payload": []}, {}
    )

    monkeypatch.setattr(
        client_agent,
        "upload_transfer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            client_agent.AgentCommandError("NETWORK_INTERRUPTED", "offline", retryable=True)
        ),
    )
    assert not client_agent.process_device_command(
        "https://host",
        "token",
        {"id": "retry", "type": "upload_file", "payload": {}},
        {},
    )
    monkeypatch.setattr(
        client_agent,
        "upload_transfer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            client_agent.AgentCommandError("SOURCE_MISSING", "missing")
        ),
    )
    assert client_agent.process_device_command(
        "https://host",
        "token",
        {"id": "failed", "type": "upload_file", "payload": {}},
        {},
    )
    assert acknowledgements[-1][1]["error_code"] == "SOURCE_MISSING"
    monkeypatch.setattr(
        client_agent,
        "download_transfer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert client_agent.process_device_command(
        "https://host",
        "token",
        {"id": "exception", "type": "download_file", "payload": {}},
        {},
    )
    assert acknowledgements[-1][1]["error_code"] == "AGENT_EXECUTION_FAILED"

    arguments: list[tuple] = []
    monkeypatch.setattr(client_agent.os, "execv", lambda executable, args: arguments.append((executable, args)))
    monkeypatch.setattr(client_agent.sys, "frozen", False, raising=False)
    client_agent._restart_agent_after_update(tmp_path / "client.json")
    assert arguments[0][1][1:3] == ["-m", "app.client_agent"]


def test_notifications_reachability_and_desktop_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert client_agent._agent_headers("secret", False) == {"X-PartyOps-Pairing": "secret"}
    assert client_agent._agent_headers("secret", True) == {"X-PartyOps-Device-Token": "secret"}
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_args: _Response(b'{"unread_count":3,"revision":"r1"}'))
    assert client_agent.host_reachable("https://host")
    summary = client_agent.fetch_notification_summary("https://host", "token", True)
    assert summary == {"unread_count": 3, "revision": "r1"}
    shown: list[int] = []
    real_show = client_agent.show_desktop_notification
    monkeypatch.setattr(client_agent, "show_desktop_notification", lambda count: shown.append(count) or True)
    assert client_agent.poll_desktop_notifications("https://host", "token", tmp_path, True)
    assert shown == [3]
    assert not client_agent.poll_desktop_notifications("https://host", "token", tmp_path, True)

    monkeypatch.setattr(client_agent, "show_desktop_notification", real_show)
    monkeypatch.setattr(client_agent.shutil, "which", lambda _name: None)
    assert not client_agent.show_desktop_notification(3)
    monkeypatch.setattr(client_agent.shutil, "which", lambda _name: "notify-send")
    monkeypatch.setattr(
        client_agent.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no display")),
    )
    assert not client_agent.show_desktop_notification(3)
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    assert not client_agent.host_reachable("https://host")
    assert client_agent.fetch_notification_summary("https://host", "token") is None


@pytest.mark.parametrize(
    ("pull_result", "reachable", "expected"),
    [
        (None, True, 0),
        (None, False, 1),
        (Path("backup.partyops-backup"), True, 0),
    ],
)
def test_agent_once_reports_backup_truthfully(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pull_result: Path | None,
    reachable: bool,
    expected: int,
) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "https://192.168.8.20:18765",
                "pairing_token": "pairing",
                "backup_dir": str(tmp_path / "backup"),
                "open_browser": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(client_agent, "configure_agent_logging", lambda _path: tmp_path / "log")
    monkeypatch.setattr(client_agent, "configure_ssl_context", lambda _config: None)
    monkeypatch.setattr(client_agent, "pull_backup", lambda *_args, **_kwargs: pull_result)
    monkeypatch.setattr(client_agent, "host_reachable", lambda _host: reachable)
    monkeypatch.setattr(client_agent, "poll_desktop_notifications", lambda *_args, **_kwargs: False)
    assert client_agent.run(config_path, once=True, open_browser=False) == expected


def test_agent_once_configuration_errors_and_main_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert client_agent.run(tmp_path / "missing.json", once=True) == 2
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"host_url": "bad"}), encoding="utf-8")
    monkeypatch.setattr(client_agent, "configure_agent_logging", lambda _path: tmp_path / "log")
    monkeypatch.setattr(client_agent, "configure_ssl_context", lambda _config: None)
    assert client_agent.run(invalid, once=True) == 2

    monkeypatch.setattr(client_agent, "run", lambda path, once=False, open_browser=None: 7)
    monkeypatch.setattr(
        sys,
        "argv",
        ["partyops-client", "--config", str(invalid), "--once", "--no-open-browser"],
    )
    with pytest.raises(SystemExit) as result:
        client_agent.main()
    assert result.value.code == 7
