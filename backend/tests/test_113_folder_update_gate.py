"""文件夹选择、更新历史和协同电脑强制版本一致性。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import db_runtime
from app.models import UpdatePackage


def test_discovery_scan_then_select_subfolders(
    client: TestClient,
    admin: dict,
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "待选择工作目录"
    included = root_path / "组织建设" / "年度重点"
    excluded = root_path / "暂不接入"
    included.mkdir(parents=True)
    excluded.mkdir(parents=True)
    (included / "换届工作计划.txt").write_text(
        "本周完成换届准备，下周提交党委会审议。",
        encoding="utf-8",
    )
    (excluded / "内部草稿.txt").write_text(
        "这段内容不应进入业务搜索。",
        encoding="utf-8",
    )

    created = client.post(
        "/api/v1/workspace/roots",
        json={
            "name": "目录选择回归",
            "absolute_path": str(root_path.resolve()),
            "selection_mode": "selected",
        },
    )
    assert created.status_code == 201, created.text
    root = created.json()
    assert root["included_paths"] == []
    assert client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "换届工作计划"},
    ).json() == []

    options = client.get(
        f"/api/v1/workspace/roots/{root['id']}/folder-options"
    )
    assert options.status_code == 200, options.text
    paths = {item["path"] for item in options.json()}
    assert {"组织建设", "组织建设/年度重点", "暂不接入"}.issubset(paths)

    selected = client.patch(
        f"/api/v1/workspace/roots/{root['id']}/selection",
        headers={"If-Match": str(root["version"])},
        json={
            "selection_mode": "selected",
            "included_paths": ["组织建设/年度重点"],
        },
    )
    assert selected.status_code == 202, selected.text
    refreshed = client.get("/api/v1/workspace/roots").json()
    current = next(item for item in refreshed if item["id"] == root["id"])
    assert current["included_paths"] == ["组织建设/年度重点"]

    found = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "换届工作计划"},
    )
    assert found.status_code == 200, found.text
    assert [item["name"] for item in found.json()] == ["换届工作计划.txt"]
    assert found.json()[0]["status"] == "indexed"
    excluded_result = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "内部草稿"},
    )
    assert excluded_result.status_code == 200
    assert excluded_result.json() == []


def test_release_history_and_device_update_gate(
    client: TestClient,
    admin: dict,
) -> None:
    target_version = get_settings().app_version
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "版本门禁测试终端"},
    )
    assert enrollment.status_code == 201, enrollment.text
    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": enrollment.json()["code"],
            "name": "版本门禁测试终端",
            "architecture": "arm64",
            "platform": "uos",
            "kernel": "4.19.0-arm64-desktop",
            "app_version": "1.1.2",
            "agent_version": "1.1.2",
            "local_username": "uos-user",
            "disk_free_bytes": 20 * 1024**3,
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    device = enrolled.json()

    browser_token = client.post(
        "/api/v1/devices/browser-token",
        headers={"X-PartyOps-Device-Token": device["device_token"]},
    )
    assert browser_token.status_code == 200, browser_token.text
    client.cookies.set(
        "partyops_device_context",
        browser_token.json()["token"],
    )

    gate = client.get("/api/v1/device/update-gate")
    assert gate.status_code == 200, gate.text
    assert gate.json()["required"] is True
    assert gate.json()["target_version"] == target_version
    assert gate.json()["package_id"] is None
    assert client.get("/api/v1/tasks").status_code == 426

    with db_runtime.session_factory() as db:
        package = UpdatePackage(
            filename=f"partyops_{target_version}_test.partyops-update",
            version=target_version,
            min_version="1.1.2",
            schema_revision="0013",
            manifest={
                "release_title": "目录选择与版本一致性测试",
                "release_notes": ["验证协同电脑进入系统前必须更新"],
            },
            sha256="0" * 64,
            signature_valid=True,
            status="completed",
            created_by=admin["id"],
        )
        db.add(package)
        db.commit()

    ready_gate = client.get("/api/v1/device/update-gate")
    assert ready_gate.status_code == 200
    assert ready_gate.json()["package_id"] is not None

    started = client.post("/api/v1/device/update-start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "applying"
    commands = client.get(
        "/api/v1/devices/commands",
        headers={"X-PartyOps-Device-Token": device["device_token"]},
    )
    assert commands.status_code == 200, commands.text
    assert any(item["type"] == "apply_update" for item in commands.json())

    heartbeat = client.post(
        "/api/v1/devices/heartbeat",
        headers={"X-PartyOps-Device-Token": device["device_token"]},
        json={
            "architecture": "arm64",
            "platform": "uos",
            "kernel": "4.19.0-arm64-desktop",
            "app_version": target_version,
            "agent_version": target_version,
            "local_username": "uos-user",
            "disk_free_bytes": 19 * 1024**3,
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    current = client.get("/api/v1/device/update-gate")
    assert current.status_code == 200
    assert current.json()["required"] is False
    assert client.get("/api/v1/tasks").status_code == 200

    history = client.get("/api/v1/admin/update-history")
    assert history.status_code == 200, history.text
    assert history.json()[0]["version"] == target_version
    assert history.json()[0]["release_notes"]
