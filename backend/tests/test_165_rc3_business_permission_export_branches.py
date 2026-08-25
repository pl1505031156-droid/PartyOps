"""会议与结构化文档权限、缺失资源及导出内容分支。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.enums import UserRole
from app.problems import ProblemException
from app.routers import business


class _DB:
    def __init__(self, values=None) -> None:
        self.values = values or {}

    def get(self, _model, identifier):
        return self.values.get(identifier)

    def commit(self) -> None:
        return None


def test_meeting_modify_export_permission_helpers() -> None:
    meeting = SimpleNamespace(
        created_by="creator",
        host_id="host",
        recorder_id="recorder",
        study_plan_id=None,
    )
    assert business._can_modify_meeting(
        _DB(), meeting, SimpleNamespace(id="admin", role=UserRole.ADMIN)
    )
    for identity in ("creator", "host", "recorder"):
        assert business._can_modify_meeting(
            _DB(), meeting, SimpleNamespace(id=identity, role=UserRole.STAFF)
        )
    ordinary = SimpleNamespace(id="ordinary", role=UserRole.STAFF)
    assert business._can_modify_meeting(_DB(), meeting, ordinary) is False
    with pytest.raises(ProblemException) as denied:
        business._require_modify_meeting(_DB(), meeting, ordinary)
    assert denied.value.code == "MEETING_MODIFY_FORBIDDEN"

    plan = SimpleNamespace(secretary_id="secretary")
    meeting.study_plan_id = "plan"
    secretary = SimpleNamespace(id="secretary", role=UserRole.STAFF)
    assert business._can_modify_meeting(_DB({"plan": plan}), meeting, secretary)
    business._require_export_meeting(
        _DB(), meeting, SimpleNamespace(id="admin", role=UserRole.ADMIN)
    )
    business._require_export_meeting(
        _DB(), meeting, SimpleNamespace(id="recorder", role=UserRole.STAFF)
    )
    business._require_export_meeting(_DB({"plan": plan}), meeting, secretary)
    with pytest.raises(ProblemException) as denied:
        business._require_export_meeting(_DB({"plan": plan}), meeting, ordinary)
    assert denied.value.code == "MEETING_EXPORT_FORBIDDEN"


def test_structured_document_blocks_ignore_untrusted_shapes() -> None:
    empty = Document()
    business._append_structured_content(empty, [])
    business._append_structured_content(empty, {"blocks": "not-list"})
    business._append_structured_content(
        empty,
        {
            "blocks": [
                "not-object",
                {"type": "heading", "text": "一级标题", "level": 99},
                {"type": "list", "text": "清单项"},
                {"text": "正文"},
            ]
        },
    )
    assert [paragraph.text for paragraph in empty.paragraphs] == [
        "一级标题",
        "清单项",
        "正文",
    ]


def test_business_missing_resources_patch_and_rich_export(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    reason = {"reason": "不存在资源"}
    assert (
        client.request(
            "DELETE",
            "/api/v1/business-meetings/missing",
            headers={"If-Match": "1"},
            json=reason,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/business-meetings/missing/restore",
            headers={"If-Match": "1"},
            json=reason,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/business-meetings/missing/topics",
            json={"title": "不存在会议议题", "amount": "0"},
        ).status_code
        == 404
    )

    template = client.get("/api/v1/workflow-templates").json()[0]
    meeting = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "中共导出分支党委",
            "title": "结构化导出会议",
            "workflow_template_id": template["id"],
            "owner_id": admin["id"],
        },
    ).json()
    assert (
        client.patch(
            f"/api/v1/business-meetings/{meeting['id']}",
            headers={"If-Match": str(meeting["version"])},
            json={"title": " 结构化导出会议（修订） ", "status": "completed"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/business-meetings/{meeting['id']}/topics/missing/deletion-impact"
        ).status_code
        == 404
    )
    assert (
        client.get(
            "/api/v1/business-meetings/missing/topics/missing/deletion-impact"
        ).status_code
        == 404
    )

    assert (
        client.request(
            "DELETE",
            "/api/v1/business-documents/missing",
            headers={"If-Match": "1"},
            json=reason,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/business-documents/missing/restore",
            headers={"If-Match": "1"},
            json=reason,
        ).status_code
        == 404
    )
    document = client.post(
        "/api/v1/business-documents",
        json={
            "document_type": "minutes",
            "title": "结构化内容导出",
            "content": {
                "blocks": [
                    "忽略此项",
                    {"type": "heading", "level": 2, "text": "会议要点"},
                    {"type": "list", "text": "第一项"},
                    {"type": "paragraph", "text": "形成会议纪要。"},
                ]
            },
        },
    ).json()
    updated = client.patch(
        f"/api/v1/business-documents/{document['id']}",
        headers={"If-Match": str(document["version"])},
        json={"title": " 结构化内容导出（修订） "},
    )
    assert updated.status_code == 200, updated.text
    document = updated.json()
    exported = client.get(f"/api/v1/business-documents/{document['id']}/export.docx")
    assert exported.status_code == 200 and exported.content.startswith(b"PK")

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": "PartyOps@2026"},
    )
    assert login.status_code == 200
    assert (
        client.get(
            f"/api/v1/business-documents/{document['id']}/deletion-impact"
        ).status_code
        == 403
    )
    assert (
        client.request(
            "DELETE",
            f"/api/v1/business-documents/{document['id']}",
            headers={"If-Match": str(document["version"])},
            json={"reason": "越权归档"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/business-documents/{document['id']}/restore",
            headers={"If-Match": str(document["version"])},
            json={"reason": "越权恢复"},
        ).status_code
        == 403
    )
    forbidden_update = client.patch(
        f"/api/v1/business-documents/{document['id']}",
        headers={"If-Match": str(document["version"])},
        json={"title": "越权修改"},
    )
    assert forbidden_update.status_code == 403
    assert forbidden_update.json()["code"] == "DOCUMENT_MODIFY_FORBIDDEN"
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "PartyOps@2026"},
        ).status_code
        == 200
    )


def test_meeting_without_task_archive_restore_and_patch_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting = SimpleNamespace(
        id="meeting",
        created_by="admin",
        host_id=None,
        recorder_id=None,
        study_plan_id=None,
        task_id="missing-task",
        workflow_template_id=None,
        version=1,
        status="planned",
        status_before_archive="planned",
        scheduled_at=None,
        completed_at=None,
        archived_at=None,
        archived_by=None,
        archive_reason="",
    )
    db = _DB({"meeting": meeting})
    user = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    request = SimpleNamespace(client=None)
    monkeypatch.setattr(
        business, "meeting_out", lambda _db, item: {"version": item.version}
    )
    monkeypatch.setattr(business, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(business, "client_ip", lambda _request: "127.0.0.1")
    archived = business.archive_meeting(
        "meeting",
        business.LifecycleReason(reason="无任务归档"),
        request,
        "1",
        user,
        db,
    )
    assert archived["version"] == 2
    restored = business.restore_meeting(
        "meeting",
        business.LifecycleReason(reason="无任务恢复"),
        request,
        "2",
        user,
        db,
    )
    assert restored["version"] == 3
    patched = business.patch_meeting(
        "meeting",
        business.MeetingPatch(scheduled_at="2026-09-01T09:00:00+08:00"),
        request,
        "3",
        user,
        db,
    )
    assert patched["version"] == 4
