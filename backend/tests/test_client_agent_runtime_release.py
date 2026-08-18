"""协同 Agent 更新、通知、命令分派与单次运行发布回归。"""

from __future__ import annotations

import json
import hashlib
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
    commands: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        client_agent,
        "_run_windows_elevated_update",
        lambda updater, target: commands.append((updater, target)) or True,
    )
    config = {"updates_dir": str(tmp_path / "updates")}
    assert client_agent.apply_update_command(
        "https://host", "token", {"package": "partyops_1.4.2.partyops-update"}, config
    )["ok"] is True
    target = tmp_path / "updates" / "partyops_1.4.2.partyops-update"
    assert target.read_bytes() == package
    assert commands == [(helper, target)]

    monkeypatch.setattr(
        client_agent,
        "_run_windows_elevated_update",
        lambda _updater, _target: False,
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


def test_apply_update_official_catalog_is_selected_and_verified_on_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """协同机必须自行验签选包，不能使用主机命令注入的下载地址。"""

    executable = tmp_path / "PartyOpsAgent.exe"
    executable.write_bytes(b"agent")
    helper = tmp_path / "PartyOpsUpdater.exe"
    helper.write_bytes(b"updater")
    package = b"official-signed-platform-update"
    package_hash = hashlib.sha256(package).hexdigest()
    catalog = {
        "available": True,
        "version": "1.4.3-rc.4",
        "package_url": "https://www.partyops.cn/releases/windows-amd64.partyops-update",
        "package_size": len(package),
        "package_sha256": package_hash,
    }
    opened: list[str] = []

    from app.routers import updates

    monkeypatch.setattr(client_agent.sys, "executable", str(executable))
    monkeypatch.setattr(client_agent.os, "name", "nt")
    monkeypatch.setattr(updates, "fetch_online_update_catalog", lambda: catalog)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda url: opened.append(url)
        or _Response(package, {"Content-Length": str(len(package))}),
    )
    installed: list[Path] = []
    monkeypatch.setattr(
        client_agent,
        "_run_windows_elevated_update",
        lambda _helper, target: installed.append(target) or True,
    )

    result = client_agent.apply_update_command(
        "https://host.invalid",
        "host-token",
        {
            "official_online": True,
            "version": "1.4.3-rc.4",
            "package": "https://attacker.invalid/injected.partyops-update",
        },
        {"updates_dir": str(tmp_path / "updates")},
    )

    assert result["ok"] is True
    assert opened == [catalog["package_url"]]
    assert len(installed) == 1
    assert installed[0].read_bytes() == package
    assert "attacker.invalid" not in installed[0].name


def test_apply_update_official_download_resumes_and_reuses_verified_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """协同机断网后续传，同一已校验包再次执行时不重复下载。"""

    from app.routers import updates

    executable = tmp_path / "PartyOpsAgent.exe"
    executable.write_bytes(b"agent")
    helper = tmp_path / "PartyOpsUpdater.exe"
    helper.write_bytes(b"updater")
    monkeypatch.setattr(client_agent.sys, "executable", str(executable))
    monkeypatch.setattr(client_agent.os, "name", "nt")
    package = b"official-platform-update-with-resume"
    digest = hashlib.sha256(package).hexdigest()
    version = "1.4.3-rc.4"
    catalog = {
        "available": True,
        "version": version,
        "package_url": "https://www.partyops.cn/releases/windows-amd64.partyops-update",
        "package_size": len(package),
        "package_sha256": digest,
    }
    monkeypatch.setattr(updates, "fetch_online_update_catalog", lambda: catalog)
    updates_dir = tmp_path / "updates"
    updates_dir.mkdir()
    filename = f"official-{version}-{digest[:12]}.partyops-update"
    resume_at = 9
    (updates_dir / f".{filename}.part").write_bytes(package[:resume_at])
    seen_headers: list[dict[str, str] | None] = []

    def open_range(_url: str, *, extra_headers=None):
        seen_headers.append(extra_headers)
        response = _Response(
            package[resume_at:],
            {
                "Content-Length": str(len(package) - resume_at),
                "Content-Range": f"bytes {resume_at}-{len(package) - 1}/{len(package)}",
            },
        )
        response.status = 206
        return response

    monkeypatch.setattr(updates, "_open_trusted_update_url", open_range)
    installed: list[Path] = []
    monkeypatch.setattr(
        client_agent,
        "_run_windows_elevated_update",
        lambda _helper, target: installed.append(target) or True,
    )
    payload = {"official_online": True, "version": version}
    config = {"updates_dir": str(updates_dir)}

    assert client_agent.apply_update_command("https://host", "token", payload, config)["ok"]
    assert seen_headers == [{"Range": f"bytes={resume_at}-"}]
    assert installed[-1].read_bytes() == package

    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应重复下载")),
    )
    assert client_agent.apply_update_command("https://host", "token", payload, config)["ok"]
    assert len(installed) == 2


def test_apply_update_official_catalog_fails_closed_on_version_or_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.routers import updates

    config = {"updates_dir": str(tmp_path / "updates")}
    monkeypatch.setattr(
        updates,
        "fetch_online_update_catalog",
        lambda: {
            "available": True,
            "version": "9.9.9",
            "package_url": "https://www.partyops.cn/releases/update.partyops-update",
            "package_size": 4,
            "package_sha256": hashlib.sha256(b"good").hexdigest(),
        },
    )
    with pytest.raises(client_agent.AgentCommandError) as version_error:
        client_agent.apply_update_command(
            "https://host", "token", {"official_online": True, "version": "1.4.3-rc.4"}, config
        )
    assert version_error.value.code == "UPDATE_CATALOG_UNAVAILABLE"

    monkeypatch.setattr(
        updates,
        "fetch_online_update_catalog",
        lambda: {
            "available": True,
            "version": "1.4.3-rc.4",
            "package_url": "https://www.partyops.cn/releases/update.partyops-update",
            "package_size": 4,
            "package_sha256": hashlib.sha256(b"good").hexdigest(),
        },
    )
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: _Response(b"evil", {"Content-Length": "4"}),
    )
    with pytest.raises(client_agent.AgentCommandError) as hash_error:
        client_agent.apply_update_command(
            "https://host", "token", {"official_online": True, "version": "1.4.3-rc.4"}, config
        )
    assert hash_error.value.code == "UPDATE_PACKAGE_HASH_MISMATCH"


@pytest.mark.parametrize(
    ("headers", "body", "expected_code"),
    [
        ({"Content-Encoding": "gzip", "Content-Length": "4"}, b"good", "UPDATE_PACKAGE_ENCODING_INVALID"),
        ({"Content-Length": "bad"}, b"good", "UPDATE_PACKAGE_LENGTH_INVALID"),
        ({"Content-Length": "-1"}, b"good", "UPDATE_PACKAGE_LENGTH_INVALID"),
        ({"Content-Length": "3"}, b"good", "UPDATE_PACKAGE_LENGTH_MISMATCH"),
        ({"Content-Length": "4"}, b"x", "UPDATE_PACKAGE_LENGTH_MISMATCH"),
        ({"Content-Length": "4"}, b"evil", "UPDATE_PACKAGE_HASH_MISMATCH"),
    ],
)
def test_apply_update_official_download_response_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    headers: dict[str, str],
    body: bytes,
    expected_code: str,
) -> None:
    from app.routers import updates

    catalog = {
        "available": True,
        "version": "1.4.3-rc.4",
        "package_url": "https://www.partyops.cn/releases/update.partyops-update",
        "package_size": 4,
        "package_sha256": hashlib.sha256(b"good").hexdigest(),
    }
    monkeypatch.setattr(updates, "fetch_online_update_catalog", lambda: catalog)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: _Response(body, headers),
    )
    with pytest.raises(client_agent.AgentCommandError) as error:
        client_agent.apply_update_command(
            "https://host",
            "token",
            {"official_online": True, "version": "1.4.3-rc.4"},
            {"updates_dir": str(tmp_path / "updates")},
        )
    assert error.value.code == expected_code
    assert not list((tmp_path / "updates").glob("*.part"))


def test_apply_update_official_download_space_size_and_network_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.routers import updates

    package = b"good"
    catalog = {
        "available": True,
        "version": "1.4.3-rc.4",
        "package_url": "https://www.partyops.cn/releases/update.partyops-update",
        "package_size": len(package),
        "package_sha256": hashlib.sha256(package).hexdigest(),
    }
    monkeypatch.setattr(updates, "fetch_online_update_catalog", lambda: catalog)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: _Response(package, {"Content-Length": str(len(package))}),
    )
    monkeypatch.setattr(
        client_agent.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1),
    )
    with pytest.raises(client_agent.AgentCommandError) as disk_error:
        client_agent.apply_update_command(
            "https://host", "token", {"official_online": True, "version": "1.4.3-rc.4"},
            {"updates_dir": str(tmp_path / "disk")},
        )
    assert disk_error.value.code == "UPDATE_DISK_FULL"

    monkeypatch.setattr(
        client_agent.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=10**12),
    )
    monkeypatch.setattr(client_agent, "MAX_UPDATE_PACKAGE_BYTES", 3)
    with pytest.raises(client_agent.AgentCommandError) as size_error:
        client_agent.apply_update_command(
            "https://host", "token", {"official_online": True, "version": "1.4.3-rc.4"},
            {"updates_dir": str(tmp_path / "size")},
        )
    assert size_error.value.code == "UPDATE_PACKAGE_TOO_LARGE"

    monkeypatch.setattr(client_agent, "MAX_UPDATE_PACKAGE_BYTES", 4 * 1024**3)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(client_agent.AgentCommandError) as network_error:
        client_agent.apply_update_command(
            "https://host", "token", {"official_online": True, "version": "1.4.3-rc.4"},
            {"updates_dir": str(tmp_path / "network")},
        )
    assert network_error.value.code == "NETWORK_INTERRUPTED"
    assert network_error.value.retryable is True


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

    monkeypatch.setattr(
        client_agent,
        "run",
        lambda path, once=False, open_browser=None, browser_url_file=None: 7,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["partyops-client", "--config", str(invalid), "--once", "--no-open-browser"],
    )
    with pytest.raises(SystemExit) as result:
        client_agent.main()
    assert result.value.code == 7
