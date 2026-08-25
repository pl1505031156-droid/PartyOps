"""三会一课、中心组学习专属模块的接口、权限与导出安全测试。"""

from __future__ import annotations

import ast
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import inspect, text

from alembic import command
from app.config import Settings
from app.database import DatabaseRuntime


def test_fastapi_models_are_python38_import_safe() -> None:
    """扫描全部路由，禁止把 Python 3.9+ 内置泛型放入会立即求值的装饰器。"""

    routers = Path(__file__).resolve().parents[1] / "app" / "routers"
    unsafe: list[tuple[str, int, str]] = []
    for source_path in sorted(routers.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg != "response_model":
                continue
            value = node.value
            if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name):
                if value.value.id in {"list", "dict", "set", "tuple", "type"}:
                    unsafe.append((source_path.name, value.lineno, value.value.id))
    assert unsafe == []


def test_backend_datetime_utc_is_python38_import_safe() -> None:
    """Win7 的 CPython 3.8 没有 datetime.UTC，所有运行模块必须使用 timezone.utc。"""

    app_root = Path(__file__).resolve().parents[1] / "app"
    violations: list[tuple[str, int]] = []
    for source_path in sorted(app_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                if any(alias.name == "UTC" for alias in node.names):
                    violations.append((source_path.relative_to(app_root).as_posix(), node.lineno))
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "datetime"
                and node.attr == "UTC"
            ):
                violations.append((source_path.relative_to(app_root).as_posix(), node.lineno))
    assert violations == []


def test_alembic_migrations_defer_builtin_generic_annotations() -> None:
    """Win7 Python 3.8 会执行全部迁移，含内置泛型时必须延迟求值。"""

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    violations: list[str] = []
    builtin_generics = {"list", "dict", "tuple", "set"}
    for path in sorted(versions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        has_builtin_generic = any(
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in builtin_generics
            for node in ast.walk(tree)
        )
        has_future_annotations = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in tree.body
        )
        if has_builtin_generic and not has_future_annotations:
            violations.append(path.name)

    assert violations == []


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def test_party_life_closed_loop_permissions_and_safe_export(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    organization = f"测试支部-{uuid4().hex[:8]}"
    _login(client, "admin")
    created = client.post(
        "/api/v1/party-life/meetings",
        json={
            "meeting_type": "party_member_meeting",
            "organization": organization,
            "title": "=HYPERLINK(\"https://example.invalid\",\"危险标题\")",
            "scheduled_at": "2026-08-20T09:00:00+08:00",
            "host_id": admin["id"],
            "recorder_id": admin["id"],
            "venue": "第一会议室",
            "business_data": {"vote_rule": "仅作风险提示"},
        },
    )
    assert created.status_code == 201, created.text
    meeting = created.json()
    assert meeting["ledger_state"] == "需补充"
    assert set(meeting["missing_items"]) == {"出席记录", "会议材料"}

    attendee = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/attendees",
        json={
            "user_id": staff["id"],
            "display_name": "协同人员",
            "role": "member",
            "attendance_status": "present",
            "voting_eligible": True,
        },
    )
    assert attendee.status_code == 201, attendee.text
    action = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/actions",
        json={
            "title": "落实会议决议",
            "responsible_user_id": staff["id"],
            "due_at": "2026-09-01T18:00:00+08:00",
            "create_task": True,
        },
    )
    assert action.status_code == 201, action.text
    assert action.json()["task_id"]

    _login(client, "staff")
    visible = client.get(
        "/api/v1/party-life/ledger",
        params={"year": 2026, "organization": organization},
    )
    assert visible.status_code == 200, visible.text
    assert [row["id"] for row in visible.json()] == [meeting["id"]]

    forbidden_attendee_patch = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/attendees/{attendee.json()['id']}",
        headers={"If-Match": str(attendee.json()["version"])},
        json={"note": "不应允许修改"},
    )
    assert forbidden_attendee_patch.status_code == 403
    assert forbidden_attendee_patch.json()["code"] == "MEETING_MODIFY_FORBIDDEN"

    own_action_patch = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/actions/{action.json()['id']}",
        headers={"If-Match": str(action.json()["version"])},
        json={
            "title": "落实会议决议（已核定）",
            "responsible_user_id": staff["id"],
            "due_at": "2026-09-02T18:00:00+08:00",
            "status": "completed",
        },
    )
    assert own_action_patch.status_code == 200, own_action_patch.text
    assert own_action_patch.json()["title"] == "落实会议决议（已核定）"
    conflict = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/actions/{action.json()['id']}",
        headers={"If-Match": str(action.json()["version"])},
        json={"note": "旧版本不得覆盖"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "VERSION_CONFLICT"

    forbidden_export = client.get(
        "/api/v1/party-life/ledger/export.xlsx",
        params={"year": 2026, "organization": organization},
    )
    assert forbidden_export.status_code == 403
    assert forbidden_export.json()["code"] == "MEETING_EXPORT_FORBIDDEN"

    _login(client, "admin")
    exported = client.get(
        "/api/v1/party-life/ledger/export.xlsx",
        params={"year": 2026, "organization": organization},
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["cache-control"] == "no-store"
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    assert workbook["台账"]["C2"].value.startswith("'=")


def test_study_center_plan_session_and_access_boundaries(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    organization = f"测试党委-{uuid4().hex[:8]}"
    _login(client, "staff")
    denied = client.post(
        "/api/v1/study-center/plans",
        json={"organization": organization, "year": 2026, "title": "年度学习计划"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "STUDY_PLAN_CREATE_FORBIDDEN"

    _login(client, "admin")
    plan_response = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": organization,
            "year": 2026,
            "title": "2026 年党委理论学习中心组学习计划",
            "group_leader_id": admin["id"],
            "secretary_id": staff["id"],
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    topic = client.post(
        f"/api/v1/study-center/plans/{plan['id']}/topics",
        json={
            "quarter": 3,
            "title": "专题学习研讨",
            "learning_materials": ["公开学习材料"],
            "research_topic": "基层调研",
            "conversion_goal": "形成改进事项",
        },
    )
    assert topic.status_code == 201, topic.text

    session = client.post(
        "/api/v1/study-center/sessions",
        json={
            "meeting_type": "study_group",
            "organization": organization,
            "title": "第三季度集体学习研讨",
            "scheduled_at": "2026-08-21T09:00:00+08:00",
            "study_plan_id": plan["id"],
            "host_id": admin["id"],
            "recorder_id": staff["id"],
        },
    )
    assert session.status_code == 201, session.text

    _login(client, "staff")
    plans = client.get(
        "/api/v1/study-center/plans",
        params={"year": 2026, "organization": organization},
    )
    assert plans.status_code == 200, plans.text
    assert plans.json()[0]["topics"][0]["title"] == "专题学习研讨"
    current_plan = plans.json()[0]
    updated_plan = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(current_plan["version"])},
        json={"notes": "由学习秘书补充的年度说明", "status": "active"},
    )
    assert updated_plan.status_code == 200, updated_plan.text
    assert updated_plan.json()["notes"] == "由学习秘书补充的年度说明"
    stale_plan = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(current_plan["version"])},
        json={"notes": "旧页面不得覆盖"},
    )
    assert stale_plan.status_code == 409
    assert stale_plan.json()["code"] == "VERSION_CONFLICT"

    updated_topic = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}/topics/{topic.json()['id']}",
        headers={"If-Match": str(topic.json()["version"])},
        json={"conversion_goal": "形成可跟踪的改进事项"},
    )
    assert updated_topic.status_code == 200, updated_topic.text
    assert updated_topic.json()["conversion_goal"] == "形成可跟踪的改进事项"
    stale_topic = client.delete(
        f"/api/v1/study-center/plans/{plan['id']}/topics/{topic.json()['id']}",
        headers={"If-Match": str(topic.json()["version"])},
    )
    assert stale_topic.status_code == 409
    secretary_topic = client.post(
        f"/api/v1/study-center/plans/{plan['id']}/topics",
        json={"quarter": 4, "title": "第四季度专题"},
    )
    assert secretary_topic.status_code == 201, secretary_topic.text
    deleted_topic = client.delete(
        f"/api/v1/study-center/plans/{plan['id']}/topics/{updated_topic.json()['id']}",
        headers={"If-Match": str(updated_topic.json()["version"])},
    )
    assert deleted_topic.status_code == 204, deleted_topic.text
    sessions = client.get(
        "/api/v1/study-center/sessions",
        params={"year": 2026, "organization": organization},
    )
    assert sessions.status_code == 200, sessions.text
    assert sessions.json()[0]["study_plan_id"] == plan["id"]


def test_specialized_routes_reject_cross_module_types(
    client: TestClient,
    admin: dict,
) -> None:
    _login(client, "admin")
    payload = {
        "meeting_type": "study_group",
        "organization": f"边界测试-{uuid4().hex[:8]}",
        "title": "错误入口",
    }
    response = client.post("/api/v1/party-life/meetings", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "MEETING_TYPE_INVALID"


def test_party_work_adversarial_validation_and_recovery_matrix(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    """覆盖缺失对象、越权、陈旧版本、非法关联和无任务决议等对抗路径。"""

    organization = f"对抗支部-{uuid4().hex[:8]}"
    study_organization = f"对抗党委-{uuid4().hex[:8]}"
    missing_id = f"missing-{uuid4().hex}"
    _login(client, "admin")

    for suffix in ("attendees", "actions"):
        missing = client.get(f"/api/v1/business-meetings/{missing_id}/{suffix}")
        assert missing.status_code == 404
        assert missing.json()["code"] == "MEETING_NOT_FOUND"

    base_meeting = {
        "meeting_type": "party_member_meeting",
        "organization": organization,
        "title": "对抗式党员大会",
        "scheduled_at": "2026-08-22T09:00:00+08:00",
    }
    invalid_owner = client.post(
        "/api/v1/party-life/meetings",
        json={**base_meeting, "owner_id": missing_id},
    )
    assert invalid_owner.status_code == 422
    assert invalid_owner.json()["code"] == "MEETING_OWNER_INVALID"
    invalid_role = client.post(
        "/api/v1/party-life/meetings",
        json={**base_meeting, "host_id": missing_id},
    )
    assert invalid_role.status_code == 422
    assert invalid_role.json()["code"] == "MEETING_ROLE_USER_INVALID"

    invalid_plan_user = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": study_organization,
            "year": 2026,
            "title": "非法人员年度计划",
            "group_leader_id": missing_id,
        },
    )
    assert invalid_plan_user.status_code == 422
    assert invalid_plan_user.json()["code"] == "STUDY_PLAN_USER_INVALID"

    plan_response = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": study_organization,
            "year": 2026,
            "title": "2026 年中心组学习计划",
            "group_leader_id": admin["id"],
            "secretary_id": staff["id"],
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    duplicate = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": study_organization,
            "year": 2026,
            "title": "重复计划",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "STUDY_PLAN_EXISTS"

    wrong_module_plan = client.post(
        "/api/v1/party-life/meetings",
        json={**base_meeting, "study_plan_id": plan["id"]},
    )
    assert wrong_module_plan.status_code == 422
    assert wrong_module_plan.json()["code"] == "STUDY_PLAN_INVALID"
    missing_session_plan = client.post(
        "/api/v1/study-center/sessions",
        json={
            "meeting_type": "study_group",
            "organization": study_organization,
            "title": "缺失计划的场次",
            "study_plan_id": missing_id,
        },
    )
    assert missing_session_plan.status_code == 422
    assert missing_session_plan.json()["code"] == "STUDY_PLAN_INVALID"

    _login(client, "staff")
    unrelated_plan = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(plan["version"])},
        json={"secretary_id": None},
    )
    assert unrelated_plan.status_code == 200
    plan = unrelated_plan.json()
    _login(client, "admin")
    admin_plan = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(plan["version"])},
        json={"secretary_id": None},
    )
    assert admin_plan.status_code == 200
    plan = admin_plan.json()
    trimmed_plan = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(plan["version"])},
        json={"title": "  2026 年中心组学习计划（核定）  "},
    )
    assert trimmed_plan.status_code == 200
    assert trimmed_plan.json()["title"] == "2026 年中心组学习计划（核定）"
    plan = trimmed_plan.json()
    _login(client, "staff")
    forbidden_plan = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(plan["version"])},
        json={"notes": "越权修改"},
    )
    assert forbidden_plan.status_code == 403
    assert forbidden_plan.json()["code"] == "STUDY_PLAN_MODIFY_FORBIDDEN"
    _login(client, "admin")
    missing_plan = client.patch(
        f"/api/v1/study-center/plans/{missing_id}",
        headers={"If-Match": "1"},
        json={"notes": "不存在"},
    )
    assert missing_plan.status_code == 404
    assert missing_plan.json()["code"] == "STUDY_PLAN_NOT_FOUND"

    second_plan_response = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": study_organization,
            "year": 2027,
            "title": "2027 年中心组学习计划",
        },
    )
    assert second_plan_response.status_code == 201
    second_plan = second_plan_response.json()
    duplicate_patch = client.patch(
        f"/api/v1/study-center/plans/{second_plan['id']}",
        headers={"If-Match": str(second_plan["version"])},
        json={"year": 2026, "title": "  合并冲突  "},
    )
    assert duplicate_patch.status_code == 409
    assert duplicate_patch.json()["code"] == "STUDY_PLAN_EXISTS"

    missing_topic_patch = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}/topics/{missing_id}",
        headers={"If-Match": "1"},
        json={"title": "不存在"},
    )
    assert missing_topic_patch.status_code == 404
    missing_topic_delete = client.delete(
        f"/api/v1/study-center/plans/{plan['id']}/topics/{missing_id}",
        headers={"If-Match": "1"},
    )
    assert missing_topic_delete.status_code == 404
    topic_response = client.post(
        f"/api/v1/study-center/plans/{plan['id']}/topics",
        json={"quarter": 1, "title": "版本冲突专题"},
    )
    assert topic_response.status_code == 201
    stale_topic_patch = client.patch(
        f"/api/v1/study-center/plans/{plan['id']}/topics/{topic_response.json()['id']}",
        headers={"If-Match": "999"},
        json={"title": "陈旧页面不得覆盖"},
    )
    assert stale_topic_patch.status_code == 409

    meeting_response = client.post("/api/v1/party-life/meetings", json=base_meeting)
    assert meeting_response.status_code == 201, meeting_response.text
    meeting = meeting_response.json()
    assert client.get(f"/api/v1/business-meetings/{meeting['id']}/attendees").json() == []
    assert client.get(f"/api/v1/business-meetings/{meeting['id']}/actions").json() == []
    bad_attendee = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/attendees",
        json={"user_id": missing_id, "display_name": "不存在的账号"},
    )
    assert bad_attendee.status_code == 422
    attendee_response = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/attendees",
        json={"display_name": "现场党员", "attendance_status": "present"},
    )
    assert attendee_response.status_code == 201
    attendee = attendee_response.json()
    missing_attendee = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/attendees/{missing_id}",
        headers={"If-Match": "1"},
        json={"note": "不存在"},
    )
    assert missing_attendee.status_code == 404
    stale_attendee = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/attendees/{attendee['id']}",
        headers={"If-Match": "999"},
        json={"note": "陈旧页面"},
    )
    assert stale_attendee.status_code == 409
    updated_attendee = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/attendees/{attendee['id']}",
        headers={"If-Match": str(attendee["version"])},
        json={"note": "已核对", "voting_eligible": True},
    )
    assert updated_attendee.status_code == 200

    bad_action = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/actions",
        json={"title": "非法负责人", "responsible_user_id": missing_id},
    )
    assert bad_action.status_code == 422
    action_without_task_response = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/actions",
        json={
            "title": "仅记录决议",
            "due_at": "2000-01-01T00:00:00Z",
            "create_task": False,
        },
    )
    assert action_without_task_response.status_code == 201
    action_without_task = action_without_task_response.json()
    assert action_without_task["task_id"] is None
    missing_action = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/actions/{missing_id}",
        headers={"If-Match": "1"},
        json={"note": "不存在"},
    )
    assert missing_action.status_code == 404

    _login(client, "staff")
    forbidden_action = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/actions/{action_without_task['id']}",
        headers={"If-Match": str(action_without_task["version"])},
        json={"note": "越权"},
    )
    assert forbidden_action.status_code == 403
    _login(client, "admin")
    invalid_action_owner = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/actions/{action_without_task['id']}",
        headers={"If-Match": str(action_without_task["version"])},
        json={"responsible_user_id": missing_id},
    )
    assert invalid_action_owner.status_code == 422
    updated_action = client.patch(
        f"/api/v1/business-meetings/{meeting['id']}/actions/{action_without_task['id']}",
        headers={"If-Match": str(action_without_task["version"])},
        json={"title": "  已完成的决议  ", "status": "completed", "due_at": None},
    )
    assert updated_action.status_code == 200
    assert updated_action.json()["title"] == "已完成的决议"

    ledger = client.get(
        "/api/v1/party-life/ledger",
        params={"year": 2026, "organization": organization},
    )
    assert ledger.status_code == 200
    assert ledger.json()[0]["present_count"] == 1
    docx = client.get(
        "/api/v1/party-life/ledger/export.docx",
        params={"year": 2026, "organization": organization},
    )
    assert docx.status_code == 200
    assert docx.content.startswith(b"PK")

    study_session_response = client.post(
        "/api/v1/study-center/sessions",
        json={
            "meeting_type": "study_group",
            "organization": study_organization,
            "title": "季度学习研讨",
            "scheduled_at": "2026-08-22T14:00:00+08:00",
            "study_plan_id": plan["id"],
            "host_id": admin["id"],
            "recorder_id": admin["id"],
        },
    )
    assert study_session_response.status_code == 201
    _login(client, "staff")
    forbidden_study_export = client.get(
        "/api/v1/study-center/ledger/export.docx",
        params={"year": 2026, "organization": study_organization},
    )
    assert forbidden_study_export.status_code == 403
    _login(client, "admin")


def test_study_secretary_can_export_without_being_recorder(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    organization = f"学习秘书导出-{uuid4().hex[:8]}"
    _login(client, "admin")
    plan = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": organization,
            "year": 2026,
            "title": "学习秘书导出边界计划",
            "group_leader_id": admin["id"],
            "secretary_id": staff["id"],
        },
    ).json()
    session = client.post(
        "/api/v1/study-center/sessions",
        json={
            "meeting_type": "study_group",
            "organization": organization,
            "title": "季度学习研讨",
            "scheduled_at": "2026-08-22T14:00:00+08:00",
            "study_plan_id": plan["id"],
            "host_id": admin["id"],
            "recorder_id": admin["id"],
        },
    )
    assert session.status_code == 201, session.text
    _login(client, "staff")
    exported = client.get(
        "/api/v1/study-center/ledger/export.docx",
        params={"year": 2026, "organization": organization},
    )
    assert exported.status_code == 200, exported.text
    assert exported.content.startswith(b"PK")
    _login(client, "admin")


def test_schema_0020_to_0021_preserves_existing_meetings(tmp_path: Path) -> None:
    """证明加法迁移不会移动、复制或改写已有会议。"""

    runtime = DatabaseRuntime(
        Settings(data_dir=tmp_path, environment="test", strict_sqlite=False)
    )
    runtime.create_schema()
    config = runtime._alembic_config()
    with runtime.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0020")
    before = inspect(runtime.engine)
    assert "meeting_attendees" not in before.get_table_names()
    assert "study_plan_id" not in {
        column["name"] for column in before.get_columns("business_meetings")
    }
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users(
                    id, username, display_name, password_hash, role,
                    active, version, created_at, updated_at
                ) VALUES (
                    'legacy-user', 'legacy-user', '历史用户', 'hash',
                    'ADMIN', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO business_meetings(
                    id, meeting_type, organization, title, status,
                    recurrence_key, version, created_by, created_at, updated_at
                ) VALUES (
                    'legacy-meeting', 'party_member_meeting', '历史支部',
                    '历史党员大会', 'completed', '', 1, 'legacy-user',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
    with runtime.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    after = inspect(runtime.engine)
    assert {
        "meeting_attendees",
        "meeting_actions",
        "study_plans",
        "study_plan_topics",
        "party_development_plan_profiles",
    }.issubset(after.get_table_names())
    assert {"host_id", "recorder_id", "venue", "study_plan_id", "business_data"}.issubset(
        {column["name"] for column in after.get_columns("business_meetings")}
    )
    with runtime.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT meeting_type, organization, title, status "
                "FROM business_meetings WHERE id = 'legacy-meeting'"
            )
        ).one()
    assert row == (
        "party_member_meeting",
        "历史支部",
        "历史党员大会",
        "completed",
    )
