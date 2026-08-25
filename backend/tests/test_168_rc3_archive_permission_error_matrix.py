"""档案授权、附件回收站和关联操作的权限与并发错误矩阵。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enums import ArchiveAttachmentStatus, ArchiveRecordStatus, UserRole
from app.models import ArchiveAccessGrant, ArchiveLink, ArchiveRecord
from app.problems import ProblemException
from app.routers import archives


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
        self.deleted = []

    def get(self, model, key):
        return self.gets.get((model, key), self.gets.get(key))

    def scalars(self, _statement):
        return _Rows(self.scalars_queue.pop(0) if self.scalars_queue else [])

    def scalar(self, _statement):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        return None

    def refresh(self, _value):
        return None


def _code(code: str, call) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == code


def _context(monkeypatch):
    category = SimpleNamespace(id="category-1", version=1)
    request = SimpleNamespace(client=None)
    admin = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    staff = SimpleNamespace(id="staff", role=UserRole.STAFF)
    monkeypatch.setattr(archives, "_category", lambda *_args, **_kwargs: category)
    monkeypatch.setattr(archives, "_request_device_id", lambda *_args: None)
    monkeypatch.setattr(
        archives,
        "_record_out",
        lambda _db, record, *_args, **_kwargs: {"id": record.id, "version": record.version},
    )
    monkeypatch.setattr(
        archives,
        "_attachment_out",
        lambda _db, item: {"id": item.id, "deleted": item.deleted_at is not None},
    )
    return category, request, admin, staff


def test_category_access_and_grant_missing_branches(monkeypatch) -> None:
    category, request, admin, _staff = _context(monkeypatch)
    payload = SimpleNamespace(access_mode=SimpleNamespace(value="all_users"), allow_device_access=True)
    _code(
        "VERSION_CONFLICT",
        lambda: archives.patch_archive_category_access(
            category.id, payload, request, "2", admin, _Db()
        ),
    )
    assert archives.list_archive_category_grants(
        category.id, True, admin, _Db(scalars=[[]])
    ) == []
    _code(
        "ARCHIVE_GRANT_NOT_FOUND",
        lambda: archives.patch_archive_category_grant(
            category.id,
            "missing",
            SimpleNamespace(),
            request,
            "1",
            admin,
            _Db(),
        ),
    )
    foreign = SimpleNamespace(category_id="other", version=1)
    _code(
        "ARCHIVE_GRANT_NOT_FOUND",
        lambda: archives.patch_archive_category_grant(
            category.id,
            "foreign",
            SimpleNamespace(),
            request,
            "1",
            admin,
            _Db(gets={(ArchiveAccessGrant, "foreign"): foreign}),
        ),
    )


def test_record_view_patch_void_restore_permission_and_version(monkeypatch) -> None:
    category, request, admin, staff = _context(monkeypatch)
    record = SimpleNamespace(
        id="record-1",
        category_id=category.id,
        version=1,
        status=ArchiveRecordStatus.ACTIVE,
    )
    db = _Db(gets={(ArchiveRecord, record.id): record})
    monkeypatch.setattr(archives, "can_view_category", lambda *_args: False)
    _code(
        "ARCHIVE_ACCESS_DENIED",
        lambda: archives.get_archive_record(record.id, request, staff, db),
    )
    monkeypatch.setattr(archives, "can_contribute_category", lambda *_args: False)
    _code(
        "ARCHIVE_CONTRIBUTE_DENIED",
        lambda: archives.patch_archive_record(
            record.id, SimpleNamespace(), request, "1", staff, db
        ),
    )
    monkeypatch.setattr(archives, "can_contribute_category", lambda *_args: True)
    _code(
        "VERSION_CONFLICT",
        lambda: archives.patch_archive_record(
            record.id, SimpleNamespace(), request, "2", staff, db
        ),
    )
    action = SimpleNamespace(reason="并发测试")
    _code(
        "VERSION_CONFLICT",
        lambda: archives.void_archive_record(record.id, action, request, "2", admin, db),
    )
    _code(
        "VERSION_CONFLICT",
        lambda: archives.restore_archive_record(record.id, action, request, "2", admin, db),
    )


def test_upload_and_list_attachment_permission_replay_matrix(monkeypatch) -> None:
    category, request, _admin, staff = _context(monkeypatch)
    record = SimpleNamespace(id="record-1", category_id=category.id, version=1)
    monkeypatch.setattr(archives, "category_for_record", lambda *_args: category)
    monkeypatch.setattr(archives, "can_contribute_category", lambda *_args: False)
    background = SimpleNamespace(add_task=lambda *_args: None)
    file = SimpleNamespace()
    _code(
        "ARCHIVE_RECORD_NOT_FOUND",
        lambda: asyncio.run(
            archives.upload_archive_attachment(
                "missing", request, background, file, "", None, staff, _Db()
            )
        ),
    )
    denied_db = _Db(gets={(ArchiveRecord, record.id): record})
    _code(
        "ARCHIVE_UPLOAD_DENIED",
        lambda: asyncio.run(
            archives.upload_archive_attachment(
                record.id, request, background, file, "", None, staff, denied_db
            )
        ),
    )

    monkeypatch.setattr(archives, "can_contribute_category", lambda *_args: True)
    foreign = SimpleNamespace(id="attachment-foreign", record_id="other", deleted_at=None)
    conflict_db = _Db(
        gets={(ArchiveRecord, record.id): record}, scalar=[foreign]
    )
    _code(
        "UPLOAD_ID_CONFLICT",
        lambda: asyncio.run(
            archives.upload_archive_attachment(
                record.id,
                request,
                background,
                file,
                "",
                "archive-upload-0001",
                staff,
                conflict_db,
            )
        ),
    )
    existing = SimpleNamespace(id="attachment-own", record_id=record.id, deleted_at=None)
    existing_db = _Db(
        gets={(ArchiveRecord, record.id): record}, scalar=[existing]
    )
    assert asyncio.run(
        archives.upload_archive_attachment(
            record.id,
            request,
            background,
            file,
            "",
            "archive-upload-0002",
            staff,
            existing_db,
        )
    )["id"] == existing.id

    monkeypatch.setattr(archives, "can_view_category", lambda *_args: False)
    _code(
        "ARCHIVE_ACCESS_DENIED",
        lambda: archives.list_archive_attachments(record.id, request, staff, denied_db),
    )


def test_download_attachment_missing_record_permission_and_file(monkeypatch, tmp_path) -> None:
    category, request, _admin, staff = _context(monkeypatch)
    attachment = SimpleNamespace(
        id="attachment-1",
        record_id="record-1",
        deleted_at=None,
        status=ArchiveAttachmentStatus.INDEXED,
        display_name="材料.pdf",
    )
    blob = SimpleNamespace(sha256="abc", mime_type="application/pdf")
    missing_path = tmp_path / "missing.pdf"
    monkeypatch.setattr(
        archives,
        "archive_attachment_path",
        lambda *_args: (attachment, blob, missing_path),
    )
    _code(
        "ARCHIVE_RECORD_NOT_FOUND",
        lambda: archives.download_archive_attachment(
            attachment.id, request, staff, _Db()
        ),
    )
    record = SimpleNamespace(id="record-1", category_id=category.id)
    db = _Db(gets={(ArchiveRecord, record.id): record})
    monkeypatch.setattr(archives, "can_download_category", lambda *_args: False)
    _code(
        "ARCHIVE_DOWNLOAD_DENIED",
        lambda: archives.download_archive_attachment(attachment.id, request, staff, db),
    )
    monkeypatch.setattr(archives, "can_download_category", lambda *_args: True)
    _code(
        "ARCHIVE_ATTACHMENT_MISSING",
        lambda: archives.download_archive_attachment(attachment.id, request, staff, db),
    )


def test_attachment_recycle_restore_and_void_error_matrix(monkeypatch) -> None:
    category, request, admin, staff = _context(monkeypatch)
    record = SimpleNamespace(
        id="record-1",
        category_id=category.id,
        version=2,
        status=ArchiveRecordStatus.ACTIVE,
    )
    attachment = SimpleNamespace(
        id="attachment-1",
        record_id=record.id,
        uploaded_by="other",
        deleted_at=None,
        status=ArchiveAttachmentStatus.INDEXED,
        note="",
    )
    monkeypatch.setattr(
        archives,
        "archive_attachment_path",
        lambda *_args: (attachment, SimpleNamespace(), Path("unused")),
    )
    monkeypatch.setattr(archives, "_can_recycle_archive_attachment", lambda *_args: False)
    db = _Db(gets={(ArchiveRecord, record.id): record})
    _code(
        "ARCHIVE_RECORD_NOT_FOUND",
        lambda: archives.delete_archive_attachment(
            attachment.id, request, "记录错误", "2", staff, _Db()
        ),
    )
    _code(
        "ARCHIVE_RECORD_NOT_FOUND",
        lambda: archives.restore_archive_attachment(
            attachment.id, request, "2", staff, _Db()
        ),
    )
    _code(
        "ARCHIVE_ATTACHMENT_DELETE_DENIED",
        lambda: archives.delete_archive_attachment(
            attachment.id, request, "记录错误", "2", staff, db
        ),
    )
    _code(
        "ARCHIVE_ATTACHMENT_RESTORE_DENIED",
        lambda: archives.restore_archive_attachment(
            attachment.id, request, "2", staff, db
        ),
    )

    monkeypatch.setattr(archives, "_can_recycle_archive_attachment", lambda *_args: True)
    record.status = ArchiveRecordStatus.VOIDED
    _code(
        "ARCHIVE_RECORD_RESTORE_REQUIRED",
        lambda: archives.delete_archive_attachment(
            attachment.id, request, "记录错误", "2", staff, db
        ),
    )
    _code(
        "ARCHIVE_RECORD_RESTORE_REQUIRED",
        lambda: archives.restore_archive_attachment(
            attachment.id, request, "2", staff, db
        ),
    )
    record.status = ArchiveRecordStatus.ACTIVE
    _code(
        "VERSION_CONFLICT",
        lambda: archives.delete_archive_attachment(
            attachment.id, request, "记录错误", "1", staff, db
        ),
    )
    _code(
        "VERSION_CONFLICT",
        lambda: archives.restore_archive_attachment(
            attachment.id, request, "1", staff, db
        ),
    )

    attachment.deleted_at = SimpleNamespace()
    assert archives.delete_archive_attachment(
        attachment.id, request, "记录错误", "2", staff, db
    )["deleted"] is True
    attachment.deleted_at = None
    assert archives.restore_archive_attachment(
        attachment.id, request, "2", staff, db
    )["deleted"] is False

    missing_db = _Db()
    _code(
        "ARCHIVE_RECORD_NOT_FOUND",
        lambda: archives.void_archive_attachment(
            attachment.id,
            SimpleNamespace(reason="作废"),
            request,
            "2",
            admin,
            missing_db,
        ),
    )
    _code(
        "VERSION_CONFLICT",
        lambda: archives.void_archive_attachment(
            attachment.id,
            SimpleNamespace(reason="作废"),
            request,
            "1",
            admin,
            db,
        ),
    )
    attachment.status = ArchiveAttachmentStatus.VOIDED
    assert archives.void_archive_attachment(
        attachment.id,
        SimpleNamespace(reason="重复作废"),
        request,
        "2",
        admin,
        db,
    )["id"] == attachment.id


def test_link_and_unlink_permissions_versions_and_missing_link(monkeypatch) -> None:
    category, request, _admin, staff = _context(monkeypatch)
    record = SimpleNamespace(id="record-1", category_id=category.id, version=3)
    db = _Db(gets={(ArchiveRecord, record.id): record})
    _code(
        "ARCHIVE_RECORD_NOT_FOUND",
        lambda: archives.unlink_archive_record(
            "missing", "link-1", request, "1", staff, _Db()
        ),
    )
    monkeypatch.setattr(archives, "can_contribute_category", lambda *_args: False)
    _code(
        "ARCHIVE_CONTRIBUTE_DENIED",
        lambda: archives.link_archive_record(
            record.id,
            SimpleNamespace(entity_type="task", entity_id="task-1", relation="relates_to"),
            request,
            staff,
            db,
        ),
    )
    _code(
        "ARCHIVE_CONTRIBUTE_DENIED",
        lambda: archives.unlink_archive_record(
            record.id, "link-1", request, "3", staff, db
        ),
    )
    monkeypatch.setattr(archives, "can_contribute_category", lambda *_args: True)
    _code(
        "VERSION_CONFLICT",
        lambda: archives.unlink_archive_record(
            record.id, "link-1", request, "2", staff, db
        ),
    )
    _code(
        "ARCHIVE_LINK_NOT_FOUND",
        lambda: archives.unlink_archive_record(
            record.id, "missing", request, "3", staff, db
        ),
    )
    foreign = SimpleNamespace(record_id="other")
    _code(
        "ARCHIVE_LINK_NOT_FOUND",
        lambda: archives.unlink_archive_record(
            record.id,
            "foreign",
            request,
            "3",
            staff,
            _Db(
                gets={
                    (ArchiveRecord, record.id): record,
                    (ArchiveLink, "foreign"): foreign,
                }
            ),
        ),
    )
