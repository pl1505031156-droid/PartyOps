from __future__ import annotations

import hashlib
import uuid

from fastapi.testclient import TestClient


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def test_fleet_admin_device_root_grant_command_and_delete_lifecycle(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    _login_admin(client)
    suffix = uuid.uuid4().hex[:10]
    invalid_limit = client.patch("/api/v1/admin/devices/config", params={"max_devices": 21})
    assert invalid_limit.status_code == 422
    saved_limit = client.patch("/api/v1/admin/devices/config", params={"max_devices": 20})
    assert saved_limit.status_code == 200
    assert client.get("/api/v1/admin/devices/config").json()["max_devices"] == 20

    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": f"发布门禁设备-{suffix}"},
    )
    assert enrollment.status_code == 201, enrollment.text
    enrollment_id = enrollment.json()["id"]
    assert client.get(f"/api/v1/admin/devices/enrollments/{enrollment_id}/status").json()["status"] == "pending"
    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": enrollment.json()["code"],
            "name": f"发布门禁设备-{suffix}",
            "architecture": "amd64",
            "platform": "win32",
            "kernel": "10.0.19045",
            "app_version": "1.4.3-rc.3",
            "agent_version": "1.4.3-rc.3",
            "local_username": "release-user",
            "disk_free_bytes": 10_000_000_000,
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    device_id = enrolled.json()["device_id"]
    token = enrolled.json()["device_token"]
    device_headers = {"X-PartyOps-Device-Token": token}
    assert client.get(f"/api/v1/admin/devices/enrollments/{enrollment_id}/status").json()["status"] == "enrolled"

    heartbeat = client.post(
        "/api/v1/devices/heartbeat",
        headers=device_headers,
        json={
            "architecture": "amd64",
            "platform": "windows11",
            "kernel": "10.0.26100",
            "app_version": "1.4.3-rc.3",
            "agent_version": "1.4.3-rc.3",
            "local_username": "release-user",
            "disk_free_bytes": 9_000_000_000,
            "root_count": 1,
            "indexed_file_count": 1,
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["platform"] == "windows"
    assert any(item["device_id"] == device_id for item in client.get("/api/v1/admin/devices/version-status").json())

    remote = client.post(
        "/api/v1/admin/workspace/remote-roots",
        json={"device_id": device_id, "name": f"管理员目录-{suffix}", "remote_key": f"admin_{suffix}"},
    )
    assert remote.status_code == 201, remote.text
    root = remote.json()["root"]
    duplicate = client.post(
        "/api/v1/admin/workspace/remote-roots",
        json={"device_id": device_id, "name": "重复目录", "remote_key": f"admin_{suffix}"},
    )
    assert duplicate.status_code == 201 and duplicate.json()["created"] is False
    approved = client.patch(
        f"/api/v1/admin/workspace/remote-roots/{root['id']}",
        headers={"If-Match": str(root["version"])},
        json={"approval_status": "approved", "approval_note": "发布门禁批准"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["enabled"] is True
    assert client.get("/api/v1/admin/workspace/remote-roots", params={"approval_status": "invalid"}).status_code == 422
    assert any(item["id"] == root["id"] for item in client.get("/api/v1/admin/workspace/remote-roots", params={"approval_status": "approved"}).json())

    invalid_grant = client.post(
        "/api/v1/admin/device-grants",
        json={"device_id": device_id, "user_id": staff["id"], "root_id": root["id"], "capabilities": ["execute"]},
    )
    assert invalid_grant.status_code == 422
    grant = client.post(
        "/api/v1/admin/device-grants",
        json={"device_id": device_id, "user_id": staff["id"], "root_id": root["id"], "capabilities": ["download", "share", "upload"]},
    )
    assert grant.status_code == 201, grant.text
    assert any(item["id"] == grant.json()["id"] for item in client.get("/api/v1/admin/device-grants").json())
    disabled_grant = client.patch(
        f"/api/v1/admin/device-grants/{grant.json()['id']}",
        params={"active": False},
        headers={"If-Match": str(grant.json()["version"])},
        json={},
    )
    assert disabled_grant.status_code == 200 and disabled_grant.json()["active"] is False

    device_root = client.post(
        "/api/v1/devices/workspace/roots",
        headers=device_headers,
        json={"name": f"设备目录-{suffix}", "remote_key": f"device_{suffix}"},
    )
    assert device_root.status_code == 201, device_root.text
    device_root_id = device_root.json()["id"]
    assert any(item["id"] == device_root_id for item in client.get("/api/v1/devices/workspace/roots", headers=device_headers).json())
    approved_device_root = client.patch(
        f"/api/v1/admin/workspace/remote-roots/{device_root_id}",
        headers={"If-Match": str(device_root.json()["version"])},
        json={"approval_status": "approved", "approval_note": "允许索引"},
    )
    assert approved_device_root.status_code == 200
    renamed = client.patch(
        f"/api/v1/devices/workspace/roots/{device_root_id}",
        headers=device_headers,
        json={"name": f"设备目录重命名-{suffix}"},
    )
    assert renamed.status_code == 200, renamed.text
    body = b"release-fleet-index"
    indexed = client.post(
        "/api/v1/devices/workspace/index-delta",
        headers=device_headers,
        json={
            "root_id": device_root_id,
            "files": [
                {
                    "relative_path": "发布材料.txt",
                    "name": "发布材料.txt",
                    "size_bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "extracted_text": "发布材料正文",
                }
            ],
        },
    )
    assert indexed.status_code == 200, indexed.text
    assert indexed.json()["changed"] == 1

    browser_token = client.post("/api/v1/devices/browser-token", headers=device_headers)
    assert browser_token.status_code == 200, browser_token.text
    assert client.post("/api/v1/device/update-start").status_code == 400
    assert client.get("/api/v1/device/update-gate").status_code == 200

    rotation = client.post(f"/api/v1/admin/devices/{device_id}/rotate-certificate")
    assert rotation.status_code == 200, rotation.text
    commands = client.get("/api/v1/devices/commands", headers=device_headers)
    assert commands.status_code == 200, commands.text
    command = next(item for item in commands.json() if item["id"] == rotation.json()["command_id"])
    failed_ack = client.post(
        f"/api/v1/devices/commands/{command['id']}/ack",
        headers=device_headers,
        json={"ok": False, "error_code": "TEST_FAILURE", "message": "发布门禁模拟失败"},
    )
    assert failed_ack.status_code == 200, failed_ack.text

    stopped = client.delete(f"/api/v1/devices/workspace/roots/{device_root_id}", headers=device_headers)
    assert stopped.status_code == 200, stopped.text
    current = next(item for item in client.get("/api/v1/admin/devices").json() if item["id"] == device_id)
    revoked = client.patch(
        f"/api/v1/admin/devices/{device_id}",
        headers={"If-Match": str(current["version"])},
        json={"active": False, "allow_host_access": False, "allow_device_transfer": False, "allow_user_shares": False},
    )
    assert revoked.status_code == 200, revoked.text
    restored = client.patch(
        f"/api/v1/admin/devices/{device_id}",
        headers={"If-Match": str(revoked.json()["version"])},
        json={"active": True},
    )
    assert restored.status_code == 200, restored.text
    rotated_token = client.post(f"/api/v1/admin/devices/{device_id}/rotate-token")
    assert rotated_token.status_code == 200 and rotated_token.json()["device_token"] != token
    deleted = client.delete(
        f"/api/v1/admin/devices/{device_id}",
        headers={"If-Match": str(restored.json()["version"] + 1)},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["history_preserved"] is True
