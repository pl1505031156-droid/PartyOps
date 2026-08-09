"""材料清单与周期汇总现场故障的回归测试。"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from .conftest import create_task


def test_material_categories_offer_presets_and_remember_custom_values(
    client: TestClient, admin: dict
) -> None:
    task = create_task(client, admin["id"], materials=[], steps=[])
    created = client.post(
        f"/api/v1/tasks/{task['id']}/materials",
        json={"category": "干部考核专用材料", "name": "年度考核汇总表", "required": True},
        headers={"If-Match": str(task["version"])},
    )
    assert created.status_code == 201, created.text
    assert created.json()["category"] == "干部考核专用材料"

    categories = client.get("/api/v1/material-categories")
    assert categories.status_code == 200, categories.text
    values = {item["value"]: item["label"] for item in categories.json()}
    assert len(values) >= 15
    assert values["final"] == "最终报送稿"
    assert values["干部考核专用材料"] == "干部考核专用材料"


def test_current_reports_are_created_and_completed_task_is_synced_immediately(
    client: TestClient, admin: dict
) -> None:
    ensured = client.post("/api/v1/period-reports/ensure-current")
    assert ensured.status_code == 200, ensured.text
    current = ensured.json()
    assert {item["period_type"] for item in current} == {
        "week",
        "month",
        "quarter",
        "year",
    }

    task = create_task(
        client,
        admin["id"],
        title="自动进入本周完成的测试事项",
        materials=[],
        steps=[],
    )
    completed = client.post(
        f"/api/v1/tasks/{task['id']}/actions",
        json={"action": "complete", "note": "已按要求完成"},
        headers={"If-Match": str(task["version"])},
    )
    assert completed.status_code == 200, completed.text

    refreshed = client.post("/api/v1/period-reports/ensure-current")
    assert refreshed.status_code == 200, refreshed.text
    matching_reports = [
        report
        for report in refreshed.json()
        if any(
            item["source_type"] == "task"
            and item["source_id"] == task["id"]
            and item["section"] == "completed"
            for item in report["items"]
        )
    ]
    assert {report["period_type"] for report in matching_reports} == {
        "week",
        "month",
        "quarter",
        "year",
    }

    completed_at = datetime.fromisoformat(
        completed.json()["completed_at"].replace("Z", "+00:00")
    )
    for report in matching_reports:
        start = datetime.fromisoformat(report["start_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(report["end_at"].replace("Z", "+00:00"))
        assert start <= completed_at < end
