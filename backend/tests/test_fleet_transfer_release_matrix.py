"""主机到设备、设备到主机、批量 ZIP 与传输状态动作发布矩阵。"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def _enroll(client: TestClient, name: str) -> dict:
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments", json={"name": name}
    )
    assert enrollment.status_code == 201, enrollment.text
    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": enrollment.json()["code"],
            "name": name,
            "architecture": "amd64",
            "platform": "windows",
            "kernel": "10.0.19045",
            "app_version": "1.4.2",
            "agent_version": "1.4.2",
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    return enrolled.json()


def test_host_to_device_transfer_actions_status_and_chunks(
    client: TestClient,
    admin: dict,
    staff: dict,
    tmp_path: Path,
) -> None:
    _login(client, "admin")
    suffix = uuid.uuid4().hex[:8]
    device = _enroll(client, f"接收协同机-{suffix}")
    headers = {"X-PartyOps-Device-Token": device["device_token"]}

    source_root = tmp_path / "主机发送目录"
    source_root.mkdir()
    content = b"host-to-device-release"
    source = source_root / "发送材料.txt"
    source.write_bytes(content)
    root = client.post(
        "/api/v1/workspace/roots",
        json={"name": f"主机发送目录-{suffix}", "absolute_path": str(source_root.resolve())},
    )
    assert root.status_code == 201, root.text
    assert client.post(f"/api/v1/workspace/roots/{root.json()['id']}/scan-now").status_code == 200
    item = next(
        value
        for value in client.get(
            "/api/v1/workspace/files", params={"root_id": root.json()["id"]}
        ).json()
        if value["name"] == source.name
    )
    assert client.post(f"/api/v1/workspace/files/{item['id']}/copy-to-inbox").status_code == 422

    created = client.post(
        "/api/v1/transfers",
        json={
            "direction": "host_to_device",
            "source_file_id": item["id"],
            "destination_device_id": device["device_id"],
            "original_name": source.name,
            "relative_path": source.name,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert created.status_code == 201, created.text
    transfer = created.json()
    assert transfer["status"] == "queued"
    commands = client.get("/api/v1/devices/commands", headers=headers)
    assert commands.status_code == 200
    assert any(item["type"] == "download_file" for item in commands.json())
    status = client.get(
        f"/api/v1/devices/transfers/{transfer['id']}/status", headers=headers
    )
    assert status.status_code == 200 and status.json()["total_chunks"] == 1
    chunk = client.get(
        f"/api/v1/devices/transfers/{transfer['id']}/chunks/0", headers=headers
    )
    assert chunk.status_code == 200 and chunk.content == content
    assert chunk.headers["x-chunk-sha256"] == hashlib.sha256(content).hexdigest()
    assert client.get(
        f"/api/v1/devices/transfers/{transfer['id']}/chunks/99", headers=headers
    ).status_code == 404

    paused = client.patch(
        f"/api/v1/transfers/{transfer['id']}",
        headers={"If-Match": str(transfer["version"])},
        json={"action": "pause", "note": "验证暂停"},
    )
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    resumed = client.patch(
        f"/api/v1/transfers/{transfer['id']}",
        headers={"If-Match": str(paused.json()["version"])},
        json={"action": "resume", "note": "验证续传"},
    )
    assert resumed.status_code == 200 and resumed.json()["status"] == "queued"
    cancelled = client.patch(
        f"/api/v1/transfers/{transfer['id']}",
        headers={"If-Match": str(resumed.json()["version"])},
        json={"action": "cancel", "note": "验证清理"},
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"

    approval = client.post(
        "/api/v1/transfers",
        json={
            "direction": "host_to_device",
            "source_file_id": item["id"],
            "destination_device_id": device["device_id"],
            "original_name": "需审批材料.exe",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "require_approval": True,
        },
    )
    assert approval.status_code == 201 and approval.json()["status"] == "awaiting_approval"
    _login(client, "staff")
    forbidden = client.patch(
        f"/api/v1/transfers/{approval.json()['id']}",
        headers={"If-Match": str(approval.json()["version"])},
        json={"action": "approve", "note": "越权审批"},
    )
    assert forbidden.status_code == 403
    _login(client, "admin")
    approved = client.patch(
        f"/api/v1/transfers/{approval.json()['id']}",
        headers={"If-Match": str(approval.json()["version"])},
        json={"action": "approve", "note": "管理员批准"},
    )
    assert approved.status_code == 200 and approved.json()["status"] == "queued"


def test_device_bundle_prepare_upload_finalize_and_browser_read(
    client: TestClient,
    admin: dict,
) -> None:
    _login(client, "admin")
    suffix = uuid.uuid4().hex[:8]
    device = _enroll(client, f"批量共享协同机-{suffix}")
    headers = {"X-PartyOps-Device-Token": device["device_token"]}
    root_response = client.post(
        "/api/v1/admin/workspace/remote-roots",
        json={
            "device_id": device["device_id"],
            "name": f"批量资料-{suffix}",
            "remote_key": f"bundle_{suffix}",
        },
    )
    assert root_response.status_code == 201, root_response.text
    root = root_response.json()["root"]
    approved = client.patch(
        f"/api/v1/admin/workspace/remote-roots/{root['id']}",
        headers={"If-Match": str(root["version"])},
        json={"approval_status": "approved", "approval_note": "允许批量共享"},
    )
    assert approved.status_code == 200
    indexed = client.post(
        "/api/v1/devices/workspace/index-delta",
        headers=headers,
        json={
            "root_id": root["id"],
            "files": [
                {
                    "relative_path": "甲.txt",
                    "name": "甲.txt",
                    "size_bytes": 4,
                    "sha256": hashlib.sha256(b"aaaa").hexdigest(),
                },
                {
                    "relative_path": "乙.txt",
                    "name": "乙.txt",
                    "size_bytes": 4,
                    "sha256": hashlib.sha256(b"bbbb").hexdigest(),
                },
            ],
        },
    )
    assert indexed.status_code == 200, indexed.text
    items = client.get(
        "/api/v1/workspace/files", params={"root_id": root["id"]}
    ).json()
    download = client.post(
        "/api/v1/workspace/downloads",
        json={
            "item_ids": [item["id"] for item in items],
            "bundle_mode": "selection_zip",
            "delivery": "browser",
        },
    )
    assert download.status_code == 201, download.text
    transfer_id = download.json()["transfer_id"]
    assert client.post(
        f"/api/v1/devices/transfers/{transfer_id}/prepare",
        headers=headers,
        json={"size_bytes": "not-number", "sha256": "0" * 64},
    ).status_code == 422
    assert client.post(
        f"/api/v1/devices/transfers/{transfer_id}/prepare",
        headers=headers,
        json={"size_bytes": 8, "sha256": "invalid"},
    ).status_code == 422

    bundle = b"PK-release-bundle"
    digest = hashlib.sha256(bundle).hexdigest()
    prepared = client.post(
        f"/api/v1/devices/transfers/{transfer_id}/prepare",
        headers=headers,
        json={"size_bytes": len(bundle), "sha256": digest},
    )
    assert prepared.status_code == 200, prepared.text
    uploaded = client.put(
        f"/api/v1/devices/transfers/{transfer_id}/chunks/0",
        headers={**headers, "X-Chunk-SHA256": digest},
        content=bundle,
    )
    assert uploaded.status_code == 200, uploaded.text
    finalized = client.post(
        f"/api/v1/devices/transfers/{transfer_id}/finalize", headers=headers
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "completed"
    content = client.get(f"/api/v1/transfers/{transfer_id}/content")
    assert content.status_code == 200 and content.content == bundle
