"""1.4.0 真协同契约测试。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.database import Base
from .conftest import create_task


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def test_comment_mentions_are_append_only_and_my_work_is_scoped(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    """评论不抢事项版本，提及和四类我的工作均按当前用户计算。"""

    _login(client, "admin")
    task = create_task(
        client,
        admin["id"],
        title="真协同评论与我的工作",
        reviewer_id=staff["id"],
        collaborator_ids=[staff["id"]],
        steps=[{"title": "协同核对", "assignee_id": staff["id"]}],
        materials=[],
    )
    version_before_comment = task["version"]
    comment = client.post(
        f"/api/v1/tasks/{task['id']}/comments",
        json={"body": "请协同核对后提交。", "mentioned_user_ids": [staff["id"]]},
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["mentioned_user_ids"] == [staff["id"]]
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["version"] == version_before_comment

    submitted = client.post(
        f"/api/v1/tasks/{task['id']}/actions",
        json={"action": "submit_review", "note": "协同核对完成"},
        headers={"If-Match": str(version_before_comment)},
    )
    assert submitted.status_code == 200, submitted.text

    _login(client, "staff")
    summary = client.get("/api/v1/tasks/my-work-summary")
    assert summary.status_code == 200, summary.text
    counts = summary.json()
    assert counts["collaborating"] >= 1
    assert counts["reviewing"] >= 1
    assert counts["step_assigned"] >= 1
    assert any(
        item["id"] == task["id"]
        for item in client.get("/api/v1/tasks", params={"scope": "reviewing"}).json()["items"]
    )
    notifications = client.get("/api/v1/notifications", params={"unread_only": True})
    assert notifications.status_code == 200, notifications.text
    mentions = [item for item in notifications.json() if item["entity_id"] == task["id"]]
    assert len(mentions) == 1
    assert mentions[0]["notification_type"] == "mention"

    # 会话级测试客户端供后续用例复用，离开前恢复管理员登录态。
    _login(client, "admin")


def test_0016_migration_round_trip(tmp_path: Path) -> None:
    """0015 原位升级应增加协同字段、移除目录名唯一限制并可回滚。"""

    database = tmp_path / "upgrade-0016.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        config.attributes["connection"] = connection
        command.stamp(config, "0016")
        command.downgrade(config, "0015")
        command.upgrade(config, "0016")

    inspector = inspect(engine)
    assert "can_contribute" in {item["name"] for item in inspector.get_columns("archive_access_grants")}
    assert "mentioned_user_ids" in {item["name"] for item in inspector.get_columns("task_comments")}
    assert "approval_note" in {item["name"] for item in inspector.get_columns("workspace_roots")}
    assert {"handled_by", "handled_at", "linked_entity_type", "linked_entity_id"}.issubset(
        {item["name"] for item in inspector.get_columns("transfers")}
    )
    unique_columns = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("workspace_roots")
    }
    assert ("name",) not in unique_columns
    assert ("device_id", "remote_key") in unique_columns


def test_windows_device_root_lifecycle_and_device_archive_grant(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    """Windows 协同机可反复管理共享目录，设备贡献授权受总开关约束。"""

    _login(client, "admin")

    def enroll(name: str) -> dict:
        code = client.post(
            "/api/v1/admin/devices/enrollments", json={"name": name}
        )
        assert code.status_code == 201, code.text
        response = client.post(
            "/api/v1/devices/enroll",
            json={
                "code": code.json()["code"],
                "name": name,
                "architecture": "amd64",
                "platform": "win32",
                "kernel": "Windows 11 10.0.26100",
                    "app_version": "1.4.2",
                    "agent_version": "1.4.2",
                "local_username": "tester",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    device = enroll("Windows 协同机 A")
    headers = {"X-PartyOps-Device-Token": device["device_token"]}
    created = client.post(
        "/api/v1/devices/workspace/roots",
        headers=headers,
        json={"name": "重要档案共享", "remote_key": "shared_root_2026"},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    assert root["approval_status"] == "pending"
    pending = client.get(
        "/api/v1/admin/workspace/remote-roots", params={"approval_status": "pending"}
    )
    assert any(item["id"] == root["id"] for item in pending.json())
    approved = client.patch(
        f"/api/v1/admin/workspace/remote-roots/{root['id']}",
        headers={"If-Match": str(root["version"])},
        json={"approval_status": "approved", "approval_note": "仅限测试资料"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_note"] == "仅限测试资料"

    category = client.post(
        "/api/v1/archives/categories",
        json={
            "name": "设备授权重要档案",
            "code": "device_granted_archive",
            "record_mode": "document",
            "access_mode": "selected",
            "allow_device_access": True,
        },
    )
    assert category.status_code == 201, category.text
    category = category.json()
    grant = client.post(
        f"/api/v1/archives/categories/{category['id']}/grants",
        json={
            "device_id": device["device_id"],
            "can_view": True,
            "can_download": True,
            "can_contribute": True,
        },
    )
    assert grant.status_code == 201, grant.text

    _login(client, "staff")
    browser = client.post("/api/v1/devices/browser-token", headers=headers)
    assert browser.status_code == 200, browser.text
    client.cookies.set("partyops_device_context", browser.json()["token"])
    visible = client.get("/api/v1/archives/categories")
    permitted = next(item for item in visible.json() if item["id"] == category["id"])
    assert permitted["permissions"]["contribute"] is True
    contributed = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2026,
            "title": "来自 Windows 协同机的档案",
        },
    )
    assert contributed.status_code == 201, contributed.text

    _login(client, "admin")
    disabled = client.patch(
        f"/api/v1/archives/categories/{category['id']}/access",
        headers={"If-Match": str(category["version"])},
        json={"access_mode": "selected", "allow_device_access": False},
    )
    assert disabled.status_code == 200, disabled.text
    _login(client, "staff")
    assert not any(
        item["id"] == category["id"]
        for item in client.get("/api/v1/archives/categories").json()
    )
    client.cookies.delete("partyops_device_context")

    renamed = client.patch(
        f"/api/v1/devices/workspace/roots/{root['id']}",
        headers=headers,
        json={"name": "重要档案共享（已核定）"},
    )
    assert renamed.status_code == 200, renamed.text
    removed = client.delete(
        f"/api/v1/devices/workspace/roots/{root['id']}", headers=headers
    )
    assert removed.status_code == 200, removed.text
    roots = client.get("/api/v1/devices/workspace/roots", headers=headers).json()
    assert next(item for item in roots if item["id"] == root["id"])["approval_status"] == "rejected"

    # 相同显示名称和 remote_key 在另一台电脑上是不同共享根，不再受全局名称唯一约束。
    _login(client, "admin")
    second = enroll("Windows 协同机 B")
    second_root = client.post(
        "/api/v1/devices/workspace/roots",
        headers={"X-PartyOps-Device-Token": second["device_token"]},
        json={"name": "重要档案共享（已核定）", "remote_key": "shared_root_2026"},
    )
    assert second_root.status_code == 201, second_root.text
    devices = client.get("/api/v1/admin/devices").json()
    assert next(item for item in devices if item["id"] == second["device_id"])["platform"] == "windows"
