from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import stat
import urllib.error
from pathlib import Path

import pytest

from app import client_agent


def test_browser_launch_url_file_is_private_and_config_scoped(tmp_path: Path) -> None:
    """Linux 桌面启动器只能接收当前协同配置旁的受保护页面地址。"""

    config_path = tmp_path / "partyops" / "client.json"
    config_path.parent.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    marker = config_path.parent / "client-browser.url"
    result = client_agent.write_browser_launch_url(
        config_path,
        marker,
        "https://192.168.20.5:18765/device-launch?token=short-lived",
    )
    assert result == marker
    assert marker.read_text(encoding="utf-8") == (
        "https://192.168.20.5:18765/device-launch?token=short-lived\n"
    )
    if os.name != "nt":
        assert stat.S_IMODE(marker.stat().st_mode) & 0o077 == 0

    with pytest.raises(ValueError, match="当前协同配置目录"):
        client_agent.write_browser_launch_url(
            config_path,
            tmp_path / "outside.url",
            "https://host.example/",
        )
    with pytest.raises(ValueError, match="HTTP/HTTPS"):
        client_agent.write_browser_launch_url(config_path, marker, "file:///etc/passwd")
    with pytest.raises(ValueError, match="控制字符"):
        client_agent.write_browser_launch_url(
            config_path,
            marker,
            "https://host.example/\n--unexpected-option",
        )


class _Response:
    def __init__(self, payload: bytes = b"{}", *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]


def _approved_config(root: Path) -> dict[str, object]:
    return {
        "device_id": "device-1",
        "device_token": "token",
        "shared_roots": [
            {
                "root_id": "root-1",
                "name": "共享资料",
                "local_path": str(root),
                "remote_key": "share_1",
                "approval_status": "approved",
                "enabled": True,
                "semantic_content_enabled": True,
            }
        ],
    }


def test_agent_logging_failure_network_and_browser_bridge(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text("{}", encoding="utf-8")
    log_path = client_agent.configure_agent_logging(config_path)
    assert log_path.parent.name == "logs"
    assert client_agent.configure_agent_logging(config_path) == log_path

    config: dict[str, object] = {}
    client_agent._record_agent_failure(config_path, config, "heartbeat", urllib.error.HTTPError("x", 403, "forbidden", {}, None))
    assert config["authentication_state"] == "reauth_required"
    assert json.loads(config_path.read_text(encoding="utf-8"))["last_agent_error"] == "heartbeat"
    error = client_agent.AgentCommandError("NETWORK_INTERRUPTED", "断线", retryable=True)
    assert error.code == "NETWORK_INTERRUPTED" and error.retryable

    requests: list[tuple[object, int, object]] = []

    def urlopen(request, *, timeout: int, context=None):
        requests.append((request, timeout, context))
        return _Response(json.dumps({"token": "一次性 令牌"}).encode())

    monkeypatch.setattr(client_agent.urllib.request, "urlopen", urlopen)
    client_agent._ACTIVE_SSL_CONTEXT = None
    with pytest.raises(client_agent.AgentCommandError, match="有效的 HTTP/HTTPS"):
        client_agent._urlopen("request", 3)
    assert client_agent._urlopen("https://partyops.test/request", 3).status == 200
    client_agent._ACTIVE_SSL_CONTEXT = object()
    assert client_agent._urlopen("https://partyops.test/request", 4).status == 200
    client_agent._ACTIVE_SSL_CONTEXT = None
    result = client_agent._json_request("http://host/test", token="token", payload={"a": 1}, method="POST")
    assert result == {"token": "一次性 令牌"}
    assert "device-launch?token=" in client_agent.create_browser_launch_url("http://host/", "http://agent", "token")

    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    assert client_agent.create_browser_launch_url("http://host/", "http://agent", "token") == "http://host"

    client_agent.configure_ssl_context({})
    assert client_agent._ACTIVE_SSL_CONTEXT is None
    monkeypatch.setattr(client_agent.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(client_agent.platform, "release", lambda: "10.0-test")
    monkeypatch.setattr(client_agent.getpass, "getuser", lambda: "tester")
    metadata = client_agent.device_metadata()
    assert metadata["architecture"] == "amd64"
    assert metadata["agent_version"] == client_agent.AGENT_VERSION

    for handler in list(client_agent.logger.handlers):
        if isinstance(handler, logging.Handler):
            handler.close()
            client_agent.logger.removeHandler(handler)


def test_shared_root_lifecycle_scan_and_path_guards(monkeypatch, tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "通知.txt").write_text("中文正文", encoding="utf-8")
    (shared / "子目录").mkdir()
    config_path = tmp_path / "client.json"
    config = _approved_config(shared)
    client_agent._save_config(config_path, config)
    calls: list[tuple[str, str, object]] = []

    def request(url: str, **kwargs):
        calls.append((url, kwargs.get("method", "GET"), kwargs.get("payload")))
        if url.endswith("/workspace/roots") and kwargs.get("method", "GET") == "GET":
            return [{"id": "root-1", "name": "主机名称", "approval_status": "approved", "enabled": True, "share_scope": "team"}]
        if url.endswith("/workspace/roots"):
            return {"id": "root-new", "approval_status": "approved", "enabled": True}
        return {"id": "root-1", "name": "新名称"}

    monkeypatch.setattr(client_agent, "_json_request", request)
    assert client_agent.register_shared_root("http://host", "token", "目录", "remote_1")["id"] == "root-new"
    assert client_agent.list_registered_shared_roots("http://host", "token")[0]["id"] == "root-1"
    refreshed = client_agent.refresh_shared_root_statuses("http://host", "token", config, config_path)
    assert refreshed[0]["name"] == "主机名称"
    renamed = client_agent.rename_shared_root("http://host", "token", config, config_path, "root-1", "新名称")
    assert renamed["name"] == "新名称"
    with pytest.raises(ValueError, match="名称不能为空"):
        client_agent.rename_shared_root("http://host", "token", config, config_path, "root-1", " ")

    pending = _approved_config(shared)
    pending["shared_roots"][0]["approval_status"] = "pending"  # type: ignore[index]
    pending["shared_roots"][0].pop("root_id")  # type: ignore[index]
    synced = client_agent.sync_shared_roots("http://host", "token", pending, config_path)
    assert synced[0]["root_id"] == "root-new"
    client_agent.remove_shared_root("http://host", "token", config, config_path, "root-1")
    assert config["shared_roots"] == []

    config = _approved_config(shared)
    valid = client_agent._resolve_shared_file(config, "device-1:share_1:通知.txt")
    assert valid.read_text(encoding="utf-8") == "中文正文"
    with client_agent._open_shared_file(config, "device-1:share_1:通知.txt") as handle:
        assert handle.read().decode() == "中文正文"
    with pytest.raises(client_agent.AgentCommandError) as mismatch:
        client_agent._resolve_shared_file(config, "other:share_1:通知.txt")
    assert mismatch.value.code == "ROOT_NOT_APPROVED"
    with pytest.raises(client_agent.AgentCommandError) as traversal:
        client_agent._resolve_shared_file(config, "device-1:share_1:../secret")
    assert traversal.value.code == "PATH_TRAVERSAL_DENIED"
    with pytest.raises(client_agent.AgentCommandError) as missing:
        client_agent._resolve_shared_file(config, "device-1:share_1:missing.txt")
    assert missing.value.code == "SOURCE_MISSING"

    old_state = {"share_1": {"已删除.txt": "old"}}
    client_agent._scan_state_path(config_path).write_text(json.dumps(old_state), encoding="utf-8")
    monkeypatch.setattr(client_agent.time, "sleep", lambda _seconds: None)
    indexed, errors = client_agent.scan_and_upload_roots("http://host", "token", config, config_path)
    assert indexed >= 2 and errors == 0
    assert any(isinstance(payload, dict) and payload.get("removed_paths") == ["已删除.txt"] for _, _, payload in calls)

    item, signature = client_agent._remote_index_item(shared, shared / "通知.txt", "different", True)
    assert item["extracted_text"] == "中文正文" and signature
    directory_item, _ = client_agent._remote_index_item(shared, shared / "子目录", "", False)
    assert directory_item["is_directory"] is True
    assert client_agent._load_scan_state(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert client_agent._load_scan_state(broken) == {}


def test_open_shared_file_posix_uses_nofollow_for_every_path_component(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """模拟 Linux dir_fd 打开链，断言最终句柄不会绕过符号链接门禁。"""

    shared = tmp_path / "shared"
    shared.mkdir()
    resolved = shared / "子目录" / "通知.txt"
    opened: list[tuple[object, int | None]] = []
    closed: list[int] = []

    class _PosixOs:
        name = "posix"
        O_RDONLY = 1
        O_DIRECTORY = 2
        O_NOFOLLOW = 4

        @staticmethod
        def open(path, _flags, *, dir_fd=None):
            opened.append((path, dir_fd))
            return 10 + len(opened) - 1

        @staticmethod
        def close(fd: int) -> None:
            closed.append(fd)

        @staticmethod
        def fstat(_fd: int):
            return type("_FileInfo", (), {"st_mode": 0})()

        @staticmethod
        def fdopen(fd: int, _mode: str):
            assert fd == 12
            return io.BytesIO(b"safe")

    monkeypatch.setattr(client_agent, "os", _PosixOs)
    monkeypatch.setattr(client_agent, "_resolve_shared_file", lambda *_args: resolved)
    monkeypatch.setattr(
        client_agent,
        "_safe_shared_roots",
        lambda _config: [{"remote_key": "share_1", "local_path": str(shared)}],
    )
    monkeypatch.setattr(client_agent.stat, "S_ISREG", lambda _mode: True)

    with client_agent._open_shared_file(
        {"device_id": "device-1"},
        "device-1:share_1:子目录/通知.txt",
    ) as handle:
        assert handle.read() == b"safe"

    assert opened == [(shared, None), ("子目录", 10), ("通知.txt", 11)]
    assert closed == [10, 11]


def test_open_shared_file_posix_rejects_missing_root_and_non_regular_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    resolved = tmp_path / "resolved"
    monkeypatch.setattr(client_agent, "_resolve_shared_file", lambda *_args: resolved)

    class _UnusedPosixOs:
        name = "posix"
        O_RDONLY = 1
        O_DIRECTORY = 2
        O_NOFOLLOW = 4

    monkeypatch.setattr(client_agent, "os", _UnusedPosixOs)
    monkeypatch.setattr(client_agent, "_safe_shared_roots", lambda _config: [])
    with pytest.raises(client_agent.AgentCommandError) as missing_root:
        client_agent._open_shared_file({}, "device-1:share_1:通知.txt")
    assert missing_root.value.code == "ROOT_NOT_APPROVED"

    closed: list[int] = []

    class _NonRegularPosixOs(_UnusedPosixOs):
        @staticmethod
        def open(_path, _flags, *, dir_fd=None):
            return 21 if dir_fd is None else 22

        @staticmethod
        def close(fd: int) -> None:
            closed.append(fd)

        @staticmethod
        def fstat(_fd: int):
            return type("_FileInfo", (), {"st_mode": 0})()

        @staticmethod
        def fdopen(_fd: int, _mode: str):
            raise AssertionError("非普通文件不得转换为 Python 文件句柄")

    shared = tmp_path / "shared"
    monkeypatch.setattr(client_agent, "os", _NonRegularPosixOs)
    monkeypatch.setattr(
        client_agent,
        "_safe_shared_roots",
        lambda _config: [{"remote_key": "share_1", "local_path": str(shared)}],
    )
    monkeypatch.setattr(client_agent.stat, "S_ISREG", lambda _mode: False)
    with pytest.raises(client_agent.AgentCommandError) as non_regular:
        client_agent._open_shared_file({}, "device-1:share_1:通知.txt")
    assert non_regular.value.code == "SOURCE_MISSING"
    assert closed == [22, 21]


def test_agent_heartbeat_commands_certificate_and_ack(monkeypatch, tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    config = _approved_config(shared)
    config_path = tmp_path / "client.json"
    responses = iter(
        [
            _Response(status=200),
            _Response(json.dumps([{"id": "cmd-1"}]).encode()),
            _Response(status=200),
        ]
    )
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: next(responses))
    assert client_agent.send_device_heartbeat("http://host", "token", config)
    assert client_agent.poll_device_commands("http://host", "token") == [{"id": "cmd-1"}]
    assert client_agent.ack_device_command("http://host", "token", "cmd-1", {"ok": True})
    assert not client_agent.send_device_heartbeat("http://host", "token", {})

    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    assert not client_agent.send_device_heartbeat("http://host", "token", config)
    assert client_agent.poll_device_commands("http://host", "token") == []
    assert not client_agent.ack_device_command("http://host", "token", "cmd-1", {})

    with pytest.raises(client_agent.AgentCommandError, match="缺少编号"):
        client_agent.rotate_device_certificate("http://host", "token", {}, config_path)
    monkeypatch.setattr(
        client_agent,
        "_json_request",
        lambda *_a, **_k: {
            "certificate_pem": "certificate",
            "ca_certificate_pem": "ca",
            "agent_url": "https://host:18766",
        },
    )
    monkeypatch.setattr(client_agent, "configure_ssl_context", lambda _config: None)
    result = client_agent.rotate_device_certificate("http://host", "token", config, config_path)
    assert result["ok"] is True
    assert (tmp_path / "pki" / "device.key").is_file()


def test_transfer_upload_bundle_download_and_command_dispatch(monkeypatch, tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    source = shared / "source.bin"
    source.write_bytes(b"abcdefg")
    folder = shared / "folder"
    folder.mkdir()
    (folder / "inside.txt").write_text("inside", encoding="utf-8")
    receive = tmp_path / "receive"
    config = {**_approved_config(shared), "receive_dir": str(receive)}

    monkeypatch.setattr(
        client_agent,
        "get_transfer_status",
        lambda *_a, **_k: {"completed_chunks": [], "chunk_size": 4, "total_chunks": 2, "size_bytes": 7, "sha256": hashlib.sha256(b"abcdefg").hexdigest(), "name": "source.bin"},
    )
    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: {})
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: _Response(b'{"status":"uploading"}'))
    upload = client_agent.upload_transfer(
        "http://host",
        "token",
        {"transfer_id": "transfer-1", "remote_file_key": "device-1:share_1:source.bin", "size_bytes": 7},
        config,
    )
    assert upload["ok"] is True

    local_bundle = tmp_path / "local.bundle"
    local_bundle.write_bytes(b"abcdefg")
    client_agent._upload_local_path("http://host", "token", "transfer-1", local_bundle)
    prepared: list[Path] = []
    monkeypatch.setattr(client_agent, "_upload_local_path", lambda _h, _t, _i, path: prepared.append(path))
    bundle = client_agent.upload_bundle_transfer(
        "http://host",
        "token",
        {
            "transfer_id": "bundle-1",
            "items": [
                {"remote_file_key": "device-1:share_1:source.bin", "relative_path": "source.bin"},
                {"remote_file_key": "device-1:share_1:folder", "relative_path": "folder"},
            ],
            "max_bytes": 1024 * 1024,
        },
        config,
    )
    assert bundle["ok"] is True and prepared
    assert not prepared[0].exists()
    with pytest.raises(client_agent.AgentCommandError) as invalid_bundle:
        client_agent.upload_bundle_transfer("http://host", "token", {"transfer_id": ""}, config)
    assert invalid_bundle.value.code == "BUNDLE_ITEMS_INVALID"

    chunks = {0: b"abcd", 1: b"efg"}

    def download_open(request, _timeout):
        number = int(request.full_url.rsplit("/", 1)[1])
        content = chunks[number]
        return _Response(content, headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()})

    monkeypatch.setattr(client_agent, "_urlopen", download_open)
    downloaded = client_agent.download_transfer("http://host", "token", {"transfer_id": "transfer-1", "name": "source.bin"}, config)
    assert downloaded["ok"] is True
    assert (receive / "source.bin").read_bytes() == b"abcdefg"
    assert client_agent._non_overwriting_target(receive, "source.bin").name == "source (1).bin"
    with pytest.raises(client_agent.AgentCommandError):
        client_agent._non_overwriting_target(receive, "..")

    acknowledgements: list[dict[str, object]] = []
    monkeypatch.setattr(client_agent, "ack_device_command", lambda _h, _t, _i, result: acknowledgements.append(result) or True)
    monkeypatch.setattr(client_agent, "upload_transfer", lambda *_a, **_k: {"ok": True})
    assert client_agent.process_device_command("http://host", "token", {"id": "cmd-1", "type": "upload_file", "payload": {}}, config)
    assert client_agent.process_device_command("http://host", "token", {"id": "cmd-2", "type": "unknown", "payload": {}}, config)
    assert acknowledgements[-1]["error_code"] == "COMMAND_UNSUPPORTED"
    assert not client_agent.process_device_command("http://host", "token", {"id": "", "payload": {}}, config)

    monkeypatch.setattr(client_agent, "upload_transfer", lambda *_a, **_k: (_ for _ in ()).throw(client_agent.AgentCommandError("RETRY", "稍后", retryable=True)))
    assert not client_agent.process_device_command("http://host", "token", {"id": "cmd-3", "type": "upload_file", "payload": {}}, config)
    monkeypatch.setattr(client_agent, "upload_transfer", lambda *_a, **_k: (_ for _ in ()).throw(client_agent.AgentCommandError("DENIED", "拒绝")))
    assert client_agent.process_device_command("http://host", "token", {"id": "cmd-4", "type": "upload_file", "payload": {}}, config)
    assert acknowledgements[-1]["error_code"] == "DENIED"
