"""一致性备份、校验、保留与原子恢复。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import emit_event, write_audit
from .config import get_settings
from .database import db_runtime, sqlite3_dbapi
from .models import BackupRun, User, utcnow
from .problems import ProblemException


FORMAT_VERSION = 1
SCHEMA_VERSION = "0017"


def current_schema_version() -> str:
    """读取数据库实际模式版本；兼容没有版本表的 1.0 试用库。"""

    try:
        with db_runtime.engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "alembic_version" in tables:
                value = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version LIMIT 1"
                ).scalar()
                if value:
                    return str(value)
            if "schema_release_notes" in tables:
                value = connection.exec_driver_sql(
                    "SELECT max(revision) FROM schema_release_notes"
                ).scalar()
                if value:
                    return str(value)
            if "tasks" in tables:
                return "0002"
    except Exception:
        return "0001"
    return "0001"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_zip_infos(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """校验备份成员边界，拒绝路径逃逸、链接、重复成员和 ZIP 炸弹。"""

    settings = get_settings()
    infos = archive.infolist()
    if len(infos) > settings.backup_max_members:
        raise ProblemException(400, "BACKUP_MEMBER_LIMIT", "备份包无效", "备份文件数量超过安全上限。")
    seen: set[str] = set()
    expanded = 0
    expanded_limit = settings.backup_restore_max_gb * 1024**3
    for member in infos:
        name = member.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        mode = (member.external_attr >> 16) & 0o170000
        if (
            not name
            or "\x00" in name
            or name.startswith("/")
            or ".." in parts
            or name in seen
            or mode == stat.S_IFLNK
        ):
            raise ProblemException(400, "BACKUP_PATH_INVALID", "备份包无效", "备份包含非法路径、链接或重复成员。")
        seen.add(name)
        expanded += max(0, int(member.file_size))
        if expanded > expanded_limit:
            raise ProblemException(400, "BACKUP_EXPANDED_LIMIT", "备份包无效", "备份解压体积超过安全上限。")
        # 极小压缩数据却声明巨大输出是典型 ZIP 炸弹。正常 SQLite、图片和文档备份
        # 即便可压缩性很高，也不应超过 1000 倍且同时大于 100 MiB。
        if member.file_size > 100 * 1024**2 and member.file_size > max(member.compress_size, 1) * 1000:
            raise ProblemException(400, "BACKUP_COMPRESSION_INVALID", "备份包无效", "备份成员压缩比异常。")
    return infos


def _safe_zip_members(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    infos = _validated_zip_infos(archive)
    for member in infos:
        target = (root / member.filename).resolve()
        if root != target and root not in target.parents:
            raise ProblemException(
                400, "BACKUP_PATH_INVALID", "备份包无效", "备份包含越界路径。"
            )
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def _ensure_data_child(path: Path) -> Path:
    root = get_settings().data_dir.resolve()
    target = path.resolve()
    if root == target or root not in target.parents:
        raise RuntimeError(f"运维路径超出数据目录：{target}")
    return target


def _database_snapshot(source: Path, destination: Path) -> None:
    source_connection = sqlite3_dbapi.connect(source)
    destination_connection = sqlite3_dbapi.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def create_backup(
    db: Session,
    actor: User | None,
    kind: str = "manual",
    ip: str = "",
) -> BackupRun:
    settings = get_settings()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    record = BackupRun(
        filename=f"PartyOps-{kind}-{stamp}.partyops-backup",
        kind=kind,
        created_by=actor.id if actor else None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    output = settings.backups_dir / record.filename
    try:
        with tempfile.TemporaryDirectory(
            prefix="partyops-backup-", dir=settings.data_dir
        ) as temp_dir_value:
            temp_dir = Path(temp_dir_value)
            snapshot = temp_dir / "partyops.db"
            # 只在确定数据库快照与不可变文件集合时短暂阻止业务写入。
            # 附件使用内容寻址且原子落盘，后续哈希/压缩无需长期占用写锁。
            with db_runtime.write_lock:
                _database_snapshot(settings.database_path, snapshot)
                attachment_paths = [
                    path
                    for path in settings.attachments_dir.rglob("*")
                    if path.is_file() and ".incoming" not in path.parts
                ]
                archive_index_paths = [
                    path for path in settings.archives_dir.rglob("*") if path.is_file()
                ]
            files: list[dict[str, object]] = [
                {
                    "path": "database/partyops.db",
                    "sha256": sha256_file(snapshot),
                    "size": snapshot.stat().st_size,
                }
            ]
            for path in attachment_paths:
                relative = path.relative_to(settings.attachments_dir).as_posix()
                files.append(
                    {
                        "path": f"attachments/{relative}",
                        "sha256": sha256_file(path),
                        "size": path.stat().st_size,
                    }
                )
            for path in archive_index_paths:
                relative = path.relative_to(settings.archives_dir).as_posix()
                files.append(
                    {
                        "path": f"archives/{relative}",
                        "sha256": sha256_file(path),
                        "size": path.stat().st_size,
                    }
                )
            manifest = {
                "format": "partyops-backup",
                "format_version": FORMAT_VERSION,
                "schema_version": current_schema_version(),
                "app_version": settings.app_version,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "files": files,
            }
            with zipfile.ZipFile(
                output, "w", zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                archive.write(snapshot, "database/partyops.db")
                for path in attachment_paths:
                    relative = path.relative_to(settings.attachments_dir).as_posix()
                    archive.write(path, f"attachments/{relative}")
                for path in archive_index_paths:
                    relative = path.relative_to(settings.archives_dir).as_posix()
                    archive.write(path, f"archives/{relative}")
                archive.writestr(
                    "config/config.json",
                    json.dumps(
                        {"mode": "host", "app_version": settings.app_version},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
        record.status = "completed"
        record.size_bytes = output.stat().st_size
        record.sha256 = sha256_file(output)
        record.completed_at = utcnow()
        write_audit(
            db,
            actor,
            "backup.create",
            "backup",
            record.id,
            {"kind": kind, "filename": record.filename},
            ip,
        )
        emit_event(db, "backup.completed", record.id, {"filename": record.filename})
        db.commit()
        apply_retention(db)
        return record
    except Exception as exc:
        record.status = "failed"
        record.message = str(exc)[:1000]
        record.completed_at = utcnow()
        db.commit()
        if output.exists():
            output.unlink()
        raise


def verify_backup(path: Path) -> dict[str, object]:
    if not path.exists() or not zipfile.is_zipfile(path):
        raise ProblemException(400, "BACKUP_INVALID", "备份包无效", "文件不是有效备份包。")
    with zipfile.ZipFile(path) as archive:
        infos = _validated_zip_infos(archive)
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ProblemException(
                400, "BACKUP_MANIFEST_INVALID", "备份清单无效", "缺少有效 manifest.json。"
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("format") != "partyops-backup":
            raise ProblemException(400, "BACKUP_FORMAT_INVALID", "备份格式不匹配", "请选择党建智办备份。")
        if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
            raise ProblemException(
                409, "BACKUP_TOO_NEW", "备份版本过新", "请使用更高版本的党建智办恢复。"
            )
        if str(manifest.get("schema_version", "0000")) > SCHEMA_VERSION:
            raise ProblemException(
                409,
                "BACKUP_SCHEMA_TOO_NEW",
                "备份数据库版本过新",
                "请使用相同或更高版本的党建智办恢复。",
            )
        declared_files = manifest.get("files")
        if not isinstance(declared_files, list):
            raise ProblemException(400, "BACKUP_MANIFEST_INVALID", "备份清单无效", "files 必须是数组。")
        members = {item.filename for item in infos}
        seen: set[str] = set()
        for item in declared_files:
            if not isinstance(item, dict):
                raise ProblemException(400, "BACKUP_MANIFEST_INVALID", "备份清单无效", "文件项格式错误。")
            item_path = str(item.get("path", ""))
            if (
                not item_path
                or item_path in seen
                or item_path not in members
                or item_path.startswith("/")
                or ".." in Path(item_path).parts
            ):
                raise ProblemException(
                    400, "BACKUP_MANIFEST_INVALID", "备份清单无效", "备份文件清单不完整或包含非法路径。"
                )
            seen.add(item_path)
            digest = hashlib.sha256()
            size = 0
            with archive.open(str(item["path"])) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            try:
                expected_size = int(item["size"])
                expected_hash = str(item["sha256"]).lower()
            except (KeyError, TypeError, ValueError) as exc:
                raise ProblemException(400, "BACKUP_MANIFEST_INVALID", "备份清单无效", "文件大小或哈希格式错误。") from exc
            if expected_size < 0 or len(expected_hash) != 64 or digest.hexdigest() != expected_hash or size != expected_size:
                raise ProblemException(
                    400, "BACKUP_HASH_MISMATCH", "备份校验失败", f"文件损坏：{item['path']}"
                )
        if "database/partyops.db" not in seen:
            raise ProblemException(
                400, "BACKUP_DATABASE_MISSING", "备份缺少数据库", "无法恢复。"
            )
        allowed_members = seen | {"manifest.json", "config/config.json"}
        unexpected = [
            member.filename
            for member in infos
            if not member.is_dir() and member.filename not in allowed_members
        ]
        if unexpected:
            raise ProblemException(
                400,
                "BACKUP_EXTRA_FILES",
                "备份包含未登记文件",
                "备份包与清单不一致，已拒绝恢复。",
            )
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            with archive.open("database/partyops.db") as source:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
            integrity_path = Path(handle.name)
        try:
            connection = sqlite3_dbapi.connect(integrity_path)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
            if integrity != "ok":
                raise ProblemException(
                    400, "BACKUP_DATABASE_CORRUPT", "备份数据库损坏", "SQLite 完整性检查未通过。"
                )
        finally:
            integrity_path.unlink(missing_ok=True)
    return manifest


def restore_backup(path: Path, actor_id: str | None = None) -> None:
    settings = get_settings()
    manifest = verify_backup(path)
    required_bytes = int(
        sum(int(item.get("size", 0)) for item in manifest.get("files", [])) * 2.5
    )
    if shutil.disk_usage(settings.data_dir).free < required_bytes:
        raise ProblemException(
            507,
            "RESTORE_SPACE_INSUFFICIENT",
            "磁盘空间不足",
            "恢复至少需要备份解压大小的 2.5 倍可用空间。",
        )
    # 恢复期间所有普通会话都会收到 503；先等待在途查询/写入结束，
    # 再制作恢复前快照和换库，避免旧连接继续向已改名数据库提交。
    with db_runtime.exclusive_maintenance():
        pre_session = db_runtime.session_factory()
        try:
            actor = pre_session.get(User, actor_id) if actor_id else None
            create_backup(pre_session, actor, kind="pre-restore")
        finally:
            pre_session.close()
        with tempfile.TemporaryDirectory(
            prefix="partyops-restore-", dir=settings.data_dir
        ) as temp:
            staging = Path(temp)
            with zipfile.ZipFile(path) as archive:
                _safe_zip_members(archive, staging)
            new_db = staging / "database" / "partyops.db"
            new_attachments = staging / "attachments"
            new_archives = staging / "archives"
            if not new_db.exists():
                raise ProblemException(
                    400, "BACKUP_DATABASE_MISSING", "备份缺少数据库", "无法恢复。"
                )
            previous_db = _ensure_data_child(
                settings.database_path.with_suffix(".db.restore-previous")
            )
            previous_attachments = _ensure_data_child(
                settings.data_dir / "attachments.restore-previous"
            )
            previous_archives = _ensure_data_child(
                settings.data_dir / "archives.restore-previous"
            )
            with db_runtime.write_lock:
                db_runtime.dispose()
                try:
                    if previous_db.exists():
                        previous_db.unlink()
                    if previous_attachments.exists():
                        shutil.rmtree(previous_attachments)
                    if previous_archives.exists():
                        shutil.rmtree(previous_archives)
                    if settings.database_path.exists():
                        os.replace(settings.database_path, previous_db)
                    if settings.attachments_dir.exists():
                        os.replace(settings.attachments_dir, previous_attachments)
                    if settings.archives_dir.exists():
                        os.replace(settings.archives_dir, previous_archives)
                    shutil.copy2(new_db, settings.database_path)
                    if new_attachments.exists():
                        shutil.copytree(new_attachments, settings.attachments_dir)
                    else:
                        settings.attachments_dir.mkdir(parents=True)
                    if new_archives.exists():
                        shutil.copytree(new_archives, settings.archives_dir)
                    else:
                        settings.archives_dir.mkdir(parents=True)
                    db_runtime.rebuild()
                    db_runtime.create_schema()
                    db_runtime.validate_capabilities()
                    previous_db.unlink(missing_ok=True)
                    if previous_attachments.exists():
                        shutil.rmtree(previous_attachments)
                    if previous_archives.exists():
                        shutil.rmtree(previous_archives)
                except Exception:
                    db_runtime.dispose()
                    settings.database_path.unlink(missing_ok=True)
                    if settings.attachments_dir.exists():
                        shutil.rmtree(settings.attachments_dir)
                    if settings.archives_dir.exists():
                        shutil.rmtree(settings.archives_dir)
                    if previous_db.exists():
                        os.replace(previous_db, settings.database_path)
                    if previous_attachments.exists():
                        os.replace(previous_attachments, settings.attachments_dir)
                    if previous_archives.exists():
                        os.replace(previous_archives, settings.archives_dir)
                    db_runtime.rebuild()
                    raise


def apply_retention(db: Session) -> None:
    settings = get_settings()
    automatic = db.scalars(
        select(BackupRun)
        .where(BackupRun.kind == "automatic", BackupRun.status == "completed")
        .order_by(BackupRun.created_at.desc())
    ).all()
    keep_ids = {item.id for item in automatic[: settings.backup_daily_keep]}
    weekly_seen: set[tuple[int, int]] = set()
    for item in automatic:
        calendar = item.created_at.isocalendar()
        key = (calendar.year, calendar.week)
        if key not in weekly_seen and len(weekly_seen) < settings.backup_weekly_keep:
            keep_ids.add(item.id)
            weekly_seen.add(key)
    for item in automatic:
        if item.id in keep_ids:
            continue
        path = settings.backups_dir / item.filename
        path.unlink(missing_ok=True)
        db.delete(item)
    db.commit()
