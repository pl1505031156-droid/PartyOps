"""身份、任务闭环、并发、材料与权限集成测试。"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from .conftest import create_task


def test_bootstrap_health_and_auth(client: TestClient, admin: dict) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["sqlite"]["fts5"] is True
    assert client.get("/api/v1/bootstrap/status").json()["configured"] is True
    assert client.get("/api/v1/auth/me").json()["id"] == admin["id"]


def test_duplicate_bootstrap_is_rejected(client: TestClient, admin: dict) -> None:
    response = client.post(
        "/api/v1/bootstrap/host",
        json={
            "username": "other",
            "display_name": "其他管理员",
            "password": "PartyOps@2026",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_CONFIGURED"
    assert "trace_id" in response.json()


def test_task_version_conflict_keeps_draft(
    client: TestClient, admin: dict, staff: dict
) -> None:
    task = create_task(
        client,
        admin["id"],
        collaborator_ids=[staff["id"]],
    )
    success = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"If-Match": str(task["version"])},
        json={"title": "并发修改成功"},
    )
    assert success.status_code == 200
    conflict = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"If-Match": str(task["version"])},
        json={"title": "并发修改冲突"},
    )
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["draft_id"]
    assert body["current"]["title"] == "并发修改成功"
    assert body["submitted"]["title"] == "并发修改冲突"


def test_full_review_material_and_archive_flow(client: TestClient, admin: dict) -> None:
    task = create_task(client, admin["id"], reviewer_id=admin["id"])
    task_id = task["id"]
    material_id = task["materials"][0]["id"]

    submitted = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "submit_review", "note": "请审核"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_review"

    returned = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "return", "note": "补齐报送稿"},
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "returned"

    restarted = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "start", "note": "已补充"},
    )
    assert restarted.json()["status"] == "in_progress"

    upload = client.post(
        f"/api/v1/tasks/{task_id}/materials/{material_id}/versions",
        data={"stage": "submitted", "is_final": "true", "note": "实际报送版本"},
        files={"file": ("终稿.docx", io.BytesIO(b"final-content"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["materials"][0]["complete"] is True

    resubmitted = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "submit_review", "note": "材料已补齐"},
    )
    assert resubmitted.status_code == 200
    completed = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "approve", "note": "审核通过"},
    )
    assert completed.status_code == 200
    archived = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "archive", "note": "材料齐全"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    reopened = client.post(
        f"/api/v1/tasks/{task_id}/actions",
        json={"action": "reopen", "note": "上级要求补充说明"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "in_progress"


def test_archive_rejects_missing_required_material(client: TestClient, admin: dict) -> None:
    task = create_task(client, admin["id"])
    completed = client.post(
        f"/api/v1/tasks/{task['id']}/actions",
        json={"action": "complete", "note": "允许先完成"},
    )
    assert completed.status_code == 200
    archived = client.post(
        f"/api/v1/tasks/{task['id']}/actions",
        json={"action": "archive", "note": ""},
    )
    assert archived.status_code == 409
    assert archived.json()["code"] == "MATERIALS_INCOMPLETE"

    material = task["materials"][0]
    without_reason = client.patch(
        f"/api/v1/tasks/{task['id']}/materials/{material['id']}",
        json={"not_applicable": True, "reason": ""},
    )
    assert without_reason.status_code == 422
    marked = client.patch(
        f"/api/v1/tasks/{task['id']}/materials/{material['id']}",
        json={"not_applicable": True, "reason": "本事项无需形成报送稿"},
    )
    assert marked.status_code == 200
    assert client.post(
        f"/api/v1/tasks/{task['id']}/actions",
        json={"action": "archive", "note": "已说明不适用"},
    ).status_code == 200


def test_restricted_task_minimizes_content(client: TestClient, admin: dict) -> None:
    rejected = client.post(
        "/api/v1/tasks",
        json={
            "title": "敏感事项",
            "description": "不应保存的正文",
            "task_type": "quick",
            "sensitivity": "restricted",
            "owner_id": admin["id"],
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "RESTRICTED_BODY_DISABLED"
    task = create_task(
        client,
        admin["id"],
        title="敏感事项",
        description="",
        sensitivity="restricted",
        materials=[],
        steps=[],
    )
    assert task["description"] == ""
    assert task["allow_sensitive_content"] is False


def test_search_exports_and_audit(client: TestClient, admin: dict) -> None:
    task = create_task(client, admin["id"], title="可检索的换届材料")
    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"If-Match": str(task["version"])},
        json={"description": "换届材料已完成检索校验。"},
    )
    assert updated.status_code == 200, updated.text
    search = client.get("/api/v1/search", params={"q": "换届"})
    assert search.status_code == 200
    assert any("换届" in item["title"] for item in search.json()["items"])

    word = client.get("/api/v1/exports/tasks.docx")
    assert word.status_code == 200
    assert word.content.startswith(b"PK")
    excel = client.get("/api/v1/exports/tasks.xlsx")
    assert excel.status_code == 200
    assert excel.content.startswith(b"PK")

    audit = client.get("/api/v1/admin/audit")
    assert audit.status_code == 200
    actions = {item["action"] for item in audit.json()}
    assert {"auth.login", "task.create", "task.update"}.issubset(actions)


def test_dashboard_all_operational_buckets(
    client: TestClient, admin: dict, staff: dict
) -> None:
    now = datetime.now(timezone.utc)
    create_task(
        client,
        admin["id"],
        title="今天必须完成",
        internal_due_at=(now + timedelta(hours=1)).isoformat(),
        formal_due_at=None,
        materials=[],
        steps=[],
    )
    create_task(
        client,
        staff["id"],
        title="等待对方三日内办理",
        internal_due_at=(now + timedelta(days=2)).isoformat(),
        formal_due_at=None,
        materials=[],
        steps=[],
    )
    create_task(
        client,
        admin["id"],
        title="已经逾期",
        internal_due_at=(now - timedelta(days=1)).isoformat(),
        formal_due_at=None,
        materials=[],
        steps=[],
    )
    feedback = create_task(
        client,
        admin["id"],
        title="等待上级反馈",
        internal_due_at=None,
        formal_due_at=None,
        materials=[],
        steps=[],
    )
    assert client.post(
        f"/api/v1/tasks/{feedback['id']}/actions",
        json={"action": "wait_feedback", "note": "已发函"},
    ).status_code == 200
    review = create_task(
        client,
        admin["id"],
        title="等待审核",
        internal_due_at=None,
        formal_due_at=None,
        materials=[],
        steps=[],
    )
    assert client.post(
        f"/api/v1/tasks/{review['id']}/actions",
        json={"action": "submit_review", "note": "提交"},
    ).status_code == 200
    risk = create_task(
        client,
        admin["id"],
        title="已完成但材料待补",
        internal_due_at=None,
        formal_due_at=None,
        steps=[],
    )
    assert client.post(
        f"/api/v1/tasks/{risk['id']}/actions",
        json={"action": "complete", "note": "先完成"},
    ).status_code == 200
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    buckets = {item["key"]: item for item in response.json()["buckets"]}
    for key in (
        "today",
        "three_days",
        "overdue",
        "my_action",
        "other_action",
        "review",
        "feedback",
        "materials",
    ):
        assert buckets[key]["count"] >= 1, key


def test_logout_revokes_session(client: TestClient, admin: dict) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    )
    assert login.status_code == 200
