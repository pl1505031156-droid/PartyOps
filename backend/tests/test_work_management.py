"""1.1.1 综合工作管理、文件中心和 AI 权限回归。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from app import ai_service, client_agent, intake, upgrades, workspace
from app.config import get_settings
from app.database import db_runtime
from app.enums import AiCapability, PeriodType
from app.models import (
    AIPolicy,
    AIProviderConfig,
    BackgroundJob,
    Notification,
    PeriodReport,
    PeriodReportItem,
    ReminderPreference,
    User,
    WorkspaceFile,
    WorkspaceRoot,
)
from app.notifications import desktop_notifications_allowed
from app.problems import ProblemException
from app.reports import auto_fill_report, period_bounds
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from .conftest import create_task


def test_period_report_auto_fill_removes_legacy_duplicate_task_items(
    client: TestClient, admin: dict
) -> None:
    """投影重建应清理历史重复条目，避免周期汇总重复展示同一事项。"""

    task = create_task(
        client,
        admin["id"],
        title="投影去重测试事项",
        formal_due_at="2099-08-05T10:00:00+08:00",
        internal_due_at="2099-08-04T17:00:00+08:00",
        planned_start_at="2099-08-04T09:00:00+08:00",
        planned_end_at="2099-08-05T17:00:00+08:00",
        steps=[],
        materials=[],
    )
    report_response = client.post(
        "/api/v1/period-reports",
        json={
            "period_type": "week",
            "anchor_at": "2099-08-03T10:00:00+08:00",
            "title": "投影去重周报",
            "auto_fill": True,
        },
    )
    assert report_response.status_code == 201, report_response.text
    report_id = report_response.json()["id"]

    with db_runtime.session_factory() as db:
        report = db.get(PeriodReport, report_id)
        user = db.get(User, admin["id"])
        assert report is not None and user is not None
        original = db.scalar(
            select(PeriodReportItem).where(
                PeriodReportItem.report_id == report_id,
                PeriodReportItem.source_id == task["id"],
            )
        )
        assert original is not None
        db.add(
            PeriodReportItem(
                report_id=report_id,
                section=original.section,
                source_type="task",
                source_id=task["id"],
                title=original.title,
                content=original.content,
                sort_order=original.sort_order + 1,
                carried_over=original.carried_over,
                created_by=user.id,
            )
        )
        db.commit()

        changed = auto_fill_report(db, report, user)
        db.commit()
        remaining = db.scalars(
            select(PeriodReportItem).where(
                PeriodReportItem.report_id == report_id,
                PeriodReportItem.source_type == "task",
                PeriodReportItem.source_id == task["id"],
            )
        ).all()

    assert changed >= 1
    assert len(remaining) == 1


def test_period_report_journal_templates_and_status(
    client: TestClient, admin: dict
) -> None:
    task = create_task(
        client,
        admin["id"],
        title="周报关联事项",
        category="基层党建",
        work_area="组织建设",
        annual_focus="年度重点任务",
        reporting_scope="党委会周报",
        planned_start_at="2026-09-07T09:00:00+08:00",
        planned_end_at="2026-09-11T17:00:00+08:00",
        steps=[],
        materials=[],
    )
    assert task["work_area"] == "组织建设"

    response = client.post(
        "/api/v1/period-reports",
        json={
            "period_type": "week",
            "anchor_at": "2026-09-09T10:00:00+08:00",
            "title": "第 37 周工作报告",
            "summary": "周工作汇总",
            "auto_fill": False,
        },
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["period_key"] == "2026-W37"

    item = client.post(
        f"/api/v1/period-reports/{report['id']}/items",
        headers={"If-Match": str(report["version"])},
        json={
            "section": "next_plan",
            "source_type": "task",
            "source_id": task["id"],
            "title": task["title"],
            "content": "按计划推进。",
            "sort_order": 10,
        },
    )
    assert item.status_code == 201, item.text
    current = client.get(f"/api/v1/period-reports/{report['id']}").json()
    assert current["items"][0]["source_id"] == task["id"]

    conflict = client.patch(
        f"/api/v1/period-reports/{report['id']}",
        headers={"If-Match": str(report["version"])},
        json={"summary": "旧版本修改"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "VERSION_CONFLICT"

    published = client.post(
        f"/api/v1/period-reports/{report['id']}/actions",
        headers={"If-Match": str(current["version"])},
        json={"action": "publish", "note": "本周定稿"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["snapshot"]["items"]
    assert client.get(f"/api/v1/period-reports/{report['id']}/export.docx").status_code == 200
    assert client.get(f"/api/v1/period-reports/{report['id']}/export.xlsx").status_code == 200

    journal = client.post(
        "/api/v1/work-journal",
        json={
            "title": "协调周报材料",
            "content": "已与协办人员核对。",
            "task_id": task["id"],
            "report_id": report["id"],
        },
    )
    assert journal.status_code == 201, journal.text
    assert journal.json()["actor_name"] == admin["display_name"]
    assert journal.json()["actor_role_label"] == "管理员"
    assert journal.json()["created_at"].endswith("Z")
    changed = client.patch(
        f"/api/v1/work-journal/{journal.json()['id']}",
        headers={"If-Match": str(journal.json()["version"])},
        json={
            "content": "已完成核对并形成材料。",
            "change_note": "补充实际完成情况",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == journal.json()["version"] + 1
    history = client.get(
        f"/api/v1/work-journal/{journal.json()['id']}/history"
    )
    assert history.status_code == 200, history.text
    assert history.json()[0]["change_note"] == "补充实际完成情况"

    template = client.post(
        "/api/v1/report-templates",
        json={
            "name": "周例会模板 1.1",
            "period_type": "week",
            "description": "固定五段式",
            "sections": ["completed", "next_plan", "risk", "coordination"],
        },
    )
    assert template.status_code == 201, template.text
    archive = client.post(
        "/api/v1/archive-templates",
        json={
            "name": "支部换届归档模板 1.1",
            "category": "组织建设",
            "structure": [{"name": "01-请示批复"}],
            "material_rules": [{"category": "final", "required": True}],
        },
    )
    assert archive.status_code == 201, archive.text

    status = client.get("/api/v1/admin/system-status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["app_version"] == get_settings().app_version
    assert body["schema_revision"] == "0024"
    assert "sse_clients" in body["service"]


def test_workspace_read_only_scan_search_link_freeze_and_missing(
    client: TestClient, admin: dict, tmp_path: Path
) -> None:
    root_path = tmp_path / "单位工作资料"
    child = root_path / "2026年" / "七月"
    child.mkdir(parents=True)
    source = child / "基层党建工作总结.txt"
    source.write_text("本周完成党员教育培训，下周计划整理迎检材料。", encoding="utf-8")
    symlink_supported = True
    try:
        (root_path / "忽略的链接").symlink_to(source)
    except OSError:
        # Windows 未启用开发者模式时创建符号链接需要额外权限；Linux/UOS
        # 发布环境仍会执行并验证“不跟随符号链接”的安全分支。
        symlink_supported = False

    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "2026 年原始工作目录", "absolute_path": str(root_path.resolve())},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    assert "absolute_path" not in root
    assert root["read_only"] is True

    scan = client.post(f"/api/v1/workspace/roots/{root['id']}/scan-now")
    assert scan.status_code == 200, scan.text
    assert scan.json()["files"] == 1
    if symlink_supported:
        assert any("符号链接" in error for error in scan.json()["errors"])

    search = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "基层党建工作总结"},
    )
    assert search.status_code == 200, search.text
    indexed = search.json()[0]
    assert indexed["name"] == source.name
    detail = client.get(f"/api/v1/workspace/files/{indexed['id']}").json()
    assert detail["preview_text"] == ""
    assert str(root_path) not in str(detail)

    tagged = client.patch(
        f"/api/v1/workspace/files/{indexed['id']}/tags",
        headers={"If-Match": str(indexed["version"])},
        json={"tags": ["周报", "重点", "周报"]},
    )
    assert tagged.status_code == 200, tagged.text
    assert set(tagged.json()["tags"]) == {"重点", "周报"}
    version = tagged.json()["version"]

    task = create_task(
        client,
        admin["id"],
        title="文件关联事项",
        category="基层党建",
        steps=[],
        materials=[],
    )
    linked = client.post(
        f"/api/v1/workspace/files/{indexed['id']}/links",
        headers={"If-Match": str(version)},
        json={"entity_type": "task", "entity_id": task["id"], "relation": "evidence"},
    )
    assert linked.status_code == 201, linked.text
    version = linked.json()["version"]
    assert linked.json()["links"][0]["entity_id"] == task["id"]

    frozen = client.post(
        f"/api/v1/workspace/files/{indexed['id']}/freeze",
        headers={"If-Match": str(version)},
    )
    assert frozen.status_code == 200, frozen.text
    assert len(frozen.json()["sha256"]) == 64
    assert client.get(f"/api/v1/workspace/files/{indexed['id']}/download").content == source.read_bytes()

    source.unlink()
    rescanned = client.post(f"/api/v1/workspace/roots/{root['id']}/scan-now")
    assert rescanned.status_code == 200
    assert rescanned.json()["missing"] >= 1
    missing = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "基层党建工作总结"},
    )
    assert any(item["status"] == "missing" for item in missing.json())


def test_ai_default_deny_masking_external_confirmation_and_restricted_block(
    client: TestClient, admin: dict, monkeypatch
) -> None:
    configured = client.patch(
        "/api/v1/ai/settings",
        json={
            "name": "测试兼容接口",
            "base_url": "https://model.example.invalid/v1",
            "model": "unit-model",
            "api_key": "secret-key-must-not-return",
            "enabled": True,
            "trusted_intranet": False,
            "timeout_seconds": 30,
        },
    )
    assert configured.status_code == 200, configured.text
    provider = configured.json()
    assert provider["has_api_key"] is True
    assert "secret-key" not in configured.text

    normal = create_task(
        client,
        admin["id"],
        title="AI 可读一般事项",
        description="只发送最小必要片段。",
        category="基层党建",
        steps=[],
        materials=[],
    )
    policy = client.post(
        "/api/v1/ai/policies",
        json={
            "name": "一般事项只读策略",
            "allowed_task_categories": ["基层党建"],
            "capabilities": ["summarize", "draft_report"],
            "allow_restricted": True,
            "active": True,
        },
    )
    assert policy.status_code == 201, policy.text
    assert policy.json()["allow_restricted"] is False

    needs_confirm = client.post(
        "/api/v1/ai/query",
        json={
            "capability": "summarize",
            "instruction": "概括办理进展",
            "task_ids": [normal["id"]],
            "confirm_external": False,
        },
    )
    assert needs_confirm.status_code == 409, needs_confirm.text
    assert needs_confirm.json()["code"] == "AI_EXTERNAL_CONFIRM_REQUIRED"
    assert needs_confirm.json()["source_count"] == 1

    monkeypatch.setattr(
        "app.routers.ai.call_compatible_model",
        lambda provider, capability, instruction, excerpts: "这是只读 AI 草稿。",
    )
    generated = client.post(
        "/api/v1/ai/query",
        json={
            "capability": "summarize",
            "instruction": "概括办理进展",
            "task_ids": [normal["id"]],
            "confirm_external": True,
        },
    )
    assert generated.status_code == 201, generated.text
    assert generated.json()["status"] == "draft"
    assert generated.json()["content"] == "这是只读 AI 草稿。"

    discarded = client.post(
        f"/api/v1/ai/drafts/{generated.json()['id']}/discard",
        headers={"If-Match": str(generated.json()["version"])},
    )
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "discarded"

    restricted = create_task(
        client,
        admin["id"],
        title="AI 禁止读取敏感事项",
        description="",
        sensitivity="restricted",
        category="基层党建",
        steps=[],
        materials=[],
    )
    denied = client.post(
        "/api/v1/ai/query",
        json={
            "capability": "summarize",
            "instruction": "摘要",
            "task_ids": [restricted["id"]],
            "confirm_external": True,
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "AI_RESTRICTED_TASK_DENIED"


def test_period_report_full_maintenance_and_notifications(
    client: TestClient, admin: dict
) -> None:
    for period_type, expected in [
        (PeriodType.YEAR, "2027"),
        (PeriodType.QUARTER, "2027-Q4"),
        (PeriodType.MONTH, "2027-12"),
        (PeriodType.WEEK, "2027-W52"),
    ]:
        key, title, start, end = period_bounds(
            period_type, datetime(2027, 12, 29, 8, tzinfo=timezone.utc)
        )
        assert key == expected
        assert title
        assert start < end

    created = client.post(
        "/api/v1/period-reports",
        json={
            "period_type": "month",
            "anchor_at": "2027-12-15T10:00:00+08:00",
            "auto_fill": True,
        },
    )
    assert created.status_code == 201, created.text
    report = created.json()
    duplicate = client.post(
        "/api/v1/period-reports",
        json={
            "period_type": "month",
            "anchor_at": "2027-12-20T10:00:00+08:00",
        },
    )
    assert duplicate.status_code == 409
    assert client.get("/api/v1/period-reports", params={"period_type": "month"}).status_code == 200
    assert client.get("/api/v1/period-reports/not-found").status_code == 404

    changed = client.patch(
        f"/api/v1/period-reports/{report['id']}",
        headers={"If-Match": str(report["version"])},
        json={"title": "十二月党建工作汇总", "summary": "月度定稿准备中"},
    )
    assert changed.status_code == 200, changed.text
    report = changed.json()
    item = client.post(
        f"/api/v1/period-reports/{report['id']}/items",
        headers={"If-Match": str(report["version"])},
        json={
            "section": "risk",
            "title": "年底材料集中",
            "content": "需统筹审核时间。",
        },
    )
    assert item.status_code == 201, item.text
    current = client.get(f"/api/v1/period-reports/{report['id']}").json()
    edited_item = client.patch(
        f"/api/v1/period-reports/{report['id']}/items/{item.json()['id']}",
        headers={"If-Match": str(item.json()["version"])},
        json={"section": "coordination", "title": "协调审核力量", "sort_order": 20},
    )
    assert edited_item.status_code == 200, edited_item.text
    stale_item = client.patch(
        f"/api/v1/period-reports/{report['id']}/items/{item.json()['id']}",
        headers={"If-Match": str(item.json()["version"])},
        json={"title": "静默覆盖不允许"},
    )
    assert stale_item.status_code == 409
    deleted = client.delete(
        f"/api/v1/period-reports/{report['id']}/items/{item.json()['id']}",
        headers={"If-Match": str(edited_item.json()["version"])},
    )
    assert deleted.status_code == 200

    current = client.get(f"/api/v1/period-reports/{report['id']}").json()
    locked = client.post(
        f"/api/v1/period-reports/{report['id']}/actions",
        headers={"If-Match": str(current["version"])},
        json={"action": "lock", "note": "月报锁定"},
    )
    assert locked.status_code == 200
    assert locked.json()["status"] == "locked"
    denied_patch = client.patch(
        f"/api/v1/period-reports/{report['id']}",
        headers={"If-Match": str(locked.json()["version"])},
        json={"summary": "锁定后不可修改"},
    )
    assert denied_patch.status_code == 409
    reopened = client.post(
        f"/api/v1/period-reports/{report['id']}/actions",
        headers={"If-Match": str(locked.json()["version"])},
        json={"action": "reopen", "note": "发现需补充内容"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "draft"
    assert client.get("/api/v1/report-templates").status_code == 200
    assert client.get("/api/v1/archive-templates").status_code == 200

    journals = client.get("/api/v1/work-journal", params={"limit": 1000})
    assert journals.status_code == 200
    system_entry = next(item for item in journals.json() if item["immutable"])
    immutable = client.patch(
        f"/api/v1/work-journal/{system_entry['id']}",
        headers={"If-Match": str(system_entry["version"])},
        json={"title": "不得篡改自动事件"},
    )
    assert immutable.status_code == 409
    assert immutable.json()["code"] == "JOURNAL_IMMUTABLE"
    assert client.get("/api/v1/work-journal", params={"created_by": admin["id"]}).status_code == 200

    with db_runtime.session_factory() as db:
        db.add_all(
            [
                Notification(
                    user_id=admin["id"],
                    notification_type="deadline",
                    title="事项即将截止",
                    body="请及时办理。",
                    entity_type="task",
                    entity_id=None,
                    dedupe_key="test-notification-one",
                ),
                Notification(
                    user_id=admin["id"],
                    notification_type="overdue",
                    title="事项已经逾期",
                    body="请核实状态。",
                    entity_type="task",
                    entity_id=None,
                    dedupe_key="test-notification-two",
                ),
            ]
        )
        db.commit()
    notices = client.get("/api/v1/notifications", params={"unread_only": True})
    assert notices.status_code == 200
    target = next(item for item in notices.json() if item["title"] == "事项即将截止")
    assert client.post(f"/api/v1/notifications/{target['id']}/read").status_code == 200
    marked = client.post("/api/v1/notifications/read-all")
    assert marked.status_code == 200
    assert client.post("/api/v1/notifications/not-found/read").status_code == 404


def test_paired_desktop_notification_is_private_and_deduplicated(
    client: TestClient,
    admin: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pairing_response = client.post(
        "/api/v1/admin/pairings",
        json={"name": "桌面提醒测试终端"},
    )
    assert pairing_response.status_code == 201
    pairing = pairing_response.json()
    with db_runtime.session_factory() as db:
        preference = db.get(ReminderPreference, admin["id"])
        if not preference:
            preference = ReminderPreference(user_id=admin["id"])
            db.add(preference)
        preference.enabled = True
        preference.desktop_enabled = True
        # 起止相同表示不启用免打扰，避免测试依赖执行时刻。
        preference.quiet_start = "00:00"
        preference.quiet_end = "00:00"
        db.add(
            Notification(
                user_id=admin["id"],
                notification_type="deadline",
                title="不得返回到终端的任务标题",
                body="不得返回到终端的任务正文",
                entity_type="task",
                entity_id=None,
                dedupe_key="paired-summary-private",
            )
        )
        db.commit()

    summary = client.get(
        "/api/v1/notifications/paired-summary",
        headers={"X-PartyOps-Pairing": pairing["token"]},
    )
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    assert payload["unread_count"] >= 1
    assert payload["revision"]
    assert "标题" not in summary.text
    assert "正文" not in summary.text
    assert (
        client.get(
            "/api/v1/notifications/paired-summary",
            headers={"X-PartyOps-Pairing": "invalid"},
        ).status_code
        == 401
    )

    assert desktop_notifications_allowed(
        SimpleNamespace(
            enabled=True,
            desktop_enabled=True,
            quiet_start="00:00",
            quiet_end="00:00",
        )
    )
    original_fetch_summary = client_agent.fetch_notification_summary
    original_show_notification = client_agent.show_desktop_notification
    sent: list[int] = []
    monkeypatch.setattr(
        client_agent,
        "fetch_notification_summary",
        lambda *_args: {"unread_count": 3, "revision": "revision-1"},
    )
    monkeypatch.setattr(
        client_agent,
        "show_desktop_notification",
        lambda count: sent.append(count) is None or True,
    )
    destination = tmp_path / "notification-client"
    assert client_agent.poll_desktop_notifications(
        "http://host", "token", destination
    )
    assert not client_agent.poll_desktop_notifications(
        "http://host", "token", destination
    )
    assert sent == [3]

    class SummaryResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int):
            return json.dumps(
                {"unread_count": 4, "revision": "revision-2"}
            ).encode("utf-8")

    monkeypatch.setattr(
        client_agent.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: SummaryResponse(),
    )
    assert original_fetch_summary("http://host", "token") == {
        "unread_count": 4,
        "revision": "revision-2",
    }
    monkeypatch.setattr(client_agent.shutil, "which", lambda _name: None)
    assert not original_show_notification(4)
    monkeypatch.setattr(
        client_agent.shutil,
        "which",
        lambda _name: "/usr/bin/notify-send",
    )
    invoked: list[list[str]] = []
    monkeypatch.setattr(
        client_agent.subprocess,
        "run",
        lambda command, **_kwargs: invoked.append(command),
    )
    assert original_show_notification(4)
    assert invoked[0][-1].startswith("您有 4 条")
    monkeypatch.setattr(
        client_agent.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("notify-send", 10)
        ),
    )
    assert not original_show_notification(4)
    assert not desktop_notifications_allowed(
        SimpleNamespace(
            enabled=True,
            desktop_enabled=True,
            quiet_start="20:00",
            quiet_end="08:00",
        ),
        datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc),
    )


def test_workspace_admin_and_security_edges(
    client: TestClient, admin: dict, tmp_path: Path
) -> None:
    root_path = tmp_path / "额外资料"
    root_path.mkdir()
    (root_path / "通知.txt").write_text("专项工作通知正文", encoding="utf-8")
    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "额外资料目录", "absolute_path": str(root_path.resolve())},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    assert client.get("/api/v1/workspace/roots").status_code == 200
    assert client.patch(
        f"/api/v1/workspace/roots/{root['id']}", json={"name": "无版本修改"}
    ).status_code == 428
    changed = client.patch(
        f"/api/v1/workspace/roots/{root['id']}",
        headers={"If-Match": str(root["version"])},
        json={"name": "额外资料只读目录"},
    )
    assert changed.status_code == 200, changed.text

    job = client.post(f"/api/v1/workspace/roots/{root['id']}/scan")
    assert job.status_code == 202, job.text
    jobs = client.get("/api/v1/admin/jobs")
    assert jobs.status_code == 200
    assert any(item["id"] == job.json()["id"] for item in jobs.json())
    nodes = client.get("/api/v1/workspace/files", params={"root_id": root["id"]})
    assert nodes.status_code == 200
    item = next(node for node in nodes.json() if not node["is_directory"])
    preview = client.get(f"/api/v1/workspace/files/{item['id']}/preview")
    assert preview.status_code == 200
    assert "默认程序打开" in preview.text
    conflict = client.patch(
        f"/api/v1/workspace/files/{item['id']}/tags",
        headers={"If-Match": "0"},
        json={"tags": ["通知"]},
    )
    assert conflict.status_code == 409
    assert client.get("/api/v1/workspace/files/not-found").status_code == 404

    updated_root = client.get("/api/v1/workspace/roots").json()
    current_root = next(value for value in updated_root if value["id"] == root["id"])
    removed = client.delete(
        f"/api/v1/workspace/roots/{root['id']}",
        headers={"If-Match": str(current_root["version"])},
    )
    assert removed.status_code == 200
    assert removed.json()["original_files_changed"] is False
    assert (root_path / "通知.txt").exists()

    relative = client.post(
        "/api/v1/workspace/roots",
        json={"name": "相对路径", "absolute_path": "relative/path"},
    )
    assert relative.status_code == 422
    regular_file = tmp_path / "不是目录.txt"
    regular_file.write_text("x", encoding="utf-8")
    not_directory = client.post(
        "/api/v1/workspace/roots",
        json={"name": "文件路径", "absolute_path": str(regular_file.resolve())},
    )
    assert not_directory.status_code == 422


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> object:
        return self._payload


class _FakeHttpClient:
    response = _FakeResponse()
    raise_on_get: Exception | None = None
    raise_on_post: Exception | None = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        if self.raise_on_get:
            raise self.raise_on_get
        return self.response

    def post(self, *args, **kwargs):
        if self.raise_on_post:
            raise self.raise_on_post
        return self.response


def test_ai_service_contracts_policy_updates_and_upgrade_record(
    client: TestClient, admin: dict, monkeypatch
) -> None:
    assert ai_service.is_private_endpoint("http://127.0.0.1:8000")
    assert ai_service.is_private_endpoint("http://10.2.3.4")
    assert ai_service.is_private_endpoint("http://192.168.1.2")
    assert ai_service.is_private_endpoint("http://172.16.0.2")
    assert not ai_service.is_private_endpoint("https://172.invalid")
    assert ai_service.is_private_endpoint("http://model.unit.local")
    assert not ai_service.is_private_endpoint("https://example.com")
    assert ai_service.endpoint_url("http://model/v1", "models") == "http://model/v1/models"
    assert ai_service.endpoint_url("http://model", "/models") == "http://model/v1/models"
    empty_provider = ai_service.provider_output(None)
    assert empty_provider["version"] == 0
    assert empty_provider["base_url"] == "https://api.deepseek.com"
    assert empty_provider["model"] == "deepseek-v4-flash"
    assert empty_provider["enabled"] is False
    encrypted = ai_service.encrypt_api_key("unit-secret")
    assert encrypted != "unit-secret"
    assert ai_service.decrypt_api_key(encrypted) == "unit-secret"
    assert ai_service.decrypt_api_key("") == ""
    with pytest.raises(ProblemException) as invalid_key:
        ai_service.decrypt_api_key("invalid-token")
    assert invalid_key.value.code == "AI_KEY_UNAVAILABLE"

    settings = client.get("/api/v1/ai/settings")
    assert settings.status_code == 200
    provider_version = settings.json()["version"]
    updated = client.patch(
        "/api/v1/ai/settings",
        headers={"If-Match": str(provider_version)},
        json={
            "name": "单位内网模型",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "local",
            "enabled": True,
            "trusted_intranet": True,
            "timeout_seconds": 15,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["trusted_intranet"] is True
    stale = client.patch(
        "/api/v1/ai/settings",
        headers={"If-Match": str(provider_version)},
        json={
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "local",
            "enabled": True,
        },
    )
    assert stale.status_code == 409

    monkeypatch.setattr("app.routers.ai.test_provider", lambda provider: None)
    tested = client.post("/api/v1/ai/settings/test")
    assert tested.status_code == 200
    assert tested.json()["last_status"] == "connected"
    monkeypatch.setattr(
        "app.routers.ai.test_provider",
        lambda provider: (_ for _ in ()).throw(
            ProblemException(502, "AI_PROVIDER_UNREACHABLE", "连接失败", "测试网络不可达")
        ),
    )
    failed_test = client.post("/api/v1/ai/settings/test")
    assert failed_test.status_code == 502
    assert failed_test.json()["code"] == "AI_PROVIDER_UNREACHABLE"
    invalid_url = client.patch(
        "/api/v1/ai/settings",
        headers={"If-Match": str(updated.json()["version"])},
        json={"base_url": "ftp://invalid", "model": "local", "enabled": True},
    )
    assert invalid_url.status_code == 422
    incomplete = client.patch(
        "/api/v1/ai/settings",
        headers={"If-Match": str(updated.json()["version"])},
        json={"base_url": "", "model": "", "enabled": True},
    )
    assert incomplete.status_code == 422
    policies = client.get("/api/v1/ai/policies")
    assert policies.status_code == 200
    policy_items = policies.json()
    if policy_items:
        policy = policy_items[0]
    else:
        created_policy = client.post("/api/v1/ai/policies", json={})
        assert created_policy.status_code == 201, created_policy.text
        policy = created_policy.json()
    stale_policy = client.patch(
        f"/api/v1/ai/policies/{policy['id']}",
        headers={"If-Match": "0"},
        json={"name": "过期策略", "capabilities": ["search"]},
    )
    assert stale_policy.status_code == 409
    changed_policy = client.patch(
        f"/api/v1/ai/policies/{policy['id']}",
        headers={"If-Match": str(policy["version"])},
        json={
            "name": "单位只读策略",
            "allowed_task_categories": ["基层党建"],
            "capabilities": ["search", "summarize"],
            "allow_restricted": True,
            "active": True,
        },
    )
    assert changed_policy.status_code == 200
    assert changed_policy.json()["allow_restricted"] is False
    assert client.patch(
        "/api/v1/ai/policies/not-found",
        headers={"If-Match": "1"},
        json={"name": "不存在", "capabilities": ["search"]},
    ).status_code == 404
    capability_denied = client.post(
        "/api/v1/ai/query",
        json={
            "capability": "classify",
            "instruction": "分类",
            "task_ids": [],
            "file_ids": [],
        },
    )
    assert capability_denied.status_code == 403
    assert client.get("/api/v1/ai/drafts").status_code == 200

    with db_runtime.session_factory() as db:
        provider = db.scalar(select(AIProviderConfig))
        assert provider is not None
        provider.api_key_encrypted = ""
        provider.base_url = "http://127.0.0.1:9000/v1"
        provider.model = "local"
        db.commit()
        db.refresh(provider)

        monkeypatch.setattr(ai_service.httpx, "Client", _FakeHttpClient)
        _FakeHttpClient.raise_on_get = None
        _FakeHttpClient.response = _FakeResponse(200, {"data": []})
        ai_service.test_provider(provider)
        _FakeHttpClient.response = _FakeResponse(401)
        with pytest.raises(ProblemException) as rejected:
            ai_service.test_provider(provider)
        assert rejected.value.code == "AI_PROVIDER_REJECTED"
        _FakeHttpClient.raise_on_get = httpx.ConnectError("offline")
        with pytest.raises(ProblemException) as unreachable:
            ai_service.test_provider(provider)
        assert unreachable.value.code == "AI_PROVIDER_UNREACHABLE"
        _FakeHttpClient.raise_on_get = None

        _FakeHttpClient.response = _FakeResponse(
            200, {"choices": [{"message": {"content": "  生成的草稿  "}}]}
        )
        assert (
            ai_service.call_compatible_model(
                provider, AiCapability.SUMMARIZE, "摘要", ["资料片段"]
            )
            == "生成的草稿"
        )
        _FakeHttpClient.response = _FakeResponse(500)
        with pytest.raises(ProblemException) as model_rejected:
            ai_service.call_compatible_model(
                provider, AiCapability.SUMMARIZE, "摘要", ["资料"]
            )
        assert model_rejected.value.code == "AI_PROVIDER_REJECTED"
        _FakeHttpClient.response = _FakeResponse(200, {"choices": []})
        with pytest.raises(ProblemException) as invalid:
            ai_service.call_compatible_model(
                provider, AiCapability.SUMMARIZE, "摘要", ["资料"]
            )
        assert invalid.value.code == "AI_RESPONSE_INVALID"
        _FakeHttpClient.response = _FakeResponse(
            200, {"choices": [{"message": {"content": " "}}]}
        )
        with pytest.raises(ProblemException) as empty:
            ai_service.call_compatible_model(
                provider, AiCapability.SUMMARIZE, "摘要", ["资料"]
            )
        assert empty.value.code == "AI_RESPONSE_EMPTY"

    monkeypatch.setattr(upgrades, "database_has_business_data", lambda: True)
    monkeypatch.setattr(upgrades, "current_schema_version", lambda: "0002")
    assert upgrades.upgrade_required() == (True, "0002")
    upgrades.record_upgrade(
        "0002",
        Path("pre-upgrade-test.partyops-backup"),
        status="completed",
        message="测试升级记录",
    )
    records = client.get("/api/v1/admin/upgrades")
    assert records.status_code == 200
    assert any(item["message"] == "测试升级记录" for item in records.json())


def test_ai_file_scope_intake_formats_scan_failure_and_upgrade_restore(
    client: TestClient, admin: dict, monkeypatch, tmp_path: Path
) -> None:
    create_task(
        client,
        admin["id"],
        title="AI 可读一般事项",
        category="基层党建",
    )
    root_path = tmp_path / "AI授权资料"
    root_path.mkdir()
    source = root_path / "AI材料.txt"
    source.write_text("本周完成组织生活会材料整理。", encoding="utf-8")
    root_response = client.post(
        "/api/v1/workspace/roots",
        json={"name": "AI 授权目录", "absolute_path": str(root_path.resolve())},
    )
    assert root_response.status_code == 201, root_response.text
    root_id = root_response.json()["id"]
    assert client.post(f"/api/v1/workspace/roots/{root_id}/scan-now").status_code == 200
    indexed = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root_id, "keyword": "AI材料"},
    ).json()[0]

    with db_runtime.session_factory() as db:
        policy = db.scalar(select(AIPolicy))
        if policy is None:
            # 本用例可独立运行，不依赖其他测试预先创建默认 AI 策略。
            policy = AIPolicy(created_by=admin["id"])
            db.add(policy)
            db.flush()
        user = db.get(User, admin["id"])
        item = db.get(WorkspaceFile, indexed["id"])
        root_model = db.get(WorkspaceRoot, root_id)
        assert policy and user and item and root_model
        with pytest.raises(ProblemException) as directory_freeze:
            workspace.freeze_workspace_file(
                db, SimpleNamespace(is_directory=True), root_model, user
            )
        assert directory_freeze.value.code == "DIRECTORY_FREEZE_DENIED"
        with pytest.raises(ProblemException) as missing_path:
            workspace.resolve_workspace_path(root_model, "不存在.txt")
        assert missing_path.value.code == "WORKSPACE_FILE_MISSING"
        policy.allowed_root_ids = [root_id]
        policy.allowed_file_types = [".txt"]
        db.commit()
        assert item.extracted_text == ""
        with pytest.raises(ProblemException) as metadata_only:
            ai_service._file_source(db, item, policy)
        assert metadata_only.value.code == "AI_FILE_TEXT_UNAVAILABLE"
        # AI 正文能力与原始文件目录扫描解耦；仅在明确授权/按需提取后使用。
        item.extracted_text = "本周完成组织生活会材料整理。"
        source_meta, excerpt = ai_service._file_source(db, item, policy)
        assert source_meta["root"] == "AI 授权目录"
        assert "组织生活会" in excerpt
        sources, excerpts = ai_service.collect_sources(
            db, user, policy, "归纳材料", [], [item.id, item.id]
        )
        assert len(sources) == 1
        assert len(excerpts) == 1

        policy.allowed_file_types = [".pdf"]
        with pytest.raises(ProblemException) as type_denied:
            ai_service._file_source(db, item, policy)
        assert type_denied.value.code == "AI_FILE_TYPE_DENIED"
        policy.allowed_file_types = [".txt"]
        item.extracted_text = ""
        item.ocr_text = ""
        with pytest.raises(ProblemException) as text_missing:
            ai_service._file_source(db, item, policy)
        assert text_missing.value.code == "AI_FILE_TEXT_UNAVAILABLE"
        item.extracted_text = "本周完成组织生活会材料整理。"
        policy.allowed_root_ids = []
        with pytest.raises(ProblemException) as scope_denied:
            ai_service._file_source(db, item, policy)
        assert scope_denied.value.code == "AI_FILE_SCOPE_DENIED"
        policy.allowed_root_ids = [root_id]
        policy.allowed_task_categories = ["基层党建"]
        db.commit()
        implicit_sources, _ = ai_service.collect_sources(
            db, user, policy, "AI 可读一般事项", [], []
        )
        assert implicit_sources
        with pytest.raises(ProblemException) as unknown_file:
            ai_service.collect_sources(db, user, policy, "摘要", [], ["not-found"])
        assert unknown_file.value.code == "WORKSPACE_FILE_NOT_FOUND"
        with pytest.raises(ProblemException) as unknown_task:
            ai_service.collect_sources(db, user, policy, "摘要", ["not-found"], [])
        assert unknown_task.value.code == "TASK_NOT_FOUND"

        job = BackgroundJob(
            job_type="workspace_scan",
            payload={"root_id": root_id},
            created_by=admin["id"],
        )
        db.add(job)
        db.commit()
        job_id = job.id
    monkeypatch.setattr(workspace, "scan_root", lambda db, root: (_ for _ in ()).throw(OSError("broken")))
    workspace.run_scan_job(job_id, root_id)
    with db_runtime.session_factory() as db:
        failed_job = db.get(BackgroundJob, job_id)
        assert failed_job and failed_job.status == "failed"
        assert failed_job.message == "目录扫描未完成，请在系统日志中使用诊断编号查询。"
        assert "OSError" not in failed_job.message
    workspace.run_scan_job("missing-job", root_id)

    workbook_path = tmp_path / "台账.xlsx"
    workbook = Workbook()
    workbook.active.title = "周计划"
    workbook.active.append(["事项", "状态"])
    workbook.active.append(["组织生活会", "已完成"])
    workbook.save(workbook_path)
    workbook.close()
    excel_text, excel_warnings = intake.extract_path_text(workbook_path)
    assert "组织生活会" in excel_text
    assert excel_warnings == []
    missing_text, missing_warning = intake.extract_path_text(tmp_path / "不存在.txt")
    assert missing_text == ""
    assert missing_warning
    too_large, size_warning = intake.extract_path_text(source, maximum_bytes=1)
    assert too_large == ""
    assert "限制" in size_warning[0]
    unsupported = tmp_path / "资料.bin"
    unsupported.write_bytes(b"binary")
    assert "仅索引文件名称和属性" in intake.extract_path_text(unsupported)[1][0]
    invalid_text = tmp_path / "乱码.txt"
    invalid_text.write_bytes(b"\xff")
    assert intake.extract_path_text(invalid_text)[0] == ""
    broken_xlsx = tmp_path / "损坏.xlsx"
    broken_xlsx.write_bytes(b"not a zip")
    assert "正文识别失败" in intake.extract_path_text(broken_xlsx)[1][0]

    rollback_dir = tmp_path / "upgrade-rollback"
    rollback_dir.mkdir()
    current_db = rollback_dir / "partyops.db"
    current_db.write_bytes(b"new-database")
    restored_source = rollback_dir / "restored-source.db"
    with sqlite3.connect(restored_source) as restored_db:
        restored_db.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        restored_db.execute("INSERT INTO evidence VALUES ('old-database')")
        restored_db.commit()
    restored_bytes = restored_source.read_bytes()
    backup_path = rollback_dir / "pre-upgrade.partyops-backup"
    import zipfile

    with zipfile.ZipFile(backup_path, "w") as archive:
        archive.writestr("database/partyops.db", restored_bytes)
    fake_settings = SimpleNamespace(data_dir=rollback_dir, database_path=current_db)
    verified_paths: list[Path] = []
    monkeypatch.setattr(upgrades, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(upgrades, "verify_backup", lambda path: verified_paths.append(path))
    monkeypatch.setattr(upgrades.db_runtime, "dispose", lambda: None)
    monkeypatch.setattr(upgrades.db_runtime, "rebuild", lambda: None)
    upgrades.restore_database_from_upgrade_backup(backup_path)
    assert verified_paths == [backup_path]
    with sqlite3.connect(current_db) as restored_db:
        assert restored_db.execute("SELECT value FROM evidence").fetchone() == (
            "old-database",
        )
    assert current_db.with_suffix(".db.upgrade-failed").read_bytes() == b"new-database"
