"""协同终端剩余容错分支与常驻循环的发布回归。"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import client_agent


class _Response:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.offset = 0

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


def test_transfer_geometry_url_and_migration_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(client_agent.AgentCommandError, match="传输参数无效"):
        client_agent._validated_transfer_geometry(
            {"chunk_size": "bad", "total_chunks": 1, "size_bytes": 1}
        )
    assert client_agent._validated_transfer_geometry(
        {"chunk_size": 4, "total_chunks": 0, "size_bytes": 0}
    ) == (4, 0, 0)
    with pytest.raises(client_agent.AgentCommandError, match="HTTP/HTTPS"):
        client_agent._urlopen("file:///tmp/partyops", 1)

    config: dict[str, object] = {
        "network_migration": {
            "expires_at": "2099-01-01T00:00:00",
            "previous_host_url": "http://old-host",
        }
    }
    monkeypatch.setattr(client_agent, "host_reachable", lambda url: url == "http://new-host")
    assert client_agent._select_migration_endpoints(
        config, "http://new-host", "http://new-agent"
    ) == ("http://new-host", "http://new-agent")
    assert config["network_migration"]["state"] == "new_address_ready"  # type: ignore[index]


def test_browser_handoff_symlink_and_enrollment_cache_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    destination = tmp_path / "client-browser.url"
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self == destination or original_is_symlink(self),
    )
    with pytest.raises(ValueError, match="符号链接"):
        client_agent.write_browser_launch_url(
            config_path, destination, "https://host.example/client"
        )

    pending = tmp_path / "pending.json"
    pending.write_text("{broken", encoding="utf-8")
    code = f"{'R' * 24}.{'a1' * 32}"
    monkeypatch.setattr(
        client_agent,
        "_json_request",
        lambda *_args, **_kwargs: {"device_token": "token"},
    )
    result = client_agent.enroll_device(
        "http://host:18765", code, "协同电脑", pending_path=pending
    )
    assert result["device_token"] == "token"

    pending_payload = json.loads(pending.read_text(encoding="utf-8"))
    pending_payload.pop("result")
    pending_payload["private_key_pem"] = "not-a-private-key"
    pending.write_text(json.dumps(pending_payload), encoding="utf-8")
    second = client_agent.enroll_device(
        "http://host:18765", code, "协同电脑", pending_path=pending
    )
    assert second["device_token"] == "token"


@pytest.mark.parametrize(
    ("headers", "free_bytes", "message"),
    [
        ({}, 0, "可用空间不足"),
        ({"Content-Length": "bad"}, 10**12, "长度无效"),
        ({"Content-Length": "-1"}, 10**12, "下载上限"),
        ({"Content-Length": "100"}, 1, "可用空间不足"),
    ],
)
def test_backup_declared_length_and_space_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    headers: dict[str, str],
    free_bytes: int,
    message: str,
) -> None:
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args, **_kwargs: _Response(b"invalid", headers=headers),
    )
    monkeypatch.setattr(
        client_agent.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(
            free=free_bytes + client_agent.BACKUP_FREE_SPACE_RESERVE_BYTES
        ),
    )
    with pytest.raises(ValueError, match=message):
        client_agent.pull_backup("http://host", "token", tmp_path / "backups")


def test_heartbeat_loop_stops_on_device_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = urllib.error.HTTPError("http://host", 403, "denied", {}, None)
    monkeypatch.setattr(
        client_agent,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    class StopEvent:
        stopped = False

        def wait(self, _seconds: int) -> bool:
            return self.stopped

        def set(self) -> None:
            self.stopped = True

    stop = StopEvent()
    client_agent._heartbeat_loop(stop, "http://host", "token", {})  # type: ignore[arg-type]
    assert stop.stopped


def test_agent_device_runtime_failure_and_background_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "http://host:18765",
                "agent_url": "http://host:18766",
                "device_token": "device-token",
                "device_id": "device-1",
                "backup_dir": str(tmp_path / "backups"),
                "open_browser": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(client_agent, "configure_agent_logging", lambda _path: tmp_path / "log")
    monkeypatch.setattr(client_agent, "configure_ssl_context", lambda _config: None)
    monkeypatch.setattr(client_agent, "send_device_heartbeat", lambda *_a, **_k: True)
    monkeypatch.setattr(client_agent, "sync_shared_roots", lambda *_a, **_k: [])
    monkeypatch.setattr(client_agent, "poll_device_commands", lambda *_a, **_k: [])
    monkeypatch.setattr(client_agent, "pull_backup", lambda *_a, **_k: None)
    monkeypatch.setattr(client_agent, "poll_desktop_notifications", lambda *_a, **_k: False)
    monkeypatch.setattr(client_agent, "_safe_shared_roots", lambda _config: [])
    monkeypatch.setattr(client_agent.time, "monotonic", lambda: 0.0)

    class FakeThread:
        started = False
        joined = False

        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            self.started = True

        def join(self, timeout: int) -> None:
            assert timeout == 2
            self.joined = True

    thread = FakeThread()
    monkeypatch.setattr(client_agent.threading, "Thread", lambda **_kwargs: thread)

    from app import official_format_service

    monkeypatch.setattr(
        official_format_service.OfficialFormatLocalService,
        "start",
        lambda _self: (_ for _ in ()).throw(OSError("port denied")),
    )
    monkeypatch.setattr(
        client_agent.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop test loop")),
    )
    with pytest.raises(RuntimeError, match="stop test loop"):
        client_agent.run(config_path, once=False, open_browser=False)
    assert thread.started and thread.joined

    monkeypatch.setattr(
        client_agent,
        "send_device_heartbeat",
        lambda *_a, **_k: (_ for _ in ()).throw(
            urllib.error.HTTPError("http://host", 403, "denied", {}, None)
        ),
    )
    assert (
        client_agent.run(config_path, once=False, open_browser=False)
        == client_agent.AUTHENTICATION_EXIT_CODE
    )
