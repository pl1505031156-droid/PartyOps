"""协同 Agent 的配置、共享、备份与通知边界分支回归。"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import urllib.error
import zipfile
from pathlib import Path

import pytest

from app import client_agent


class _Response:
    def __init__(self, payload: bytes = b"{}", *, status: int = 200, headers=None) -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]


def _http_error(code: int, payload: object) -> urllib.error.HTTPError:
    body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
    return urllib.error.HTTPError("http://host", code, "error", {}, io.BytesIO(body))


def _write_backup(path: Path, *, item_path: str = "database/partyops.db", content: bytes = b"ok", size=None, digest=None, fmt="partyops-backup") -> None:
    manifest = {
        "format": fmt,
        "files": [{
            "path": item_path,
            "size": len(content) if size is None else size,
            "sha256": hashlib.sha256(content).hexdigest() if digest is None else digest,
        }],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(item_path, content)


def test_backup_filename_and_config_validation_matrix(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not-a-zip")
    with pytest.raises(ValueError, match="有效备份包"):
        client_agent.verify_local_backup(invalid)

    wrong_format = tmp_path / "wrong-format.zip"
    _write_backup(wrong_format, fmt="other")
    with pytest.raises(ValueError, match="格式不匹配"):
        client_agent.verify_local_backup(wrong_format)

    illegal = tmp_path / "illegal.zip"
    _write_backup(illegal, item_path="../data.txt")
    with pytest.raises(ValueError, match="非法路径"):
        client_agent.verify_local_backup(illegal)

    wrong_size = tmp_path / "wrong-size.zip"
    _write_backup(wrong_size, size=99)
    with pytest.raises(ValueError, match="大小不匹配"):
        client_agent.verify_local_backup(wrong_size)

    wrong_hash = tmp_path / "wrong-hash.zip"
    _write_backup(wrong_hash, digest="0" * 64)
    with pytest.raises(ValueError, match="哈希不匹配"):
        client_agent.verify_local_backup(wrong_hash)

    valid = tmp_path / "valid.zip"
    _write_backup(valid)
    assert client_agent.verify_local_backup(valid)["format"] == "partyops-backup"

    assert client_agent._response_filename("attachment; filename*=UTF-8''%E5%A4%87%E4%BB%BD.zip") == "备份.zip"
    assert client_agent._response_filename('attachment; filename="daily.zip"') == "daily.zip"
    assert client_agent._response_filename("attachment; invalid") == "PartyOps-latest.partyops-backup"

    for host_url in ("", "ftp://host", "http://user:pass@host", "http:///missing"):
        with pytest.raises(ValueError, match="host_url"):
            client_agent.validate_config({"host_url": host_url, "device_token": "token"})
    with pytest.raises(ValueError, match="配对令牌"):
        client_agent.validate_config({"host_url": "http://host"})
    host, token, destination = client_agent.validate_config({
        "host_url": "http://host/",
        "pairing_token": " old-token ",
        "backup_dir": str(tmp_path / "backup"),
    })
    assert (host, token, destination.name) == ("http://host", "old-token", "backup")


def test_ssl_metadata_request_and_enrollment_errors(monkeypatch, tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("ca", encoding="utf-8")

    class Context:
        def __init__(self) -> None:
            self.loaded = False

        def load_cert_chain(self, _cert, _key) -> None:
            self.loaded = True

    contexts: list[Context] = []
    monkeypatch.setattr(
        client_agent.ssl,
        "create_default_context",
        lambda **_kwargs: contexts.append(Context()) or contexts[-1],
    )
    client_agent.configure_ssl_context({"ca_file": str(ca)})
    assert client_agent._ACTIVE_SSL_CONTEXT is contexts[-1] and not contexts[-1].loaded
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    client_agent.configure_ssl_context({"ca_file": str(ca), "certificate_file": str(cert), "key_file": str(key)})
    assert contexts[-1].loaded

    monkeypatch.setattr(client_agent.shutil, "disk_usage", lambda _path: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(client_agent.platform, "machine", lambda: "mips-special-architecture")
    monkeypatch.setattr(client_agent.sys, "platform", "linux")
    metadata = client_agent.device_metadata()
    assert metadata["architecture"] == "mips-special-arc" and metadata["platform"] == "uos" and metadata["disk_free_bytes"] == 0
    monkeypatch.setattr(client_agent.sys, "platform", "darwin")
    assert client_agent.device_metadata()["platform"] == "macos"

    captured = []
    monkeypatch.setattr(client_agent, "_urlopen", lambda request, timeout: captured.append((request, timeout)) or _Response(b"[]"))
    assert client_agent._json_request("http://host/plain") == []
    assert not captured[-1][0].headers

    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: [])
    assert client_agent.create_browser_launch_url("http://host/", "http://agent", "token") == "http://host"
    with pytest.raises(ValueError, match="入网码不完整"):
        client_agent.normalize_enrollment_code("bad-code")

    cases = [
        ({"code": "ENROLLMENT_INVALID"}, "无效或已过期"),
        ({"code": "ENROLLMENT_ALREADY_COMPLETED", "detail": "已经完成"}, "已经完成"),
        ({"code": "ENROLLMENT_RECOVERY_UNAVAILABLE"}, "已经在主机创建"),
        ({"code": "DEVICE_NAME_EXISTS", "title": "名称重复"}, "名称重复"),
        ({"code": "OTHER"}, "HTTP 409"),
    ]
    for payload, message in cases:
        assert message in str(client_agent.enrollment_http_error(_http_error(409, payload)))
    assert "HTTP 500" in str(client_agent.enrollment_http_error(_http_error(500, b"not-json")))


def test_shared_root_validation_and_sync_matrix(monkeypatch, tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    nested = shared / "nested"
    nested.mkdir()
    file_path = tmp_path / "plain.txt"
    file_path.write_text("x", encoding="utf-8")
    config_path = tmp_path / "client.json"

    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: [])
    with pytest.raises(ValueError, match="共享目录编号"):
        client_agent.register_shared_root("http://host", "token", "资料", "root_1")
    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: {})
    with pytest.raises(ValueError, match="共享目录列表"):
        client_agent.list_registered_shared_roots("http://host", "token")

    assert client_agent._safe_shared_roots({"shared_roots": "bad"}) == []
    roots = client_agent._safe_shared_roots({"shared_roots": [
        "bad",
        {"local_path": str(tmp_path / "missing"), "remote_key": "root_1"},
        {"local_path": str(file_path), "remote_key": "root_1"},
        {"local_path": str(shared), "remote_key": "含空格"},
        {"local_path": str(shared), "remote_key": "root_1"},
    ]})
    assert len(roots) == 1 and roots[0]["remote_key"] == "root_1"

    with pytest.raises(ValueError, match="真实文件夹"):
        client_agent.add_shared_root("http://host", "token", {}, config_path, file_path)
    with pytest.raises(ValueError, match="重复或相互嵌套"):
        client_agent.add_shared_root(
            "http://host",
            "token",
            {"shared_roots": [{"local_path": str(shared), "remote_key": "root_1"}]},
            config_path,
            nested,
        )

    monkeypatch.setattr(client_agent, "register_shared_root", lambda *_a, **_k: {"id": "new-root"})
    config: dict[str, object] = {}
    root = client_agent.add_shared_root("http://host", "token", config, config_path, shared)
    assert root["name"] == "shared" and root["approval_status"] == "pending" and root["enabled"] is False

    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: "not-a-dict")
    with pytest.raises(ValueError, match="未找到"):
        client_agent.rename_shared_root("http://host", "token", {}, config_path, "missing", "新名称")
    renamed = client_agent.rename_shared_root("http://host", "token", config, config_path, "new-root", "新名称")
    assert renamed == {"id": "new-root", "name": "新名称"}

    monkeypatch.setattr(client_agent, "list_registered_shared_roots", lambda *_a: [{"id": "other"}])
    unchanged = client_agent.refresh_shared_root_statuses("http://host", "token", config, config_path)
    assert unchanged[0]["name"] == "新名称"
    writes = []
    monkeypatch.setattr(client_agent, "_save_config", lambda *_a: writes.append(True))
    approved = {"shared_roots": [{**unchanged[0], "approval_status": "approved"}]}
    assert client_agent.sync_shared_roots("http://host", "token", approved, config_path)[0]["root_id"] == "new-root"
    assert not writes


def test_command_notification_and_network_fallbacks(monkeypatch, tmp_path: Path) -> None:
    assert client_agent._agent_headers("token") == {"X-PartyOps-Pairing": "token"}
    assert client_agent._agent_headers("token", True) == {"X-PartyOps-Device-Token": "token"}

    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: _Response(status=204))
    assert not client_agent.host_reachable("http://host")
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()))
    assert not client_agent.host_reachable("http://host")

    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: _Response(b'{"unread_count":-2,"revision":123}'))
    assert client_agent.fetch_notification_summary("http://host", "token") == {"unread_count": 0, "revision": "123"}
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_a, **_k: _Response(b"{"))
    assert client_agent.fetch_notification_summary("http://host", "token") is None

    monkeypatch.setattr(client_agent.shutil, "which", lambda _name: None)
    assert not client_agent.show_desktop_notification(1)
    monkeypatch.setattr(client_agent.shutil, "which", lambda _name: "notify-send")
    assert not client_agent.show_desktop_notification(0)
    monkeypatch.setattr(client_agent.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0))
    assert client_agent.show_desktop_notification(2)
    monkeypatch.setattr(client_agent.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(OSError("missing")))
    assert not client_agent.show_desktop_notification(2)

    monkeypatch.setattr(client_agent, "fetch_notification_summary", lambda *_a, **_k: None)
    assert not client_agent.poll_desktop_notifications("http://host", "token", tmp_path)
    for summary in (
        {"revision": "", "unread_count": 2},
        {"revision": "r1", "unread_count": 0},
    ):
        monkeypatch.setattr(client_agent, "fetch_notification_summary", lambda *_a, value=summary, **_k: value)
        assert not client_agent.poll_desktop_notifications("http://host", "token", tmp_path)
    monkeypatch.setattr(client_agent, "fetch_notification_summary", lambda *_a, **_k: {"revision": "r1", "unread_count": 2})
    monkeypatch.setattr(client_agent, "show_desktop_notification", lambda _count: False)
    assert not client_agent.poll_desktop_notifications("http://host", "token", tmp_path)
    monkeypatch.setattr(client_agent, "show_desktop_notification", lambda _count: True)
    assert client_agent.poll_desktop_notifications("http://host", "token", tmp_path)
    assert not client_agent.poll_desktop_notifications("http://host", "token", tmp_path)

    acknowledgements = []
    monkeypatch.setattr(client_agent, "ack_device_command", lambda *_a, **_k: acknowledgements.append(_a[-1]) or True)
    monkeypatch.setattr(client_agent, "upload_bundle_transfer", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(client_agent, "download_transfer", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(client_agent, "apply_update_command", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(client_agent, "rotate_device_certificate", lambda *_a, **_k: {"ok": True})
    restarted = []
    monkeypatch.setattr(client_agent, "_restart_agent_after_update", lambda path: restarted.append(path))
    for index, command_type in enumerate(("upload_bundle", "download_file", "rotate_certificate"), start=1):
        assert client_agent.process_device_command("http://host", "token", {"id": f"c{index}", "type": command_type, "payload": {}}, {}, tmp_path / "client.json")
    assert client_agent.process_device_command("http://host", "token", {"id": "c4", "type": "apply_update", "payload": {}}, {}, tmp_path / "client.json")
    assert restarted == [tmp_path / "client.json"]
    monkeypatch.setattr(client_agent, "download_transfer", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")))
    assert client_agent.process_device_command("http://host", "token", {"id": "c5", "type": "download_file", "payload": {}}, {})
    assert acknowledgements[-1]["error_code"] == "AGENT_EXECUTION_FAILED"
