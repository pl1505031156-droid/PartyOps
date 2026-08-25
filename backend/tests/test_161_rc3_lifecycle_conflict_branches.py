"""发展档案、会议、议题与业务文档的可恢复删除边界。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _case(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/party-development/cases",
        json={
            "party_committee": "中共删除审查党委",
            "party_branch": "第一支部",
            "name": name,
            "application_date": "2026-01-05",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _meeting(client: TestClient, admin: dict, title: str) -> dict:
    template = client.get("/api/v1/workflow-templates").json()[0]
    response = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "中共删除审查党委",
            "title": title,
            "scheduled_at": "2026-09-10T09:00:00+08:00",
            "workflow_template_id": template["id"],
            "owner_id": admin["id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_party_case_progress_and_archive_conflicts(
    client: TestClient,
    admin: dict,
) -> None:
    case = _case(client, "删除分支测试甲")
    case_id = case["id"]

    assert (
        client.get("/api/v1/party-development/cases/missing/timeline").status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/party-development/cases",
            params={
                "party_committee": "中共删除审查党委",
                "party_branch": "第一支部",
                "case_status": "",
            },
        ).status_code
        == 200
    )
    invalid = client.post(
        f"/api/v1/party-development/cases/{case_id}/progress-events",
        headers={"If-Match": str(case["version"])},
        json={"milestone_type": "unknown_node", "actual_date": "2026-01-06"},
    )
    assert (
        invalid.status_code == 422
        and invalid.json()["code"] == "PARTY_DEVELOPMENT_PROGRESS_TYPE_INVALID"
    )
    stale = client.post(
        f"/api/v1/party-development/cases/{case_id}/progress-events",
        headers={"If-Match": "999"},
        json={"milestone_type": "conversation", "actual_date": "2026-01-06"},
    )
    assert stale.status_code == 409

    created = client.post(
        f"/api/v1/party-development/cases/{case_id}/progress-events",
        headers={"If-Match": str(case["version"])},
        json={
            "milestone_type": "conversation",
            "actual_date": "2026-01-06",
            "evidence_note": "谈话记录已核对",
        },
    )
    assert created.status_code == 201, created.text
    current = created.json()
    event = next(
        item
        for item in current["case"]["progress_events"]
        if item["milestone_type"] == "conversation"
    )
    duplicate = client.post(
        f"/api/v1/party-development/cases/{case_id}/progress-events",
        headers={"If-Match": str(current["case"]["version"])},
        json={"milestone_type": "conversation", "actual_date": "2026-01-06"},
    )
    assert duplicate.status_code == 409
    assert (
        client.post(
            "/api/v1/party-development/progress-events/missing/correct",
            headers={"If-Match": "1"},
            json={"actual_date": "2026-01-07", "evidence_note": "纠正日期"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/party-development/progress-events/{event['id']}/correct",
            headers={"If-Match": "999"},
            json={"actual_date": "2026-01-07", "evidence_note": "纠正日期"},
        ).status_code
        == 409
    )
    corrected = client.post(
        f"/api/v1/party-development/progress-events/{event['id']}/correct",
        headers={"If-Match": str(event["version"])},
        json={"actual_date": "2026-01-07", "evidence_note": "纠正日期"},
    )
    assert corrected.status_code == 200, corrected.text
    corrected_event = next(
        item
        for item in corrected.json()["case"]["progress_events"]
        if item["milestone_type"] == "conversation"
    )
    assert corrected_event["supersedes_event_id"] == event["id"]

    assert (
        client.post(
            "/api/v1/party-development/progress-events/missing/void",
            headers={"If-Match": "1"},
            json={"reason": "记录有误"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/party-development/progress-events/{corrected_event['id']}/void",
            headers={"If-Match": "999"},
            json={"reason": "记录有误"},
        ).status_code
        == 409
    )
    voided = client.post(
        f"/api/v1/party-development/progress-events/{corrected_event['id']}/void",
        headers={"If-Match": str(corrected_event["version"])},
        json={"reason": "记录有误"},
    )
    assert voided.status_code == 200, voided.text
    restored_fact = next(
        item
        for item in voided.json()["case"]["progress_events"]
        if item["milestone_type"] == "conversation"
    )
    assert restored_fact["id"] == event["id"]

    case = voided.json()["case"]
    assert (
        client.request(
            "DELETE",
            f"/api/v1/party-development/cases/{case_id}",
            headers={"If-Match": "999"},
            json={"reason": "版本冲突测试"},
        ).status_code
        == 409
    )
    archived = client.request(
        "DELETE",
        f"/api/v1/party-development/cases/{case_id}",
        headers={"If-Match": str(case["version"])},
        json={"reason": "重复人员，归档保留审计"},
    )
    assert archived.status_code == 200
    archived_case = archived.json()
    again = client.request(
        "DELETE",
        f"/api/v1/party-development/cases/{case_id}",
        headers={"If-Match": str(archived_case["version"])},
        json={"reason": "重复请求"},
    )
    assert again.status_code == 200 and again.json()["status"] == "archived"
    assert (
        client.post(
            f"/api/v1/party-development/cases/{case_id}/progress-events",
            headers={"If-Match": str(archived_case["version"])},
            json={"milestone_type": "oath", "actual_date": "2026-08-01"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/party-development/cases/{case_id}/restore",
            headers={"If-Match": "999"},
            json={"reason": "错误版本"},
        ).status_code
        == 409
    )
    restored = client.post(
        f"/api/v1/party-development/cases/{case_id}/restore",
        headers={"If-Match": str(archived_case["version"])},
        json={"reason": "确认继续办理"},
    )
    assert restored.status_code == 200, restored.text
    assert (
        client.post(
            f"/api/v1/party-development/cases/{case_id}/restore",
            headers={"If-Match": str(restored.json()["version"])},
            json={"reason": "重复恢复"},
        ).status_code
        == 409
    )


def test_workflow_meeting_topic_and_document_lifecycle_conflicts(
    client: TestClient,
    admin: dict,
) -> None:
    built_in = next(
        item
        for item in client.get("/api/v1/workflow-templates").json()
        if item["built_in"]
    )
    assert (
        client.request(
            "DELETE",
            "/api/v1/workflow-templates/missing",
            json={"reason": "不存在"},
        ).status_code
        == 404
    )
    assert (
        client.request(
            "DELETE",
            f"/api/v1/workflow-templates/{built_in['id']}",
            json={"reason": "内置不可删"},
        ).status_code
        == 409
    )
    custom = client.post(
        "/api/v1/workflow-templates",
        json={
            "name": "删除审查自定义流程",
            "business_type": "party_committee",
            "steps": [{"title": "材料核对", "offset_days": -1}],
        },
    ).json()
    archived_template = client.request(
        "DELETE",
        f"/api/v1/workflow-templates/{custom['id']}",
        json={"reason": "自定义流程停用"},
    )
    assert archived_template.status_code == 200
    assert (
        client.get(
            "/api/v1/workflow-templates", params={"include_archived": True}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/workflow-templates/missing/restore",
            json={"reason": "恢复不存在流程"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/workflow-templates/{custom['id']}/restore",
            json={"reason": "重新启用流程"},
        ).status_code
        == 200
    )

    meeting = _meeting(client, admin, "删除审查会议")
    meeting_id = meeting["id"]
    for params in (
        {"lifecycle": "all"},
        {"scope": "other"},
        {"year": 2026},
        {"organization": "中共删除审查党委"},
        {"meeting_type": "party_committee"},
    ):
        assert client.get("/api/v1/business-meetings", params=params).status_code == 200
    assert (
        client.get("/api/v1/business-meetings/missing/deletion-impact").status_code
        == 404
    )
    assert (
        client.request(
            "DELETE",
            f"/api/v1/business-meetings/{meeting_id}",
            headers={"If-Match": "999"},
            json={"reason": "版本冲突"},
        ).status_code
        == 409
    )

    topic = client.post(
        f"/api/v1/business-meetings/{meeting_id}/topics",
        json={
            "title": "审议预算",
            "review_result": "同意",
            "amount": "100.25",
            "reviewed": True,
            "amount_confirmed": True,
        },
    )
    assert topic.status_code == 201, topic.text
    topic = topic.json()
    assert (
        client.patch(
            f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}",
            headers={"If-Match": "999"},
            json={"title": "新标题"},
        ).status_code
        == 409
    )
    topic = client.patch(
        f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}",
        headers={"If-Match": str(topic["version"])},
        json={"title": " 调整后议题 ", "amount": "200.50", "reviewed": False},
    ).json()
    assert topic["title"] == "调整后议题"
    assert (
        client.request(
            "DELETE",
            f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}",
            headers={"If-Match": "999"},
            json={"reason": "版本冲突"},
        ).status_code
        == 409
    )
    archived_topic = client.request(
        "DELETE",
        f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}",
        headers={"If-Match": str(topic["version"])},
        json={"reason": "议题重复"},
    ).json()
    again = client.request(
        "DELETE",
        f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}",
        headers={"If-Match": str(archived_topic["version"])},
        json={"reason": "重复归档"},
    )
    assert again.status_code == 200
    assert (
        client.patch(
            f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}",
            headers={"If-Match": str(archived_topic["version"])},
            json={"title": "不可修改"},
        ).status_code
        == 409
    )

    document = client.post(
        "/api/v1/business-documents",
        json={
            "document_type": "minutes",
            "title": "独立会议记录",
            "content": {"blocks": [{"type": "paragraph", "text": "正文"}]},
        },
    ).json()
    assert (
        client.get("/api/v1/business-documents/missing/deletion-impact").status_code
        == 404
    )
    assert (
        client.request(
            "DELETE",
            f"/api/v1/business-documents/{document['id']}",
            headers={"If-Match": "999"},
            json={"reason": "版本冲突"},
        ).status_code
        == 409
    )
    archived_document = client.request(
        "DELETE",
        f"/api/v1/business-documents/{document['id']}",
        headers={"If-Match": str(document["version"])},
        json={"reason": "重复文档"},
    ).json()
    assert (
        client.get(
            "/api/v1/business-documents", params={"lifecycle": "archived"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/business-documents", params={"lifecycle": "all"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/business-documents/{document['id']}/restore",
            headers={"If-Match": "999"},
            json={"reason": "版本冲突"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/business-documents/{document['id']}/restore",
            headers={"If-Match": str(archived_document["version"])},
            json={"reason": "确认恢复"},
        ).status_code
        == 200
    )

    archived_meeting = client.request(
        "DELETE",
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(meeting["version"])},
        json={"reason": "会议重复"},
    )
    assert archived_meeting.status_code == 200, archived_meeting.text
    archived_meeting = archived_meeting.json()
    assert (
        client.post(
            f"/api/v1/business-meetings/{meeting_id}/topics",
            json={"title": "归档后新增", "amount": "0"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}/restore",
            headers={"If-Match": str(archived_topic["version"])},
            json={"reason": "会议未恢复"},
        ).status_code
        == 409
    )
    again = client.request(
        "DELETE",
        f"/api/v1/business-meetings/{meeting_id}",
        headers={"If-Match": str(archived_meeting["version"])},
        json={"reason": "重复归档"},
    )
    assert again.status_code == 200
    assert (
        client.post(
            f"/api/v1/business-meetings/{meeting_id}/restore",
            headers={"If-Match": "999"},
            json={"reason": "版本冲突"},
        ).status_code
        == 409
    )
    restored_meeting = client.post(
        f"/api/v1/business-meetings/{meeting_id}/restore",
        headers={"If-Match": str(archived_meeting["version"])},
        json={"reason": "确认恢复会议"},
    )
    assert restored_meeting.status_code == 200, restored_meeting.text
    restored_meeting = restored_meeting.json()
    assert (
        client.post(
            f"/api/v1/business-meetings/{meeting_id}/restore",
            headers={"If-Match": str(restored_meeting["version"])},
            json={"reason": "重复恢复"},
        ).status_code
        == 200
    )
    restored_topic = client.post(
        f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}/restore",
        headers={"If-Match": str(archived_topic["version"])},
        json={"reason": "恢复议题"},
    )
    assert restored_topic.status_code == 200, restored_topic.text
    restored_topic = restored_topic.json()
    assert (
        client.post(
            f"/api/v1/business-meetings/{meeting_id}/topics/{topic['id']}/restore",
            headers={"If-Match": str(restored_topic["version"])},
            json={"reason": "重复恢复"},
        ).status_code
        == 200
    )
