"""重要档案字段、唯一性、筛选与权限错误分支回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.enums import ArchiveRecordMode, ArchiveRecordStatus, UserRole
from app.models import ArchiveCategory
from app.problems import ProblemException
from app.routers import archives


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class Db:
    def __init__(self, objects=None, rows=None, scalars=None) -> None:
        self.objects = objects or {}
        self.rows = list(rows or [])
        self.scalar_values = list(scalars or [])
        self.added = []

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalars(self, _query):
        return Rows(self.rows.pop(0) if self.rows else [])

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value) -> None:
        self.added.append(value)


def _request():
    return SimpleNamespace(client=None, cookies={})


def _user(role=UserRole.STAFF):
    return SimpleNamespace(id="user-1", role=role)


def _category(**overrides):
    values = {"id": "category-1", "active": True, "record_mode": ArchiveRecordMode.DOCUMENT}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_category_schema_attachment_and_duplicate_branches(monkeypatch) -> None:
    assert archives.client_ip(_request()) == ""
    with pytest.raises(ProblemException):
        archives.parse_version(None)
    with pytest.raises(ProblemException):
        archives.parse_version("bad")
    assert archives.parse_version('"3"') == 3

    with pytest.raises(ProblemException) as missing:
        archives._category(Db(), "missing", _user())
    assert missing.value.code == "ARCHIVE_CATEGORY_NOT_FOUND"
    inactive = _category(active=False)
    with pytest.raises(ProblemException):
        archives._category(Db(objects={(ArchiveCategory, "category-1"): inactive}), "category-1", _user())
    monkeypatch.setattr(archives, "can_view_category", lambda *_a, **_k: False)
    with pytest.raises(ProblemException) as denied:
        archives._category(Db(objects={(ArchiveCategory, "category-1"): inactive}), "category-1", _user(), include_inactive=True)
    assert denied.value.code == "ARCHIVE_ACCESS_DENIED"
    assert archives._category(Db(objects={(ArchiveCategory, "category-1"): inactive}), "category-1", _user(UserRole.ADMIN), include_inactive=True) is inactive

    for schema in (
        [{"key": ""}],
        [{"key": "x"}, {"key": "x"}],
        [{"key": "x", "type": "select", "options": []}],
    ):
        with pytest.raises(ProblemException) as invalid:
            archives._validate_field_schema(schema)
        assert invalid.value.code == "ARCHIVE_FIELD_SCHEMA_INVALID"
    normalized = archives._validate_field_schema([
        {"key": "result", "type": "select", "options": [" 优秀 ", "", "优秀"], "required": 1},
        {"key": "note"},
    ])
    assert normalized[0]["options"] == ["优秀"] and normalized[0]["required"] is True
    assert normalized[1]["label"] == "note" and normalized[1]["type"] == "text"

    attachment = SimpleNamespace(blob_sha256="missing")
    with pytest.raises(ProblemException) as missing_blob:
        archives._attachment_out(Db(), attachment)
    assert missing_blob.value.code == "ARCHIVE_ATTACHMENT_MISSING"

    record = SimpleNamespace(id="r1", category_id="c1", archive_year=2026, person_name="张三")
    assert archives._duplicate_warnings(Db(), record, _category(record_mode=ArchiveRecordMode.DOCUMENT)) == []
    record.person_name = ""
    assert archives._duplicate_warnings(Db(), record, _category(record_mode=ArchiveRecordMode.PERSON_YEAR)) == []
    record.person_name = "张三"
    assert archives._duplicate_warnings(Db(scalars=[None]), record, _category(record_mode=ArchiveRecordMode.PERSON_YEAR)) == []
    warning = archives._duplicate_warnings(Db(scalars=["duplicate"]), record, _category(record_mode=ArchiveRecordMode.PERSON_YEAR))
    assert "同名年度考核" in warning[0]


def test_archive_unique_constraints_and_revision_number(monkeypatch) -> None:
    category = _category(record_mode=ArchiveRecordMode.PERSON_YEAR)
    common = dict(archive_year=2026, sequence_no=1, document_no="党字1号", person_identifier="001")
    with pytest.raises(ProblemException) as sequence:
        archives._assert_unique(Db(scalars=["exists"]), category, **common)
    assert sequence.value.code == "ARCHIVE_SEQUENCE_EXISTS"
    with pytest.raises(ProblemException) as document:
        archives._assert_unique(Db(scalars=[None, "exists"]), category, **common, exclude_id="current")
    assert document.value.code == "ARCHIVE_DOCUMENT_NO_EXISTS"
    with pytest.raises(ProblemException) as person:
        archives._assert_unique(Db(scalars=[None, None, "exists"]), category, **common)
    assert person.value.code == "ARCHIVE_PERSON_EXISTS"
    archives._assert_unique(Db(scalars=[None, None]), _category(), **{**common, "document_no": "", "person_identifier": ""})

    monkeypatch.setattr(archives, "record_snapshot", lambda record: {"id": record.id})
    db = Db(scalars=[None])
    archives._add_revision(db, SimpleNamespace(id="record-1"), SimpleNamespace(id="user-1"), "修订")
    assert db.added[0].revision_no == 1
    db = Db(scalars=[4])
    archives._add_revision(db, SimpleNamespace(id="record-1"), SimpleNamespace(id="user-1"), "修订")
    assert db.added[0].revision_no == 5


def test_record_endpoints_reject_missing_objects(monkeypatch) -> None:
    user = _user()
    request = _request()
    db = Db()
    calls = [
        lambda: archives.get_archive_record("missing", request, user, db),
        lambda: archives.patch_archive_record("missing", SimpleNamespace(), request, '"1"', user, db),
        lambda: archives.void_archive_record("missing", SimpleNamespace(reason="x"), request, '"1"', user, db),
        lambda: archives.restore_archive_record("missing", SimpleNamespace(reason="x"), request, '"1"', user, db),
        lambda: archives.list_archive_attachments("missing", request, user, db),
        lambda: archives.archive_history("missing", request, user, db),
        lambda: archives.link_archive_record("missing", SimpleNamespace(), request, user, db),
    ]
    for call in calls:
        with pytest.raises(ProblemException) as error:
            call()
        assert error.value.code == "ARCHIVE_RECORD_NOT_FOUND"


def test_record_list_filters_fts_and_search_visibility(monkeypatch) -> None:
    category = _category()
    record = SimpleNamespace(id="r1", category_id="category-1")
    user = _user()
    request = _request()
    monkeypatch.setattr(archives, "_request_device_id", lambda *_a: None)
    monkeypatch.setattr(archives, "can_view_category", lambda *_a, **_k: True)
    monkeypatch.setattr(archives, "_record_out", lambda _db, item, *_a, **_k: {"id": item.id})
    monkeypatch.setattr(archives, "fts_search", lambda *_a, **_k: ["r1"])
    db = Db(rows=[[category], [record]])
    result = archives.list_archive_records(
        request, 2026, "category-1", "关键词", "张三", "党字", ArchiveRecordStatus.ACTIVE,
        100, 0, user, db,
    )
    assert result == [{"id": "r1"}]

    monkeypatch.setattr(archives, "fts_search", lambda *_a, **_k: [])
    db = Db(rows=[[category], [record]])
    result = archives.list_archive_records(request, None, None, "关键词", "", "", None, 100, 0, user, db)
    assert result == [{"id": "r1"}]
    db = Db(rows=[[category]])
    assert archives.list_archive_records(request, None, "not-visible", "", "", "", None, 100, 0, user, db) == []

    monkeypatch.setattr(archives, "category_for_record", lambda *_a: category)
    monkeypatch.setattr(archives, "fts_search", lambda *_a, **_k: ["r1"])
    db = Db(rows=[[record]])
    assert archives.search_archives(request, "关键词", 100, user, db) == [{"id": "r1"}]
    monkeypatch.setattr(archives, "can_view_category", lambda *_a, **_k: False)
    monkeypatch.setattr(archives, "fts_search", lambda *_a, **_k: [])
    db = Db(rows=[[record]])
    assert archives.search_archives(request, "关键词", 100, user, db) == []
