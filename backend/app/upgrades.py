"""1.0 数据库原位升级、升级前备份和失败回滚。"""

from __future__ import annotations

import os
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from sqlalchemy import text

from .backups import SCHEMA_VERSION, create_backup, current_schema_version, verify_backup
from .config import get_settings
from .database import db_runtime
from .models import UpgradeRecord, utcnow


def database_has_business_data() -> bool:
    if not get_settings().database_path.exists():
        return False
    try:
        with db_runtime.engine.connect() as connection:
            return bool(
                connection.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
                ).scalar()
            )
    except Exception:
        return False


def upgrade_required() -> tuple[bool, str]:
    revision = current_schema_version()
    return database_has_business_data() and revision < SCHEMA_VERSION, revision


def create_pre_upgrade_backup() -> Path:
    with db_runtime.session_factory() as db:
        record = create_backup(db, None, kind="pre-upgrade")
        return get_settings().backups_dir / record.filename


def restore_database_from_upgrade_backup(path: Path) -> None:
    """仅回滚数据库；模式迁移不会改动原始目录或附件内容。"""

    settings = get_settings()
    # 迁移失败前后可能经过较长时间；再次校验清单、成员闭包、哈希和 SQLite
    # 完整性，避免路径被替换后把未校验数据库覆盖回生产数据目录。
    verify_backup(path)
    with tempfile.TemporaryDirectory(
        prefix="partyops-upgrade-rollback-", dir=settings.data_dir
    ) as temporary:
        extracted = Path(temporary) / "partyops.db"
        with zipfile.ZipFile(path) as archive:
            with archive.open("database/partyops.db") as source, extracted.open(
                "wb"
            ) as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
        with closing(sqlite3_connect(extracted)) as restored:
            if restored.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("升级前备份数据库完整性检查失败，拒绝覆盖当前数据")
        db_runtime.dispose()
        failed = settings.database_path.with_suffix(".db.upgrade-failed")
        failed.unlink(missing_ok=True)
        # SQLite WAL 属于原数据库；替换主文件前必须清除旧 WAL/SHM，避免失败
        # 迁移在恢复后的数据库上被再次回放。
        Path(f"{settings.database_path}-wal").unlink(missing_ok=True)
        Path(f"{settings.database_path}-shm").unlink(missing_ok=True)
        if settings.database_path.exists():
            os.replace(settings.database_path, failed)
        try:
            os.replace(extracted, settings.database_path)
        except OSError:
            if failed.exists() and not settings.database_path.exists():
                os.replace(failed, settings.database_path)
            raise
        db_runtime.rebuild()
        with closing(sqlite3_connect(settings.database_path)) as restored:
            if restored.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("恢复后的数据库完整性检查失败")


def sqlite3_connect(path: Path):
    """复用生产 SQLite 驱动做只读前置完整性核对。"""

    from .database import sqlite3_dbapi

    return sqlite3_dbapi.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def record_upgrade(
    from_revision: str,
    backup_path: Path,
    *,
    status: str,
    message: str = "",
) -> None:
    with db_runtime.session_factory() as db:
        db.add(
            UpgradeRecord(
                from_version="1.0.0",
                to_version=get_settings().app_version,
                schema_revision=SCHEMA_VERSION,
                status=status,
                backup_filename=backup_path.name,
                message=message[:1000],
                completed_at=utcnow(),
            )
        )
        db.commit()
