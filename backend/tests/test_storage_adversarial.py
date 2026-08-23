"""附件恢复、路径与并发门禁的对抗性分支测试。"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, UploadFile

from app import storage
from app.enums import MaterialStage, Sensitivity, TaskStatus, UserRole
from app.problems import ProblemException


def _code(callable_, expected: str) -> None:
    with pytest.raises(ProblemException) as raised:
        callable_()
    assert raised.value.code == expected


class _FakeDb:
    def __init__(self, *, scalars: list[object] | None = None, scalar_values: list[object] | None = None) -> None:
        self.scalar_rows = iter(scalars or [])
        self.scalar_values = iter(scalar_values or [])
        self.deleted: list[object] = []
        self.added: list[object] = []
        self.commits = 0
        self.refresh_hook = None
        self.blobs: dict[str, object] = {}

    def scalars(self, statement):
        rows = next(self.scalar_rows)
        return SimpleNamespace(all=lambda: rows)

    def scalar(self, statement):
        return next(self.scalar_values, None)

    def refresh(self, value) -> None:
        if self.refresh_hook:
            self.refresh_hook(value)

    def delete(self, value) -> None:
        self.deleted.append(value)

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def get(self, model, key):
        return self.blobs.get(key)


def _objects(*, status: TaskStatus = TaskStatus.IN_PROGRESS, role: UserRole = UserRole.ADMIN):
    actor = SimpleNamespace(id="user-1", role=role)
    task = SimpleNamespace(
        id="task-1",
        title="测试事项",
        status=status,
        sensitivity=Sensitivity.NORMAL,
        allow_sensitive_content=True,
        version=3,
        updated_by=None,
    )
    material = SimpleNamespace(id="material-1", name="会议材料", version=1)
    version = SimpleNamespace(
        id="version-1",
        material_item_id=material.id,
        blob_sha256="abc",
        uploaded_by=actor.id,
        display_name="记录.pdf",
        is_final=False,
        deleted_at=None,
        deleted_by=None,
        delete_reason="",
        purge_after=None,
        deleted_was_final=False,
        version_no=1,
    )
    return actor, task, material, version


def _upload(name: str, content: bytes, content_type: str = "application/pdf") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=name,
        headers=Headers({"content-type": content_type}),
    )


def test_storage_validates_display_names_upload_ids_and_blob_paths(monkeypatch, tmp_path: Path) -> None:
    assert storage.safe_original_name(None) == "未命名文件"
    assert storage.safe_original_name("../目录/材料.pdf") == "材料.pdf"
    assert len(storage.safe_original_name("甲" * 300)) == 255
    assert storage.normalize_client_upload_id("  upload-12345678  ") == "upload-12345678"
    assert storage.normalize_client_upload_id("  ") is None
    _code(lambda: storage.normalize_client_upload_id("bad"), "CLIENT_UPLOAD_ID_INVALID")
    storage._assert_business_filename_allowed("材料.PDF")
    _code(lambda: storage._assert_business_filename_allowed("危险.CmD"), "BUSINESS_FILE_TYPE_BLOCKED")

    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(attachments_dir=root),
    )
    assert storage.resolve_blob_path("ab/file").is_relative_to(root)
    assert storage.resolve_blob_path("") == root.resolve()
    _code(lambda: storage.resolve_blob_path("../escape"), "INVALID_FILE_PATH")


@pytest.mark.asyncio
async def test_save_attachment_rejects_replays_permissions_capacity_and_races(monkeypatch, tmp_path: Path) -> None:
    actor, task, material, _version = _objects()
    existing = SimpleNamespace(material_item_id=material.id)
    replay_db = _FakeDb(scalar_values=[existing])
    assert await storage.save_attachment(
        replay_db,
        task,
        material,
        _upload("材料.pdf", b"same"),
        MaterialStage.DRAFT,
        False,
        actor,
        client_upload_id="upload-replay-0001",
    ) is existing

    conflict = SimpleNamespace(material_item_id="other")
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            _FakeDb(scalar_values=[conflict]),
            task,
            material,
            _upload("材料.pdf", b"same"),
            MaterialStage.DRAFT,
            False,
            actor,
            client_upload_id="upload-replay-0002",
        )
    assert raised.value.code == "UPLOAD_ID_CONFLICT"

    monkeypatch.setattr(storage, "can_edit_task", lambda *args: False)
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            _FakeDb(), task, material, _upload("材料.pdf", b"x"), MaterialStage.DRAFT, False, actor
        )
    assert raised.value.code == "MATERIAL_EDIT_DENIED"
    monkeypatch.setattr(storage, "can_edit_task", lambda *args: True)

    task.sensitivity = Sensitivity.RESTRICTED
    task.allow_sensitive_content = False
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            _FakeDb(), task, material, _upload("材料.pdf", b"x"), MaterialStage.DRAFT, False, actor
        )
    assert raised.value.code == "RESTRICTED_ATTACHMENT_DISABLED"
    task.sensitivity = Sensitivity.NORMAL
    task.allow_sensitive_content = True
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            _FakeDb(),
            task,
            material,
            _upload("材料.pdf", b"x"),
            MaterialStage.DRAFT,
            False,
            actor,
            expected_task_version=2,
        )
    assert raised.value.code == "VERSION_CONFLICT"

    root = tmp_path / "attachments"
    root.mkdir()
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(attachments_dir=root, max_upload_mb=0),
    )
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            _FakeDb(scalar_values=[None]),
            task,
            material,
            _upload("超限.pdf", b"x"),
            MaterialStage.DRAFT,
            False,
            actor,
        )
    assert raised.value.code == "FILE_TOO_LARGE"

    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(attachments_dir=root, max_upload_mb=1),
    )
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            _FakeDb(scalar_values=[None]),
            task,
            material,
            _upload("空文件.pdf", b""),
            MaterialStage.DRAFT,
            False,
            actor,
        )
    assert raised.value.code == "EMPTY_FILE"

    payload = b"already-stored"
    sha256 = hashlib.sha256(payload).hexdigest()
    stored = root / sha256[:2] / sha256
    stored.parent.mkdir(parents=True)
    stored.write_bytes(payload)
    raced = _FakeDb(scalar_values=[None])
    raced.refresh_hook = lambda value: setattr(task, "status", TaskStatus.ARCHIVED) if value is task else None
    with pytest.raises(ProblemException) as raised:
        await storage.save_attachment(
            raced,
            task,
            material,
            _upload("已存在.pdf", payload),
            MaterialStage.DRAFT,
            False,
            actor,
        )
    assert raised.value.code == "TASK_ARCHIVED_IMMUTABLE"
    task.status = TaskStatus.IN_PROGRESS


def test_delete_attachment_guard_matrix_and_atomic_recheck(monkeypatch) -> None:
    monkeypatch.setattr(storage, "write_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "record_system_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "emit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(deleted_attachment_retention_days=30),
    )
    actor, task, material, version = _objects(role=UserRole.STAFF)
    _code(
        lambda: storage.delete_attachment_version(
            _FakeDb(), task, material, version, actor, "x", expected_task_version=3
        ),
        "ATTACHMENT_DELETE_REASON_REQUIRED",
    )

    task.status = TaskStatus.COMPLETED
    _code(
        lambda: storage.delete_attachment_version(
            _FakeDb(), task, material, version, actor, "内容错误", expected_task_version=3
        ),
        "TASK_REOPEN_REQUIRED",
    )
    task.status = TaskStatus.IN_PROGRESS
    monkeypatch.setattr(storage, "can_manage_task", lambda *args: False)
    monkeypatch.setattr(storage, "can_edit_task", lambda *args: False)
    _code(
        lambda: storage.delete_attachment_version(
            _FakeDb(), task, material, version, actor, "内容错误", expected_task_version=3
        ),
        "ATTACHMENT_DELETE_DENIED",
    )

    monkeypatch.setattr(storage, "can_manage_task", lambda *args: True)
    _code(
        lambda: storage.delete_attachment_version(
            _FakeDb(), task, material, version, actor, "内容错误", expected_task_version=2
        ),
        "VERSION_CONFLICT",
    )
    version.deleted_at = datetime.now(timezone.utc)
    assert storage.delete_attachment_version(
        _FakeDb(), task, material, version, actor, "内容错误", expected_task_version=3
    ) is version
    version.deleted_at = None

    changing_db = _FakeDb()
    changing_db.refresh_hook = lambda value: setattr(task, "version", 4) if value is task else None
    _code(
        lambda: storage.delete_attachment_version(
            changing_db, task, material, version, actor, "内容错误", expected_task_version=3
        ),
        "VERSION_CONFLICT",
    )
    task.version = 3
    raced_db = _FakeDb()
    raced_db.refresh_hook = (
        lambda value: setattr(version, "deleted_at", datetime.now(timezone.utc))
        if value is version
        else None
    )
    assert storage.delete_attachment_version(
        raced_db, task, material, version, actor, "内容错误", expected_task_version=3
    ) is version

    version.deleted_at = None
    version.display_name = ""
    success_db = _FakeDb()
    saved = storage.delete_attachment_version(
        success_db, task, material, version, actor, " 内容错误 ", expected_task_version=3
    )
    assert saved.deleted_at is not None and saved.purge_after > saved.deleted_at
    assert success_db.commits == 1


def test_restore_attachment_guard_matrix_and_final_resolution(monkeypatch) -> None:
    monkeypatch.setattr(storage, "write_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "record_system_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "emit_event", lambda *args, **kwargs: None)
    actor, task, material, version = _objects(role=UserRole.STAFF)
    deleted_at = datetime.now(timezone.utc)
    version.deleted_at = deleted_at
    monkeypatch.setattr(storage, "can_manage_task", lambda *args: False)
    monkeypatch.setattr(storage, "can_edit_task", lambda *args: False)
    _code(
        lambda: storage.restore_attachment_version(
            _FakeDb(), task, material, version, actor, expected_task_version=3
        ),
        "ATTACHMENT_RESTORE_DENIED",
    )
    monkeypatch.setattr(storage, "can_manage_task", lambda *args: True)
    version.deleted_at = None
    assert storage.restore_attachment_version(
        _FakeDb(), task, material, version, actor, expected_task_version=3
    ) is version
    version.deleted_at = deleted_at
    _code(
        lambda: storage.restore_attachment_version(
            _FakeDb(), task, material, version, actor, expected_task_version=2
        ),
        "VERSION_CONFLICT",
    )

    changing_db = _FakeDb()
    changing_db.refresh_hook = lambda value: setattr(task, "version", 4) if value is task else None
    _code(
        lambda: storage.restore_attachment_version(
            changing_db, task, material, version, actor, expected_task_version=3
        ),
        "VERSION_CONFLICT",
    )
    task.version = 3
    raced_db = _FakeDb()
    raced_db.refresh_hook = lambda value: setattr(version, "deleted_at", None) if value is version else None
    assert storage.restore_attachment_version(
        raced_db, task, material, version, actor, expected_task_version=3
    ) is version

    version.deleted_at = deleted_at
    version.deleted_was_final = True
    success_db = _FakeDb(scalar_values=[None])
    restored = storage.restore_attachment_version(
        success_db, task, material, version, actor, expected_task_version=3
    )
    assert restored.deleted_at is None and restored.is_final is True

    version.deleted_at = deleted_at
    version.deleted_was_final = True
    conflict_db = _FakeDb(scalar_values=["other-final"])
    restored = storage.restore_attachment_version(
        conflict_db, task, material, version, actor, expected_task_version=task.version
    )
    assert restored.is_final is False


def test_purge_deleted_attachments_preserves_references_and_retries_locked_files(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    class FakePath:
        def __init__(self, name: str) -> None:
            self.name = name

        def unlink(self, *, missing_ok: bool) -> None:
            if self.name == "locked":
                raise OSError("文件占用")

    monkeypatch.setattr(storage, "resolve_blob_path", lambda name: FakePath(name))

    referenced = _FakeDb(
        scalars=[[SimpleNamespace(blob_sha256="referenced")], []],
        scalar_values=["task-ref", None],
    )
    assert storage.purge_expired_deleted_attachments(referenced, now=now)["blobs"] == 0

    missing = _FakeDb(
        scalars=[[SimpleNamespace(blob_sha256="missing")], []],
        scalar_values=[None, None],
    )
    assert storage.purge_expired_deleted_attachments(missing, now=now)["blobs"] == 0

    locked = _FakeDb(
        scalars=[[], [SimpleNamespace(blob_sha256="locked")]],
        scalar_values=[None, None],
    )
    locked.blobs["locked"] = SimpleNamespace(relative_path="locked")
    assert storage.purge_expired_deleted_attachments(locked, now=now)["blobs"] == 0

    purged = _FakeDb(
        scalars=[[], [SimpleNamespace(blob_sha256="purged")]],
        scalar_values=[None, None],
    )
    purged.blobs["purged"] = SimpleNamespace(relative_path="purged")
    result = storage.purge_expired_deleted_attachments(purged, now=now)
    assert result == {"task_versions": 0, "archive_attachments": 1, "blobs": 1}
    assert len(purged.deleted) == 2

    empty = _FakeDb(scalars=[[], []])
    assert storage.purge_expired_deleted_attachments(empty, now=now)["blobs"] == 0


def test_rollback_attachment_rejects_unsafe_or_stale_targets(monkeypatch, tmp_path: Path) -> None:
    actor, task, material, target = _objects()
    monkeypatch.setattr(storage, "can_manage_task", lambda *args: True)
    _code(
        lambda: storage.rollback_attachment_version(
            _FakeDb(), task, material, target, actor, "x", expected_task_version=3
        ),
        "ROLLBACK_REASON_REQUIRED",
    )
    monkeypatch.setattr(storage, "can_manage_task", lambda *args: False)
    _code(
        lambda: storage.rollback_attachment_version(
            _FakeDb(), task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "MATERIAL_ROLLBACK_DENIED",
    )
    monkeypatch.setattr(storage, "can_manage_task", lambda *args: True)
    _code(
        lambda: storage.rollback_attachment_version(
            _FakeDb(), task, material, target, actor, "历史更正", expected_task_version=2
        ),
        "VERSION_CONFLICT",
    )
    task.status = TaskStatus.ARCHIVED
    _code(
        lambda: storage.rollback_attachment_version(
            _FakeDb(), task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "TASK_ARCHIVED_IMMUTABLE",
    )
    task.status = TaskStatus.IN_PROGRESS
    target.material_item_id = "other"
    _code(
        lambda: storage.rollback_attachment_version(
            _FakeDb(), task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "ATTACHMENT_NOT_FOUND",
    )
    target.material_item_id = material.id
    _code(
        lambda: storage.rollback_attachment_version(
            _FakeDb(), task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "ATTACHMENT_MISSING",
    )
    missing_db = _FakeDb()
    missing_db.blobs["abc"] = SimpleNamespace(relative_path="missing")
    monkeypatch.setattr(storage, "resolve_blob_path", lambda name: tmp_path / name)
    _code(
        lambda: storage.rollback_attachment_version(
            missing_db, task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "ATTACHMENT_MISSING",
    )

    source = tmp_path / "source"
    source.write_bytes(b"history")
    blob = SimpleNamespace(relative_path="source", original_name="历史.pdf")
    monkeypatch.setattr(storage, "write_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "record_system_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "emit_event", lambda *args, **kwargs: None)

    changed = _FakeDb()
    changed.blobs["abc"] = blob
    changed.refresh_hook = lambda value: setattr(task, "version", 4) if value is task else None
    _code(
        lambda: storage.rollback_attachment_version(
            changed, task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "VERSION_CONFLICT",
    )
    task.version = 3
    archived = _FakeDb()
    archived.blobs["abc"] = blob
    archived.refresh_hook = (
        lambda value: setattr(task, "status", TaskStatus.ARCHIVED) if value is task else None
    )
    _code(
        lambda: storage.rollback_attachment_version(
            archived, task, material, target, actor, "历史更正", expected_task_version=3
        ),
        "TASK_ARCHIVED_IMMUTABLE",
    )
    task.status = TaskStatus.IN_PROGRESS

    success = _FakeDb(scalar_values=[None, 0])
    success.blobs["abc"] = blob
    restored = storage.rollback_attachment_version(
        success, task, material, target, actor, "历史更正", expected_task_version=3
    )
    assert restored.is_final is True and restored.version_no == 1
