"""生产力中心批处理、视图、规则、日历、比较与查重发布回归。"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.database import db_runtime
from app.models import WorkspaceFile

from .conftest import create_task


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def test_productivity_release_batch_ownership_and_document_tools(
    client: TestClient,
    admin: dict,
    staff: dict,
    tmp_path: Path,
) -> None:
    _login(client, "admin")
    suffix = uuid.uuid4().hex[:8]
    first = create_task(
        client,
        admin["id"],
        title=f"批量办理一-{suffix}",
        steps=[],
        materials=[],
    )
    second = create_task(
        client,
        admin["id"],
        title=f"批量办理二-{suffix}",
        steps=[],
        materials=[],
    )
    denied = client.post(
        "/api/v1/tasks/batch",
        json={"task_ids": ["missing-task"], "tags": ["发布"]},
    )
    assert denied.status_code == 403
    invalid_owner = client.post(
        "/api/v1/tasks/batch",
        json={"task_ids": [first["id"]], "owner_id": "missing-user"},
    )
    assert invalid_owner.status_code == 422
    invalid_transition = client.post(
        "/api/v1/tasks/batch",
        json={"task_ids": [first["id"]], "status": "archived"},
    )
    assert invalid_transition.status_code == 409
    accepted = client.post(
        "/api/v1/tasks/batch",
        json={
            "task_ids": [first["id"], second["id"]],
            "status": "in_progress",
            "owner_id": staff["id"],
            "tags": ["发布门禁", "批量办理"],
            "note": "正式版批量受理",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["count"] == 2

    view = client.post(
        "/api/v1/saved-views",
        json={
            "name": f"发布视图-{suffix}",
            "view_type": "tasks",
            "filters": {"owner_id": staff["id"]},
            "columns": ["title", "status"],
            "pinned": True,
        },
    )
    assert view.status_code == 201, view.text
    assert client.delete("/api/v1/saved-views/missing").status_code == 404

    topic = client.post(
        "/api/v1/topics",
        json={"name": f"发布专题-{suffix}", "description": "聚合正式版验收对象"},
    )
    assert topic.status_code == 201, topic.text
    topic_conflict = client.patch(
        f"/api/v1/topics/{topic.json()['id']}",
        headers={"If-Match": "99"},
        json={"task_ids": [first["id"]]},
    )
    assert topic_conflict.status_code == 409
    topic_updated = client.patch(
        f"/api/v1/topics/{topic.json()['id']}",
        headers={"If-Match": str(topic.json()["version"])},
        json={
            "task_ids": [first["id"], second["id"]],
            "description": "已完成对象聚合",
        },
    )
    assert topic_updated.status_code == 200, topic_updated.text

    rule = client.post(
        "/api/v1/automation-rules",
        json={
            "name": f"发布提醒-{suffix}",
            "trigger": "task_due",
            "conditions": {"days": 3},
            "actions": {"notify": True},
            "enabled": True,
        },
    )
    assert rule.status_code == 201, rule.text
    assert client.patch(
        f"/api/v1/automation-rules/{rule.json()['id']}",
        json={"enabled": False},
    ).status_code == 428
    changed_rule = client.patch(
        f"/api/v1/automation-rules/{rule.json()['id']}",
        headers={"If-Match": str(rule.json()["version"])},
        json={"enabled": False, "conditions": {"days": 1}},
    )
    assert changed_rule.status_code == 200, changed_rule.text
    assert client.delete(
        f"/api/v1/automation-rules/{rule.json()['id']}",
        headers={"If-Match": str(changed_rule.json()["version"])},
    ).status_code == 200

    calendar = client.post(
        "/api/v1/work-calendar",
        json={
            "date_key": "2026-08-11",
            "title": f"正式版发布日-{suffix}",
            "kind": "workday",
            "is_workday": True,
            "note": "发布门禁",
        },
    )
    assert calendar.status_code == 201, calendar.text
    calendar_changed = client.patch(
        f"/api/v1/work-calendar/{calendar.json()['id']}",
        headers={"If-Match": str(calendar.json()["version"])},
        json={"title": f"正式版复核日-{suffix}", "note": "复核完成"},
    )
    assert calendar_changed.status_code == 200, calendar_changed.text
    assert client.get("/api/v1/work-calendar", params={"year": 2026}).status_code == 200

    root_path = tmp_path / "生产力文档"
    root_path.mkdir()
    common = "党建协同正式版文档比较和重复检测。\n第二行材料。"
    (root_path / "版本甲.txt").write_text(common, encoding="utf-8")
    (root_path / "版本乙.txt").write_text(common, encoding="utf-8")
    (root_path / "版本丙.txt").write_text(
        common.replace("第二行材料", "第二行材料已修订"), encoding="utf-8"
    )
    root = client.post(
        "/api/v1/workspace/roots",
        json={"name": f"生产力文档-{suffix}", "absolute_path": str(root_path.resolve())},
    )
    assert root.status_code == 201, root.text
    assert client.post(f"/api/v1/workspace/roots/{root.json()['id']}/scan-now").status_code == 200
    files = client.get(
        "/api/v1/workspace/files", params={"root_id": root.json()["id"]}
    ).json()
    by_name = {item["name"]: item for item in files}
    with db_runtime.session_factory() as db:
        for name, text in {
            "版本甲.txt": common,
            "版本乙.txt": common,
            "版本丙.txt": common.replace("第二行材料", "第二行材料已修订"),
        }.items():
            item = db.get(WorkspaceFile, by_name[name]["id"])
            assert item is not None
            item.extracted_text = text
        db.commit()
    missing_compare = client.post(
        "/api/v1/document-comparisons",
        json={"left_file_id": "missing", "right_file_id": "missing", "comparison_type": "text"},
    )
    assert missing_compare.status_code == 404
    text_compare = client.post(
        "/api/v1/document-comparisons",
        json={
            "left_file_id": by_name["版本甲.txt"]["id"],
            "right_file_id": by_name["版本丙.txt"]["id"],
            "comparison_type": "text",
        },
    )
    assert text_compare.status_code == 201, text_compare.text
    assert text_compare.json()["result"]["changed"] is True
    metadata_compare = client.post(
        "/api/v1/document-comparisons",
        json={
            "left_file_id": by_name["版本甲.txt"]["id"],
            "right_file_id": by_name["版本乙.txt"]["id"],
            "comparison_type": "metadata",
        },
    )
    assert metadata_compare.status_code == 201, metadata_compare.text
    assert client.get("/api/v1/document-comparisons").status_code == 200

    duplicates = client.post("/api/v1/duplicates/scan")
    assert duplicates.status_code == 200, duplicates.text
    assert any(
        set(group["file_ids"])
        == {by_name["版本甲.txt"]["id"], by_name["版本乙.txt"]["id"]}
        for group in duplicates.json()
    )
    assert client.get("/api/v1/duplicates").status_code == 200
    search = client.get("/api/v1/global-search", params={"q": suffix, "limit": 100})
    assert search.status_code == 200
    assert any(item["type"] == "task" for item in search.json()["items"])
    workbench = client.get("/api/v1/workbench")
    assert workbench.status_code == 200 and "recent_files" in workbench.json()

    _login(client, "staff")
    assert client.delete(f"/api/v1/saved-views/{view.json()['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/topics/{topic.json()['id']}",
        headers={"If-Match": str(topic_updated.json()["version"])},
        json={"description": "越权修改"},
    ).status_code == 404
    assert client.get("/api/v1/document-comparisons").status_code == 200

    _login(client, "admin")
    assert client.delete(
        f"/api/v1/saved-views/{view.json()['id']}",
        headers={"If-Match": str(view.json()["version"])},
    ).status_code == 200
    assert client.delete(
        f"/api/v1/work-calendar/{calendar.json()['id']}",
        headers={"If-Match": str(calendar_changed.json()["version"])},
    ).status_code == 200
    handover = client.post("/api/v1/handover")
    assert handover.status_code == 201, handover.text
    assert client.get(f"/api/v1/handover/{handover.json()['id']}/download").status_code == 200
    assert client.get("/api/v1/handover/missing/download").status_code == 404
