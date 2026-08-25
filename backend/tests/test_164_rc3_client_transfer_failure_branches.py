"""协同文件中转的断点、哈希、网络与压缩边界分支。"""

from __future__ import annotations

import hashlib
import urllib.error
from pathlib import Path

import pytest
from app import client_agent
from app.client_agent import AgentCommandError


class _Response:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


def _config(shared: Path, receive: Path) -> dict[str, object]:
    return {
        "device_id": "device-1",
        "receive_dir": str(receive),
        "shared_roots": [
            {
                "local_path": str(shared),
                "remote_key": "root-1",
                "approval_status": "approved",
            }
        ],
    }


def _status(payload: bytes, *, digest: str = "") -> dict[str, object]:
    return {
        "completed_chunks": [],
        "chunk_size": 4,
        "total_chunks": 1,
        "size_bytes": len(payload),
        "sha256": digest or hashlib.sha256(payload).hexdigest(),
        "name": "received.bin",
    }


def test_upload_transfer_source_network_hash_and_resume_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    source = shared / "source.bin"
    source.write_bytes(b"data")
    config = _config(shared, tmp_path / "receive")
    payload = {
        "transfer_id": "upload-a",
        "remote_file_key": "device-1:root-1:source.bin",
        "size_bytes": 5,
    }
    with pytest.raises(AgentCommandError, match="大小已变化"):
        client_agent.upload_transfer("http://host", "token", payload, config)

    payload["size_bytes"] = 4
    payload["modified_at"] = "2000-01-01T00:00:00+00:00"
    with pytest.raises(AgentCommandError, match="修改时间"):
        client_agent.upload_transfer("http://host", "token", payload, config)
    payload.pop("modified_at")
    monkeypatch.setattr(
        client_agent,
        "get_transfer_status",
        lambda *_args: {**_status(b"data"), "completed_chunks": [0, "bad"]},
    )
    finalized: list[str] = []
    monkeypatch.setattr(
        client_agent,
        "_json_request",
        lambda url, **_kwargs: finalized.append(url) or {},
    )
    assert client_agent.upload_transfer("http://host", "token", payload, config)["ok"]
    assert finalized

    monkeypatch.setattr(
        client_agent, "get_transfer_status", lambda *_args: _status(b"data")
    )
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(AgentCommandError) as interrupted:
        client_agent.upload_transfer("http://host", "token", payload, config)
    assert (
        interrupted.value.code == "NETWORK_INTERRUPTED" and interrupted.value.retryable
    )

    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args: _Response(b'{"status":"failed","error_code":"HASH_BAD"}'),
    )
    with pytest.raises(AgentCommandError) as failed:
        client_agent.upload_transfer("http://host", "token", payload, config)
    assert failed.value.code == "HASH_BAD"


def test_generated_bundle_invalid_items_duplicates_and_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    source = shared / "source.txt"
    source.write_text("content", encoding="utf-8")
    config = _config(shared, tmp_path / "receive")
    common = {"transfer_id": "bundle-a"}
    for items in ("bad", [], ["bad"]):
        with pytest.raises(AgentCommandError):
            client_agent.upload_bundle_transfer(
                "http://host",
                "token",
                {**common, "items": items},
                config,
            )
    with pytest.raises(AgentCommandError, match="相对路径"):
        client_agent.upload_bundle_transfer(
            "http://host",
            "token",
            {
                **common,
                "items": [
                    {
                        "remote_file_key": "device-1:root-1:source.txt",
                        "relative_path": "../escape",
                    }
                ],
            },
            config,
        )
    with pytest.raises(AgentCommandError, match="超过20GB"):
        client_agent.upload_bundle_transfer(
            "http://host",
            "token",
            {
                **common,
                "max_bytes": 1,
                "items": [
                    {
                        "remote_file_key": "device-1:root-1:source.txt",
                        "relative_path": "source.txt",
                    }
                ],
            },
            config,
        )

    prepared: list[str] = []
    monkeypatch.setattr(
        client_agent,
        "_json_request",
        lambda url, **_kwargs: prepared.append(url) or {},
    )
    monkeypatch.setattr(
        client_agent,
        "_upload_local_path",
        lambda *_args: None,
    )
    result = client_agent.upload_bundle_transfer(
        "http://host",
        "token",
        {
            **common,
            "max_bytes": 1024,
            "items": [
                {
                    "remote_file_key": "device-1:root-1:source.txt",
                    "relative_path": "same.txt",
                },
                {
                    "remote_file_key": "device-1:root-1:source.txt",
                    "relative_path": "same.txt",
                },
            ],
        },
        config,
    )
    assert result["ok"] and prepared


@pytest.mark.parametrize(
    ("opener", "expected_code"),
    [
        (
            lambda request, _timeout: (_ for _ in ()).throw(
                urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)
            ),
            "CHUNK_NOT_READY",
        ),
        (
            lambda request, _timeout: (_ for _ in ()).throw(
                urllib.error.URLError("offline")
            ),
            "NETWORK_INTERRUPTED",
        ),
        (lambda _request, _timeout: _Response(b"12345"), "HASH_MISMATCH"),
        (
            lambda _request, _timeout: _Response(b"data", {"X-Chunk-SHA256": "0" * 64}),
            "HASH_MISMATCH",
        ),
    ],
)
def test_download_transfer_chunk_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    opener,
    expected_code: str,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    config = _config(shared, tmp_path / "receive")
    monkeypatch.setattr(
        client_agent, "get_transfer_status", lambda *_args: _status(b"data")
    )
    monkeypatch.setattr(client_agent, "_urlopen", opener)
    with pytest.raises(AgentCommandError) as raised:
        client_agent.download_transfer(
            "http://host",
            "token",
            {"transfer_id": f"download-{expected_code.lower()}"},
            config,
        )
    assert raised.value.code == expected_code


def test_download_resume_size_and_final_hash_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    receive = tmp_path / "receive"
    config = _config(shared, receive)

    monkeypatch.setattr(
        client_agent, "get_transfer_status", lambda *_args: _status(b"data")
    )
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_args: _Response(b"dat"))
    with pytest.raises(AgentCommandError, match="大小"):
        client_agent.download_transfer(
            "http://host",
            "token",
            {"transfer_id": "download-short"},
            config,
        )

    wrong = _status(b"data", digest="0" * 64)
    monkeypatch.setattr(client_agent, "get_transfer_status", lambda *_args: wrong)
    monkeypatch.setattr(client_agent, "_urlopen", lambda *_args: _Response(b"data"))
    with pytest.raises(AgentCommandError, match="整体校验"):
        client_agent.download_transfer(
            "http://host",
            "token",
            {"transfer_id": "download-hash"},
            config,
        )
    assert not (receive / ".partyops-transfers" / "download-hash.part").exists()

    status = _status(b"data")
    monkeypatch.setattr(client_agent, "get_transfer_status", lambda *_args: status)
    staging = receive / ".partyops-transfers"
    staging.mkdir(parents=True, exist_ok=True)
    part = staging / "download-resume.part"
    part.write_bytes(b"data")
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应重复下载")),
    )
    result = client_agent.download_transfer(
        "http://host",
        "token",
        {"transfer_id": "download-resume", "name": "received.bin"},
        config,
    )
    assert result["ok"] and (receive / "received.bin").read_bytes() == b"data"


def test_transfer_status_and_network_migration_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(client_agent, "_json_request", lambda *_args, **_kwargs: [])
    with pytest.raises(AgentCommandError, match="无效传输状态"):
        client_agent.get_transfer_status("http://host", "token", "transfer-a")

    acknowledged: list[dict[str, object]] = []
    monkeypatch.setattr(
        client_agent,
        "apply_network_migration_command",
        lambda *_args: {"ok": True},
    )
    monkeypatch.setattr(
        client_agent,
        "ack_device_command",
        lambda _host, _token, _id, result: acknowledged.append(result) or True,
    )
    monkeypatch.setattr(client_agent, "_restart_agent_after_update", lambda _path: None)
    assert client_agent.process_device_command(
        "http://host",
        "token",
        {"id": "cmd-network", "type": "network_migration", "payload": {}},
        {},
        tmp_path / "client.json",
    )
    assert acknowledged[-1]["ok"] is True
