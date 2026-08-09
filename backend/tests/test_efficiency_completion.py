"""效率功能闭环的独立回归测试。"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.ai_service import _citation_excerpt

from .conftest import create_task


def test_global_search_batch_and_smart_folders(
    client: TestClient, admin: dict
) -> None:
    task = create_task(
        client,
        admin["id"],
        title="跨域检索甲事项",
        annual_focus="年度重点",
    )
    contact = client.post(
        "/api/v1/contacts",
        json={
            "name": "跨域检索甲联系人",
            "organization": "测试组织",
            "phone": "",
            "note": "跨域检索甲",
        },
    )
    assert contact.status_code == 201, contact.text
    journal = client.post(
        "/api/v1/work-journal",
        json={
            "title": "跨域检索甲日志",
            "content": "用于验证统一搜索。",
            "task_id": task["id"],
        },
    )
    assert journal.status_code == 201, journal.text

    result = client.get(
        "/api/v1/global-search",
        params={"q": "跨域检索甲", "limit": 100},
    )
    assert result.status_code == 200, result.text
    types = {item["type"] for item in result.json()["items"]}
    assert {"task", "contact", "journal"}.issubset(types)
    assert "description" not in result.text

    batch = client.post(
        "/api/v1/tasks/batch",
        json={
            "task_ids": [task["id"]],
            "planned_start_at": "2031-01-06T00:00:00+08:00",
            "planned_end_at": "2031-01-12T23:59:59+08:00",
            "tags": ["批量", "计划周"],
            "note": "效率闭环测试",
        },
    )
    assert batch.status_code == 200, batch.text
    updated = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert updated["planned_start_at"].startswith("2031-01-")
    assert updated["tags"] == ["批量", "计划周"]

    smart = client.get("/api/v1/search", params={"smart": "annual_focus"})
    assert smart.status_code == 200, smart.text
    assert any(item["id"] == task["id"] for item in smart.json()["items"])


def test_recurrence_generates_at_internal_node_and_uses_report_template(
    client: TestClient, admin: dict
) -> None:
    template = client.post(
        "/api/v1/templates",
        json={
            "name": "提前生成周期模板-效率闭环",
            "category": "周期测试",
            "task_type": "standard",
            "description": "验证正式节点前生成。",
            "steps": ["准备材料"],
            "materials": [{"category": "final", "name": "最终稿", "required": True}],
        },
    )
    assert template.status_code == 201, template.text
    formal_due = datetime.now(timezone.utc) + timedelta(days=2)
    recurrence = client.post(
        "/api/v1/recurrences",
        json={
            "name": "提前生成周期规则-效率闭环",
            "template_id": template.json()["id"],
            "owner_id": admin["id"],
            "kind": "monthly",
            "internal_lead_days": 5,
            "next_run_at": formal_due.isoformat(),
        },
    )
    assert recurrence.status_code == 201, recurrence.text
    generated = client.post("/api/v1/recurrences/run-due")
    assert generated.status_code == 200, generated.text
    generated_tasks = [
        client.get(f"/api/v1/tasks/{task_id}").json()
        for task_id in generated.json()
    ]
    matched = next(
        item
        for item in generated_tasks
        if item["recurrence_rule_id"] == recurrence.json()["id"]
    )
    formal_value = datetime.fromisoformat(matched["formal_due_at"])
    internal_value = datetime.fromisoformat(matched["internal_due_at"])
    formal_value = formal_value if formal_value.tzinfo else formal_value.replace(tzinfo=timezone.utc)
    internal_value = internal_value if internal_value.tzinfo else internal_value.replace(tzinfo=timezone.utc)
    assert formal_value > datetime.now(timezone.utc)
    assert internal_value <= datetime.now(timezone.utc)

    report_template = client.post(
        "/api/v1/report-templates",
        json={
            "name": "效率闭环年度模板",
            "period_type": "year",
            "description": "只保留完成和风险栏目",
            "sections": ["completed", "risk"],
        },
    )
    assert report_template.status_code == 201, report_template.text
    report = client.post(
        "/api/v1/period-reports",
        json={
            "period_type": "year",
            "anchor_at": "2032-06-01T00:00:00+08:00",
            "template_id": report_template.json()["id"],
            "auto_fill": False,
        },
    )
    assert report.status_code == 201, report.text
    assert report.json()["snapshot"]["design"]["sections"] == ["completed", "risk"]


def test_rules_calendar_handover_and_citation_are_maintainable(
    client: TestClient, admin: dict
) -> None:
    rule = client.post(
        "/api/v1/automation-rules",
        json={
            "name": "效率闭环归档建议",
            "trigger": "workspace_file_indexed",
            "conditions": {"name_contains": "报送稿", "extensions": [".docx"]},
            "actions": {"type": "archive_suggestion", "tags": ["报送", "终稿"]},
            "enabled": True,
        },
    )
    assert rule.status_code == 201, rule.text
    disabled = client.patch(
        f"/api/v1/automation-rules/{rule.json()['id']}",
        headers={"If-Match": str(rule.json()["version"])},
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert client.delete(f"/api/v1/automation-rules/{rule.json()['id']}").status_code == 428
    assert client.delete(
        f"/api/v1/automation-rules/{rule.json()['id']}",
        headers={"If-Match": str(disabled.json()["version"])},
    ).status_code == 200

    calendar = client.post(
        "/api/v1/work-calendar",
        json={
            "date_key": "2031-05-02",
            "title": "效率闭环休息日",
            "kind": "holiday",
            "is_workday": False,
        },
    )
    assert calendar.status_code == 201, calendar.text
    changed = client.patch(
        f"/api/v1/work-calendar/{calendar.json()['id']}",
        headers={"If-Match": str(calendar.json()["version"])},
        json={"note": "单位统一工作日历"},
    )
    assert changed.status_code == 200, changed.text
    assert client.delete(f"/api/v1/work-calendar/{calendar.json()['id']}").status_code == 428
    assert client.delete(
        f"/api/v1/work-calendar/{calendar.json()['id']}",
        headers={"If-Match": str(changed.json()["version"])},
    ).status_code == 200

    contact = client.post(
        "/api/v1/contacts",
        json={
            "name": "交接联系人-效率闭环",
            "organization": "党建办",
            "phone": "内线",
            "note": "",
        },
    )
    assert contact.status_code == 201, contact.text
    create_task(
        client,
        admin["id"],
        title="交接事项-效率闭环",
        contact_ids=[contact.json()["id"]],
    )
    handover = client.post("/api/v1/handover")
    assert handover.status_code == 201, handover.text
    package = client.get(f"/api/v1/handover/{handover.json()['id']}/download")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        task = next(item for item in manifest["tasks"] if item["title"] == "交接事项-效率闭环")
        assert task["contacts"][0]["name"] == "交接联系人-效率闭环"
        assert "校验清单.sha256" in archive.namelist()

    excerpt = _citation_excerpt("第一段\n\n第二段包含   多余空格", limit=20)
    assert "\n" not in excerpt
    assert "  " not in excerpt
