"""1.0 数据库原位升级、升级前备份和失败回滚。"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from .backups import (
    SCHEMA_VERSION,
    create_backup_archive,
    current_schema_version,
    register_backup_artifact,
    sha256_file,
    verify_backup,
)
from .config import get_settings
from .database import db_runtime
from .models import UpgradeRecord, utcnow
from .time_utils import beijing_iso, beijing_now

UPGRADE_TRANSACTION_FILENAME = "upgrade-transaction.json"
ACTIVE_UPGRADE_STATES = frozenset({"backup_verified", "migrating", "validating"})


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
    """先完成模式无关快照，迁移前绝不实例化当前 ORM 模型。"""

    return create_backup_archive(kind="pre-upgrade").path


def upgrade_transaction_path() -> Path:
    """返回启动事务日志；文件只记录受控文件名和阶段，不含业务数据。"""

    return get_settings().logs_dir / UPGRADE_TRANSACTION_FILENAME


def read_upgrade_transaction_state() -> dict[str, object] | None:
    """读取上次升级阶段；损坏日志必须阻止盲目继续迁移。"""

    path = upgrade_transaction_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("升级事务日志损坏，无法确认上次迁移状态") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("state"), str):
        raise RuntimeError("升级事务日志格式无效")
    return payload


def write_upgrade_transaction_state(
    state: str,
    *,
    from_revision: str,
    backup_path: Path | None,
    detail_code: str = "",
) -> Path:
    """原子持久化升级阶段，供进程中断后的下一次启动恢复。"""

    settings = get_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    backup_filename = ""
    if backup_path is not None:
        resolved = backup_path.resolve()
        if resolved.parent != settings.backups_dir.resolve():
            raise RuntimeError("升级事务备份不在受控备份目录")
        backup_filename = resolved.name
    payload = {
        "format": "partyops-upgrade-transaction",
        "format_version": 1,
        "state": state,
        "from_revision": from_revision,
        "target_revision": SCHEMA_VERSION,
        "backup_filename": backup_filename,
        "detail_code": detail_code,
        "updated_at": beijing_iso(),
    }
    target = upgrade_transaction_path()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".upgrade-transaction-", suffix=".tmp", dir=settings.logs_dir
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def recover_interrupted_upgrade() -> Path | None:
    """发现未完成迁移时先恢复已校验快照，再允许重新执行完整事务。"""

    payload = read_upgrade_transaction_state()
    if payload is None or payload["state"] not in ACTIVE_UPGRADE_STATES:
        return None
    settings = get_settings()
    filename = str(payload.get("backup_filename", ""))
    if not filename or Path(filename).name != filename:
        raise RuntimeError("未完成升级事务缺少有效备份文件名")
    backup_path = settings.backups_dir / filename
    from_revision = str(payload.get("from_revision", ""))
    if not from_revision:
        raise RuntimeError("未完成升级事务缺少来源迁移版本")
    restore_database_from_upgrade_backup(backup_path)
    write_upgrade_transaction_state(
        "interrupted_rolled_back",
        from_revision=from_revision,
        backup_path=backup_path,
        detail_code="PROCESS_INTERRUPTED",
    )
    return backup_path


def register_pre_upgrade_backup(path: Path) -> None:
    """仅在数据库已经到达当前模式后登记升级前备份。"""

    with db_runtime.session_factory() as db:
        register_backup_artifact(db, path, kind="pre-upgrade")


def validate_upgrade_postconditions(from_revision: str, backup_path: Path) -> None:
    """迁移提交前复核快照来源与数据库头版本，防止半迁移继续启动。"""

    manifest = verify_backup(backup_path)
    if str(manifest.get("schema_version", "")) != from_revision:
        raise RuntimeError("schema upgrade backup revision mismatch")
    revision = current_schema_version()
    if revision != SCHEMA_VERSION:
        raise RuntimeError(
            f"schema upgrade revision mismatch: expected {SCHEMA_VERSION}, got {revision}"
        )


def restore_database_from_upgrade_backup(path: Path) -> None:
    """仅回滚数据库；模式迁移不会改动原始目录或附件内容。"""

    settings = get_settings()
    # 迁移失败前后可能经过较长时间；再次校验清单、成员闭包、哈希和 SQLite
    # 完整性，避免路径被替换后把未校验数据库覆盖回生产数据目录。
    manifest = verify_backup(path)
    expected_database = next(
        (
            item
            for item in manifest.get("files", [])
            if isinstance(item, dict) and item.get("path") == "database/partyops.db"
        ),
        None,
    )
    if not expected_database:
        raise RuntimeError("升级前备份缺少数据库摘要")
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
        failed = settings.database_path.with_name(
            f"{settings.database_path.stem}.upgrade-failed-"
            f"{beijing_now().strftime('%Y%m%d-%H%M%S-%f')}"
            f"{settings.database_path.suffix}"
        )
        moved_companions: list[tuple[Path, Path]] = []
        if settings.database_path.exists():
            os.replace(settings.database_path, failed)
        for suffix in ("-wal", "-shm"):
            source = Path(f"{settings.database_path}{suffix}")
            if source.exists():
                destination = Path(f"{failed}{suffix}")
                os.replace(source, destination)
                moved_companions.append((source, destination))
        try:
            os.replace(extracted, settings.database_path)
        except OSError:
            if failed.exists() and not settings.database_path.exists():
                os.replace(failed, settings.database_path)
            for source, destination in moved_companions:
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            raise
        if settings.database_path.stat().st_size != int(expected_database["size"]):
            raise RuntimeError("恢复后的数据库长度与升级前快照不一致")
        if sha256_file(settings.database_path) != str(expected_database["sha256"]):
            raise RuntimeError("恢复后的数据库哈希与升级前快照不一致")
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
