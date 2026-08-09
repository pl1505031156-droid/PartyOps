"""权限边界、终端伴随进程和异常分支测试。"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import client_agent
from app.client_agent import pull_backup, run

from .conftest import create_task


def login(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200
    return response.json()


def test_staff_cannot_admin_and_cannot_view_unrelated_restricted(
    client: TestClient, admin: dict, staff: dict
) -> None:
    restricted = create_task(
        client,
        admin["id"],
        title="仅主办可见的敏感事项",
        description="",
        sensitivity="restricted",
        materials=[],
        steps=[],
    )
    login(client, "staff")
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post(
        "/api/v1/templates",
        json={"name": "越权模板"},
    ).status_code == 403
    assert client.get(f"/api/v1/tasks/{restricted['id']}").status_code == 404
    login(client, "admin")


def test_login_failure_and_pairing_errors(client: TestClient, admin: dict) -> None:
    failed = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert failed.status_code == 401
    assert client.get(
        "/api/v1/backups/latest",
        headers={"X-PartyOps-Pairing": "invalid"},
    ).status_code == 401
    assert client.get(
        "/api/v1/backups/not-found/download",
        headers={"X-PartyOps-Pairing": "invalid"},
    ).status_code == 401
    invalid_upload = client.post(
        "/api/v1/admin/backups/verify",
        files={"file": ("bad.partyops-backup", b"not-a-zip", "application/zip")},
    )
    assert invalid_upload.status_code == 400
    assert client.post(
        "/api/v1/admin/backups/restore",
        params={"backup_id": "missing"},
    ).status_code == 404
    duplicate_user = client.post(
        "/api/v1/admin/users",
        json={
            "username": "staff",
            "display_name": "重复用户",
            "password": "PartyOps@2026",
            "role": "staff",
        },
    )
    assert duplicate_user.status_code == 409


class FakeResponse:
    def __init__(self, content: bytes, filename: str = "PartyOps-test.partyops-backup"):
        self._content = content
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_client_agent_pull_and_run_once(monkeypatch, tmp_path: Path) -> None:
    content = b"partyops-backup-content"
    monkeypatch.setattr(
        client_agent.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(content, "../safe.partyops-backup"),
    )
    monkeypatch.setattr(
        client_agent,
        "verify_local_backup",
        lambda _path: {"format": "partyops-backup"},
    )
    destination = tmp_path / "copies"
    pulled = pull_backup("http://host:18765/", "token", destination)
    assert pulled == destination / "safe.partyops-backup"
    assert pulled.read_bytes() == content
    checksum = pulled.with_suffix(".partyops-backup.sha256").read_text(encoding="utf-8")
    assert checksum.startswith(hashlib.sha256(content).hexdigest())

    config = tmp_path / "client.json"
    config.write_text(
        json.dumps(
            {
                "host_url": "http://host:18765",
                "pairing_token": "token",
                "backup_dir": str(destination),
                "open_browser": False,
            }
        ),
        encoding="utf-8",
    )
    assert run(config, once=True) == 0
    assert run(tmp_path / "missing.json", once=True) == 2


def test_client_agent_unreachable(monkeypatch, tmp_path: Path) -> None:
    def unavailable(*_args, **_kwargs):
        raise client_agent.urllib.error.URLError("offline")

    monkeypatch.setattr(client_agent.urllib.request, "urlopen", unavailable)
    assert pull_backup("http://offline", "token", tmp_path) is None
    config = tmp_path / "client-offline.json"
    config.write_text(
        json.dumps(
            {
                "host_url": "http://offline",
                "pairing_token": "token",
                "backup_dir": str(tmp_path / "copies"),
                "open_browser": False,
            }
        ),
        encoding="utf-8",
    )
    assert run(config, once=True) == 1


def test_device_enrollment_normalizes_copy_and_explains_invalid_code(monkeypatch) -> None:
    code = f"{'A' * 24}.{'b' * 64}"
    captured: dict[str, object] = {}

    def successful_request(_url: str, **kwargs):
        captured.update(kwargs["payload"])
        return {"device_token": "device-token"}

    monkeypatch.setattr(client_agent, "_json_request", successful_request)
    result = client_agent.enroll_device(
        "http://host:18765",
        f"  {code[:30]}\n{code[30:]}  ",
        "协同电脑",
    )
    assert result["device_token"] == "device-token"
    assert captured["code"] == code
    result = client_agent.enroll_device(
        "http://host:18765",
        f"一次性入网码（89 个字符）：\u200b{code[:30]}\n{code[30:]}\ufeff",
        "复制标签协同电脑",
    )
    assert result["device_token"] == "device-token"
    assert captured["code"] == code

    with pytest.raises(ValueError, match="复制完整入网码"):
        client_agent.normalize_enrollment_code(code[:-12])

    body = io.BytesIO(
        json.dumps(
            {
                "code": "ENROLLMENT_INVALID",
                "detail": "入网码无效或已过期",
            }
        ).encode("utf-8")
    )
    error = urllib.error.HTTPError(
        "http://host/api/v1/devices/enroll",
        400,
        "Bad Request",
        {},
        body,
    )
    assert "复制完整入网码" in str(client_agent.enrollment_http_error(error))

    format_error = urllib.error.HTTPError(
        "http://host/api/v1/devices/enroll",
        400,
        "Bad Request",
        {},
        io.BytesIO(
            json.dumps(
                {
                    "code": "ENROLLMENT_CODE_FORMAT_INVALID",
                    "detail": "入网码格式不完整",
                }
            ).encode("utf-8")
        ),
    )
    assert "复制完整入网码" in str(client_agent.enrollment_http_error(format_error))


def test_first_heartbeat_reports_stale_database_identity(
    monkeypatch, tmp_path: Path
) -> None:
    """旧数据库签发的令牌不能再被误报为普通端口故障。"""

    error = urllib.error.HTTPError(
        "https://host:18766/api/v1/devices/heartbeat",
        401,
        "Unauthorized",
        {},
        io.BytesIO(b"{}"),
    )
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    config = {
        "device_token": "stale-token",
        "backup_dir": str(tmp_path),
    }
    assert (
        client_agent.send_device_heartbeat(
            "https://host:18766",
            "stale-token",
            config,
        )
        is False
    )
    with pytest.raises(ValueError, match="不属于主机正在使用的数据库"):
        client_agent.send_device_heartbeat(
            "https://host:18766",
            "stale-token",
            config,
            strict_identity=True,
        )


def test_device_enrollment_retries_same_request_and_recovers_pending_result(
    monkeypatch, tmp_path: Path
) -> None:
    code = f"{'R' * 24}.{'c3' * 32}"
    payloads: list[dict[str, object]] = []

    def interrupted_then_success(_url: str, **kwargs):
        payloads.append(dict(kwargs["payload"]))
        if len(payloads) == 1:
            raise urllib.error.URLError("response lost")
        return {
            "device_id": "device-1",
            "device_token": "device-token",
            "certificate_pem": "certificate",
            "ca_certificate_pem": "ca",
        }

    monkeypatch.setattr(client_agent, "_json_request", interrupted_then_success)
    monkeypatch.setattr(client_agent.time, "sleep", lambda _seconds: None)
    pending = tmp_path / "pending-enrollment.json"
    first = client_agent.enroll_device(
        "http://host:18765",
        code,
        "协同电脑",
        pending_path=pending,
    )
    assert first["device_token"] == "device-token"
    assert len(payloads) == 2
    assert payloads[0]["csr_pem"] == payloads[1]["csr_pem"]
    assert pending.is_file()

    def must_not_call(*_args, **_kwargs):
        raise AssertionError("已有成功响应时不应重复消费入网码")

    monkeypatch.setattr(client_agent, "_json_request", must_not_call)
    recovered = client_agent.enroll_device(
        "http://host:18765",
        code,
        "协同电脑",
        pending_path=pending,
    )
    assert recovered["device_id"] == "device-1"
    assert recovered["_private_key_pem"] == first["_private_key_pem"]
    replacement_payloads: list[dict[str, object]] = []

    def enroll_with_new_code(_url: str, **kwargs):
        replacement_payloads.append(dict(kwargs["payload"]))
        return {
            "device_id": "device-2",
            "device_token": "replacement-token",
            "certificate_pem": "replacement-certificate",
            "ca_certificate_pem": "replacement-ca",
        }

    monkeypatch.setattr(client_agent, "_json_request", enroll_with_new_code)
    new_code = f"{'S' * 24}.{'d4' * 32}"
    recovered_with_new_code = client_agent.enroll_device(
        "http://host:18765",
        new_code,
        "协同电脑",
        pending_path=pending,
    )
    assert recovered_with_new_code["device_id"] == "device-2"
    assert recovered_with_new_code["device_token"] == "replacement-token"
    assert len(replacement_payloads) == 1
    assert replacement_payloads[0]["code"] == new_code

    already_completed = urllib.error.HTTPError(
        "http://host/api/v1/devices/enroll",
        409,
        "Conflict",
        {},
        io.BytesIO(
            json.dumps(
                {
                    "code": "ENROLLMENT_ALREADY_COMPLETED",
                    "detail": "主机已创建设备，但终端未完成配置",
                }
            ).encode("utf-8")
        ),
    )
    assert "终端未完成配置" in str(
        client_agent.enrollment_http_error(already_completed)
    )
