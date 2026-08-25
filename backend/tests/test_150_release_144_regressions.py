"""1.4.4 已知生产故障的最小回归用例。"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from app import client_agent
from app.config import Settings
from app.database import DatabaseRuntime, db_runtime
from app.models import (
    BusinessMeeting,
    Device,
    Notification,
    PartyDevelopmentMilestone,
    ReminderPreference,
    Task,
    User,
)
from app.notifications import (
    _refresh_daily,
    desktop_notifications_allowed,
    reconcile_task_deadline_notifications,
    refresh_notifications,
    refresh_party_development_notifications,
)
from app.security import hash_token
from sqlalchemy import inspect, select, text

from .conftest import create_task


def test_schema_0019_to_0020_roundtrip_preserves_existing_rows(tmp_path: Path) -> None:
    """模拟 rc.9 数据库升级，并证明既有业务行不被增量迁移覆盖。"""

    runtime = DatabaseRuntime(
        Settings(data_dir=tmp_path, environment="test", strict_sqlite=False)
    )
    runtime.create_schema()
    config = runtime._alembic_config()
    with runtime.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0019")
    assert "party_development_cases" not in inspect(runtime.engine).get_table_names()
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users(
                    id, username, display_name, password_hash, role,
                    active, version, created_at, updated_at
                ) VALUES (
                    'upgrade-user', 'upgrade-user', '升级保留用户', 'hash',
                    'STAFF', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
    with runtime.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    inspector = inspect(runtime.engine)
    assert "party_development_cases" in inspector.get_table_names()
    assert {"archived_at", "archived_by"}.issubset(
        {item["name"] for item in inspector.get_columns("users")}
    )
    with runtime.engine.connect() as connection:
        preserved = connection.execute(
            text(
                "SELECT display_name, archived_at FROM users "
                "WHERE id = 'upgrade-user'"
            )
        ).one()
    assert preserved == ("升级保留用户", None)


def _forbidden() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://host/api/v1/backups/latest",
        403,
        "Forbidden",
        {},
        io.BytesIO(b"{}"),
    )


def _write_config(path: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "host_url": "https://host",
        "backup_dir": str(path.parent / "backups"),
        "open_browser": False,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_desktop_page_handoff_is_independent_from_agent_backup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    destination = tmp_path / "client-browser.url"
    _write_config(config_path, device_token="expired-device-token")
    monkeypatch.setattr(
        client_agent,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("桌面入口不应执行心跳")),
    )
    monkeypatch.setattr(
        client_agent,
        "pull_backup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("桌面入口不应拉取灾备")),
    )
    monkeypatch.setattr(
        client_agent,
        "create_browser_launch_url",
        lambda *_args, **_kwargs: "https://host/device-launch?token=short-lived",
    )

    assert client_agent.run(
        config_path,
        once=True,
        open_browser=False,
        browser_url_file=destination,
    ) == 0
    assert destination.read_text(encoding="utf-8").startswith("https://host/device-launch")


def test_device_403_marks_reauthorization_and_stops_agent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    _write_config(config_path, device_token="expired-device-token")
    monkeypatch.setattr(
        client_agent,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_forbidden()),
    )

    assert client_agent.run(config_path, once=True, open_browser=False) == 4
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["authentication_state"] == "reauth_required"
    assert saved["last_agent_error"] == "heartbeat"
    assert saved["protocol_version"] == 2
    assert saved["runtime_version"] == "1.4.5-rc.3"


def test_legacy_pairing_403_stops_backup_retry_loop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    _write_config(config_path, pairing_token="expired-pairing-token")
    monkeypatch.setattr(
        client_agent,
        "pull_backup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_forbidden()),
    )

    assert client_agent.run(config_path, once=True, open_browser=False) == 4
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["authentication_state"] == "reauth_required"
    assert saved["last_agent_error"] == "backup_pull"


def test_task_due_change_updates_then_revokes_stale_notification(
    client,
    admin: dict,
) -> None:
    local_tz = timezone(timedelta(hours=8))
    due_local = datetime.combine(
        datetime.now(local_tz).date() + timedelta(days=3),
        datetime.min.time(),
        tzinfo=local_tz,
    ).replace(hour=12)
    due = due_local.astimezone(timezone.utc)
    task = create_task(
        client,
        admin["id"],
        title="1.4.4 通知联动回归",
        formal_due_at=due.isoformat(),
        internal_due_at=None,
    )
    with db_runtime.session_factory() as db:
        stored_task = db.get(Task, task["id"])
        assert stored_task is not None
        assert reconcile_task_deadline_notifications(db, stored_task) >= 1
        db.commit()
        first = db.query(Notification).filter(
            Notification.entity_id == task["id"],
            Notification.user_id == admin["id"],
            Notification.notification_type == "deadline",
            Notification.revoked_at.is_(None),
        ).one()
        first_id = first.id
        first_body = first.body

    moved_same_day = due.replace(hour=(due.hour + 2) % 24)
    response = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"If-Match": str(task["version"])},
        json={"formal_due_at": moved_same_day.isoformat()},
    )
    assert response.status_code == 200, response.text
    updated_task = response.json()
    with db_runtime.session_factory() as db:
        active = db.query(Notification).filter(
            Notification.entity_id == task["id"],
            Notification.user_id == admin["id"],
            Notification.notification_type == "deadline",
            Notification.revoked_at.is_(None),
        ).all()
        assert len(active) == 1
        assert active[0].id == first_id
        assert active[0].body != first_body
        assert f"{moved_same_day.astimezone(local_tz):%H:%M}" in active[0].body

    response = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"If-Match": str(updated_task["version"])},
        json={"formal_due_at": (due + timedelta(days=4)).isoformat()},
    )
    assert response.status_code == 200, response.text
    with db_runtime.session_factory() as db:
        current = db.query(Notification).filter(
            Notification.entity_id == task["id"],
            Notification.user_id == admin["id"],
            Notification.notification_type == "deadline",
            Notification.revoked_at.is_(None),
        ).all()
        assert current == []
        assert db.get(Task, task["id"]) is not None


def test_device_credential_upgrade_rotates_once_and_preserves_binding(client, admin: dict) -> None:
    old_token = "legacy-device-token"
    with db_runtime.session_factory() as db:
        device = Device(
            name="1.4.4 凭据迁移终端",
            architecture="arm64",
            platform="uos",
            agent_token_hash=hash_token(old_token),
            created_by=admin["id"],
        )
        db.add(device)
        db.commit()
        device_id = device.id
    upgraded = client.post(
        "/api/v1/client-agents/credential/upgrade",
        headers={"X-PartyOps-Device-Token": old_token},
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["protocol_version"] == 2
    new_token = upgraded.json()["device_token"]
    assert new_token != old_token
    assert client.post(
        "/api/v1/devices/heartbeat",
        headers={"X-PartyOps-Device-Token": old_token},
        json={"architecture": "arm64", "platform": "uos"},
    ).status_code == 401
    current = client.post(
        "/api/v1/devices/heartbeat",
        headers={"X-PartyOps-Device-Token": new_token},
        json={"architecture": "arm64", "platform": "uos", "protocol_version": 2},
    )
    assert current.status_code == 200, current.text
    assert current.json()["id"] == device_id


def test_user_archive_requires_transfer_and_can_restore(client, admin: dict) -> None:
    source = client.post("/api/v1/admin/users", json={"username": "archive-source", "display_name": "待移交用户", "password": "PartyOps@2026", "role": "staff"}).json()
    receiver = client.post("/api/v1/admin/users", json={"username": "archive-target", "display_name": "责任接收用户", "password": "PartyOps@2026", "role": "staff"}).json()
    task = create_task(client, source["id"], title="需要责任移交的事项")
    impact = client.get(f"/api/v1/admin/users/{source['id']}/deletion-impact")
    assert impact.status_code == 200
    assert impact.json()["requires_transfer"] is True
    assert client.delete(f"/api/v1/admin/users/{source['id']}").status_code == 409
    archived = client.delete(f"/api/v1/admin/users/{source['id']}?transfer_to={receiver['id']}")
    assert archived.status_code == 200, archived.text
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["owner_id"] == receiver["id"]
    restored = client.post(f"/api/v1/admin/users/{source['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["active"] is True


def test_meeting_workflow_structured_document_and_development_case(client, admin: dict) -> None:
    templates = client.get("/api/v1/workflow-templates")
    assert templates.status_code == 200, templates.text
    template_id = templates.json()[0]["id"]
    meeting = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "中共测试委员会",
            "title": "八月党委会",
            "scheduled_at": "2026-08-28T09:00:00+08:00",
            "workflow_template_id": template_id,
            "owner_id": admin["id"],
        },
    )
    assert meeting.status_code == 201, meeting.text
    assert meeting.json()["progress"]["total"] == 6
    invalid_template = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "中共测试委员会",
            "title": "无效流程会议",
            "workflow_template_id": "missing-template",
        },
    )
    assert invalid_template.status_code == 422
    precise_amount = client.post(
        f"/api/v1/business-meetings/{meeting.json()['id']}/topics",
        json={"title": "精度异常", "amount": "1.001"},
    )
    assert precise_amount.status_code == 422
    document = client.post(
        "/api/v1/business-documents",
        json={
            "meeting_id": meeting.json()["id"],
            "document_type": "agenda",
            "title": "八月党委会议程",
            "content": {"blocks": [{"type": "heading", "text": "八月党委会议程", "level": 1}]},
        },
    )
    assert document.status_code == 201, document.text
    changed = client.patch(
        f"/api/v1/business-documents/{document.json()['id']}",
        headers={"If-Match": str(document.json()["version"])},
        json={"content": {"blocks": [{"type": "paragraph", "text": "审议第一议题"}]}, "change_note": "补充议题"},
    )
    assert changed.status_code == 200, changed.text
    assert client.get(f"/api/v1/business-documents/{document.json()['id']}/export.docx").status_code == 200

    case = client.post(
        "/api/v1/party-development/cases",
        json={
            "party_committee": "中共测试委员会",
            "party_branch": "第一党支部",
            "name": "张三",
            "application_date": "2026-01-15",
            "training_contacts": ["李四"],
            "introducers": [],
        },
    )
    assert case.status_code == 201, case.text
    generated = client.post(f"/api/v1/party-development/cases/{case.json()['id']}/generate-milestones")
    assert generated.status_code == 200, generated.text
    first_nodes = {node["milestone_type"]: node for node in generated.json()["milestones"]}
    assert first_nodes["application"]["actual_at"].startswith("2026-01-15")
    assert first_nodes["application"]["version"] == 1
    regenerated = client.post(f"/api/v1/party-development/cases/{case.json()['id']}/generate-milestones")
    assert regenerated.status_code == 200, regenerated.text
    second_nodes = {node["milestone_type"]: node for node in regenerated.json()["milestones"]}
    assert second_nodes["application"]["actual_at"].startswith("2026-01-15")
    assert second_nodes["application"]["version"] == 2
    assert generated.json()["milestones"]
    assert any(row["legal_deadline_at"] and not row["actual_at"] for row in generated.json()["milestones"])
    docx_export = client.get("/api/v1/party-development/cases/export.docx")
    xlsx_export = client.get("/api/v1/party-development/cases/export.xlsx")
    assert docx_export.status_code == 200 and docx_export.content.startswith(b"PK")
    assert xlsx_export.status_code == 200 and xlsx_export.content.startswith(b"PK")
    assert "filename*=UTF-8''" in docx_export.headers["content-disposition"]


def test_monthly_meeting_generation_is_idempotent(client, admin: dict) -> None:
    template = client.post(
        "/api/v1/workflow-templates",
        json={
            "name": "每月党委会自动筹备（1.4.4 回归）",
            "business_type": "party_committee",
            "description": "验证周期调度不会重复建会。",
            "steps": [
                {
                    "title": "会议议程起草",
                    "offset_days": -10,
                    "responsible_role": "承办人",
                    "required_document": "agenda",
                }
            ],
            "recurrence": {
                "kind": "monthly",
                "enabled": True,
                "organization": "中共自动化测试委员会",
                "day": 20,
                "hour": 9,
                "months_ahead": 1,
                "owner_id": admin["id"],
            },
        },
    )
    assert template.status_code == 201, template.text
    first = client.post("/api/v1/business-meetings/recurring/generate")
    assert first.status_code == 200, first.text
    assert first.json()["generated"] == 2
    second = client.post("/api/v1/business-meetings/recurring/generate")
    assert second.status_code == 200, second.text
    assert second.json()["generated"] == 0


def test_business_workflow_adversarial_lifecycle(client, admin: dict) -> None:
    """覆盖会议、议题和结构化文档的失败边界与统计闭环。"""

    assert client.post(
        "/api/v1/workflow-templates",
        json={"name": "空流程", "business_type": "party_committee", "steps": []},
    ).status_code == 422
    template = client.post(
        "/api/v1/workflow-templates",
        json={
            "name": "对抗式会议流程",
            "business_type": "party_committee",
            "steps": [
                {"title": "起草", "offset_days": -2, "responsible_role": "承办人"},
                {"title": "记录", "offset_days": 0, "responsible_role": "记录人"},
            ],
        },
    )
    assert template.status_code == 201, template.text
    template_id = template.json()["id"]
    assert client.delete("/api/v1/workflow-templates/missing").status_code == 404
    archived = client.delete(f"/api/v1/workflow-templates/{template_id}")
    assert archived.status_code == 200
    archived_meeting = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "测试党委",
            "title": "停用模板会议",
            "workflow_template_id": template_id,
        },
    )
    assert archived_meeting.status_code == 422

    base = {
        "organization": "测试党委",
        "title": "九月党委会",
        "scheduled_at": "2026-09-20T09:00:00+08:00",
    }
    assert client.post("/api/v1/business-meetings", json={**base, "meeting_type": "unknown"}).status_code == 422
    assert client.post(
        "/api/v1/business-meetings",
        json={**base, "meeting_type": "party_committee", "owner_id": "missing"},
    ).status_code == 422
    assert client.post(
        "/api/v1/business-meetings",
        json={**base, "meeting_type": "party_committee", "assignees": {"承办人": "missing"}},
    ).status_code == 422
    meeting = client.post(
        "/api/v1/business-meetings",
        json={
            **base,
            "meeting_type": "party_committee",
            "owner_id": admin["id"],
            "assignees": {"承办人": admin["id"]},
            "recurrence_key": "adversarial-2026-09",
        },
    )
    assert meeting.status_code == 201, meeting.text
    meeting_id = meeting.json()["id"]
    assert meeting.json()["steps"][0]["assignee_id"] == admin["id"]
    duplicate = client.post(
        "/api/v1/business-meetings",
        json={**base, "meeting_type": "party_committee", "recurrence_key": "adversarial-2026-09"},
    )
    assert duplicate.status_code == 409
    assert client.get("/api/v1/business-meetings?year=2026&organization=测试党委&meeting_type=party_committee").json()
    assert client.patch(
        "/api/v1/business-meetings/missing", headers={"If-Match": "1"}, json={"status": "completed"}
    ).status_code == 404
    assert client.patch(
        f"/api/v1/business-meetings/{meeting_id}", headers={"If-Match": "999"}, json={"status": "completed"}
    ).status_code == 409
    changed = client.patch(
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(meeting.json()["version"])},
        json={"scheduled_at": "2026-09-27T09:00:00+08:00", "status": "completed"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["completed_at"]

    assert client.post("/api/v1/business-meetings/missing/topics", json={"title": "不存在"}).status_code == 404
    for amount in ("abc", "-1", "1.001", "90000000000001"):
        assert client.post(
            f"/api/v1/business-meetings/{meeting_id}/topics",
            json={"title": "金额边界", "amount": amount},
        ).status_code == 422
    topic = client.post(
        f"/api/v1/business-meetings/{meeting_id}/topics",
        json={"title": "审议预算", "review_result": "通过", "amount": "123.45", "reviewed": True, "amount_confirmed": True},
    )
    assert topic.status_code == 201, topic.text
    stats = client.get("/api/v1/business-meetings/statistics/annual?year=2026&organization=测试党委&meeting_type=party_committee")
    assert stats.status_code == 200
    assert stats.json()["reviewed_topics"] == 1
    assert stats.json()["confirmed_amount"] == "123.45"
    assert client.get("/api/v1/business-meetings").status_code == 200
    assert client.get("/api/v1/business-meetings/statistics/annual?year=2099").json()["completed_meetings"] == 0
    status_only = client.patch(
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(changed.json()["version"])},
        json={"status": "planned"},
    )
    assert status_only.status_code == 200
    cleared_schedule = client.patch(
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(status_only.json()["version"])},
        json={"scheduled_at": None},
    )
    assert cleared_schedule.status_code == 200
    assert all(step["due_at"] is None for step in cleared_schedule.json()["steps"])

    assert client.post(
        "/api/v1/business-documents",
        json={"meeting_id": "missing", "document_type": "agenda", "title": "错误关联"},
    ).status_code == 422
    assert client.post(
        "/api/v1/business-documents",
        json={"task_step_id": "missing", "document_type": "agenda", "title": "错误步骤"},
    ).status_code == 422
    step_id = changed.json()["steps"][0]["id"]
    document = client.post(
        "/api/v1/business-documents",
        json={
            "meeting_id": meeting_id,
            "task_step_id": step_id,
            "document_type": "agenda",
            "title": "结构化议程",
            "content": {"blocks": []},
        },
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]
    standalone = client.post(
        "/api/v1/business-documents",
        json={"document_type": "other", "title": "独立结构化文档", "content": {}},
    )
    assert standalone.status_code == 201
    assert client.get("/api/v1/business-documents").status_code == 200
    assert client.get(f"/api/v1/business-documents?meeting_id={meeting_id}").json()[0]["id"] == document_id
    assert client.patch(
        "/api/v1/business-documents/missing", headers={"If-Match": "1"}, json={"title": "不存在"}
    ).status_code == 404
    assert client.patch(
        f"/api/v1/business-documents/{document_id}", headers={"If-Match": "999"}, json={"title": "冲突"}
    ).status_code == 409
    edited = client.patch(
        f"/api/v1/business-documents/{document_id}",
        headers={"If-Match": str(document.json()["version"])},
        json={
            "title": "结构化议程（修订）",
            "content": {
                "blocks": [
                    {"type": "heading", "text": "议程", "level": 9},
                    {"type": "list", "text": "第一项"},
                    {"type": "paragraph", "text": "会议内容"},
                    "忽略无效块",
                ]
            },
            "change_note": "覆盖导出分支",
        },
    )
    assert edited.status_code == 200, edited.text
    title_only = client.patch(
        f"/api/v1/business-documents/{document_id}",
        headers={"If-Match": str(edited.json()["version"])},
        json={"title": "只修改标题"},
    )
    assert title_only.status_code == 200
    content_only = client.patch(
        f"/api/v1/business-documents/{document_id}",
        headers={"If-Match": str(title_only.json()["version"])},
        json={"content": {"blocks": [{"type": "paragraph", "text": "只修改正文"}]}},
    )
    assert content_only.status_code == 200
    assert len(client.get(f"/api/v1/business-documents/{document_id}/revisions").json()) == 4
    assert client.get("/api/v1/business-documents/missing/revisions").status_code == 404
    assert client.get(f"/api/v1/business-documents/{document_id}/export.docx").status_code == 200
    assert client.get("/api/v1/business-documents/missing/export.docx").status_code == 404

    other_meeting = client.post(
        "/api/v1/business-meetings",
        json={"meeting_type": "party_committee", "organization": "另一党委", "title": "另一会议"},
    )
    assert other_meeting.status_code == 201
    mismatch = client.post(
        "/api/v1/business-documents",
        json={
            "meeting_id": other_meeting.json()["id"],
            "task_step_id": step_id,
            "document_type": "agenda",
            "title": "跨会议错误关联",
        },
    )
    assert mismatch.status_code == 422


def test_user_lifecycle_adversarial_boundaries(client, admin: dict) -> None:
    """用户编辑、归档和恢复必须保护当前用户、最后管理员与版本并发。"""

    assert client.get("/api/v1/users/missing/deletion-impact").status_code == 404
    assert client.delete("/api/v1/users/missing").status_code == 404
    assert client.post("/api/v1/users/missing/restore").status_code == 404
    assert client.delete(f"/api/v1/users/{admin['id']}").status_code == 409
    assert client.patch(
        "/api/v1/admin/users/missing", headers={"If-Match": "1"}, json={"display_name": "不存在"}
    ).status_code == 404
    assert client.patch(
        f"/api/v1/admin/users/{admin['id']}", json={"display_name": "缺少版本"}
    ).status_code == 428
    assert client.patch(
        f"/api/v1/admin/users/{admin['id']}", headers={"If-Match": "bad"}, json={"display_name": "版本错误"}
    ).status_code == 400
    assert client.patch(
        f"/api/v1/admin/users/{admin['id']}", headers={"If-Match": "999"}, json={"display_name": "冲突"}
    ).status_code == 409
    assert client.patch(
        f"/api/v1/admin/users/{admin['id']}", headers={"If-Match": str(admin["version"])}, json={"active": False}
    ).status_code == 409
    assert client.patch(
        f"/api/v1/admin/users/{admin['id']}", headers={"If-Match": str(admin["version"])}, json={"role": "staff"}
    ).status_code == 409

    target = client.post(
        "/api/v1/admin/users",
        json={"username": "lifecycle-empty", "display_name": "生命周期用户", "password": "PartyOps@2026", "role": "staff"},
    ).json()
    updated = client.patch(
        f"/api/v1/admin/users/{target['id']}",
        headers={"If-Match": str(target["version"])},
        json={"display_name": "生命周期用户已编辑", "role": "admin"},
    )
    assert updated.status_code == 200, updated.text
    archived = client.delete(f"/api/v1/users/{target['id']}")
    assert archived.status_code == 200, archived.text
    assert client.delete(f"/api/v1/users/{target['id']}").status_code == 200
    restored = client.post(f"/api/v1/users/{target['id']}/restore")
    assert restored.status_code == 200 and restored.json()["active"] is True

    responsibility = client.post(
        "/api/v1/admin/users",
        json={"username": "lifecycle-responsible", "display_name": "责任用户", "password": "PartyOps@2026", "role": "staff"},
    ).json()
    create_task(client, responsibility["id"], title="拒绝无效移交")
    assert client.delete(
        f"/api/v1/users/{responsibility['id']}?transfer_to={responsibility['id']}"
    ).status_code == 409
    assert client.delete(
        f"/api/v1/users/{responsibility['id']}?transfer_to=missing"
    ).status_code == 409


def test_network_configuration_validation_and_pending_update(client, admin: dict) -> None:
    """网络地址修改先校验并持久化待重启事务，重复写入更新同一快照。"""

    current = client.get("/api/v1/system/network")
    assert current.status_code == 200, current.text
    value = current.json()
    for payload in (
        {"port": "not-a-port"},
        {"port": 100},
        {"bind_host": "http://127.0.0.1", "advertise_host": "127.0.0.1"},
        {"bind_host": "", "advertise_host": "127.0.0.1"},
        {"bind_host": "0.0.0.0", "advertise_host": "127.0.0.1"},
        {"bind_host": "0.0.0.0", "advertise_host": "0.0.0.0"},
    ):
        assert client.post("/api/v1/system/network/validate", json=payload).status_code == 422
    advertised = "192.168.123.8"
    validated = client.post(
        "/api/v1/system/network/validate",
        json={"bind_host": "0.0.0.0", "advertise_host": advertised, "port": value["port"] + 11},
    )
    assert validated.status_code == 200 and validated.json()["valid"] is True
    first = client.patch(
        "/api/v1/system/network",
        json={
            "bind_host": "0.0.0.0",
            "advertise_host": advertised,
            "port": value["port"] + 11,
            "migration_grace_hours": 999,
        },
    )
    assert first.status_code == 200 and first.json()["restart_required"] is True
    assert first.json()["migration_grace_hours"] == 168
    assert first.json()["transaction_id"]
    status = client.get(f"/api/v1/system/network/transactions/{first.json()['transaction_id']}")
    assert status.status_code == 200 and status.json()["state"] == "restart_required"
    second = client.patch(
        "/api/v1/system/network",
        json={
            "bind_host": "0.0.0.0",
            "advertise_host": advertised,
            "port": value["port"] + 12,
            "migration_grace_hours": 0,
        },
    )
    assert second.status_code == 200 and second.json()["migration_grace_hours"] == 1
    pending = client.get("/api/v1/system/network").json()["pending"]
    assert pending and pending["requested"]["port"] == value["port"] + 12


def test_meeting_legacy_schedule_fallback_and_recurring_owner_fallback(client, admin: dict) -> None:
    """无模板旧会议仍可改期；停用周期负责人自动回退管理员。"""

    meeting = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "旧数据党委",
            "title": "旧数据会议",
            "scheduled_at": "2026-10-01T09:00:00+08:00",
        },
    )
    assert meeting.status_code == 201, meeting.text
    meeting_id = meeting.json()["id"]
    with db_runtime.session_factory() as db:
        stored = db.get(BusinessMeeting, meeting_id)
        stored.workflow_template_id = None
        db.commit()
    moved = client.patch(
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(meeting.json()["version"])},
        json={"scheduled_at": "2026-10-03T09:00:00+08:00"},
    )
    assert moved.status_code == 200, moved.text
    cleared = client.patch(
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(moved.json()["version"])},
        json={"scheduled_at": None},
    )
    assert cleared.status_code == 200 and all(step["due_at"] is None for step in cleared.json()["steps"])

    inactive = client.post(
        "/api/v1/admin/users",
        json={"username": "recurring-inactive", "display_name": "停用周期负责人", "password": "PartyOps@2026"},
    ).json()
    disabled = client.patch(
        f"/api/v1/admin/users/{inactive['id']}",
        headers={"If-Match": str(inactive["version"])},
        json={"active": False},
    )
    assert disabled.status_code == 200
    no_org = client.post(
        "/api/v1/workflow-templates",
        json={
            "name": "缺少组织的周期流程",
            "business_type": "party_committee",
            "steps": [{"title": "准备"}],
            "recurrence": {"kind": "monthly", "enabled": True},
        },
    )
    assert no_org.status_code == 201
    owner_fallback = client.post(
        "/api/v1/workflow-templates",
        json={
            "name": "停用负责人的周期流程",
            "business_type": "party_committee",
            "steps": [{"title": "准备"}],
            "recurrence": {
                "kind": "monthly",
                "enabled": True,
                "organization": "周期党委",
                "owner_id": inactive["id"],
                "months_ahead": 0,
            },
        },
    )
    assert owner_fallback.status_code == 201
    generated = client.post("/api/v1/business-meetings/recurring/generate")
    assert generated.status_code == 200
    assert any(
        item["organization"] == "周期党委"
        for item in client.get("/api/v1/business-meetings").json()
    )


def test_party_development_case_and_milestone_boundaries(client, admin: dict) -> None:
    """档案和节点保持实际日期、参考日期、提醒策略及版本冲突边界。"""

    assert client.patch(
        "/api/v1/party-development/cases/missing", headers={"If-Match": "1"}, json={"name": "无"}
    ).status_code == 404
    assert client.post("/api/v1/party-development/cases/missing/generate-milestones").status_code == 404
    assert client.patch(
        "/api/v1/party-development/milestones/missing", headers={"If-Match": "1"}, json={"actual_date": "2026-08-21"}
    ).status_code == 404
    created = client.post(
        "/api/v1/party-development/cases",
        json={
            "party_committee": "中共覆盖率委员会",
            "party_branch": "覆盖率支部",
            "name": "李雷",
            "gender": "男",
            "ethnicity": "汉族",
            "birth_date": "1990-01-02",
            "education": "本科",
            "application_date": "2025-01-01",
            "activist_date": "2025-08-01",
            "training_contacts": ["甲", "乙"],
            "introducers": ["丙"],
        },
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    assert client.patch(
        f"/api/v1/party-development/cases/{case_id}", headers={"If-Match": "999"}, json={"name": "冲突"}
    ).status_code == 409
    changed = client.patch(
        f"/api/v1/party-development/cases/{case_id}",
        headers={"If-Match": str(created.json()["version"])},
        json={
            "name": "李雷（修订）",
            "birth_date": "1990-02-03",
            "activist_date": "2025-09-01",
            "development_object_date": "2026-06-01",
            "probationary_date": "2026-07-01",
            "converted_date": None,
            "stage": "probationary",
        },
    )
    assert changed.status_code == 200, changed.text
    generated = client.post(f"/api/v1/party-development/cases/{case_id}/generate-milestones")
    assert generated.status_code == 200, generated.text
    pending = next(row for row in generated.json()["milestones"] if not row["actual_at"])
    assert client.patch(
        f"/api/v1/party-development/milestones/{pending['id']}",
        headers={"If-Match": "999"},
        json={"adjusted_date": "2026-08-22"},
    ).status_code == 409
    assert client.patch(
        f"/api/v1/party-development/milestones/{pending['id']}",
        headers={"If-Match": str(pending["version"])},
        json={"reminder_days": [-1, 4000]},
    ).status_code == 422
    milestone = client.patch(
        f"/api/v1/party-development/milestones/{pending['id']}",
        headers={"If-Match": str(pending["version"])},
        json={"actual_date": None, "adjusted_date": "2026-08-22", "reminder_days": [1, 30, 1]},
    )
    assert milestone.status_code == 200, milestone.text
    assert milestone.json()["rule_version"] == "2026.05"
    listed = client.get(
        "/api/v1/party-development/cases?party_committee=中共覆盖率委员会&party_branch=覆盖率支部&case_status=active"
    )
    assert listed.status_code == 200 and listed.json()
    stats = client.get(
        "/api/v1/party-development/statistics?party_committee=中共覆盖率委员会&party_branch=覆盖率支部"
    )
    assert stats.status_code == 200 and stats.json()["total"] == 1
    assert client.get(
        "/api/v1/party-development/cases/export.docx?party_committee=中共覆盖率委员会&party_branch=覆盖率支部"
    ).status_code == 200
    assert client.get(
        "/api/v1/party-development/cases/export.xlsx?party_committee=中共覆盖率委员会&party_branch=覆盖率支部"
    ).status_code == 200


def test_party_development_notification_converges_after_date_change(client, admin: dict) -> None:
    """节点改期应更新、撤销当前未读提醒，而不是保留新旧两条。"""

    created = client.post(
        "/api/v1/party-development/cases",
        json={
            "party_committee": "中共提醒委员会",
            "party_branch": "提醒支部",
            "name": "提醒测试人",
            "application_date": "2025-01-01",
        },
    )
    case_id = created.json()["id"]
    generated = client.post(f"/api/v1/party-development/cases/{case_id}/generate-milestones").json()
    milestone_id = next(row["id"] for row in generated["milestones"] if not row["actual_at"])
    with db_runtime.session_factory() as db:
        milestone = db.get(PartyDevelopmentMilestone, milestone_id)
        milestone.actual_at = None
        milestone.adjusted_at = datetime.now(timezone.utc) + timedelta(days=1)
        db.commit()
        first = refresh_party_development_notifications(db, now=datetime.now(timezone.utc))
        db.commit()
        assert first > 0
        assert refresh_party_development_notifications(db, now=datetime.now(timezone.utc)) == 0
        notice = db.scalar(
            select(Notification).where(
                Notification.entity_type == "party_development_case",
                Notification.entity_id == case_id,
                Notification.dedupe_key.like(f"%:{milestone_id}:%"),
                Notification.revoked_at.is_(None),
            )
        )
        assert notice is not None
        notice.title = "旧标题"
        db.commit()
        assert refresh_party_development_notifications(db, now=datetime.now(timezone.utc)) > 0
        db.commit()
        milestone.adjusted_at = datetime.now(timezone.utc) + timedelta(days=90)
        db.commit()
        assert refresh_party_development_notifications(db, now=datetime.now(timezone.utc)) > 0
        db.commit()
        assert db.scalar(
            select(Notification).where(
                Notification.entity_type == "party_development_case",
                Notification.entity_id == case_id,
                Notification.dedupe_key.like(f"%:{milestone_id}:%"),
                Notification.revoked_at.is_(None),
            )
        ) is None
        milestone.adjusted_at = None
        milestone.planned_at = None
        milestone.legal_deadline_at = None
        db.commit()
        assert refresh_party_development_notifications(db, now=datetime.now(timezone.utc)) == 0


def test_notification_disabled_preference_and_daily_refresh_branches(client, admin: dict) -> None:
    """停用偏好阻止弹窗，日提醒在跨日后复用同一通知行。"""

    create_task(
        client,
        admin["id"],
        title="提醒分支事项",
        formal_due_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        internal_due_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    with db_runtime.session_factory() as db:
        user = db.get(User, admin["id"])
        task = db.scalar(select(Task).where(Task.title == "提醒分支事项"))
        preference = db.get(ReminderPreference, user.id)
        if preference is None:
            preference = ReminderPreference(user_id=user.id)
            db.add(preference)
            db.flush()
            db.refresh(preference)
        original_enabled = preference.enabled
        original_desktop_enabled = preference.desktop_enabled
        try:
            preference.enabled = False
            preference.desktop_enabled = True
            db.commit()
            assert desktop_notifications_allowed(preference) is False
            assert reconcile_task_deadline_notifications(db, task, now=datetime.now(timezone.utc)) >= 0
            assert refresh_notifications(db) >= 0
            db.commit()

            key = f"{user.id}:{task.id}:daily-coverage"
            now = datetime.now(timezone.utc)
            assert _refresh_daily(db, user, task, "coverage", "今日提醒", "正文", key, now) is True
            db.commit()
            assert _refresh_daily(db, user, task, "coverage", "今日提醒", "正文", key, now) is False
            notice = db.scalar(select(Notification).where(Notification.dedupe_key == key))
            notice.created_at = now - timedelta(days=2)
            db.commit()
            assert _refresh_daily(db, user, task, "coverage", "跨日刷新", "新正文", key, now) is True
            db.commit()
        finally:
            preference.enabled = original_enabled
            preference.desktop_enabled = original_desktop_enabled
            db.commit()
