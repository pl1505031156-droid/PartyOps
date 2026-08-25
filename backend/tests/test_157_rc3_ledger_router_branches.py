"""台账导入路由状态机、映射确认与重复处理的分支回归。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.ledger_imports import FieldSpec
from app.problems import ProblemException
from app.routers import ledger_imports as routes
from app.schemas import (
    LedgerImportCommitRequest,
    LedgerImportMappingPatch,
    LedgerImportProfilePatch,
    LedgerImportTemplateCreate,
    LedgerImportTemplatePatch,
)


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _DB:
    def __init__(self, *, gets=None, scalars=None, scalar=None):
        self.gets = gets or {}
        self.scalars_queue = list(scalars or [])
        self.scalar_queue = list(scalar or [])
        self.added = []
        self.deleted = []
        self.commits = 0

    def get(self, model, identifier):
        return self.gets.get((model, identifier), self.gets.get(identifier))

    def scalars(self, _query):
        return _Scalars(self.scalars_queue.pop(0) if self.scalars_queue else [])

    def scalar(self, _query):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def add(self, item):
        self.added.append(item)

    def delete(self, item):
        self.deleted.append(item)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1


def _code(callable_, *args, **kwargs) -> str:
    with pytest.raises(ProblemException) as raised:
        callable_(*args, **kwargs)
    return raised.value.code


def _mapping(*items: dict, version: int = 1) -> LedgerImportMappingPatch:
    return LedgerImportMappingPatch(sheet_name="台账", header_row=1, version=version, mappings=list(items))


def test_time_device_target_and_job_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    naive = datetime(2026, 8, 25, 8, 0)
    aware = naive.replace(tzinfo=UTC)
    assert routes._aware(naive).tzinfo == UTC
    assert routes._aware(aware) is aware
    assert routes._date_time(None) is None
    assert routes._date_time(date(2026, 8, 25)) == datetime(2026, 8, 25, tzinfo=UTC)

    monkeypatch.setattr(routes, "request_device", lambda request, db: None)
    assert routes._device_id(SimpleNamespace(), _DB()) is None
    monkeypatch.setattr(routes, "request_device", lambda request, db: SimpleNamespace(id="device-1"))
    assert routes._device_id(SimpleNamespace(), _DB()) == "device-1"

    definition = SimpleNamespace(key="custom_level", label="等级", field_type="text", aliases=["档案等级"], required=False)
    fields = routes._target_fields(_DB(scalars=[[definition]]), "party_development", None)
    assert fields[-1].key == "custom_level"
    assert _code(routes._target_fields, _DB(), "archive", None) == "LEDGER_TARGET_INVALID"
    assert _code(routes._target_fields, _DB(gets={"missing": None}), "archive", "missing") == "ARCHIVE_CATEGORY_NOT_FOUND"
    inactive = SimpleNamespace(active=False)
    assert _code(routes._target_fields, _DB(gets={"inactive": inactive}), "archive", "inactive") == "ARCHIVE_CATEGORY_NOT_FOUND"
    category = SimpleNamespace(active=True, field_schema=[])
    assert routes._target_fields(_DB(gets={"category": category}), "archive", "category")[0].key == "archive_year"

    user = SimpleNamespace(id="user-1")
    request = SimpleNamespace()
    assert routes._ensure_target_permission(_DB(), request, user, "party_development", None) is None
    assert _code(routes._ensure_target_permission, _DB(), request, user, "archive", None) == "LEDGER_TARGET_INVALID"
    assert _code(routes._ensure_target_permission, _DB(gets={"bad": inactive}), request, user, "archive", "bad") == "ARCHIVE_CATEGORY_NOT_FOUND"
    monkeypatch.setattr(routes, "can_contribute_category", lambda *args: False)
    assert _code(routes._ensure_target_permission, _DB(gets={"category": category}), request, user, "archive", "category") == "LEDGER_TARGET_CONTRIBUTE_DENIED"
    monkeypatch.setattr(routes, "can_contribute_category", lambda *args: True)
    assert routes._ensure_target_permission(_DB(gets={"category": category}), request, user, "archive", "category") is category

    assert _code(routes._job, _DB(), "missing", user) == "LEDGER_JOB_NOT_FOUND"
    foreign = SimpleNamespace(created_by="other")
    assert _code(routes._job, _DB(gets={"foreign": foreign}), "foreign", user) == "LEDGER_JOB_NOT_FOUND"
    expired = SimpleNamespace(id="expired", created_by=user.id, expires_at=datetime.now(UTC) - timedelta(seconds=1), status="mapped")
    expired_db = _DB(gets={"expired": expired})
    monkeypatch.setattr(routes, "delete_stage", lambda job_id: None)
    assert _code(routes._job, expired_db, "expired", user) == "LEDGER_JOB_EXPIRED"
    assert expired.status == "expired" and expired_db.commits == 1
    finalized = SimpleNamespace(created_by=user.id, expires_at=datetime.now(UTC) - timedelta(seconds=1), status="committed")
    assert routes._job(_DB(gets={"done": finalized}), "done", user) is finalized
    versioned = SimpleNamespace(version=2)
    assert _code(routes._assert_version, versioned, 1) == "VERSION_CONFLICT"
    routes._assert_version(versioned, 2)


def test_bounded_upload_and_mapping_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    class Upload:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def read(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

    assert asyncio.run(routes._bounded_upload(Upload([b"ab", b"cd"]))) == b"abcd"
    monkeypatch.setattr(routes, "MAX_FILE_BYTES", 2)
    with pytest.raises(ProblemException) as raised:
        asyncio.run(routes._bounded_upload(Upload([b"abc"])))
    assert raised.value.code == "LEDGER_FILE_TOO_LARGE"

    fields = [FieldSpec("name", "姓名", "text", ("人员姓名",), True), FieldSpec("gender", "性别", "text", ("性别",))]
    assert _code(
        routes._mapping_payload,
        _mapping(
            {"source_column": "姓名", "action": "map", "target_field": "name", "confirmed": True},
            {"source_column": " 姓名 ", "action": "ignore"},
        ),
        fields,
    ) == "LEDGER_MAPPING_SOURCE_DUPLICATE"
    assert _code(routes._mapping_payload, _mapping({"source_column": "新列", "action": "create", "create_label": "新列", "create_type": "text", "confirmed": False}), fields) == "LEDGER_NEW_FIELD_CONFIRM_REQUIRED"
    assert _code(routes._mapping_payload, _mapping({"source_column": "未知", "action": "map", "target_field": "unknown", "confirmed": True}), fields) == "LEDGER_MAPPING_TARGET_INVALID"
    assert _code(
        routes._mapping_payload,
        _mapping(
            {"source_column": "姓名", "action": "map", "target_field": "name", "confirmed": True},
            {"source_column": "名字", "action": "map", "target_field": "name", "confirmed": True},
        ),
        fields,
    ) == "LEDGER_MAPPING_TARGET_DUPLICATE"
    assert _code(routes._mapping_payload, _mapping({"source_column": "姓名", "action": "map", "target_field": "name", "confirmed": False}), fields) == "LEDGER_MAPPING_CONFIRM_REQUIRED"
    mapped = routes._mapping_payload(
        _mapping(
            {"source_column": "备注", "action": "ignore"},
            {"source_column": "姓名", "action": "map", "target_field": "name", "confirmed": True},
            {"source_column": "档案等级", "action": "create", "create_label": "档案等级", "create_type": "text", "confirmed": True},
        ),
        fields,
    )
    assert mapped[0]["target_field"] is None
    assert mapped[2]["target_field"].startswith("custom_")


def test_value_normalization_duplicate_detection_and_stages() -> None:
    assert routes._validate_mapped_value("text", None, "文本") is None
    with pytest.raises(ValueError, match="公式样式"):
        routes._validate_mapped_value("text", "=1+1", "备注")
    assert routes._validate_mapped_value("date", "2026-08-25", "日期") == "2026-08-25"
    assert routes._validate_mapped_value("number", "2", "数量") == 2
    assert routes._validate_mapped_value("number", "2.5", "数量") == 2.5
    assert routes._validate_mapped_value("list", "甲、乙", "人员") == ["甲", "乙"]
    assert routes._validate_mapped_value("text", " 多   空格 ", "文本") == "多 空格"

    assert routes._party_duplicate(_DB(), {"name": "", "party_committee": "党委", "party_branch": "支部"}) == (None, "none")
    one = SimpleNamespace(id="one")
    two = SimpleNamespace(id="two")
    values = {"name": "张三", "party_committee": "党委", "party_branch": "支部", "birth_date": "1990-01-01"}
    assert routes._party_duplicate(_DB(scalars=[[one]]), values) == (one, "exact")
    assert routes._party_duplicate(_DB(scalars=[[one, two]]), values) == (None, "conflict")
    assert routes._party_duplicate(_DB(scalars=[[]]), values) == (None, "none")
    values.pop("birth_date")
    assert routes._party_duplicate(_DB(scalars=[[one]]), values) == (one, "possible")
    assert routes._party_duplicate(_DB(scalars=[[one, two]]), values) == (None, "conflict")

    archive = {"archive_year": 2026, "document_no": "党委〔2026〕1号"}
    assert routes._archive_duplicate(_DB(scalars=[[one]]), "category", archive) == (one, "exact")
    assert routes._archive_duplicate(_DB(scalars=[[one, two]]), "category", archive) == (None, "conflict")
    assert routes._archive_duplicate(_DB(scalars=[[]]), "category", {"archive_year": 2026, "person_identifier": "P001"}) == (None, "none")
    assert routes._archive_duplicate(_DB(), "category", {"archive_year": 2026}) == (None, "none")

    assert routes._next_stage({"converted_date": "2028-01-01"}) == "completed"
    assert routes._next_stage({"probationary_date": "2027-01-01"}) == "probationary"
    assert routes._next_stage({"branch_acceptance_date": "2027-01-01"}) == "probationary"
    assert routes._next_stage({"development_object_date": "2026-12-01"}) == "development_object"
    assert routes._next_stage({"activist_date": "2026-06-01"}) == "activist"
    assert routes._next_stage({}) == "application"
    assert routes._selected_update_fields("fill", {"a": 1}) == {"a"}
    assert routes._selected_update_fields("update:a,missing", {"a": 1}) == {"a"}
    assert routes._selected_update_fields("new", {"a": 1}) == {"a"}
    assert routes._is_empty([]) is True
    assert routes._is_empty("有值") is False


def test_custom_fields_apply_party_and_restore_snapshots() -> None:
    user = SimpleNamespace(id="user-1")
    job = SimpleNamespace(id="job-1", target_type="party_development", mapping={"columns": [
        {"action": "ignore"},
        {"action": "create", "target_field": "custom_level", "create_label": "档案等级", "create_type": "text", "source_column": "等级"},
    ]})
    db = _DB(scalars=[[]])
    assert routes._create_custom_fields(db, job, user, None) == ["custom_level"]
    assert db.added[0].label == "档案等级"
    existing = SimpleNamespace(key="custom_level", label="档案等级")
    assert routes._create_custom_fields(_DB(scalars=[[existing]]), job, user, None) == []
    conflict = SimpleNamespace(key="other", label="档案等级")
    assert _code(routes._create_custom_fields, _DB(scalars=[[conflict]]), job, user, None) == "LEDGER_FIELD_LABEL_CONFLICT"

    archive_job = SimpleNamespace(id="job-2", target_type="archive", mapping=job.mapping)
    assert _code(routes._create_custom_fields, _DB(), archive_job, user, None) == "LEDGER_TARGET_INVALID"
    category = SimpleNamespace(field_schema=[], version=1)
    assert routes._create_custom_fields(_DB(), archive_job, user, category) == ["custom_level"]
    assert category.version == 2 and category.field_schema[0]["active"] is True
    category = SimpleNamespace(field_schema=[{"key": "custom_level", "label": "旧等级"}], version=1)
    assert routes._create_custom_fields(_DB(), archive_job, user, category) == []
    category = SimpleNamespace(field_schema=[{"key": "other", "label": "档案等级"}], version=1)
    assert _code(routes._create_custom_fields, _DB(), archive_job, user, category) == "LEDGER_FIELD_LABEL_CONFLICT"

    item = SimpleNamespace(
        party_committee="旧党委", party_branch="旧支部", name="张三", gender="", ethnicity="汉族", education="大学",
        training_contacts=[], introducers=["旧介绍人"], birth_date=None, application_at=datetime(2026, 1, 1, tzinfo=UTC),
        activist_at=None, development_object_at=None, probationary_at=None, converted_at=None, extra_fields={"keep": "保留"},
    )
    values = {
        "party_committee": "新党委", "party_branch": "新支部", "name": "张三", "gender": "男",
        "training_contacts": ["甲"], "introducers": ["乙"], "birth_date": "1990-01-01",
        "activist_date": "2026-06-01", "custom_level": "一级",
    }
    routes._apply_party_values(item, values, set(values), fill_only=True)
    assert item.party_committee == "旧党委"
    assert item.gender == "男" and item.training_contacts == ["甲"]
    assert item.birth_date == datetime(1990, 1, 1, tzinfo=UTC)
    assert item.activist_at == datetime(2026, 6, 1, tzinfo=UTC)
    assert item.extra_fields == {"keep": "保留", "custom_level": "一级"}
    routes._apply_party_values(item, {"gender": "女", "introducers": ["丙"], "birth_date": None}, {"gender", "introducers", "birth_date"}, fill_only=False)
    assert item.gender == "女" and item.introducers == ["丙"] and item.birth_date is None

    snapshot = {
        "party_committee": "党委", "party_branch": "支部", "name": "李四", "gender": "女", "ethnicity": "汉族",
        "education": "研究生", "stage": "activist", "status": "active", "birth_date": None,
        "application_at": "2026-01-01T00:00:00+00:00", "activist_at": None, "development_object_at": None,
        "probationary_at": None, "converted_at": None, "training_contacts": ["甲"], "introducers": [], "extra_fields": {"x": 1},
    }
    routes._restore_party(item, snapshot)
    assert item.name == "李四" and item.birth_date is None and item.application_at.tzinfo is not None

    archive = SimpleNamespace()
    archive_snapshot = {
        "archive_year": 2026, "sequence_no": 1, "document_no": "", "title": "档案", "summary": "", "source_unit": "",
        "person_name": "张三", "person_identifier": "", "personnel_type": "", "organization": "", "assessment_result": "",
        "void_reason": "", "involved_persons": [], "tags": [], "custom_fields": {}, "document_date": None, "status": "active",
    }
    routes._restore_archive(archive, archive_snapshot)
    assert archive.title == "档案" and archive.document_date is None
    archive_snapshot["document_date"] = "2026-08-25T00:00:00+00:00"
    routes._restore_archive(archive, archive_snapshot)
    assert archive.document_date.date() == date(2026, 8, 25)


def test_progress_event_merge_branches() -> None:
    user = SimpleNamespace(id="user-1")
    job = SimpleNamespace(id="job-1")
    item = SimpleNamespace(id="case-1")
    actual = datetime(2026, 8, 25, tzinfo=UTC)
    same = SimpleNamespace(id="same", actual_at=actual, status="confirmed", version=1)
    fill_previous = SimpleNamespace(id="fill", actual_at=datetime(2026, 8, 1, tzinfo=UTC), status="confirmed", version=1)
    replace_previous = SimpleNamespace(id="replace", actual_at=datetime(2026, 8, 2, tzinfo=UTC), status="confirmed", version=1)
    db = _DB(scalar=[same, fill_previous, replace_previous, None])
    values = {
        "conversation_date": "2026-08-25",
        "activist_date": "2026-08-25",
        "development_object_date": "2026-08-25",
        "oath_date": "2026-08-25",
    }
    routes._append_progress_events(db, job, item, 2, values, {"conversation_date"}, "update:conversation_date", user)
    routes._append_progress_events(db, job, item, 2, values, {"activist_date"}, "fill", user)
    routes._append_progress_events(db, job, item, 2, values, {"development_object_date"}, "update:development_object_date", user)
    routes._append_progress_events(db, job, item, 2, values, {"oath_date"}, "new", user)
    routes._append_progress_events(db, job, item, 2, values, {"missing"}, "new", user)
    assert replace_previous.status == "superseded" and replace_previous.version == 2
    assert any(getattr(value, "milestone_type", "") == "development_object" for value in db.added)
    assert any(getattr(value, "milestone_type", "") == "oath" for value in db.added)


def test_full_row_validation_errors_warnings_and_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    fields = [
        FieldSpec("name", "姓名", "text", ("姓名",), True),
        FieldSpec("party_committee", "所属党委", "text", ("党委",), True),
        FieldSpec("party_branch", "所属党支部", "text", ("支部",), True),
        FieldSpec("application_date", "申请时间", "date", ("申请时间",), True),
        FieldSpec("score", "分数", "number", ("分数",)),
    ]
    job = SimpleNamespace(
        id="job-1", target_type="party_development", target_id=None, sheet_name="台账", header_row=1,
        mapping={"columns": [
            {"action": "map", "target_field": "name"},
            {"action": "map", "target_field": "party_committee"},
            {"action": "map", "target_field": "party_branch"},
            {"action": "map", "target_field": "application_date"},
            {"action": "map", "target_field": "score"},
        ]},
    )
    rows = [
        {"row_number": 2, "values": {"name": "=cmd", "party_committee": "党委", "party_branch": "", "application_date": "bad"}},
        {"row_number": 3, "values": {"name": "重复", "party_committee": "党委", "party_branch": "支部", "application_date": "2026-01-01"}},
        {"row_number": 4, "values": {"name": "可能", "party_committee": "党委", "party_branch": "支部", "application_date": "2026-01-01"}},
        {"row_number": 5, "values": {"name": "正常", "party_committee": "党委", "party_branch": "支部", "application_date": "2026-01-01", "score": "2.5"}},
        {"row_number": 6, "values": {"name": "动作", "party_committee": "党委", "party_branch": "支部", "application_date": "2026-01-01"}},
        {"row_number": 7, "values": {"name": "更新", "party_committee": "党委", "party_branch": "支部", "application_date": "2026-01-01"}},
    ]
    monkeypatch.setattr(routes, "_target_fields", lambda *_args: fields)
    monkeypatch.setattr(routes, "mapped_rows", lambda *_args: rows)

    def duplicate(_db, values):
        if values.get("name") in {"重复", "可能"}:
            return SimpleNamespace(id="match-1"), "possible"
        return None, "none"

    monkeypatch.setattr(routes, "_party_duplicate", duplicate)
    checked, summary = routes._validated_rows(
        _DB(),
        job,
        {"4": "skip", "6": "invalid", "7": "update:missing"},
    )
    assert summary["checked_rows"] == 6
    assert summary["warning_rows"] == 1
    assert summary["error_rows"] == 4
    assert next(item for item in checked if item["row_number"] == 5)["values"]["score"] == 2.5

    archive_job = SimpleNamespace(**{**job.__dict__, "target_type": "archive", "target_id": "category"})
    monkeypatch.setattr(routes, "_archive_duplicate", lambda *_args: (None, "none"))
    checked, summary = routes._validated_rows(_DB(), archive_job, {})
    assert checked and summary["checked_rows"] == 6
    no_mapping = SimpleNamespace(**{**job.__dict__, "mapping": {}})
    assert _code(routes._validated_rows, _DB(), no_mapping, {}) == "LEDGER_MAPPING_REQUIRED"


def test_profile_mapping_commit_and_template_state_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(id="user-1")
    request = SimpleNamespace(client=None)
    monkeypatch.setattr(routes, "_assert_version", lambda *_args: None)
    monkeypatch.setattr(routes, "_ensure_target_permission", lambda *_args: None)
    monkeypatch.setattr(routes, "_target_fields", lambda *_args: [FieldSpec("name", "姓名", "text", ("姓名",), True)])
    monkeypatch.setattr(routes, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "client_ip", lambda _request: "127.0.0.1")
    db = _DB()
    finalized = SimpleNamespace(status="committed", target_type="party_development", target_id=None)
    monkeypatch.setattr(routes, "_job", lambda *_args: finalized)
    profile_payload = LedgerImportProfilePatch(sheet_name="台账", header_row=1, version=1)
    assert _code(routes.patch_profile, "job", profile_payload, request, user, db) == "LEDGER_JOB_FINALIZED"
    mapping_payload = _mapping({"source_column": "姓名", "action": "map", "target_field": "name", "confirmed": True})
    assert _code(routes.patch_mapping, "job", mapping_payload, request, user, db) == "LEDGER_JOB_FINALIZED"

    job = SimpleNamespace(
        id="job", status="mapped", target_type="party_development", target_id=None, profile={}, mapping={}, validation={},
        sheet_name="台账", header_row=1, total_rows=0, version=1,
    )
    monkeypatch.setattr(routes, "_job", lambda *_args: job)
    monkeypatch.setattr(routes, "load_stage", lambda _id: {})
    assert _code(routes.patch_profile, "job", profile_payload, request, user, db) == "LEDGER_SHEET_NOT_FOUND"
    assert _code(routes.patch_mapping, "job", mapping_payload, request, user, db) == "LEDGER_SHEET_NOT_FOUND"
    monkeypatch.setattr(routes, "load_stage", lambda _id: {"台账": [["姓名", "姓名"], ["张三", "张三"]]})
    assert _code(routes.patch_mapping, "job", mapping_payload, request, user, db) == "LEDGER_DUPLICATE_HEADERS"

    committed = SimpleNamespace(status="committed")
    monkeypatch.setattr(routes, "_job", lambda *_args: committed)
    monkeypatch.setattr(routes, "_job_out", lambda value: {"status": value.status})
    assert routes.commit_import("job", LedgerImportCommitRequest(version=1), request, user, db) is not None
    undone = SimpleNamespace(status="undone")
    monkeypatch.setattr(routes, "_job", lambda *_args: undone)
    assert _code(routes.commit_import, "job", LedgerImportCommitRequest(version=1), request, user, db) == "LEDGER_JOB_FINALIZED"
    monkeypatch.setattr(routes, "_job", lambda *_args: job)
    assert _code(routes.commit_import, "job", LedgerImportCommitRequest(version=1), request, user, db) == "LEDGER_SHARED_STORAGE_CONFIRM_REQUIRED"
    job.mapping = {"columns": [{"action": "create"}]}
    assert _code(routes.commit_import, "job", LedgerImportCommitRequest(version=1, confirm_shared_storage=True), request, user, db) == "LEDGER_NEW_FIELD_CONFIRM_REQUIRED"
    monkeypatch.setattr(routes, "_validated_rows", lambda *_args: ([], {"checked_rows": 1, "valid_rows": 0, "warning_rows": 0, "error_rows": 1, "issues": [], "issues_truncated": 0}))
    assert _code(routes.commit_import, "job", LedgerImportCommitRequest(version=1, confirm_shared_storage=True, confirm_new_fields=True), request, user, db) == "LEDGER_VALIDATION_FAILED"

    payload = LedgerImportTemplateCreate(name="我的映射", target_type="party_development", header_signature="a" * 64, mapping={})
    monkeypatch.setattr(routes, "_ensure_target_permission", lambda *_args: None)
    existing = SimpleNamespace(id="template", created_by=user.id, target_type="party_development", target_id=None, name="旧名", header_signature="a" * 64, mapping={}, active=False, version=1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    db = _DB(scalar=[existing])
    result = routes.create_template(payload, request, user, db)
    assert result["name"] == "我的映射" and result["active"] is True
    db = _DB(scalar=[None])
    created = routes.create_template(payload, request, user, db)
    assert created["name"] == "我的映射" and db.added

    patch = LedgerImportTemplatePatch(name="新名称", active=False, version=1)
    assert _code(routes.patch_template, "missing", patch, request, user, _DB()) == "LEDGER_TEMPLATE_NOT_FOUND"
    foreign = SimpleNamespace(created_by="other")
    assert _code(routes.patch_template, "foreign", patch, request, user, _DB(gets={"foreign": foreign})) == "LEDGER_TEMPLATE_NOT_FOUND"
    stale = SimpleNamespace(created_by=user.id, version=2)
    assert _code(routes.patch_template, "stale", patch, request, user, _DB(gets={"stale": stale})) == "VERSION_CONFLICT"
    item = SimpleNamespace(id="template", created_by=user.id, target_type="party_development", target_id=None, name="旧名", header_signature="a" * 64, mapping={}, active=True, version=1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    result = routes.patch_template("template", patch, request, user, _DB(gets={"template": item}))
    assert result["name"] == "新名称" and result["active"] is False
    assert _code(routes.delete_template, "missing", request, user, _DB()) == "LEDGER_TEMPLATE_NOT_FOUND"
    routes.delete_template("template", request, user, _DB(gets={"template": item}))
    assert item.active is False
