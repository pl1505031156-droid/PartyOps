"""发展党员真实进度、节点重算、修改与导出的完整分支。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_from_calculation_patch_generate_statistics_and_exports(
    client: TestClient,
    admin: dict,
) -> None:
    created = client.post(
        "/api/v1/party-development/cases/from-calculation",
        json={
            "party_committee": "中共节点分支党委",
            "party_branch": "第二支部",
            "name": "节点分支测试乙",
            "gender": "男",
            "birth_date": "1992-02-02",
            "application_date": "2025-01-02",
            "actual_dates": {
                "conversation_date": "2025-01-08",
                "activist_date": "2025-07-10",
                "development_object_date": "2026-07-15",
                "branch_acceptance_date": "2026-08-01",
                "training_days": 3,
                "training_hours": 24,
            },
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["progress_events"] and item["stage"] == "probationary"

    for path in (
        "/api/v1/party-development/cases/missing/reference-plan",
        "/api/v1/party-development/cases/missing/reference-plan/recalculate-preview",
        "/api/v1/party-development/cases/missing/generate-milestones",
    ):
        response = (
            client.post(path)
            if path.endswith(("generate-milestones", "recalculate-preview"))
            else client.get(path)
        )
        assert response.status_code == 404
    assert (
        client.put(
            "/api/v1/party-development/cases/missing/reference-plan",
            headers={"If-Match": "1"},
            json={"adjustments": {}},
        ).status_code
        == 404
    )

    assert (
        client.patch(
            f"/api/v1/party-development/cases/{item['id']}",
            headers={"If-Match": "999"},
            json={"name": "冲突"},
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/party-development/cases/{item['id']}",
            headers={"If-Match": str(item["version"])},
            json={"application_date": None},
        ).status_code
        == 422
    )
    patched = client.patch(
        f"/api/v1/party-development/cases/{item['id']}",
        headers={"If-Match": str(item["version"])},
        json={
            "name": " 节点分支测试乙 ",
            "birth_date": None,
            "application_date": "2025-01-03",
            "activist_date": "2025-07-11",
            "development_object_date": "2026-07-16",
            "probationary_date": "2026-08-02",
            "converted_date": None,
        },
    )
    assert patched.status_code == 200, patched.text
    item = patched.json()
    assert item["name"] == "节点分支测试乙" and item["birth_date"] is None

    first = client.post(
        f"/api/v1/party-development/cases/{item['id']}/generate-milestones"
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/v1/party-development/cases/{item['id']}/generate-milestones"
    )
    assert second.status_code == 200, second.text
    item = second.json()
    milestone = item["milestones"][0]
    assert (
        client.patch(
            "/api/v1/party-development/milestones/missing",
            headers={"If-Match": "1"},
            json={"adjusted_date": "2026-10-01"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/party-development/milestones/{milestone['id']}",
            headers={"If-Match": "999"},
            json={"adjusted_date": "2026-10-01"},
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/party-development/milestones/{milestone['id']}",
            headers={"If-Match": str(milestone["version"])},
            json={"reminder_days": [4000]},
        ).status_code
        == 422
    )
    updated = client.patch(
        f"/api/v1/party-development/milestones/{milestone['id']}",
        headers={"If-Match": str(milestone["version"])},
        json={
            "actual_date": "2026-08-20",
            "adjusted_date": "2026-08-21",
            "reminder_days": [30, 7, 30],
        },
    )
    assert updated.status_code == 200, updated.text
    updated_milestone = next(
        row for row in updated.json()["milestones"] if row["id"] == milestone["id"]
    )
    assert updated_milestone["reminder_days"] == [30, 7]

    assert (
        client.get(
            "/api/v1/party-development/statistics",
            params={
                "party_committee": "中共节点分支党委",
                "party_branch": "第二支部",
            },
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/party-development/statistics",
            params={"party_committee": "不存在的党委"},
        ).json()["total"]
        == 0
    )

    for path in (
        "/api/v1/party-development/cases/export.docx",
        "/api/v1/party-development/cases/export.xlsx",
    ):
        response = client.get(
            path,
            params={
                "party_committee": "中共节点分支党委",
                "party_branch": "第二支部",
            },
        )
        assert response.status_code == 200 and response.content


def test_reference_plan_version_and_adjustment_confirmation(
    client: TestClient,
    admin: dict,
) -> None:
    created = client.post(
        "/api/v1/party-development/cases",
        json={
            "party_committee": "中共参考计划党委",
            "party_branch": "第三支部",
            "name": "参考计划测试丙",
            "application_date": "2026-04-01",
        },
    ).json()
    plan = client.get(f"/api/v1/party-development/cases/{created['id']}/reference-plan")
    assert plan.status_code == 200, plan.text
    preview = client.post(
        f"/api/v1/party-development/cases/{created['id']}/reference-plan/recalculate-preview"
    )
    assert preview.status_code == 200
    node = next(row for row in preview.json()["nodes"] if row["reference_date"])
    assert (
        client.put(
            f"/api/v1/party-development/cases/{created['id']}/reference-plan",
            headers={"If-Match": "999"},
            json={"adjustments": {}},
        ).status_code
        == 409
    )
    confirmed = client.put(
        f"/api/v1/party-development/cases/{created['id']}/reference-plan",
        headers={"If-Match": str(created["version"])},
        json={"adjustments": {node["key"]: node["reference_date"]}},
    )
    assert confirmed.status_code == 200, confirmed.text
