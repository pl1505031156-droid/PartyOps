"""协同入网、重新授权与主机文件交付的端到端分支回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import __version__ as APP_VERSION
from app.problems import ProblemException
from app.routers import fleet
from app.schemas import DeviceRemoteRootCreate


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def _device_token(client: TestClient) -> tuple[str, str]:
    devices = client.get("/api/v1/admin/devices").json()
    active = next((item for item in devices if item["active"]), None)
    if active:
        rotated = client.post(f"/api/v1/admin/devices/{active['id']}/rotate-token")
        assert rotated.status_code == 200, rotated.text
        return active["id"], rotated.json()["device_token"]

    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "rc.3 主机下载验证终端"},
    )
    assert enrollment.status_code == 201, enrollment.text
    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": enrollment.json()["code"],
            "name": "rc.3 主机下载验证终端",
            "architecture": "amd64",
            "platform": "windows",
            "kernel": "Windows 11",
            "app_version": APP_VERSION,
            "agent_version": APP_VERSION,
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    return enrolled.json()["device_id"], enrolled.json()["device_token"]


def test_host_files_deliver_to_current_device_and_browser_zip(
    client: TestClient,
    admin: dict,
    tmp_path: Path,
) -> None:
    _login_admin(client)
    _device_id, token = _device_token(client)
    device_headers = {"X-PartyOps-Device-Token": token}
    heartbeat = client.post(
        "/api/v1/devices/heartbeat",
        headers=device_headers,
        json={
            "architecture": "amd64",
            "platform": "windows",
            "kernel": "Windows 11",
            "app_version": APP_VERSION,
            "agent_version": APP_VERSION,
            "protocol_version": 2,
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    browser_token = client.post(
        "/api/v1/devices/browser-token", headers=device_headers
    )
    assert browser_token.status_code == 200, browser_token.text
    launched = client.get(
        "/device-launch",
        params={"token": browser_token.json()["token"]},
        follow_redirects=False,
    )
    assert launched.status_code == 303

    root_path = tmp_path / "host-share"
    folder = root_path / "会议材料"
    folder.mkdir(parents=True)
    first = folder / "议程.txt"
    second = folder / "纪要.txt"
    first.write_text("议程", encoding="utf-8")
    second.write_text("纪要", encoding="utf-8")
    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "主机交付验证", "absolute_path": str(root_path.resolve())},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    scanned = client.post(f"/api/v1/workspace/roots/{root['id']}/scan-now")
    assert scanned.status_code == 200, scanned.text
    files = [
        client.get(
            "/api/v1/workspace/search",
            params={"root_id": root["id"], "keyword": name},
        ).json()[0]
        for name in ("议程.txt", "纪要.txt")
    ]
    directory = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "会议材料"},
    ).json()[0]

    delivered = client.post(
        "/api/v1/workspace/downloads",
        json={
            "item_ids": [files[0]["id"]],
            "bundle_mode": "single",
            "delivery": "current_device",
        },
    )
    assert delivered.status_code == 201, delivered.text
    assert delivered.json()["delivery"] == "current_device"
    assert delivered.json()["content_url"] == ""

    selected_zip = client.post(
        "/api/v1/workspace/downloads",
        json={
            "item_ids": [item["id"] for item in files],
            "bundle_mode": "selection_zip",
            "delivery": "browser",
        },
    )
    assert selected_zip.status_code == 201, selected_zip.text
    client.cookies.delete("partyops_device_context")
    zipped = client.get(selected_zip.json()["content_url"])
    assert zipped.status_code == 200, zipped.text
    assert zipped.content.startswith(b"PK")

    folder_zip = client.post(
        "/api/v1/workspace/downloads",
        json={
            "item_ids": [directory["id"]],
            "bundle_mode": "folder_zip",
            "delivery": "browser",
        },
    )
    assert folder_zip.status_code == 201, folder_zip.text
    assert client.get(folder_zip.json()["content_url"]).content.startswith(b"PK")


def test_enrollment_reauthorization_and_local_share_denial_branches(
    client: TestClient,
    admin: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _login_admin(client)
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "无效码验证终端"},
    )
    assert enrollment.status_code == 201, enrollment.text
    valid_code = enrollment.json()["code"]
    corrupted_code = ("A" if valid_code[0] != "A" else "B") + valid_code[1:]
    invalid = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": corrupted_code,
            "name": "无效终端",
            "architecture": "amd64",
            "platform": "windows",
            "kernel": "Windows",
            "app_version": APP_VERSION,
            "agent_version": APP_VERSION,
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "ENROLLMENT_INVALID"

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    missing_db = SimpleNamespace(get=lambda *_args: None)
    with pytest.raises(ProblemException) as missing:
        fleet.reauthorize_client_agent(request, "request", admin, missing_db)
    assert missing.value.code == "DEVICE_NOT_FOUND"

    disabled = SimpleNamespace(id="disabled", active=False, device_metadata={})
    disabled_db = SimpleNamespace(get=lambda *_args: disabled)
    with pytest.raises(ProblemException) as inactive:
        fleet.reauthorize_client_agent(request, "disabled", admin, disabled_db)
    assert inactive.value.code == "DEVICE_DISABLED"

    device = SimpleNamespace(
        id="device-1",
        active=True,
        device_metadata={},
        status="online",
        created_by=admin["id"],
    )
    commits: list[bool] = []
    db = SimpleNamespace(get=lambda *_args: device, commit=lambda: commits.append(True))
    monkeypatch.setattr(fleet, "issue_v2_device_credential", lambda *_args: "new-token")
    monkeypatch.setattr(fleet, "write_audit", lambda *_args, **_kwargs: None)
    result = fleet.reauthorize_client_agent(request, "device-1", admin, db)
    assert result["binding_preserved"] is True
    assert result["device_token"] == "new-token"
    assert device.status == "offline" and commits == [True]

    local_device = SimpleNamespace(id="device-1", allow_user_shares=False)
    action = SimpleNamespace(id="action-1")
    share_db = SimpleNamespace(scalar=lambda _statement: action)
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_args: local_device)
    payload = DeviceRemoteRootCreate(
        name="受限目录",
        remote_key="restricted-root",
        action_token="a" * 32,
    )
    with pytest.raises(ProblemException) as denied:
        fleet.create_device_root(payload, request, "token", share_db)
    assert denied.value.code == "LOCAL_SHARE_DISABLED"
