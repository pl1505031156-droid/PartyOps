from __future__ import annotations

import io
import hashlib
import json
import zipfile

from fastapi.testclient import TestClient

from app.config import get_settings


def test_device_enrollment_heartbeat_permissions_and_workbench(
    client: TestClient, admin: dict
) -> None:
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "D2000 协同电脑"},
    )
    assert enrollment.status_code == 201, enrollment.text
    code = enrollment.json()["code"]
    truncated = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": code[:-12],
            "name": "截断码电脑",
            "architecture": "arm64",
            "platform": "uos",
            "kernel": "4.19.0-arm64-desktop",
        },
    )
    assert truncated.status_code == 400
    assert truncated.json()["code"] == "ENROLLMENT_CODE_FORMAT_INVALID"

    enrollment_payload = {
        "code": code,
        "name": "D2000 协同电脑",
        "architecture": "arm64",
        "platform": "uos",
        "kernel": "4.19.0-arm64-desktop",
        "agent_version": "1.1.1",
        "local_username": "staff",
        "disk_free_bytes": 10_000_000_000,
    }
    enrolled = client.post(
        "/api/v1/devices/enroll",
        json=enrollment_payload,
    )
    assert enrolled.status_code == 201, enrolled.text
    device = enrolled.json()
    token = device["device_token"]

    # 如果主机已经提交事务但终端没有收到响应，同一请求可以安全重试；
    # 不会创建第二台设备，也不会把已消费入网码误报为过期。
    recovered = client.post("/api/v1/devices/enroll", json=enrollment_payload)
    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["device_id"] == device["device_id"]
    assert recovered.json()["device_token"] == token
    listed_before_heartbeat = client.get("/api/v1/admin/devices")
    enrolled_device = next(
        item
        for item in listed_before_heartbeat.json()
        if item["id"] == device["device_id"]
    )
    assert enrolled_device["status"] == "offline"
    assert enrolled_device["last_seen_at"] is None

    consumed_by_other_request = client.post(
        "/api/v1/devices/enroll",
        json={**enrollment_payload, "name": "另一台电脑"},
    )
    assert consumed_by_other_request.status_code == 409
    assert (
        consumed_by_other_request.json()["code"]
        == "ENROLLMENT_ALREADY_COMPLETED"
    )

    heartbeat = client.post(
        "/api/v1/devices/heartbeat",
        headers={"X-PartyOps-Device-Token": token},
        json={
            "architecture": "arm64",
            "platform": "uos",
            "kernel": "4.19.0-arm64-desktop",
            "agent_version": "1.1.1",
            "local_username": "staff",
            "disk_free_bytes": 9_000_000_000,
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["status"] == "online"
    rotation = client.post(
        f"/api/v1/admin/devices/{device['device_id']}/rotate-certificate",
        json={},
    )
    assert rotation.status_code == 200, rotation.text

    roots = client.post(
        "/api/v1/admin/workspace/remote-roots",
        json={
            "device_id": device["device_id"],
            "name": "D2000 工作资料",
            "remote_key": "工作资料",
        },
    )
    assert roots.status_code == 201, roots.text
    root = roots.json()["root"]
    assert "absolute_path" not in roots.text

    approved = client.patch(
        f"/api/v1/admin/workspace/remote-roots/{root['id']}",
        headers={"If-Match": str(root["version"])},
        json={"approval_status": "approved"},
    )
    assert approved.status_code == 200, approved.text

    grant = client.post(
        "/api/v1/admin/device-grants",
        json={
            "device_id": device["device_id"],
            "root_id": root["id"],
            "capabilities": ["download", "share"],
        },
    )
    assert grant.status_code == 201, grant.text

    index = client.post(
        "/api/v1/devices/workspace/index-delta",
        headers={"X-PartyOps-Device-Token": token},
        json={
            "root_id": root["id"],
            "files": [
                {
                    "relative_path": "通知/工作计划.docx",
                    "name": "工作计划.docx",
                    "extension": ".docx",
                    "size_bytes": 1024,
                    "extracted_text": "下周工作计划",
                }
            ],
        },
    )
    assert index.status_code == 200, index.text

    files = client.get(f"/api/v1/workspace/files?root_id={root['id']}")
    assert files.status_code == 200, files.text
    file_id = files.json()[0]["id"]

    transfer = client.post(f"/api/v1/workspace/files/{file_id}/copy-to-inbox")
    assert transfer.status_code == 201, transfer.text
    assert transfer.json()["direction"] == "device_to_host"

    workbench = client.get("/api/v1/workbench")
    assert workbench.status_code == 200, workbench.text
    assert "pending_transfers" in workbench.json()


def test_device_enrollment_accepts_code_copied_with_labels_and_zero_width_chars(
    client: TestClient, admin: dict
) -> None:
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "剪贴板兼容测试终端"},
    )
    assert enrollment.status_code == 201, enrollment.text
    code = enrollment.json()["code"]
    copied = f"一次性入网码（{len(code)} 个字符）：\u200b{code[:35]}\n{code[35:]}\ufeff"

    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": copied,
            "name": "剪贴板兼容测试终端",
            "architecture": "amd64",
            "platform": "uos",
            "kernel": "4.19.0-amd64-desktop",
        },
    )
    assert enrolled.status_code == 201, enrolled.text


def test_saved_views_topics_calendar_and_handover(client: TestClient, admin: dict) -> None:
    view = client.post(
        "/api/v1/saved-views",
        json={"name": "本周逾期", "view_type": "tasks", "filters": {"status": "in_progress"}, "pinned": True},
    )
    assert view.status_code == 201, view.text
    assert client.get("/api/v1/saved-views").json()[0]["name"] == "本周逾期"

    topic = client.post("/api/v1/topics", json={"name": "年度重点工作", "description": "统一收纳事项和材料"})
    assert topic.status_code == 201, topic.text
    updated = client.patch(
        f"/api/v1/topics/{topic.json()['id']}",
        headers={"If-Match": str(topic.json()["version"])},
        json={"task_ids": []},
    )
    assert updated.status_code == 200, updated.text

    calendar = client.post(
        "/api/v1/work-calendar",
        json={"date_key": "2026-10-01", "title": "国庆节", "kind": "holiday", "is_workday": False},
    )
    assert calendar.status_code == 201, calendar.text
    assert client.get("/api/v1/work-calendar?year=2026").status_code == 200

    handover = client.post("/api/v1/handover")
    assert handover.status_code == 201, handover.text
    assert client.get(f"/api/v1/handover/{handover.json()['id']}/download").status_code == 200


def test_update_package_validation_and_queue(client: TestClient, admin: dict) -> None:
    current_version = get_settings().app_version
    major, minor, patch = (int(part) for part in current_version.split("."))
    update_version = f"{major}.{minor}.{patch + 1}"
    artifacts = {
        f"partyops_{update_version}_amd64.deb": b"amd64-placeholder",
        f"partyops_{update_version}_arm64.deb": b"arm64-placeholder",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                    {
                        "format": "partyops-update",
                        "format_version": 2,
                            "version": update_version,
                            "min_version": "1.1.1",
                            "schema_revision": "0017",
                            "architecture_artifacts": {
                                "amd64": f"partyops_{update_version}_amd64.deb",
                                "arm64": f"partyops_{update_version}_arm64.deb",
                        },
                        "artifacts": {
                            filename: {
                                "sha256": hashlib.sha256(content).hexdigest(),
                                "size": len(content),
                            }
                            for filename, content in artifacts.items()
                            },
                            "release_notes": ["测试双架构更新包与主机优先队列"],
                            "signature": "",
                    }
                ),
            )
        for filename, content in artifacts.items():
            archive.writestr(filename, content)
    stream.seek(0)
    uploaded = client.post(
        "/api/v1/admin/updates/upload",
        files={"file": (f"partyops_{update_version}.partyops-update", stream.getvalue(), "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    applied = client.post(f"/api/v1/admin/updates/{uploaded.json()['id']}/apply", json={"target_device_ids": []})
    assert applied.status_code == 202, applied.text
    assert applied.json()[0]["status"] == "applying"
