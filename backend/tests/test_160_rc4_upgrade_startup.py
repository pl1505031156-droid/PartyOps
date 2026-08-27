"""1.4.5-rc.6 原位升级启动事务回归。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select, text

from alembic import command
from app import backups, upgrades, windows_host_status
from app import main as app_main
from app.config import Settings
from app.database import DatabaseRuntime, sqlite3_dbapi
from app.enums import UserRole
from app.models import BackupRun, UpgradeRecord, User
from app.startup_diagnostics import (
    DATA_DIR_FULL,
    DATABASE_IO_FAILED,
    DATABASE_SCHEMA_FAILED,
    SQLITE_RUNTIME_FAILED,
    UPGRADE_BACKUP_FAILED,
    classify_database_startup_error,
)
from app.windows_host_status import classify_runtime_failure


def _runtime_at_0023(tmp_path: Path) -> tuple[DatabaseRuntime, Settings]:
    """建立带管理员与附件的真实 0023 数据库，而不是手写近似表结构。"""

    settings = Settings(data_dir=tmp_path, environment="test", strict_sqlite=False)
    runtime = DatabaseRuntime(settings)
    runtime.create_schema()
    with runtime.session_factory() as db:
        db.add(
            User(
                id="rc4-upgrade-admin",
                username="rc4-admin",
                display_name="原位升级管理员",
                password_hash="test-only",
                role=UserRole.ADMIN,
                active=True,
            )
        )
        db.commit()
    config = runtime._alembic_config()
    with runtime.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0023")
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    (settings.attachments_dir / "preserved.txt").write_text(
        "升级前附件必须保持不变", encoding="utf-8"
    )
    assert "deleted_at" not in {
        column["name"] for column in inspect(runtime.engine).get_columns("backup_runs")
    }
    return runtime, settings


def _bind_runtime(monkeypatch, runtime: DatabaseRuntime, settings: Settings) -> None:
    """把共享启动函数绑定到隔离数据库，同时保留真实备份和 Alembic 路径。"""

    monkeypatch.setattr(backups, "db_runtime", runtime)
    monkeypatch.setattr(backups, "get_settings", lambda: settings)
    monkeypatch.setattr(upgrades, "db_runtime", runtime)
    monkeypatch.setattr(upgrades, "get_settings", lambda: settings)
    monkeypatch.setattr(app_main, "db_runtime", runtime)
    monkeypatch.setattr(app_main, "settings", settings)
    monkeypatch.setattr(app_main, "configure_logging", lambda: None)
    monkeypatch.setattr(app_main, "validate_bind_host", lambda *_a, **_k: None)
    monkeypatch.setattr(app_main, "validate_transport_security", lambda **_k: None)
    monkeypatch.setattr(app_main, "ensure_device_context_secret", lambda _db: None)
    monkeypatch.setattr(app_main, "ensure_current_release", lambda _db: None)
    monkeypatch.setattr(app_main, "seed_templates", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app_main.party_work, "ensure_party_work_templates", lambda *_a, **_k: None
    )


def _revision(runtime: DatabaseRuntime) -> str:
    with runtime.engine.connect() as connection:
        return str(
            connection.execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one()
        )


def test_full_initialize_runtime_upgrades_real_0023_database(monkeypatch, tmp_path: Path) -> None:
    """精确复现 rc.3 的 backup_runs 缺列故障，并要求完整启动事务成功。"""

    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)

    capabilities = app_main._initialize_runtime()

    assert capabilities["fts5"] is True
    with runtime.engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0026"
        assert connection.execute(
            text("SELECT display_name FROM users WHERE id='rc4-upgrade-admin'")
        ).scalar_one() == "原位升级管理员"
    with runtime.session_factory() as db:
        backup = db.scalar(select(BackupRun).where(BackupRun.kind == "pre-upgrade"))
        upgrade = db.scalar(select(UpgradeRecord).order_by(UpgradeRecord.created_at.desc()))
        assert backup is not None and backup.status == "completed"
        assert upgrade is not None and upgrade.status == "completed"
    manifest = backups.verify_backup(settings.backups_dir / backup.filename)
    assert manifest["schema_version"] == "0023"
    assert (settings.attachments_dir / "preserved.txt").read_text(encoding="utf-8") == (
        "升级前附件必须保持不变"
    )


def test_upgrade_backup_failure_never_touches_0023_database(monkeypatch, tmp_path: Path) -> None:
    """磁盘满等备份失败必须在 Alembic 执行前停止，原库字节保持不变。"""

    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)
    runtime.dispose()
    original_digest = backups.sha256_file(settings.database_path)
    monkeypatch.setattr(
        app_main,
        "create_pre_upgrade_backup",
        lambda: (_ for _ in ()).throw(OSError("database or disk is full")),
    )

    with pytest.raises(RuntimeError, match=UPGRADE_BACKUP_FAILED):
        app_main._initialize_runtime()

    runtime.dispose()
    assert backups.sha256_file(settings.database_path) == original_digest
    assert _revision(runtime) == "0023"
    assert not upgrades.upgrade_transaction_path().exists()


def test_migration_failure_atomically_restores_verified_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    """迁移已写入新结构后报错，也必须恢复旧版本、核心数据和附件。"""

    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)
    create_schema = runtime.create_schema

    def migrate_then_fail() -> None:
        create_schema()
        with runtime.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE users SET display_name='不应保留的半迁移数据' "
                    "WHERE id='rc4-upgrade-admin'"
                )
            )
        raise RuntimeError("no such column: injected_after_migration")

    monkeypatch.setattr(runtime, "create_schema", migrate_then_fail)

    with pytest.raises(RuntimeError, match=DATABASE_SCHEMA_FAILED):
        app_main._initialize_runtime()

    assert _revision(runtime) == "0023"
    with runtime.engine.connect() as connection:
        assert connection.execute(
            text("SELECT display_name FROM users WHERE id='rc4-upgrade-admin'")
        ).scalar_one() == "原位升级管理员"
        assert connection.execute(text("PRAGMA quick_check")).scalar_one() == "ok"
    assert (settings.attachments_dir / "preserved.txt").read_text(encoding="utf-8") == (
        "升级前附件必须保持不变"
    )
    failed_copies = list(settings.data_dir.glob("partyops.upgrade-failed-*.db"))
    assert len(failed_copies) == 1
    state = upgrades.read_upgrade_transaction_state()
    assert state is not None and state["state"] == "rolled_back"
    backup_path = settings.backups_dir / str(state["backup_filename"])
    manifest = backups.verify_backup(backup_path)
    expected = next(
        item for item in manifest["files"] if item["path"] == "database/partyops.db"
    )
    assert backups.sha256_file(settings.database_path) == expected["sha256"]


def test_next_launch_recovers_process_interrupted_during_migration(
    monkeypatch, tmp_path: Path
) -> None:
    """模拟进程在 Alembic 后被强退；下一启动先回滚，再允许重做升级。"""

    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)
    backup_path = upgrades.create_pre_upgrade_backup()
    upgrades.write_upgrade_transaction_state(
        "migrating", from_revision="0023", backup_path=backup_path
    )
    runtime.create_schema()
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET display_name='进程中断前的半迁移值' "
                "WHERE id='rc4-upgrade-admin'"
            )
        )
    assert _revision(runtime) == "0026"

    recovered = upgrades.recover_interrupted_upgrade()

    assert recovered == backup_path
    assert _revision(runtime) == "0023"
    with runtime.engine.connect() as connection:
        assert connection.execute(
            text("SELECT display_name FROM users WHERE id='rc4-upgrade-admin'")
        ).scalar_one() == "原位升级管理员"
    state = json.loads(upgrades.upgrade_transaction_path().read_text(encoding="utf-8"))
    assert state["state"] == "interrupted_rolled_back"
    assert state["detail_code"] == "PROCESS_INTERRUPTED"


def test_upgrade_backup_is_schema_neutral_until_migration(monkeypatch, tmp_path: Path) -> None:
    """备份阶段只能生成制品，不能用 0026 ORM 向 0023 的 backup_runs 写入。"""

    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)

    backup_path = upgrades.create_pre_upgrade_backup()

    with runtime.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM backup_runs")).scalar_one() == 0
    assert backups.verify_backup(backup_path)["schema_version"] == "0023"


def test_corrupt_upgrade_archive_is_rejected_before_database_overwrite(
    monkeypatch, tmp_path: Path
) -> None:
    """恢复包被截断时不得先移动当前数据库。"""

    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)
    backup_path = upgrades.create_pre_upgrade_backup()
    # 在线备份会执行 SQLite 一致性快照/检查点，因此在备份完成后固定比较基线。
    runtime.dispose()
    before = backups.sha256_file(settings.database_path)
    content = backup_path.read_bytes()
    backup_path.write_bytes(content[: max(1, len(content) // 2)])

    with pytest.raises(Exception):
        upgrades.restore_database_from_upgrade_backup(backup_path)

    assert backups.sha256_file(settings.database_path) == before
    assert _revision(runtime) == "0023"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (
            "sqlite3.OperationalError: table backup_runs has no column named deleted_at",
            DATABASE_SCHEMA_FAILED,
        ),
        ("当前 SQLite 未启用 FTS5", SQLITE_RUNTIME_FAILED),
        (f"[{UPGRADE_BACKUP_FAILED}] disk full", UPGRADE_BACKUP_FAILED),
    ],
)
def test_windows_launcher_preserves_precise_database_diagnostic(
    detail: str, expected: str
) -> None:
    assert classify_runtime_failure(detail) == expected


def test_backend_classifier_does_not_misreport_schema_as_sqlite_runtime() -> None:
    schema_code, _ = classify_database_startup_error(
        RuntimeError("table backup_runs has no column named deleted_at")
    )
    runtime_code, _ = classify_database_startup_error(
        RuntimeError("当前 SQLite 未启用 FTS5")
    )
    full_code, _ = classify_database_startup_error(RuntimeError("no space left"))
    io_code, _ = classify_database_startup_error(RuntimeError("readonly database"))
    assert schema_code == DATABASE_SCHEMA_FAILED
    assert runtime_code == SQLITE_RUNTIME_FAILED
    assert full_code == DATA_DIR_FULL
    assert io_code == DATABASE_IO_FAILED


def test_upgrade_transaction_journal_rejects_corruption_and_path_escape(
    monkeypatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        data_dir=tmp_path,
        logs_dir=tmp_path / "logs",
        backups_dir=tmp_path / "backups",
    )
    settings.logs_dir.mkdir()
    settings.backups_dir.mkdir()
    monkeypatch.setattr(upgrades, "get_settings", lambda: settings)
    journal = settings.logs_dir / upgrades.UPGRADE_TRANSACTION_FILENAME

    journal.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="日志损坏"):
        upgrades.read_upgrade_transaction_state()
    journal.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="格式无效"):
        upgrades.read_upgrade_transaction_state()

    outside = tmp_path / "outside.partyops-backup"
    outside.write_bytes(b"evidence")
    with pytest.raises(RuntimeError, match="不在受控备份目录"):
        upgrades.write_upgrade_transaction_state(
            "migrating", from_revision="0023", backup_path=outside
        )
    upgrades.write_upgrade_transaction_state(
        "completed", from_revision="0023", backup_path=None
    )
    assert upgrades.recover_interrupted_upgrade() is None

    journal.write_text(
        json.dumps(
            {
                "state": "migrating",
                "backup_filename": "../escape.partyops-backup",
                "from_revision": "0023",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="有效备份文件名"):
        upgrades.recover_interrupted_upgrade()
    journal.write_text(
        json.dumps(
            {
                "state": "migrating",
                "backup_filename": "safe.partyops-backup",
                "from_revision": "",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="来源迁移版本"):
        upgrades.recover_interrupted_upgrade()


def test_upgrade_preconditions_and_postconditions_fail_closed(monkeypatch, tmp_path: Path) -> None:
    class BrokenEngine:
        def connect(self):
            raise OSError("database unavailable")

    monkeypatch.setattr(upgrades, "db_runtime", SimpleNamespace(engine=BrokenEngine()))
    monkeypatch.setattr(
        upgrades,
        "get_settings",
        lambda: SimpleNamespace(database_path=tmp_path / "partyops.db"),
    )
    (tmp_path / "partyops.db").write_bytes(b"not-opened")
    assert upgrades.database_has_business_data() is False

    backup = tmp_path / "backup.partyops-backup"
    monkeypatch.setattr(
        upgrades, "verify_backup", lambda _path: {"schema_version": "0022", "files": []}
    )
    with pytest.raises(RuntimeError, match="backup revision mismatch"):
        upgrades.validate_upgrade_postconditions("0023", backup)
    monkeypatch.setattr(
        upgrades, "verify_backup", lambda _path: {"schema_version": "0023", "files": []}
    )
    monkeypatch.setattr(upgrades, "current_schema_version", lambda: "0023")
    with pytest.raises(RuntimeError, match="revision mismatch"):
        upgrades.validate_upgrade_postconditions("0023", backup)
    with pytest.raises(RuntimeError, match="缺少数据库摘要"):
        upgrades.restore_database_from_upgrade_backup(backup)


def test_schema_neutral_backup_rejects_unsafe_names_and_revision_drift(
    monkeypatch, tmp_path: Path
) -> None:
    runtime, settings = _runtime_at_0023(tmp_path)
    _bind_runtime(monkeypatch, runtime, settings)
    with pytest.raises(RuntimeError, match="越界语义"):
        backups.create_backup_archive(kind="pre-upgrade", filename="../escape")

    existing = settings.backups_dir / "existing.partyops-backup"
    existing.write_bytes(b"do-not-overwrite")
    with pytest.raises(RuntimeError, match="目标已存在"):
        backups.create_backup_archive(kind="pre-upgrade", filename=existing.name)
    assert existing.read_bytes() == b"do-not-overwrite"

    monkeypatch.setattr(
        backups,
        "verify_backup",
        lambda _path: {"schema_version": "unexpected"},
    )
    with pytest.raises(RuntimeError, match="版本在校验期间发生变化"):
        backups.create_backup_archive(
            kind="pre-upgrade", filename="revision-drift.partyops-backup"
        )
    assert not (settings.backups_dir / "revision-drift.partyops-backup").exists()
    with pytest.raises(RuntimeError, match="不在受控备份目录"):
        backups.register_backup_artifact(
            object(), tmp_path / "outside.partyops-backup", kind="pre-upgrade"
        )


def test_snapshot_schema_version_covers_legacy_fallbacks(tmp_path: Path) -> None:
    release_notes = tmp_path / "release-notes.db"
    connection = sqlite3_dbapi.connect(release_notes)
    connection.execute(
        "CREATE TABLE schema_release_notes (revision TEXT PRIMARY KEY)"
    )
    connection.execute("INSERT INTO schema_release_notes VALUES ('0019')")
    connection.commit()
    connection.close()
    assert backups._snapshot_schema_version(release_notes) == "0019"

    legacy = tmp_path / "legacy.db"
    connection = sqlite3_dbapi.connect(legacy)
    connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    assert backups._snapshot_schema_version(legacy) == "0002"

    empty = tmp_path / "empty.db"
    sqlite3_dbapi.connect(empty).close()
    assert backups._snapshot_schema_version(empty) == "0001"


def test_windows_database_failure_classifier_remaining_paths() -> None:
    cases = {
        "no such file or directory": windows_host_status.RUNTIME_EXECUTABLE_MISSING,
        "database disk image is malformed": windows_host_status.DATABASE_CORRUPT,
        "database table is locked": windows_host_status.DATABASE_LOCKED,
        "schema upgrade migration failed": windows_host_status.CONFIG_MIGRATION_FAILED,
        "address already in use": windows_host_status.PORT_IN_USE,
        "certificate verify failed": windows_host_status.TLS_INIT_FAILED,
    }
    for detail, expected in cases.items():
        assert classify_runtime_failure(detail) == expected


def _mock_runtime_without_database(monkeypatch, tmp_path: Path) -> None:
    settings = SimpleNamespace(
        network_bind_host="127.0.0.1",
        network_advertise_host="127.0.0.1",
        environment="test",
        tls_enabled=False,
        data_dir=tmp_path,
    )
    monkeypatch.setattr(app_main, "settings", settings)
    monkeypatch.setattr(app_main, "configure_logging", lambda: None)
    monkeypatch.setattr(app_main, "validate_bind_host", lambda *_a, **_k: None)
    monkeypatch.setattr(app_main, "validate_transport_security", lambda **_k: None)
    monkeypatch.setattr(app_main, "upgrade_required", lambda: (False, "0026"))
    monkeypatch.setattr(app_main, "ensure_device_context_secret", lambda _db: None)
    monkeypatch.setattr(app_main, "ensure_current_release", lambda _db: None)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _query):
            return None

        def commit(self):
            return None

    monkeypatch.setattr(
        app_main,
        "db_runtime",
        SimpleNamespace(
            create_schema=lambda: None,
            validate_capabilities=lambda: {"fts5": True},
            session_factory=lambda: Session(),
        ),
    )


def test_initialize_runtime_reports_interrupted_recovery_failure(monkeypatch, tmp_path: Path) -> None:
    _mock_runtime_without_database(monkeypatch, tmp_path)
    recovered = tmp_path / "backups" / "previous.partyops-backup"
    monkeypatch.setattr(app_main, "recover_interrupted_upgrade", lambda: recovered)
    assert app_main._initialize_runtime() == {"fts5": True}

    monkeypatch.setattr(
        app_main,
        "recover_interrupted_upgrade",
        lambda: (_ for _ in ()).throw(RuntimeError("journal broken")),
    )
    with pytest.raises(RuntimeError, match=DATABASE_SCHEMA_FAILED):
        app_main._initialize_runtime()


def test_initialize_runtime_windows_backup_and_rollback_status_paths(
    monkeypatch, tmp_path: Path
) -> None:
    _mock_runtime_without_database(monkeypatch, tmp_path)
    monkeypatch.setattr(app_main, "recover_interrupted_upgrade", lambda: None)
    monkeypatch.setattr(app_main, "upgrade_required", lambda: (True, "0023"))
    monkeypatch.setattr(
        app_main,
        "create_pre_upgrade_backup",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )
    proxy = SimpleNamespace(**vars(os))
    proxy.name = "nt"
    monkeypatch.setattr(app_main, "os", proxy)
    statuses: list[dict[str, object]] = []
    monkeypatch.setattr(
        windows_host_status,
        "write_service_status",
        lambda *_a, **kwargs: statuses.append(kwargs),
    )
    with pytest.raises(RuntimeError, match=UPGRADE_BACKUP_FAILED):
        app_main._initialize_runtime()
    assert statuses[-1]["stage"] == "upgrade_backup_failed"

    monkeypatch.setattr(
        windows_host_status,
        "write_service_status",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("status denied")),
    )
    with pytest.raises(RuntimeError, match=UPGRADE_BACKUP_FAILED):
        app_main._initialize_runtime()


def test_initialize_runtime_reports_admin_invariant_and_failed_rollback(
    monkeypatch, tmp_path: Path
) -> None:
    _mock_runtime_without_database(monkeypatch, tmp_path)
    backup = tmp_path / "backups" / "upgrade.partyops-backup"
    backup.parent.mkdir()
    backup.write_bytes(b"verified-by-test-double")
    monkeypatch.setattr(app_main, "recover_interrupted_upgrade", lambda: None)
    monkeypatch.setattr(app_main, "upgrade_required", lambda: (True, "0023"))
    monkeypatch.setattr(app_main, "create_pre_upgrade_backup", lambda: backup)
    monkeypatch.setattr(app_main, "write_upgrade_transaction_state", lambda *_a, **_k: None)
    monkeypatch.setattr(app_main, "validate_upgrade_postconditions", lambda *_a: None)
    monkeypatch.setattr(app_main, "register_pre_upgrade_backup", lambda *_a: None)
    monkeypatch.setattr(app_main, "record_upgrade", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app_main,
        "restore_database_from_upgrade_backup",
        lambda _path: (_ for _ in ()).throw(OSError("restore denied")),
    )

    class Session:
        values = iter([None, 1])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _query):
            return next(self.values)

        def commit(self):
            return None

    app_main.db_runtime.session_factory = lambda: Session()
    proxy = SimpleNamespace(**vars(os))
    proxy.name = "nt"
    monkeypatch.setattr(app_main, "os", proxy)
    statuses: list[dict[str, object]] = []
    monkeypatch.setattr(
        windows_host_status,
        "write_service_status",
        lambda *_a, **kwargs: statuses.append(kwargs),
    )

    with pytest.raises(RuntimeError, match=DATABASE_SCHEMA_FAILED):
        app_main._initialize_runtime()

    assert statuses[-1]["stage"] == "schema_failed"
    assert "自动回滚未完成" in str(statuses[-1]["detail"])
