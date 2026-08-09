"""1.2.0 五域整合、日历、双向关联、引导和投影回归。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from .conftest import create_task


def test_today_calendar_preferences_and_workdays(
    client: TestClient,
    admin: dict,
) -> None:
    now = datetime.now(timezone.utc)
    # 测试数据必须落在北京时间当天；直接用 UTC now + 2 小时会在晚间跨到次日，
    # 造成“今日工作台”测试随执行时刻随机失败。
    beijing = timezone(timedelta(hours=8))
    local_today_noon = now.astimezone(beijing).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    task_time = local_today_noon.astimezone(timezone.utc)
    task = create_task(
        client,
        admin["id"],
        title="今日工作台与日历回归事项",
        formal_due_at=(task_time + timedelta(hours=2)).isoformat(),
        internal_due_at=(task_time + timedelta(hours=1)).isoformat(),
        planned_start_at=(task_time - timedelta(minutes=30)).isoformat(),
        planned_end_at=(task_time + timedelta(hours=3)).isoformat(),
    )
    today = client.get("/api/v1/today")
    assert today.status_code == 200, today.text
    assert task["id"] in {item["id"] for item in today.json()["today_tasks"]}

    calendar = client.get(
        "/api/v1/calendar/events",
        params={
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now + timedelta(days=2)).isoformat(),
        },
    )
    assert calendar.status_code == 200, calendar.text
    task_events = [item for item in calendar.json() if item["object_id"] == task["id"]]
    assert {item["event_type"] for item in task_events} >= {"task_due", "task_plan"}

    preference = client.get("/api/v1/calendar/preferences")
    assert preference.status_code == 200
    changed = client.patch(
        "/api/v1/calendar/preferences",
        json={"default_view": "month", "visible_event_types": ["task_due", "holiday"]},
        headers={"If-Match": str(preference.json()["version"])},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["default_view"] == "month"

    imported = client.post(
        "/api/v1/calendar/workdays/import",
        json={
            "items": [
                {
                    "date_key": "2026-09-30",
                    "title": "节前调休测试日",
                    "kind": "holiday",
                    "is_workday": False,
                    "note": "离线年度日历",
                }
            ]
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()[0]["title"] == "节前调休测试日"


def test_bidirectional_links_activity_onboarding_and_projection(
    client: TestClient,
    admin: dict,
) -> None:
    task = create_task(client, admin["id"], title="统一关联回归事项")
    contact = client.post(
        "/api/v1/contacts",
        json={"name": "关联联系人", "organization": "党建办", "phone": "", "note": ""},
    )
    assert contact.status_code == 201, contact.text
    created = client.post(
        f"/api/v1/objects/task/{task['id']}/links",
        json={
            "target_type": "contact",
            "target_id": contact.json()["id"],
            "link_type": "relates_to",
            "note": "负责报送口径",
        },
        headers={"Idempotency-Key": "test-object-link-120"},
    )
    assert created.status_code == 201, created.text
    repeated = client.post(
        f"/api/v1/objects/task/{task['id']}/links",
        json={
            "target_type": "contact",
            "target_id": contact.json()["id"],
            "link_type": "relates_to",
        },
        headers={"Idempotency-Key": "test-object-link-120"},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]

    reverse = client.get(
        f"/api/v1/objects/contact/{contact.json()['id']}/links"
    )
    assert reverse.status_code == 200, reverse.text
    assert reverse.json()[0]["direction"] == "incoming"
    activity = client.get(f"/api/v1/objects/task/{task['id']}/activity")
    assert activity.status_code == 200
    assert any(item["event_code"] == "object.linked" for item in activity.json())

    progress = client.get("/api/v1/me/onboarding")
    assert progress.status_code == 200
    assert {item["key"] for item in progress.json()["steps"]} >= {
        "calendar",
        "files",
        "devices",
    }
    changed = client.patch(
        "/api/v1/me/onboarding",
        json={"completed_steps": ["calendar", "files"]},
        headers={"If-Match": str(progress.json()["version"])},
    )
    assert changed.status_code == 200
    assert changed.json()["completed_steps"] == ["calendar", "files"]

    rebuilt = client.post("/api/v1/admin/projections/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["name"] == "period_reports"
    status = client.get("/api/v1/admin/projections/status")
    assert status.status_code == 200
    assert status.json()[0]["status"] == "idle"


def test_recurrence_last_workday_preview(
    client: TestClient,
    admin: dict,
) -> None:
    template = client.post(
        "/api/v1/templates",
        json={
            "name": "月末最后工作日模板",
            "category": "周期回归",
            "task_type": "standard",
            "description": "",
            "steps": [],
            "materials": [],
        },
    )
    assert template.status_code == 201, template.text
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "name": "月末最后工作日规则",
            "template_id": template.json()["id"],
            "owner_id": admin["id"],
            "kind": "monthly",
            "internal_lead_days": 0,
            "next_run_at": "2026-10-31T10:00:00Z",
            "schedule_config": {"mode": "last_workday"},
        },
    )
    assert rule.status_code == 201, rule.text
    preview = client.get(f"/api/v1/recurrences/{rule.json()['id']}/preview?count=1")
    assert preview.status_code == 200, preview.text
    assert preview.json()[0]["occurrence_at"].startswith("2026-10-31")
    assert preview.json()[0]["effective_at"].startswith("2026-10-30")
