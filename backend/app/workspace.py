"""原始文件夹只读索引、路径约束、关联与固化归档。"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import get_settings
from .database import db_runtime
from .enums import ContentIndexStatus, FileIndexStatus
from .models import (
    BackgroundJob,
    FileBlob,
    User,
    WorkspaceFile,
    WorkspaceFileTag,
    WorkspaceLink,
    WorkspaceRoot,
    utcnow,
)
from .problems import ProblemException
from .schemas import WorkspaceFileOut, WorkspaceScanOut


logger = logging.getLogger("partyops.workspace")
_SCAN_COMMIT_BATCH_SIZE = 500
_scan_locks: dict[str, threading.Lock] = {}
_scan_locks_guard = threading.Lock()


def _scan_lock(root_id: str) -> threading.Lock:
    """返回进程内稳定的根目录扫描锁。

    PartyOps 主机服务使用单进程运行；按根目录串行化可以允许不同目录并行，
    同时避免“新建后自动扫描”和用户“立即同步”竞争同一唯一索引键。
    """

    with _scan_locks_guard:
        return _scan_locks.setdefault(root_id, threading.Lock())


def validate_root_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ProblemException(422, "ROOT_PATH_NOT_ABSOLUTE", "目录路径无效", "必须选择主机上的绝对路径。")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProblemException(422, "ROOT_PATH_UNAVAILABLE", "目录不可用", "主机无法访问该目录。") from exc
    if not resolved.is_dir():
        raise ProblemException(422, "ROOT_PATH_NOT_DIRECTORY", "路径不是目录", "请选择文件夹。")
    data_dir = get_settings().data_dir.resolve()
    if resolved == data_dir or data_dir in resolved.parents or resolved in data_dir.parents:
        raise ProblemException(
            422,
            "ROOT_PATH_RESERVED",
            "不能纳管系统数据目录",
            "请选择业务资料目录，避免循环索引备份和附件。",
        )
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ProblemException(
            403,
            "ROOT_PATH_PERMISSION_DENIED",
            "目录没有只读权限",
            "请在主机桌面向导中为党建智办授予该目录只读权限。",
        )
    return resolved


def resolve_workspace_path(root: WorkspaceRoot, relative_path: str) -> Path:
    root_path = Path(root.absolute_path).resolve()
    candidate = root_path / relative_path
    try:
        target = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProblemException(410, "WORKSPACE_FILE_MISSING", "原始文件不可用", "文件可能已被移动或删除。") from exc
    if target != root_path and root_path not in target.parents:
        raise ProblemException(
            403,
            "WORKSPACE_PATH_OUTSIDE_ROOT",
            "文件超出授权范围",
            "系统已拒绝访问授权目录之外的文件。",
        )
    if candidate.is_symlink():
        raise ProblemException(
            403,
            "WORKSPACE_SYMLINK_DENIED",
            "不允许访问符号链接",
            "请直接授权真实文件所在目录。",
        )
    return target


def file_to_out(db: Session, item: WorkspaceFile, include_preview: bool = False) -> WorkspaceFileOut:
    tags = list(
        db.scalars(
            select(WorkspaceFileTag.tag)
            .where(WorkspaceFileTag.file_id == item.id)
            .order_by(WorkspaceFileTag.tag)
        ).all()
    )
    links = [
        {
            "id": link.id,
            "entity_type": link.entity_type,
            "entity_id": link.entity_id,
            "relation": link.relation,
        }
        for link in db.scalars(
            select(WorkspaceLink)
            .where(WorkspaceLink.file_id == item.id)
            .order_by(WorkspaceLink.created_at)
        ).all()
    ]
    return WorkspaceFileOut(
        id=item.id,
        root_id=item.root_id,
        parent_id=item.parent_id,
        relative_path=item.relative_path,
        name=item.name,
        is_directory=item.is_directory,
        in_scope=item.in_scope,
        extension=item.extension,
        size_bytes=item.size_bytes,
        modified_at=item.modified_at,
        mime_type=item.mime_type,
        sha256=item.sha256,
        device_id=item.device_id,
        availability=item.availability,
        status=item.status,
        content_status=item.content_status,
        content_error_code=item.content_error_code,
        detected_type=item.detected_type,
        archive_member_count=item.archive_member_count,
        indexed_at=item.indexed_at,
        last_seen_at=item.last_seen_at,
        version=item.version,
        tags=tags,
        links=links,
        preview_text=(item.extracted_text or item.ocr_text)[:8_000]
        if include_preview
        else "",
    )


def _stat_signature(path: Path) -> tuple[int, int, str, str]:
    stat = path.stat(follow_symlinks=False)
    return stat.st_size, stat.st_mtime_ns, str(stat.st_dev), str(stat.st_ino)


def _upsert_node(
    db: Session,
    root: WorkspaceRoot,
    existing: dict[str, WorkspaceFile],
    parent_id: str | None,
    relative_path: str,
    path: Path,
    is_directory: bool,
    seen_at: datetime,
    *,
    extract_content: bool = False,
    in_scope: bool = True,
) -> tuple[WorkspaceFile, bool]:
    size, modified_ns, device_id, inode = _stat_signature(path)
    modified = datetime.fromtimestamp(
        modified_ns // 1_000_000_000,
        timezone.utc,
    ).replace(microsecond=(modified_ns % 1_000_000_000) // 1_000)
    item = existing.get(relative_path)
    stored_modified = None
    if item is not None and item.modified_at is not None:
        stored_modified = (
            item.modified_at.replace(tzinfo=timezone.utc)
            if item.modified_at.tzinfo is None
            else item.modified_at.astimezone(timezone.utc)
        )
    content_changed = (
        item is None
        or item.size_bytes != size
        # Python datetime 与 SQLite 都只保留微秒。直接把数据库值换算为
        # 纳秒与 st_mtime_ns 比较，会把低于一微秒的文件系统余数误判为
        # “文件已变化”，导致每次重扫都产生假告警。
        or stored_modified != modified
        or item.status == FileIndexStatus.MISSING
    )
    scope_changed = item is not None and item.in_scope != in_scope
    changed = content_changed or scope_changed
    if item is None:
        item = WorkspaceFile(
            root_id=root.id,
            parent_id=parent_id,
            relative_path=relative_path,
            name=path.name,
            is_directory=is_directory,
            extension="" if is_directory else path.suffix.lower()[:32],
        )
        db.add(item)
        db.flush()
        existing[relative_path] = item
    item.parent_id = parent_id
    item.name = path.name
    item.is_directory = is_directory
    item.in_scope = in_scope
    item.extension = "" if is_directory else path.suffix.lower()[:32]
    item.size_bytes = 0 if is_directory else size
    item.modified_at = modified
    item.device_id = device_id
    item.inode = inode
    item.mime_type = (
        "inode/directory"
        if is_directory
        else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    if is_directory:
        item.detected_type = "inode/directory"
        item.content_status = ContentIndexStatus.METADATA_ONLY
        item.content_error_code = ""
        item.archive_member_count = 0
    item.last_seen_at = seen_at
    item.indexed_at = seen_at
    # 管理员首次勾选接入范围只是“开始纳管”，不代表原文件发生变化。
    # 只有大小、时间或缺失恢复等真实文件变化才显示“文件已变化”。
    item.status = (
        FileIndexStatus.CHANGED
        if content_changed and item.version > 1
        else FileIndexStatus.INDEXED
    )
    if changed:
        item.version += 1
        item.sha256 = None
        if not is_directory:
            # 原始文件中心只建立目录、文件名和基础属性索引。正文、OCR、压缩包
            # 内容均不读取，既避免大目录扫描拖慢业务，也不改变原文件的打开方式。
            item.extracted_text = ""
            item.ocr_text = ""
            item.content_status = ContentIndexStatus.METADATA_ONLY
            item.content_error_code = ""
            item.detected_type = item.mime_type
            item.archive_member_count = 0
        else:
            item.extracted_text = ""
            item.ocr_text = ""
    if not is_directory:
        supported_text = path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".log"}
        if extract_content and in_scope and supported_text and size <= 2 * 1024 * 1024:
            raw = path.read_bytes()[:200_000]
            try:
                item.extracted_text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                item.extracted_text = raw.decode("gb18030", errors="replace")
            item.ocr_text = ""
            item.content_status = ContentIndexStatus.INDEXED
        else:
            # 正文索引必须由目录发布人或管理员逐目录开启；关闭后重扫即清除。
            item.extracted_text = ""
            item.ocr_text = ""
            item.content_status = ContentIndexStatus.METADATA_ONLY
        item.content_error_code = ""
        item.detected_type = item.mime_type
        item.archive_member_count = 0
    return item, changed


def normalize_included_paths(values: list[str]) -> list[str]:
    """规范化目录选择，折叠已被父目录覆盖的重复项。"""

    normalized: set[str] = set()
    for raw in values:
        raw_value = str(raw or "").strip().replace("\\", "/")
        if raw_value.startswith("/") or re.match(r"^[A-Za-z]:", raw_value):
            raise ProblemException(
                422,
                "WORKSPACE_SELECTION_PATH_INVALID",
                "接入目录范围无效",
                "只能选择授权根目录内已经发现的相对文件夹。",
            )
        value = raw_value.strip("/")
        if value in {"", "."}:
            normalized.add(".")
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or len(value) > 2_048:
            raise ProblemException(
                422,
                "WORKSPACE_SELECTION_PATH_INVALID",
                "接入目录范围无效",
                "只能选择授权根目录内已经发现的相对文件夹。",
            )
        normalized.add(path.as_posix())
    if "." in normalized:
        return ["."]
    result: list[str] = []
    for value in sorted(normalized, key=lambda item: (len(PurePosixPath(item).parts), item)):
        if any(value == parent or value.startswith(f"{parent}/") for parent in result):
            continue
        result.append(value)
    return result


def path_scope_state(
    relative_path: str,
    *,
    is_directory: bool,
    selection_mode: str,
    included_paths: list[str],
) -> bool:
    """判断节点是否属于已批准范围；目录祖先保留用于安全导航。"""

    if selection_mode == "all":
        return True
    selected = normalize_included_paths(included_paths)
    if not selected:
        return False
    if "." in selected:
        return True
    normalized = relative_path.strip("/")
    if is_directory:
        return any(
            normalized == chosen
            or normalized.startswith(f"{chosen}/")
            or chosen.startswith(f"{normalized}/")
            for chosen in selected
        )
    parent = PurePosixPath(normalized).parent.as_posix()
    parent = "" if parent == "." else parent
    return any(parent == chosen or parent.startswith(f"{chosen}/") for chosen in selected)


def validate_selection_paths(
    db: Session,
    root: WorkspaceRoot,
    values: list[str],
) -> list[str]:
    normalized = normalize_included_paths(values)
    if normalized == ["."] or not normalized:
        return normalized
    known = set(
        db.scalars(
            select(WorkspaceFile.relative_path).where(
                WorkspaceFile.root_id == root.id,
                WorkspaceFile.is_directory.is_(True),
            )
        ).all()
    )
    unknown = [value for value in normalized if value not in known]
    if unknown:
        raise ProblemException(
            422,
            "WORKSPACE_SELECTION_NOT_DISCOVERED",
            "选择的文件夹尚未被系统发现",
            "请先完成目录发现扫描，再重新选择接入范围。",
            extra={"paths": unknown[:20]},
        )
    return normalized


def scan_root(db: Session, root: WorkspaceRoot) -> WorkspaceScanOut:
    """串行扫描同一个根目录，消除自动扫描与手工同步的写入竞态。"""

    with _scan_lock(root.id):
        return _scan_root_locked(db, root)


def _scan_root_locked(db: Session, root: WorkspaceRoot) -> WorkspaceScanOut:
    root_path = validate_root_path(root.absolute_path)
    existing = {
        item.relative_path: item
        for item in db.scalars(
            select(WorkspaceFile).where(WorkspaceFile.root_id == root.id)
        ).all()
    }
    seen: set[str] = set()
    directory_ids: dict[str, str | None] = {"": None}
    files = 0
    directories = 0
    changed = 0
    errors: list[str] = []
    content_indexed = 0
    metadata_only = 0
    pending_ocr = 0
    content_failed = 0
    skipped_directories = 0
    diagnostic_id = uuid.uuid4().hex[:12]
    scanned_at = utcnow()
    root.scan_status = "running"
    root.error_message = ""
    db.commit()
    pending_batch_items = 0
    try:
        def commit_scan_batch() -> None:
            """分批持久化索引，避免大型目录长期占用 SQLite 写锁。"""

            nonlocal pending_batch_items
            pending_batch_items += 1
            if pending_batch_items >= _SCAN_COMMIT_BATCH_SIZE:
                db.commit()
                pending_batch_items = 0

        def on_walk_error(exc: OSError) -> None:
            nonlocal skipped_directories
            skipped_directories += 1
            relative = "子目录"
            if exc.filename:
                try:
                    relative = Path(exc.filename).relative_to(root_path).as_posix()
                except (OSError, ValueError):
                    pass
            errors.append(f"{relative}：目录无法读取，已跳过")

        for current, dirnames, filenames in os.walk(
            root_path,
            followlinks=False,
            onerror=on_walk_error,
        ):
            current_path = Path(current)
            current_relative = (
                "" if current_path == root_path else current_path.relative_to(root_path).as_posix()
            )
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not (current_path / name).is_symlink()
            ]
            parent_id = directory_ids.get(current_relative)
            for name in dirnames:
                path = current_path / name
                relative = path.relative_to(root_path).as_posix()
                try:
                    in_scope = path_scope_state(
                        relative,
                        is_directory=True,
                        selection_mode=root.selection_mode,
                        included_paths=root.included_paths,
                    )
                    item, was_changed = _upsert_node(
                        db,
                        root,
                        existing,
                        parent_id,
                        relative,
                        path,
                        True,
                        scanned_at,
                        in_scope=in_scope,
                    )
                    directory_ids[relative] = item.id
                    seen.add(relative)
                    directories += 1
                    changed += int(was_changed)
                    commit_scan_batch()
                except OSError:
                    skipped_directories += 1
                    errors.append(f"{relative}：目录无法读取，已跳过")
            for name in sorted(filenames):
                path = current_path / name
                relative = path.relative_to(root_path).as_posix()
                if path.is_symlink():
                    errors.append(f"{relative}：已跳过符号链接")
                    continue
                try:
                    in_scope = path_scope_state(
                        relative,
                        is_directory=False,
                        selection_mode=root.selection_mode,
                        included_paths=root.included_paths,
                    )
                    with db.begin_nested():
                        item, was_changed = _upsert_node(
                            db,
                            root,
                            existing,
                            parent_id,
                            relative,
                            path,
                            False,
                            scanned_at,
                            extract_content=root.semantic_content_enabled,
                            in_scope=in_scope,
                        )
                    seen.add(relative)
                    files += int(in_scope)
                    changed += int(was_changed)
                    commit_scan_batch()
                    if not in_scope:
                        continue
                    metadata_only += 1
                except SQLAlchemyError:
                    # 数据库写入异常不能伪装成单文件问题，必须触发整批回滚。
                    raise
                except OSError:
                    errors.append(f"{relative}：文件当前不可读取，已跳过")
                except Exception:
                    # 文件被外部程序同时移动、锁定或改名时，只影响该文件。
                    logger.warning(
                        "workspace_file_metadata_failed root_id=%s relative=%s diagnostic_id=%s",
                        root.id,
                        relative,
                        diagnostic_id,
                        exc_info=True,
                    )
                    persisted = db.scalar(
                        select(WorkspaceFile).where(
                            WorkspaceFile.root_id == root.id,
                            WorkspaceFile.relative_path == relative,
                        )
                    )
                    if persisted:
                        existing[relative] = persisted
                    else:
                        existing.pop(relative, None)
                    try:
                        with db.begin_nested():
                            item, was_changed = _upsert_node(
                                db,
                                root,
                                existing,
                                parent_id,
                                relative,
                                path,
                                False,
                                scanned_at,
                                extract_content=root.semantic_content_enabled,
                                in_scope=in_scope,
                            )
                            item.content_status = ContentIndexStatus.METADATA_ONLY
                            item.content_error_code = ""
                        seen.add(relative)
                        files += int(in_scope)
                        changed += int(was_changed)
                        metadata_only += int(in_scope)
                        commit_scan_batch()
                    except SQLAlchemyError:
                        raise
                    except Exception:
                        errors.append(f"{relative}：文件当前不可读取，已跳过")
        missing = 0
        for relative, item in existing.items():
            if relative not in seen:
                item.status = FileIndexStatus.MISSING
                item.version += 1
                missing += 1
            elif not item.in_scope:
                item.extracted_text = ""
                item.ocr_text = ""
        root.scan_status = "completed" if not errors else "completed_with_errors"
        root.last_scan_at = scanned_at
        root.file_count = files
        root.directory_count = directories
        root.error_message = "；".join(errors[:10])
        # version 仅表示目录配置版本；扫描状态和索引统计是运行时数据，
        # 不应让自动扫描与管理员正在编辑的目录配置发生伪冲突。
        db.commit()
        return WorkspaceScanOut(
            root_id=root.id,
            files=files,
            directories=directories,
            changed=changed,
            missing=missing,
            content_indexed=content_indexed,
            metadata_only=metadata_only,
            pending_ocr=pending_ocr,
            content_failed=content_failed,
            skipped_directories=skipped_directories,
            diagnostic_id=diagnostic_id,
            errors=errors[:50],
        )
    except Exception:
        logger.exception(
            "workspace_scan_failed root_id=%s diagnostic_id=%s",
            root.id,
            diagnostic_id,
        )
        db.rollback()
        root = db.get(WorkspaceRoot, root.id)
        if root:
            root.scan_status = "failed"
            root.error_message = "目录扫描失败，请在系统日志中查看追踪编号。"
            db.commit()
        raise


def run_scan_job(job_id: str, root_id: str) -> None:
    with db_runtime.session_factory() as db:
        job = db.get(BackgroundJob, job_id)
        root = db.get(WorkspaceRoot, root_id)
        if not job or not root:
            return
        job.status = "running"
        job.started_at = utcnow()
        job.progress = 5
        db.commit()
        try:
            result = scan_root(db, root)
            job = db.get(BackgroundJob, job_id)
            job.status = "completed"
            job.progress = 100
            job.message = (
                f"已纳管 {result.files} 个文件、{result.directories} 个目录；"
                f"全部采用轻量属性索引，不读取文件正文"
            )
            job.payload = result.model_dump(mode="json")
            job.completed_at = utcnow()
            db.commit()
        except Exception:
            db.rollback()
            job = db.get(BackgroundJob, job_id)
            if job:
                job.status = "failed"
                job.message = "目录扫描未完成，请在系统日志中使用诊断编号查询。"
                job.completed_at = utcnow()
                db.commit()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def store_managed_path(
    db: Session,
    source: Path,
    original_name: str,
    mime_type: str,
    expected_sha256: str = "",
) -> FileBlob:
    """把已落盘文件复制进受管附件库，并在复制前后校验哈希。"""

    if not source.is_file():
        raise ProblemException(410, "SOURCE_MISSING", "源文件不存在", "请重新发起文件传输。")
    sha256 = hash_file(source)
    if expected_sha256 and sha256 != expected_sha256.lower():
        raise ProblemException(409, "HASH_MISMATCH", "文件整体校验失败", "请重新发起文件传输。")
    relative_path = f"{sha256[:2]}/{sha256}"
    destination = get_settings().attachments_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = destination.with_suffix(".incoming")
        shutil.copy2(source, temporary)
        if hash_file(temporary) != sha256:
            temporary.unlink(missing_ok=True)
            raise ProblemException(500, "FREEZE_HASH_MISMATCH", "固化校验失败", "文件在复制过程中发生变化。")
        os.replace(temporary, destination)
    blob = db.get(FileBlob, sha256)
    if not blob:
        blob = FileBlob(
            sha256=sha256,
            relative_path=relative_path,
            size_bytes=source.stat().st_size,
            mime_type=mime_type,
            original_name=original_name,
        )
        db.add(blob)
        db.flush()
    return blob


def freeze_workspace_file(
    db: Session, item: WorkspaceFile, root: WorkspaceRoot, actor: User
) -> FileBlob:
    if item.is_directory:
        raise ProblemException(422, "DIRECTORY_FREEZE_DENIED", "不能固化目录", "请选择具体文件。")
    source = resolve_workspace_path(root, item.relative_path)
    blob = store_managed_path(db, source, item.name, item.mime_type)
    sha256 = blob.sha256
    item.sha256 = sha256
    item.version += 1
    frozen = db.scalar(
        select(WorkspaceLink).where(
            WorkspaceLink.file_id == item.id,
            WorkspaceLink.entity_type == "frozen",
            WorkspaceLink.entity_id == sha256[:36],
        )
    )
    if not frozen:
        db.add(
            WorkspaceLink(
                file_id=item.id,
                entity_type="frozen",
                entity_id=sha256[:36],
                relation="frozen",
                created_by=actor.id,
            )
        )
    db.flush()
    return blob


def search_workspace_files(
    db: Session,
    keyword: str,
    root_id: str | None = None,
    limit: int = 100,
) -> list[WorkspaceFile]:
    normalized = keyword.strip()
    if not normalized:
        statement = select(WorkspaceFile)
    else:
        phrase = '"' + normalized.replace('"', '""') + '"'
        ids = [
            row[0]
            for row in db.execute(
                    text(
                        "SELECT file_id FROM workspace_name_fts "
                        "WHERE workspace_name_fts MATCH :query LIMIT :limit"
                    ),
                {"query": phrase, "limit": limit},
            ).all()
        ]
        if ids:
            # FTS 仅覆盖名称和相对路径。命中后只按主键回表，避免
            # “FTS 命中 OR LIKE”迫使 SQLite 在十万级文件上再次全表扫描。
            statement = select(WorkspaceFile).where(WorkspaceFile.id.in_(ids))
        else:
            statement = select(WorkspaceFile).where(
                or_(
                    WorkspaceFile.name.contains(normalized),
                    WorkspaceFile.relative_path.contains(normalized),
                )
            )
    if root_id:
        statement = statement.where(WorkspaceFile.root_id == root_id)
    statement = statement.where(WorkspaceFile.in_scope.is_(True))
    return list(
        db.scalars(
            statement.order_by(
                WorkspaceFile.status == FileIndexStatus.MISSING,
                WorkspaceFile.is_directory.desc(),
                WorkspaceFile.modified_at.desc(),
            ).limit(limit)
        ).all()
    )
