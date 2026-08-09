"""1.0 数据库原位升级、升级前备份和失败回滚。"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import text

from .backups import SCHEMA_VERSION, create_backup, current_schema_version
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
        db_runtime.dispose()
        failed = settings.database_path.with_suffix(".db.upgrade-failed")
        failed.unlink(missing_ok=True)
        if settings.database_path.exists():
            os.replace(settings.database_path, failed)
        os.replace(extracted, settings.database_path)
        db_runtime.rebuild()


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
