"""rc.3 冻结前高风险恢复、数据库维护与权限分支回归。"""

from __future__ import annotations

import threading
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import backups, database, main, schemas, task_service
from app.enums import TaskStatus, UserRole
from app.problems import ProblemException
from app.routers import productivity
from app.routers import workspace as workspace_router
from app.models import WorkspaceRoot
from tests.test_rc3_remaining_branch_gate import _Db, _request, _task


class _Rows:
    def __init__(self, rows=()) -> None:
        self.rows = list(rows)

    def fetchall(self):
        return list(self.rows)


class _ConnectionContext:
    def __init__(self, value) -> None:
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


def _runtime() -> database.DatabaseRuntime:
    runtime = object.__new__(database.DatabaseRuntime)
    runtime.write_lock = threading.RLock()
    runtime._activity_condition = threading.Condition()
    runtime._active_sessions = 0
    runtime._maintenance_active = False
    runtime._maintenance_owner = None
    return runtime


def test_database_exclusive_maintenance_rejects_nested_and_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    runtime._maintenance_active = True
    with pytest.raises(ProblemException) as nested:
        with runtime.exclusive_maintenance():
            pass
    assert nested.value.code == "DATABASE_MAINTENANCE_ACTIVE"

    runtime._maintenance_active = False
    runtime._active_sessions = 1
    ticks = iter((10.0, 10.2))
    monkeypatch.setattr(database.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ProblemException) as busy:
        with runtime.exclusive_maintenance(timeout_seconds=0.1):
            pass
    assert busy.value.code == "DATABASE_MAINTENANCE_BUSY"
    assert runtime._maintenance_active is False


def test_database_capability_fail_closed_for_old_sqlite_and_missing_fts5() -> None:
    class Connection:
        def __init__(self, version: str, options: list[tuple[str]]) -> None:
            self.version = version
            self.options = options

        def execute(self, _statement):
            return SimpleNamespace(scalar_one=lambda: self.version)

        def exec_driver_sql(self, _statement):
            return _Rows(self.options)

    runtime = _runtime()
    runtime.settings = SimpleNamespace(sqlite_min_version="3.45.0", strict_sqlite=True)
    runtime.engine = SimpleNamespace(
        connect=lambda: _ConnectionContext(Connection("3.40.0", [("ENABLE_FTS5",)]))
    )
    with pytest.raises(RuntimeError, match="低于生产最低版本"):
        runtime.validate_capabilities()

    runtime.settings.strict_sqlite = False
    runtime.engine = SimpleNamespace(
        connect=lambda: _ConnectionContext(Connection("3.45.0", []))
    )
    with pytest.raises(RuntimeError, match="未启用 FTS5"):
        runtime.validate_capabilities()


def test_database_legacy_schema_and_frozen_alembic_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    runtime.engine = object()
    calls: list[str] = []
    monkeypatch.setattr(
        database,
        "inspect",
        lambda _engine: SimpleNamespace(get_table_names=lambda: ["tasks"]),
    )
    runtime._apply_legacy_baseline = lambda: calls.append("legacy")
    runtime._stamp_revision = lambda revision: calls.append(f"stamp:{revision}")
    runtime._upgrade_to_head = lambda: calls.append("upgrade")
    runtime._record_schema_head = lambda: calls.append("record")
    runtime._create_search_schema = lambda: calls.append("search")
    runtime.create_schema()
    assert calls == ["legacy", "stamp:0010", "upgrade", "record", "search"]

    monkeypatch.setattr(database.sys, "frozen", True, raising=False)
    monkeypatch.setattr(database.sys, "_MEIPASS", str(tmp_path), raising=False)
    config = runtime._alembic_config()
    assert Path(config.config_file_name) == tmp_path / "alembic.ini"
    assert Path(config.get_main_option("script_location")) == tmp_path / "alembic"


def test_database_rejects_missing_migration_head_and_applies_legacy_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    runtime._alembic_config = lambda: object()
    from alembic.script import ScriptDirectory

    monkeypatch.setattr(
        ScriptDirectory,
        "from_config",
        staticmethod(lambda _config: SimpleNamespace(get_current_head=lambda: None)),
    )
    with pytest.raises(RuntimeError, match="迁移链顶端"):
        runtime._record_schema_head()

    class Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def exec_driver_sql(self, statement: str, _params=None):
            self.statements.append(statement)
            if statement.startswith("PRAGMA table_info"):
                table = statement.split('"')[1]
                if table == "users":
                    return _Rows([])
                if table == "tasks":
                    return _Rows([(0, "id")])
                return _Rows([])
            return _Rows([])

    connection = Connection()
    runtime.engine = SimpleNamespace(begin=lambda: _ConnectionContext(connection))
    runtime._apply_legacy_baseline()
    assert any(statement.startswith('ALTER TABLE "tasks"') for statement in connection.statements)
    assert not any(statement.startswith('ALTER TABLE "users"') for statement in connection.statements)
    assert any("ix_tasks_category" in statement for statement in connection.statements)


def _backup_settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return SimpleNamespace(
        data_dir=data_dir,
        database_path=data_dir / "partyops.db",
        attachments_dir=data_dir / "attachments",
        archives_dir=data_dir / "archives",
        backups_dir=data_dir / "backups",
        app_version="1.4.3-rc.3",
        backup_max_members=10_000,
        backup_restore_max_gb=10,
    )


def _patch_restore_runtime(monkeypatch: pytest.MonkeyPatch, settings) -> None:
    settings.backups_dir.mkdir(exist_ok=True)
    session = SimpleNamespace(get=lambda *_a: None, close=lambda: None)
    monkeypatch.setattr(backups, "get_settings", lambda: settings)
    monkeypatch.setattr(
        backups,
        "verify_backup",
        lambda _path: {"files": [{"path": "database/partyops.db", "size": 2}]},
    )
    monkeypatch.setattr(backups, "create_backup", lambda *_a, **_k: None)
    monkeypatch.setattr(backups.db_runtime, "session_factory", lambda: session)
    monkeypatch.setattr(backups.db_runtime, "dispose", lambda: None)
    monkeypatch.setattr(backups.db_runtime, "rebuild", lambda: None)
    monkeypatch.setattr(backups.db_runtime, "create_schema", lambda: None)
    monkeypatch.setattr(backups.db_runtime, "validate_capabilities", lambda: None)


def test_restore_backup_succeeds_without_previous_data_or_optional_trees(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _backup_settings(tmp_path)
    _patch_restore_runtime(monkeypatch, settings)
    archive_path = tmp_path / "fresh.partyops-backup"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("database/partyops.db", b"db")

    backups.restore_backup(archive_path)
    assert settings.database_path.read_bytes() == b"db"
    assert settings.attachments_dir.is_dir()
    assert settings.archives_dir.is_dir()


def test_restore_backup_replaces_and_cleans_existing_data_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _backup_settings(tmp_path)
    _patch_restore_runtime(monkeypatch, settings)
    settings.database_path.write_bytes(b"old")
    settings.attachments_dir.mkdir()
    settings.archives_dir.mkdir()
    (settings.attachments_dir / "old.txt").write_text("old", encoding="utf-8")
    (settings.archives_dir / "old.txt").write_text("old", encoding="utf-8")
    previous_db = settings.database_path.with_suffix(".db.restore-previous")
    previous_db.write_bytes(b"stale")
    stale_attachments = settings.data_dir / "attachments.restore-previous"
    stale_archives = settings.data_dir / "archives.restore-previous"
    stale_attachments.mkdir()
    stale_archives.mkdir()

    archive_path = tmp_path / "replace.partyops-backup"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("database/partyops.db", b"new")
        archive.writestr("attachments/new.txt", b"new attachment")
        archive.writestr("archives/new.txt", b"new archive")

    backups.restore_backup(archive_path)
    assert settings.database_path.read_bytes() == b"new"
    assert (settings.attachments_dir / "new.txt").is_file()
    assert (settings.archives_dir / "new.txt").is_file()
    assert not previous_db.exists()
    assert not stale_attachments.exists()
    assert not stale_archives.exists()


def test_backup_failure_removes_partial_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _backup_settings(tmp_path)
    settings.backups_dir.mkdir()
    settings.attachments_dir.mkdir()
    settings.archives_dir.mkdir()
    settings.database_path.write_bytes(b"db")
    monkeypatch.setattr(backups, "get_settings", lambda: settings)
    monkeypatch.setattr(backups, "_database_snapshot", lambda source, target: target.write_bytes(source.read_bytes()))

    def fail_after_open(path, *_args, **_kwargs):
        Path(path).write_bytes(b"partial")
        raise RuntimeError("压缩器异常")

    monkeypatch.setattr(backups.zipfile, "ZipFile", fail_after_open)
    db = _Db()
    with pytest.raises(RuntimeError, match="压缩器异常"):
        backups.create_backup(db, None)
    assert list(settings.backups_dir.iterdir()) == []


def test_backup_guards_missing_output_missing_database_and_escape_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _backup_settings(tmp_path)
    settings.backups_dir.mkdir()
    settings.attachments_dir.mkdir()
    settings.archives_dir.mkdir()
    settings.database_path.write_bytes(b"db")
    monkeypatch.setattr(backups, "get_settings", lambda: settings)
    monkeypatch.setattr(
        backups,
        "_database_snapshot",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("快照失败")),
    )
    with pytest.raises(RuntimeError, match="快照失败"):
        backups.create_backup(_Db(), None)
    assert list(settings.backups_dir.iterdir()) == []

    member = SimpleNamespace(is_dir=lambda: False)
    monkeypatch.setattr(backups, "_validated_zip_infos", lambda _archive: [member])
    monkeypatch.setattr(backups, "_portable_zip_name", lambda _member: ("../escape", "escape"))
    with pytest.raises(ProblemException) as escaped:
        backups._safe_zip_members(object(), tmp_path / "destination")
    assert escaped.value.code == "BACKUP_PATH_INVALID"

    _patch_restore_runtime(monkeypatch, settings)
    archive_path = tmp_path / "missing-db.partyops-backup"
    with zipfile.ZipFile(archive_path, "w"):
        pass
    monkeypatch.setattr(backups, "_validated_zip_infos", lambda archive: archive.infolist())
    monkeypatch.setattr(
        backups,
        "_portable_zip_name",
        lambda member: (member.filename, member.filename.casefold()),
    )
    with pytest.raises(ProblemException) as missing:
        backups.restore_backup(archive_path)
    assert missing.value.code == "BACKUP_DATABASE_MISSING"


def test_trace_id_with_noncanonical_uuid_form_is_replaced() -> None:
    decorated = "{f81d4fae-7dec-11d0-a765-00a0c91e6bf6}"
    assert main.normalize_trace_id(decorated) != decorated


def test_workspace_remaining_success_and_safety_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.ADMIN)
    root = SimpleNamespace(
        id="root",
        name="共享目录",
        source=SimpleNamespace(value="device"),
        version=1,
        semantic_content_enabled=True,
        share_scope="team",
        enabled=True,
        approval_note="",
        scan_status="ready",
    )
    monkeypatch.setattr(workspace_router, "current_device_id", lambda *_a: None)
    monkeypatch.setattr(workspace_router, "require_root_manager", lambda *_a: root)
    monkeypatch.setattr(workspace_router, "root_to_out", lambda *_a: root)
    monkeypatch.setattr(workspace_router, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(workspace_router, "emit_event", lambda *_a, **_k: None)
    payload = SimpleNamespace(share_scope="selected", semantic_content_enabled=False)
    assert workspace_router.patch_workspace_root_sharing(
        "root", payload, _request(), '"1"', user, _Db(scalars_values=[[]])
    ) is root

    item = SimpleNamespace(id="file", in_scope=True, status="ready")
    checkpoint = SimpleNamespace(id="checkpoint")
    root.version = 3
    db = _Db(
        objects={(WorkspaceRoot, "root"): root},
        scalar_values=[None],
        scalars_values=[[item], [checkpoint]],
    )
    result = workspace_router.delete_workspace_root("root", _request(), '"3"', user, db)
    assert result["deleted"] is True
    assert db.deleted == [checkpoint]

    monkeypatch.setattr(
        workspace_router,
        "workspace_root_permissions",
        lambda *_a: {"browse": True},
    )
    monkeypatch.setattr(workspace_router, "workspace_file_out", lambda _db, value, *_a: value)
    db = _Db(objects={(WorkspaceRoot, "root"): root}, scalars_values=[[item]])
    assert workspace_router.list_workspace_files(
        _request(), "root", None, True, 10, user, db
    ) == [item]


def test_workspace_existing_link_and_valid_unlink_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.ADMIN)
    item = SimpleNamespace(id="file", name="材料.pdf", version=1)
    existing = SimpleNamespace(id="link", file_id="file", entity_type="task")
    monkeypatch.setattr(workspace_router, "current_device_id", lambda *_a: None)
    monkeypatch.setattr(workspace_router, "get_file", lambda *_a: (item, object()))
    monkeypatch.setattr(workspace_router, "validate_link_target", lambda *_a: None)
    monkeypatch.setattr(workspace_router, "workspace_file_out", lambda _db, value, *_a: value)
    monkeypatch.setattr(workspace_router, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(workspace_router, "emit_event", lambda *_a, **_k: None)
    monkeypatch.setattr(workspace_router, "record_system_entry", lambda *_a, **_k: None)
    payload = SimpleNamespace(
        entity_type="task",
        entity_id="task",
        relation="source",
        model_dump=lambda: {
            "entity_type": "task",
            "entity_id": "task",
            "relation": "source",
        },
    )
    assert workspace_router.link_workspace_file(
        "file", payload, _request(), '"1"', user, _Db(scalar_values=[existing])
    ) is item
    assert item.version == 1

    db = _Db(objects={"link": existing})
    assert workspace_router.unlink_workspace_file(
        "file", "link", _request(), '"1"', user, db
    ) is item
    assert db.deleted == [existing]


def test_productivity_batch_permission_due_date_and_duplicate_shortcuts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id="user", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(productivity, "can_edit_task", lambda *_a: True)
    monkeypatch.setattr(productivity, "can_manage_task", lambda *_a: False)
    with pytest.raises(ProblemException) as denied:
        productivity.batch_tasks(
            schemas.TaskBatchPatch(task_ids=[task.id], owner_id="other"),
            _request(),
            actor,
            _Db(objects={task.id: task}),
        )
    assert denied.value.code == "BATCH_MANAGE_DENIED"

    monkeypatch.setattr(productivity, "write_audit", lambda *_a, **_k: None)
    due = datetime(2026, 8, 20, tzinfo=timezone.utc)
    result = productivity.batch_tasks(
        schemas.TaskBatchPatch(task_ids=[task.id], internal_due_at=due),
        _request(),
        actor,
        _Db(objects={task.id: task}),
    )
    assert result == {"updated": [task.id], "count": 1}
    assert task.internal_due_at == due

    files = [
        SimpleNamespace(id="hash-one", sha256="single", extracted_text="", ocr_text=""),
        SimpleNamespace(id="short", sha256=None, extracted_text="短文本", ocr_text=""),
        SimpleNamespace(id="long", sha256=None, extracted_text="甲乙丙丁戊己庚辛壬癸子丑寅卯", ocr_text=""),
        SimpleNamespace(id="right-short", sha256=None, extracted_text="也很短", ocr_text=""),
    ]
    db = _Db(scalars_values=[files])
    assert productivity.scan_duplicates(actor, db) == []


def test_task_update_clears_reviewer_participant_without_stale_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task()
    task.reviewer_id = None
    actor = SimpleNamespace(id="owner", role=UserRole.ADMIN)
    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "can_manage_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "record_system_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "emit_event", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "task_to_out", lambda *_a, **_k: task)
    payload = schemas.TaskUpdate(reviewer_id=None)
    db = _Db(execute_values=[SimpleNamespace(rowcount=1)])
    result = task_service.update_task(db, task, payload, task.version, actor, "")
    assert result.reviewer_id is None
