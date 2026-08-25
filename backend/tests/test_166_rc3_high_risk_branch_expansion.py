"""rc.3 高风险异常、隐私边界与数据生命周期的补充分支回归。"""

from __future__ import annotations

import http.client
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, UploadFile

from app import archive_service, scheduler
from app import official_format_service as format_service
from app.enums import ArchiveRecordMode, UserRole
from app.official_format import LocalDocument
from app.official_format_service import (
    OfficialFormatLocalService,
    issue_local_format_ticket,
)
from app.problems import ProblemException
from app.routers import official_format as official_routes
from app.routers import party_development as development_routes

ORIGIN = "https://partyops.local"
SECRET = "s" * 64


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class _Db:
    def __init__(self, *, gets=None, scalars=None, scalar=None, executed=None):
        self.gets = dict(gets or {})
        self.scalars_queue = list(scalars or [])
        self.scalar_queue = list(scalar or [])
        self.executed = list(executed or [])
        self.added = []
        self.commits = 0

    def get(self, model, key):
        return self.gets.get((model, key), self.gets.get(key))

    def scalars(self, _statement):
        return _Rows(self.scalars_queue.pop(0) if self.scalars_queue else [])

    def scalar(self, _statement):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def execute(self, _statement):
        if self.executed:
            return self.executed.pop(0)
        return SimpleNamespace(one_or_none=lambda: None, all=lambda: [])

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1


def _problem(code: str, call) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == code


def _local_request(
    service: OfficialFormatLocalService,
    method: str,
    path: str,
    *,
    origin: str = ORIGIN,
    host: str | None = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", service.port, timeout=10)
    request_headers = {
        "Host": host or f"127.0.0.1:{service.port}",
        "Origin": origin,
    }
    request_headers.update(headers or {})
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    parsed = json.loads(payload.decode("utf-8")) if payload else {}
    return response.status, parsed


def _new_local_session(service: OfficialFormatLocalService) -> tuple[str, str]:
    ticket, _ = issue_local_format_ticket(
        SECRET,
        origin=ORIGIN,
        user_id="user-1",
        device_id="device-1",
    )
    status, payload = _local_request(
        service,
        "POST",
        "/v1/sessions",
        headers={"Authorization": f"Bearer {ticket}"},
    )
    assert status == 201
    return str(payload["session_id"]), str(payload["session_token"])


def test_official_format_router_rejects_origin_and_device_confusion(monkeypatch) -> None:
    request = SimpleNamespace(headers={"Origin": "ftp://untrusted.invalid"})
    monkeypatch.setattr(
        official_routes,
        "get_settings",
        lambda: SimpleNamespace(environment="production", official_format_port=18768),
    )
    _problem("LOCAL_FORMAT_ORIGIN_DENIED", lambda: official_routes._request_origin(request))

    empty = SimpleNamespace(headers={})
    _problem("LOCAL_FORMAT_ORIGIN_DENIED", lambda: official_routes._request_origin(empty))
    monkeypatch.setattr(
        official_routes,
        "get_settings",
        lambda: SimpleNamespace(environment="test", official_format_port=18768),
    )
    assert official_routes._request_origin(empty) == ""

    payload = official_routes.LocalFormatTicketCreate(origin=ORIGIN)
    user = SimpleNamespace(id="user-1")
    mismatch = SimpleNamespace(headers={"Origin": "https://other.local"})
    _problem(
        "LOCAL_FORMAT_ORIGIN_MISMATCH",
        lambda: official_routes.create_local_format_ticket(payload, mismatch, user, _Db()),
    )

    request = SimpleNamespace(headers={"Origin": ORIGIN})
    inactive = SimpleNamespace(
        id="device-1", credential_state="reauthorize_required", agent_token_hash="hash"
    )
    monkeypatch.setattr(official_routes, "request_device", lambda *_args: inactive)
    _problem(
        "LOCAL_FORMAT_DEVICE_REAUTHORIZE_REQUIRED",
        lambda: official_routes.create_local_format_ticket(payload, request, user, _Db()),
    )
    missing_hash = SimpleNamespace(id="device-1", credential_state="active", agent_token_hash="")
    monkeypatch.setattr(official_routes, "request_device", lambda *_args: missing_hash)
    _problem(
        "LOCAL_FORMAT_DEVICE_REAUTHORIZE_REQUIRED",
        lambda: official_routes.create_local_format_ticket(payload, request, user, _Db()),
    )

    active = SimpleNamespace(id="device-1", credential_state="active", agent_token_hash=SECRET)
    monkeypatch.setattr(official_routes, "request_device", lambda *_args: active)
    issued = official_routes.create_local_format_ticket(payload, request, user, _Db())
    assert issued["ticket"] and "127.0.0.1:18768" in issued["local_base_url"]

    monkeypatch.setattr(official_routes, "request_device", lambda *_args: None)
    monkeypatch.setattr(official_routes, "is_host_local_request", lambda _request: False)
    _problem(
        "LOCAL_FORMAT_HELPER_REQUIRED",
        lambda: official_routes.create_local_format_ticket(payload, request, user, _Db()),
    )
    _problem(
        "LOCAL_FORMAT_ORIGIN_INVALID",
        lambda: official_routes.create_local_format_ticket(
            official_routes.LocalFormatTicketCreate(origin="ftp://invalid.local"),
            request,
            user,
            _Db(),
        ),
    )


def test_official_format_local_service_rejects_route_host_auth_and_gone_result(
    tmp_path, monkeypatch
) -> None:
    service = OfficialFormatLocalService(secret=SECRET, config_dir=tmp_path, port=0).start()
    try:
        status, payload = _local_request(service, "OPTIONS", "/v1/sessions", origin="ftp://bad")
        assert status == 403 and payload["code"] == "LOCAL_ORIGIN_DENIED"
        status, payload = _local_request(service, "GET", "/health", host="attacker.invalid")
        assert status == 403 and payload["code"] == "LOCAL_HOST_DENIED"
        status, payload = _local_request(
            service, "POST", "/v1/sessions", host="attacker.invalid"
        )
        assert status == 403 and payload["code"] == "LOCAL_ORIGIN_DENIED"
        status, payload = _local_request(service, "GET", "/missing")
        assert status == 404 and payload["code"] == "LOCAL_ROUTE_NOT_FOUND"
        status, payload = _local_request(service, "POST", "/v1/sessions")
        assert status == 422 and payload["code"] == "LOCAL_TICKET_REQUIRED"
        status, payload = _local_request(service, "POST", "/missing")
        assert status == 404 and payload["code"] == "LOCAL_ROUTE_NOT_FOUND"

        session_id, token = _new_local_session(service)
        session = service.sessions[session_id]
        old_source = session.workspace / "old.docx"
        old_source.write_bytes(b"old")
        session.documents["old"] = LocalDocument(
            source=old_source, original_stem="旧文件", converted=False
        )
        report = SimpleNamespace(as_dict=lambda: {"paragraph_count": 1})
        monkeypatch.setattr(format_service, "_extract_upload", lambda _handler: ("新文件.docx", b"new"))
        monkeypatch.setattr(
            format_service, "prepare_docx", lambda source, _workspace: (source, False)
        )
        monkeypatch.setattr(format_service, "diagnose_docx", lambda _source: report)
        status, payload = _local_request(
            service,
            "POST",
            f"/v1/sessions/{session_id}/diagnose",
            headers={"X-PartyOps-Local-Token": token},
        )
        assert status == 200 and "old" not in session.documents
        document_id = str(payload["document_id"])
        monkeypatch.setattr(
            format_service,
            "format_docx",
            lambda *_args: (_ for _ in ()).throw(OSError("converter failed")),
        )
        status, payload = _local_request(
            service,
            "POST",
            f"/v1/sessions/{session_id}/documents/{document_id}/format",
            headers={"X-PartyOps-Local-Token": token},
        )
        assert status == 422 and payload["code"] == "FORMAT_PROCESS_FAILED"
        status, payload = _local_request(
            service,
            "POST",
            f"/v1/sessions/{session_id}/documents/{'0' * 32}/format",
            headers={"X-PartyOps-Local-Token": token},
        )
        assert status == 422 and payload["code"] == "FORMAT_DOCUMENT_GONE"
        status, payload = _local_request(
            service,
            "GET",
            f"/v1/sessions/{session_id}/documents/{'0' * 32}/download",
            headers={"X-PartyOps-Local-Token": token},
        )
        assert status == 422 and payload["code"] == "FORMAT_RESULT_GONE"
        status, payload = _local_request(service, "DELETE", "/missing")
        assert status == 404 and payload["code"] == "LOCAL_ROUTE_NOT_FOUND"
        status, payload = _local_request(
            service,
            "DELETE",
            f"/v1/sessions/{session_id}",
            headers={"X-PartyOps-Local-Token": token},
        )
        assert status == 204 and payload == {}
    finally:
        service.close()

    # 未启动的服务也必须能够幂等关闭，不产生误诊断。
    OfficialFormatLocalService(secret=SECRET, config_dir=tmp_path / "never-started").close()


def test_official_format_posix_workspace_permission_branch(tmp_path, monkeypatch) -> None:
    formatter = OfficialFormatLocalService(secret=SECRET, config_dir=tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(format_service, "os", SimpleNamespace(name="posix"))
        session = formatter.create_session(ORIGIN)
        assert session.workspace.is_dir()
    formatter.remove_session(session.id)


def test_archive_schema_permissions_and_missing_resources() -> None:
    inactive = SimpleNamespace(active=False)
    record = SimpleNamespace(category_id="category-1")
    _problem(
        "ARCHIVE_CATEGORY_DISABLED",
        lambda: archive_service.category_for_record(_Db(gets={"category-1": inactive}), record),
    )

    category = SimpleNamespace(
        id="category-1",
        active=True,
        allow_device_access=False,
        access_mode="grants",
        field_schema=[],
        record_mode=ArchiveRecordMode.PERSON_YEAR,
    )
    admin = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    staff = SimpleNamespace(id="staff", role=UserRole.STAFF)
    assert archive_service.archive_permissions(_Db(), category, admin)["manage"] is True
    assert archive_service.archive_permissions(_Db(), category, staff, "device-1")["view"] is False
    category.allow_device_access = True
    category.access_mode = "admins_only"
    assert archive_service.archive_permissions(_Db(), category, staff)["view"] is False
    category.access_mode = "all_users"
    assert archive_service.archive_permissions(_Db(), category, staff)["contribute"] is True

    _problem(
        "ARCHIVE_FIELD_UNKNOWN",
        lambda: archive_service.validate_custom_fields(category, {"unknown": "x"}),
    )
    category.field_schema = [{"key": "remark", "label": "备注", "type": "text"}]
    assert archive_service.validate_custom_fields(category, {"remark": "  已核对  "}) == {
        "remark": "已核对"
    }
    _problem(
        "ARCHIVE_PERSON_REQUIRED",
        lambda: archive_service.validate_record_mode(category, {"person_name": ""}),
    )
    category.field_schema = [
        {
            "key": "assessment_result",
            "label": "考核结果",
            "type": "select",
            "required": True,
            "options": ["优秀", "合格"],
        }
    ]
    _problem(
        "ARCHIVE_FIELD_REQUIRED",
        lambda: archive_service.validate_record_mode(category, {"person_name": "张三"}),
    )
    _problem(
        "ARCHIVE_FIELD_INVALID",
        lambda: archive_service.validate_record_mode(
            category, {"person_name": "张三", "assessment_result": "未知"}
        ),
    )
    archive_service.validate_record_mode(
        category, {"person_name": "张三", "assessment_result": "优秀"}
    )

    archive_service.refresh_search_index(_Db(), "missing")
    _problem(
        "ARCHIVE_ATTACHMENT_NOT_FOUND",
        lambda: archive_service.archive_attachment_path(_Db(), "missing"),
    )
    assert archive_service.fts_search(_Db(), "  \t  ") == []


def _upload(name: str, payload: bytes) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename=name,
        headers=Headers({"content-type": "application/pdf"}),
    )


@pytest.mark.asyncio
async def test_archive_upload_replay_conflict_type_size_empty_and_dedup(monkeypatch, tmp_path) -> None:
    record = SimpleNamespace(id="record-1")
    actor = SimpleNamespace(id="user-1")
    existing = SimpleNamespace(record_id=record.id)
    result = await archive_service.save_archive_upload(
        _Db(scalar=[existing]),
        record,
        _upload("材料.pdf", b"ignored"),
        actor,
        client_upload_id="archive-upload-0001",
    )
    assert result is existing
    with pytest.raises(ProblemException) as raised:
        await archive_service.save_archive_upload(
            _Db(scalar=[SimpleNamespace(record_id="other")]),
            record,
            _upload("材料.pdf", b"ignored"),
            actor,
            client_upload_id="archive-upload-0002",
        )
    assert raised.value.code == "UPLOAD_ID_CONFLICT"
    with pytest.raises(ProblemException) as raised:
        await archive_service.save_archive_upload(
            _Db(), record, _upload("危险.cmd", b"x"), actor
        )
    assert raised.value.code == "ARCHIVE_FILE_TYPE_NOT_ALLOWED"

    settings = SimpleNamespace(attachments_dir=tmp_path / "attachments", max_upload_mb=0)
    monkeypatch.setattr(archive_service, "get_settings", lambda: settings)
    with pytest.raises(ProblemException) as raised:
        await archive_service.save_archive_upload(
            _Db(), record, _upload("超限.pdf", b"x"), actor
        )
    assert raised.value.code == "FILE_TOO_LARGE"
    settings.max_upload_mb = 1
    monkeypatch.setattr(
        archive_service,
        "resolve_blob_path",
        lambda relative: settings.attachments_dir / relative,
    )
    with pytest.raises(ProblemException) as raised:
        await archive_service.save_archive_upload(
            _Db(), record, _upload("空文件.pdf", b""), actor
        )
    assert raised.value.code == "EMPTY_FILE"

    payload = b"same-content"
    import hashlib

    sha256 = hashlib.sha256(payload).hexdigest()
    stored = settings.attachments_dir / sha256[:2] / sha256
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(payload)
    blob = SimpleNamespace(sha256=sha256)
    db = _Db(gets={sha256: blob}, scalar=[3])
    saved = await archive_service.save_archive_upload(
        db, record, _upload("重复内容.pdf", payload), actor
    )
    assert saved.version_no == 4 and saved.blob_sha256 == sha256
    assert db.added == [saved]


def test_scheduler_notification_filters_and_dedupe_paths() -> None:
    admin = SimpleNamespace(id="admin-1")
    rules = [
        SimpleNamespace(
            id="file-rule",
            name="台账筛选",
            trigger="workspace_file_indexed",
            owner_id="owner-1",
            conditions={
                "name_contains": "报告",
                "path_contains": "年度",
                "extensions": ["pdf"],
            },
            actions={"material_category": "正式稿", "tags": ["归档", ""]},
        ),
        SimpleNamespace(
            id="task-rule",
            name="截止提醒",
            trigger="task_due_soon",
            owner_id="owner-1",
            conditions={},
            actions={"type": "notify"},
        ),
    ]
    files = [
        SimpleNamespace(id="name", name="说明.txt", relative_path="年度/说明.txt", extension="txt", version=1),
        SimpleNamespace(id="path", name="报告.pdf", relative_path="其他/报告.pdf", extension="pdf", version=1),
        SimpleNamespace(id="ext", name="报告.txt", relative_path="年度/报告.txt", extension="txt", version=1),
        SimpleNamespace(id="duplicate", name="报告.pdf", relative_path="年度/报告.pdf", extension="pdf", version=1),
        SimpleNamespace(id="valid", name="年度报告.pdf", relative_path="年度/年度报告.pdf", extension="pdf", version=1),
    ]
    task = SimpleNamespace(id="task-1", title="完成归档", owner_id=None)

    class RuleDb(_Db):
        def __init__(self):
            super().__init__(scalars=[rules, files, [task]])
            self.scalar_calls = 0

        def scalar(self, _statement):
            self.scalar_calls += 1
            # 第一个符合文件和任务提醒都模拟已有去重记录；第二个文件实际创建。
            return "existing" if self.scalar_calls in {1, 3} else None

    db = RuleDb()
    scheduler.run_automation_rules(db, admin)
    notifications = [item for item in db.added if item.__class__.__name__ == "Notification"]
    assert len(notifications) == 1
    assert "材料类别：正式稿" in notifications[0].body
    assert "标签：归档" in notifications[0].body


def test_scheduler_retention_naive_time_symlink_and_rejected_escape(monkeypatch, tmp_path) -> None:
    inbox = tmp_path / "inbox"
    exports = tmp_path / "exports"
    data_dir = tmp_path / "data"
    inbox.mkdir()
    exports.mkdir()
    upgrade = data_dir / "upgrade-backups"
    upgrade.mkdir(parents=True)
    target = upgrade / "target"
    target.mkdir()
    link = upgrade / "old-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 策略不允许创建测试符号链接")
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    import os

    os.utime(target, (old, old))
    transfer = SimpleNamespace(
        id="transfer-1",
        handled_at=datetime(2020, 1, 1, tzinfo=UTC),
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        error_code="",
        error_message="",
        transit_path="old",
        version=1,
    )

    class RetentionDb(_Db):
        def execute(self, _statement):
            return SimpleNamespace(rowcount=0)

    db = RetentionDb(scalars=[[transfer]])
    settings = SimpleNamespace(
        notification_read_retention_days=30,
        session_retention_days=30,
        transient_record_retention_days=30,
        event_outbox_retention_days=30,
        inbox_handled_retention_days=30,
        inbox_unhandled_retention_days=7,
        export_retention_days=7,
        upgrade_backup_keep=0,
        upgrade_backup_retention_days=7,
        inbox_dir=inbox,
        exports_dir=exports,
        data_dir=data_dir,
    )
    monkeypatch.setattr(
        scheduler,
        "purge_expired_deleted_attachments",
        lambda *_args, **_kwargs: {
            "task_versions": 0,
            "archive_attachments": 0,
            "blobs": 0,
        },
    )
    counts = scheduler.cleanup_runtime_retention(
        db, settings, now=datetime(2026, 8, 25, 8, 0)
    )
    assert counts["upgrade_backups"] == 2
    assert transfer.error_code == "INBOX_RETAINED_COPY_EXPIRED"


def test_party_development_payload_profile_and_stage_recalculation(monkeypatch) -> None:
    item = SimpleNamespace(
        id="case-1",
        name="张三",
        application_at=datetime(2026, 1, 1, tzinfo=UTC),
        activist_at=None,
        development_object_at=None,
        probationary_at=None,
        converted_at=None,
        planning_profile_snapshot="legacy-invalid",
        planning_profile_id="profile-1",
    )
    event = SimpleNamespace(actual_at=datetime(2026, 2, 1, tzinfo=UTC))
    payload = development_routes._case_payload(
        item, {"conversation": event, "unknown_custom_fact": event}
    )
    assert payload.actual_dates.conversation_date.isoformat() == "2026-02-01"

    profile = SimpleNamespace(assumptions={"conversation_days": 5})
    persisted = SimpleNamespace(
        milestone_type="conversation",
        planned_at=datetime(2026, 2, 6, tzinfo=UTC),
        adjusted_at=datetime(2026, 2, 7, tzinfo=UTC),
        version=3,
    )
    db = _Db(gets={"profile-1": profile}, scalars=[[], [persisted]])
    monkeypatch.setattr(
        development_routes,
        "calculate_reference_plan",
        lambda **kwargs: {
            "nodes": [
                {"key": "conversation", "reference_date": kwargs["application_date"]},
                {"key": "training", "reference_date": kwargs["application_date"]},
            ]
        },
    )
    plan = development_routes._calculated_reference_plan(db, item)
    assert plan["nodes"][0]["effective_date"].isoformat() == "2026-02-07"
    assert plan["nodes"][1]["version"] == 0
    assert plan["profile_snapshot"] == {}

    monkeypatch.setattr(development_routes, "_current_progress_events", lambda *_args: {})
    for attributes, expected in (
        ({"converted_at": datetime.now(UTC)}, "completed"),
        ({"probationary_at": datetime.now(UTC)}, "probationary"),
        ({"development_object_at": datetime.now(UTC)}, "development_object"),
        ({"activist_at": datetime.now(UTC)}, "activist"),
        ({}, "application"),
    ):
        values = {
            "id": "case-1",
            "activist_at": None,
            "development_object_at": None,
            "probationary_at": None,
            "converted_at": None,
        }
        values.update(attributes)
        target = SimpleNamespace(
            **values,
        )
        development_routes._sync_high_level_facts(_Db(), target)
        assert target.stage == expected


def test_party_development_timeline_covers_all_visual_states(monkeypatch) -> None:
    now = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    milestones = [
        {
            "id": "done",
            "milestone_type": "conversation",
            "actual_at": None,
            "legal_earliest_at": None,
            "legal_deadline_at": None,
            "planned_at": now,
            "adjusted_at": None,
            "plan_kind": "reference",
            "reminder_days": [],
            "version": 1,
        },
        {
            "id": "overdue",
            "milestone_type": "activist_date",
            "actual_at": None,
            "legal_earliest_at": None,
            "legal_deadline_at": now.replace(year=2025),
            "planned_at": None,
            "adjusted_at": None,
            "plan_kind": "legal",
            "reminder_days": [],
            "version": 1,
        },
        {
            "id": "upcoming",
            "milestone_type": "training",
            "actual_at": None,
            "legal_earliest_at": None,
            "legal_deadline_at": now.replace(day=30),
            "planned_at": None,
            "adjusted_at": None,
            "plan_kind": "legal",
            "reminder_days": [],
            "version": 1,
        },
        {
            "id": "planned",
            "milestone_type": "review",
            "actual_at": None,
            "legal_earliest_at": None,
            "legal_deadline_at": None,
            "planned_at": None,
            "adjusted_at": None,
            "plan_kind": "reference",
            "reminder_days": [],
            "version": 1,
        },
    ]
    progress = [
        {
            "id": "fact-1",
            "milestone_type": "conversation",
            "actual_at": now.isoformat(),
            "version": 1,
        },
        {
            "id": "fact-2",
            "milestone_type": "oath",
            "actual_at": now.isoformat(),
            "version": 1,
        },
        {
            "id": "fact-3",
            "milestone_type": "legacy_bad_date",
            "actual_at": "bad-date",
            "version": 1,
        },
    ]
    monkeypatch.setattr(development_routes, "_case_or_404", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        development_routes,
        "_case_out",
        lambda *_args: {"milestones": milestones, "progress_events": progress},
    )
    monkeypatch.setattr(development_routes, "utcnow", lambda: now)
    result = development_routes.get_case_timeline("case-1", SimpleNamespace(), _Db())
    states = {row["milestone_type"]: row["visual_state"] for row in result["timeline"]}
    assert states == {
        "conversation": "completed",
        "activist_date": "overdue",
        "training": "upcoming",
        "review": "planned",
        "oath": "completed",
        "legacy_bad_date": "completed",
    }


def test_party_development_invalid_adjustment_is_not_persisted(monkeypatch) -> None:
    item = SimpleNamespace()
    monkeypatch.setattr(
        development_routes,
        "_calculated_reference_plan",
        lambda *_args: {"nodes": [{"key": "conversation"}]},
    )
    _problem(
        "REFERENCE_PLAN_NODE_INVALID",
        lambda: development_routes._apply_reference_plan(
            _Db(), item, adjustments={"unknown": datetime(2026, 1, 1).date()}
        ),
    )
