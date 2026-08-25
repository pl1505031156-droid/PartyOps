"""台账导入规模上限、字段回收、更新策略与安全撤销分支。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.enums import ArchiveRecordMode, ArchiveRecordStatus
from app.ledger_imports import FieldSpec
from app.models import (
    ArchiveCategory,
    ArchiveRecord,
    PartyDevelopmentCase,
    PartyDevelopmentProgressEvent,
)
from app.problems import ProblemException
from app.routers import ledger_imports as routes
from app.routers import party_development as development_routes
from app.schemas import (
    LedgerImportProfilePatch,
    LedgerImportTemplatePatch,
    LedgerImportUndoRequest,
)


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class _Db:
    def __init__(self, *, gets=None, scalars=None, scalar=None):
        self.gets = dict(gets or {})
        self.scalars_queue = list(scalars or [])
        self.scalar_queue = list(scalar or [])
        self.added = []
        self.deleted = []
        self.commits = 0

    def get(self, model, key):
        return self.gets.get((model, key), self.gets.get(key))

    def scalars(self, _statement):
        return _Rows(self.scalars_queue.pop(0) if self.scalars_queue else [])

    def scalar(self, _statement):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1


def _code(code: str, call) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == code


def test_validation_scales_to_full_sheet_and_truncates_diagnostics(monkeypatch) -> None:
    fields = [FieldSpec("name", "姓名", "text", ("姓名",), True)]
    job = SimpleNamespace(
        id="job-scale",
        target_type="party_development",
        target_id=None,
        sheet_name="台账",
        header_row=1,
        mapping={"columns": [{"action": "map", "target_field": "name"}]},
    )
    monkeypatch.setattr(routes, "_target_fields", lambda *_args: fields)
    monkeypatch.setattr(routes, "_party_duplicate", lambda *_args: (None, "none"))
    rows = [
        {"row_number": index + 2, "values": {"name": "=cmd"}}
        for index in range(1_005)
    ]
    monkeypatch.setattr(routes, "mapped_rows", lambda *_args: rows)
    checked, summary = routes._validated_rows(_Db(), job, {})
    assert len(checked) == 1_005
    assert summary["error_rows"] == 1_005
    assert len(summary["issues"]) == 1_000
    assert summary["issues_truncated"] == 5

    possible_rows = [
        {"row_number": index + 2, "values": {"name": f"人员{index}"}}
        for index in range(1_005)
    ]
    monkeypatch.setattr(routes, "mapped_rows", lambda *_args: possible_rows)
    monkeypatch.setattr(
        routes,
        "_party_duplicate",
        lambda *_args: (SimpleNamespace(id="existing"), "possible"),
    )
    actions = {str(index + 2): "skip" for index in range(1_005)}
    _, summary = routes._validated_rows(_Db(), job, actions)
    assert summary["warning_rows"] == 1_005
    assert len(summary["issues"]) == 1_000
    assert summary["issues_truncated"] == 5

    monkeypatch.setattr(
        routes,
        "mapped_rows",
        lambda *_args: [{"row_number": 2, "values": {"name": "可更新人员"}}],
    )
    monkeypatch.setattr(
        routes,
        "_party_duplicate",
        lambda *_args: (SimpleNamespace(id="existing"), "exact"),
    )
    checked, summary = routes._validated_rows(
        _Db(), job, {"2": "update:name"}
    )
    assert summary["error_rows"] == 0 and checked[0]["action"] == "update:name"


def test_fill_only_respects_existing_party_dates_and_custom_values() -> None:
    existing = datetime(2026, 1, 1, tzinfo=UTC)
    item = SimpleNamespace(
        party_committee="原党委",
        party_branch="",
        name="张三",
        gender="",
        ethnicity="汉族",
        education="",
        training_contacts=["原联系人"],
        introducers=[],
        birth_date=existing,
        application_at=existing,
        activist_at=None,
        development_object_at=None,
        probationary_at=None,
        converted_at=None,
        extra_fields={"custom_keep": "已有", "custom_empty": ""},
    )
    values = {
        "party_committee": "新党委",
        "party_branch": "新支部",
        "training_contacts": ["新联系人"],
        "introducers": ["新介绍人"],
        "birth_date": "1990-01-01",
        "activist_date": "2026-06-01",
        "custom_keep": "覆盖值",
        "custom_empty": "补齐值",
    }
    routes._apply_party_values(item, values, set(values), fill_only=True)
    assert item.party_committee == "原党委"
    assert item.party_branch == "新支部"
    assert item.training_contacts == ["原联系人"]
    assert item.introducers == ["新介绍人"]
    assert item.birth_date == existing
    assert item.activist_at.date().isoformat() == "2026-06-01"
    assert item.extra_fields == {"custom_keep": "已有", "custom_empty": "补齐值"}


def _archive_record() -> SimpleNamespace:
    return SimpleNamespace(
        id="record-1",
        category_id="category-1",
        archive_year=2026,
        sequence_no=1,
        document_no="原文号",
        title="原标题",
        summary="",
        involved_persons=[],
        source_unit="",
        document_date=None,
        person_name="张三",
        person_identifier="P001",
        personnel_type="事业编",
        organization="第一支部",
        assessment_result="合格",
        tags=[],
        custom_fields={"custom_keep": "已有", "custom_empty": ""},
        status=ArchiveRecordStatus.ACTIVE,
        void_reason="",
        version=1,
        updated_by=None,
    )


def test_archive_update_skip_fill_and_explicit_fields(monkeypatch) -> None:
    category = SimpleNamespace(
        id="category-1",
        record_mode=ArchiveRecordMode.DOCUMENT,
        field_schema=[
            {"key": "custom_keep", "label": "保留字段", "type": "text"},
            {"key": "custom_empty", "label": "待补字段", "type": "text"},
        ],
    )
    record = _archive_record()
    job = SimpleNamespace(id="job-12345678")
    user = SimpleNamespace(id="user-1")
    monkeypatch.setattr(routes, "refresh_search_index", lambda *_args: None)
    monkeypatch.setattr(routes, "_record_change", lambda *_args: None)

    skipped = _Db(gets={(ArchiveRecord, record.id): record})
    routes._create_or_update_archive(
        skipped,
        job,
        category,
        {"row_number": 2, "matched_id": record.id, "action": "skip", "values": {}},
        [],
        user,
    )
    assert skipped.added == []

    values = {
        "archive_year": 2026,
        "title": "新标题",
        "summary": "补充摘要",
        "custom_keep": "覆盖值",
        "custom_empty": "补齐值",
    }
    db = _Db(gets={(ArchiveRecord, record.id): record}, scalar=[2])
    routes._create_or_update_archive(
        db,
        job,
        category,
        {
            "row_number": 3,
            "matched_id": record.id,
            "action": "fill",
            "values": values,
        },
        [],
        user,
    )
    assert record.title == "原标题"
    assert record.summary == "补充摘要"
    assert record.custom_fields == {"custom_keep": "已有", "custom_empty": "补齐值"}
    assert record.version == 2 and len(db.added) == 1

    db = _Db(gets={(ArchiveRecord, record.id): record}, scalar=[3])
    routes._create_or_update_archive(
        db,
        job,
        category,
        {
            "row_number": 4,
            "matched_id": record.id,
            "action": "update:title,custom_keep",
            "values": values,
        },
        [],
        user,
    )
    assert record.title == "新标题"
    assert record.custom_fields["custom_keep"] == "覆盖值"


def test_party_row_skip_and_update_paths(monkeypatch) -> None:
    item = SimpleNamespace(
        id="case-1",
        version=1,
        party_committee="党委",
        party_branch="支部",
        name="张三",
        gender="",
        ethnicity="汉族",
        education="本科",
        birth_date=None,
        application_at=datetime(2026, 1, 1, tzinfo=UTC),
        activist_at=None,
        development_object_at=None,
        probationary_at=None,
        converted_at=None,
        training_contacts=[],
        introducers=[],
        extra_fields={},
        stage="application",
        status="active",
    )
    job = SimpleNamespace(id="job-1")
    user = SimpleNamespace(id="user-1")
    monkeypatch.setattr(routes, "_append_progress_events", lambda *_args: None)
    monkeypatch.setattr(routes, "_record_change", lambda *_args: None)
    monkeypatch.setattr(development_routes, "_apply_reference_plan", lambda *_args, **_kwargs: {})
    db = _Db(gets={(PartyDevelopmentCase, item.id): item})
    routes._create_or_update_party(
        db,
        job,
        {"row_number": 2, "matched_id": item.id, "action": "skip", "values": {}},
        [],
        user,
    )
    assert item.version == 1
    routes._create_or_update_party(
        db,
        job,
        {
            "row_number": 3,
            "matched_id": item.id,
            "action": "update:gender",
            "values": {"gender": "男"},
        },
        [],
        user,
    )
    assert item.gender == "男" and item.version == 2


def test_undo_field_cleanup_retains_used_fields_and_removes_unused() -> None:
    def change(*keys: str):
        return SimpleNamespace(new_field_keys=list(keys))

    party_job = SimpleNamespace(id="job-1", target_type="party_development", target_id=None)
    missing = _Db(scalars=[[]], scalar=[None])
    routes._cleanup_undone_fields(missing, party_job, [change("custom_missing")])
    assert missing.deleted == []

    definition = SimpleNamespace(active=True, version=1)
    used_case = SimpleNamespace(extra_fields={"custom_used": "值"})
    used = _Db(scalars=[[used_case]], scalar=[definition])
    routes._cleanup_undone_fields(used, party_job, [change("custom_used")])
    assert definition.active is False and definition.version == 2

    unused_definition = SimpleNamespace(active=True, version=1)
    unused = _Db(scalars=[[]], scalar=[unused_definition])
    routes._cleanup_undone_fields(unused, party_job, [change("custom_unused")])
    assert unused.deleted == [unused_definition]

    archive_job = SimpleNamespace(id="job-2", target_type="archive", target_id=None)
    routes._cleanup_undone_fields(_Db(), archive_job, [change("custom_any")])

    category = SimpleNamespace(
        id="category-1",
        version=1,
        field_schema=[
            {"key": "unrelated", "active": True},
            {"key": "custom_used", "active": True},
            {"key": "custom_unused", "active": True},
        ],
    )
    archive_job.target_id = category.id
    other = SimpleNamespace(custom_fields={"custom_used": "仍在使用"})
    db = _Db(gets={(ArchiveCategory, category.id): category}, scalars=[[other]])
    routes._cleanup_undone_fields(
        db, archive_job, [change("custom_used", "custom_unused")]
    )
    assert category.version == 2
    assert category.field_schema == [
        {"key": "unrelated", "active": True},
        {"key": "custom_used", "active": False},
    ]
    unchanged = SimpleNamespace(
        id="category-2",
        version=1,
        field_schema=[{"key": "unrelated", "active": True}],
    )
    archive_job.target_id = unchanged.id
    routes._cleanup_undone_fields(
        _Db(gets={(ArchiveCategory, unchanged.id): unchanged}, scalars=[[]]),
        archive_job,
        [change("custom_absent")],
    )
    assert unchanged.version == 1


def _party_snapshot() -> dict:
    return {
        "party_committee": "党委",
        "party_branch": "支部",
        "name": "张三",
        "gender": "男",
        "ethnicity": "汉族",
        "education": "本科",
        "stage": "activist",
        "status": "active",
        "birth_date": None,
        "application_at": "2026-01-01T00:00:00+00:00",
        "activist_at": None,
        "development_object_at": None,
        "probationary_at": None,
        "converted_at": None,
        "training_contacts": [],
        "introducers": [],
        "extra_fields": {},
    }


def _archive_snapshot() -> dict:
    return {
        "archive_year": 2026,
        "sequence_no": 1,
        "document_no": "",
        "title": "原档案",
        "summary": "",
        "source_unit": "",
        "person_name": "张三",
        "person_identifier": "P001",
        "personnel_type": "事业编",
        "organization": "支部",
        "assessment_result": "合格",
        "void_reason": "",
        "involved_persons": [],
        "tags": [],
        "custom_fields": {},
        "document_date": None,
        "status": "active",
    }


def test_undo_import_guards_conflicts_and_restores_every_entity(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    request = SimpleNamespace(client=None)
    monkeypatch.setattr(routes, "_assert_version", lambda *_args: None)
    monkeypatch.setattr(routes, "_ensure_target_permission", lambda *_args: None)
    monkeypatch.setattr(routes, "_cleanup_undone_fields", lambda *_args: None)
    monkeypatch.setattr(routes, "refresh_search_index", lambda *_args: None)
    monkeypatch.setattr(routes, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "emit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "client_ip", lambda *_args: "127.0.0.1")
    monkeypatch.setattr(routes, "_job_out", lambda job: {"status": job.status})

    job = SimpleNamespace(id="job-1", status="undone", version=1, target_type="archive", target_id="category")
    monkeypatch.setattr(routes, "_job", lambda *_args: job)
    assert routes.undo_import(
        job.id, LedgerImportUndoRequest(version=1), request, user, _Db()
    ) == {"status": "undone"}
    job.status = "mapped"
    _code(
        "LEDGER_NOT_COMMITTED",
        lambda: routes.undo_import(
            job.id, LedgerImportUndoRequest(version=1), request, user, _Db()
        ),
    )

    job.status = "committed"
    missing_change = SimpleNamespace(
        entity_type="party_development_case",
        entity_id="missing",
        after_version=1,
    )
    _code(
        "LEDGER_UNDO_CONFLICT",
        lambda: routes.undo_import(
            job.id,
            LedgerImportUndoRequest(version=1),
            request,
            user,
            _Db(scalars=[[missing_change]]),
        ),
    )
    event_change = SimpleNamespace(
        entity_type="party_development_progress_event",
        entity_id="event-conflict",
        after_version=1,
    )
    conflicted_event = SimpleNamespace(version=1, status="voided")
    _code(
        "LEDGER_UNDO_CONFLICT",
        lambda: routes.undo_import(
            job.id,
            LedgerImportUndoRequest(version=1),
            request,
            user,
            _Db(
                gets={(PartyDevelopmentProgressEvent, "event-conflict"): conflicted_event},
                scalars=[[event_change]],
            ),
        ),
    )

    party_created = SimpleNamespace(version=1, status="active")
    party_updated = SimpleNamespace(version=2, **_party_snapshot())
    archive_created = _archive_record()
    archive_updated = _archive_record()
    archive_updated.id = "archive-update"
    event = SimpleNamespace(version=1, status="confirmed", voided_at=None)
    previous = SimpleNamespace(version=1, status="superseded")

    def change(entity_type, entity_id, action, after_version, snapshot=None):
        return SimpleNamespace(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            after_version=after_version,
            before_snapshot=snapshot or {},
            status="active",
            reverted_at=None,
            new_field_keys=[],
        )

    changes = [
        change("party_development_case", "party-create", "create", 1),
        change("party_development_case", "party-update", "update", 2, _party_snapshot()),
        change("archive_record", "archive-create", "create", 1),
        change("archive_record", "archive-update", "update", 1, _archive_snapshot()),
        change(
            "party_development_progress_event",
            "event",
            "create",
            1,
            {"supersedes_event_id": "previous"},
        ),
    ]
    db = _Db(
        gets={
            (PartyDevelopmentCase, "party-create"): party_created,
            (PartyDevelopmentCase, "party-update"): party_updated,
            (ArchiveRecord, "archive-create"): archive_created,
            (ArchiveRecord, "archive-update"): archive_updated,
            (PartyDevelopmentProgressEvent, "event"): event,
            (PartyDevelopmentProgressEvent, "previous"): previous,
        },
        scalars=[changes],
    )
    result = routes.undo_import(
        job.id, LedgerImportUndoRequest(version=1), request, user, db
    )
    assert result == {"status": "undone"}
    assert party_created.status == "archived"
    assert party_updated.name == "张三"
    assert archive_created.status == ArchiveRecordStatus.VOIDED
    assert archive_updated.title == "原档案"
    assert event.status == "voided" and previous.status == "confirmed"
    assert all(item.status == "reverted" for item in changes)


def test_profile_success_and_template_patch_without_optional_fields(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    request = SimpleNamespace(client=None)
    job = SimpleNamespace(
        id="job-1",
        status="mapped",
        target_type="party_development",
        target_id=None,
        profile={},
        mapping={"old": True},
        validation={"old": True},
        sheet_name="旧表",
        header_row=1,
        total_rows=0,
        version=1,
    )
    monkeypatch.setattr(routes, "_job", lambda *_args: job)
    monkeypatch.setattr(routes, "_assert_version", lambda *_args: None)
    monkeypatch.setattr(routes, "_ensure_target_permission", lambda *_args: None)
    monkeypatch.setattr(
        routes,
        "_target_fields",
        lambda *_args: [FieldSpec("name", "姓名", "text", ("姓名",), True)],
    )
    monkeypatch.setattr(routes, "load_stage", lambda *_args: {"台账": [["姓名"], ["张三"]]})
    monkeypatch.setattr(
        routes,
        "profile_sheet",
        lambda *_args: {"total_rows": 1, "columns": []},
    )
    monkeypatch.setattr(routes, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "client_ip", lambda *_args: "127.0.0.1")
    monkeypatch.setattr(routes, "_job_out", lambda value: {"status": value.status})
    result = routes.patch_profile(
        job.id,
        LedgerImportProfilePatch(sheet_name="台账", header_row=1, version=1),
        request,
        user,
        _Db(),
    )
    assert result["status"] == "inspected" and job.mapping == {}

    template = SimpleNamespace(
        id="template-1",
        created_by=user.id,
        target_type="party_development",
        target_id=None,
        name="原名称",
        header_signature="a" * 64,
        mapping={},
        active=True,
        version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db = _Db(gets={"template-1": template})
    routes.patch_template(
        template.id,
        LedgerImportTemplatePatch(version=1),
        request,
        user,
        db,
    )
    assert template.name == "原名称" and template.active is True and template.version == 2
