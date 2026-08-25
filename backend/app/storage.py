"""附件落盘、去重、版本与安全路径处理。"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import emit_event, write_audit
from .config import get_settings
from .database import db_runtime
from .enums import MaterialStage, Sensitivity, TaskStatus, UserRole
from .models import (
    ArchiveAttachment,
    AttachmentVersion,
    FileBlob,
    MaterialItem,
    Task,
    User,
)
from .problems import ProblemException
from .task_service import can_edit_task, can_manage_task
from .work_journal import record_system_entry


def _safe_original_name(filename: str | None) -> str:
    name = Path(filename or "未命名文件").name.strip()
    return name[:255] or "未命名文件"


_BLOCKED_BUSINESS_SUFFIXES = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".hta",
    ".jar",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".msp",
    ".ps1",
    ".scr",
    ".sys",
    ".vbe",
    ".vbs",
    ".wsf",
}


def safe_original_name(filename: str | None) -> str:
    """返回可显示的叶子文件名，供原子快速上传入口复用。"""

    return _safe_original_name(filename)


def normalize_client_upload_id(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,79}", normalized):
        raise ProblemException(
            422,
            "CLIENT_UPLOAD_ID_INVALID",
            "上传标识无效",
            "请重新选择文件后再试。",
        )
    return normalized


def _assert_business_filename_allowed(filename: str) -> None:
    if Path(filename).suffix.casefold() in _BLOCKED_BUSINESS_SUFFIXES:
        raise ProblemException(
            415,
            "BUSINESS_FILE_TYPE_BLOCKED",
            "该文件类型不能上传",
            "为避免误运行程序，请上传 PDF、Office、图片、压缩包或普通文本资料。",
        )


def resolve_blob_path(relative_path: str) -> Path:
    root = get_settings().attachments_dir.resolve()
    target = (root / relative_path).resolve()
    if root != target and root not in target.parents:
        raise ProblemException(400, "INVALID_FILE_PATH", "文件路径无效", "附件路径越界。")
    return target


async def save_attachment(
    db: Session,
    task: Task,
    material: MaterialItem,
    upload: UploadFile,
    stage: MaterialStage,
    is_final: bool,
    actor: User,
    note: str = "",
    ip: str = "",
    expected_task_version: int | None = None,
    client_upload_id: str | None = None,
) -> AttachmentVersion:
    normalized_upload_id = normalize_client_upload_id(client_upload_id)
    if normalized_upload_id:
        existing = db.scalar(
            select(AttachmentVersion).where(
                AttachmentVersion.client_upload_id == normalized_upload_id
            )
        )
        if existing:
            if existing.material_item_id == material.id:
                return existing
            raise ProblemException(
                409,
                "UPLOAD_ID_CONFLICT",
                "上传请求已被使用",
                "系统检测到重复的上传请求，请重新选择该文件。",
            )
    if not can_edit_task(db, task, actor):
        raise ProblemException(403, "MATERIAL_EDIT_DENIED", "无权上传", "你不是该事项参与人。")
    if task.sensitivity == Sensitivity.RESTRICTED and not task.allow_sensitive_content:
        raise ProblemException(
            403,
            "RESTRICTED_ATTACHMENT_DISABLED",
            "敏感附件尚未授权",
            "此事项默认不保存敏感附件。",
        )
    if expected_task_version is not None and task.version != expected_task_version:
        raise ProblemException(
            409,
            "VERSION_CONFLICT",
            "事项已被他人更新",
            "请刷新材料目录后重新上传。",
            extra={
                "current_version": task.version,
                "submitted_version": expected_task_version,
            },
        )
    if task.status == TaskStatus.ARCHIVED:
        raise ProblemException(
            409,
            "TASK_ARCHIVED_IMMUTABLE",
            "归档事项不能修改材料",
            "如确需补充，请由主办人说明原因并重新打开事项。",
        )
    existing_final_id = db.scalar(
        select(AttachmentVersion.id)
        .where(
            AttachmentVersion.material_item_id == material.id,
            AttachmentVersion.is_final.is_(True),
        )
        .limit(1)
    )
    if existing_final_id:
        raise ProblemException(
            409,
            "FINAL_VERSION_LOCKED",
            "最终版本已锁定",
            "该材料已有最终版本，系统不会静默替换；如需更正，请由管理员按更正流程处理。",
        )
    if is_final and stage != MaterialStage.SUBMITTED:
        raise ProblemException(
            422,
            "FINAL_STAGE_INVALID",
            "最终版本阶段不正确",
            "只有“实际报送稿”才能确认为最终版本。",
        )
    settings = get_settings()
    maximum = settings.max_upload_mb * 1024 * 1024
    original_name = _safe_original_name(upload.filename)
    _assert_business_filename_allowed(original_name)
    incoming = settings.attachments_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="upload-", dir=incoming)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ProblemException(
                        413,
                        "FILE_TOO_LARGE",
                        "附件过大",
                        f"单个附件不得超过 {settings.max_upload_mb} MB。",
                    )
                digest.update(chunk)
                handle.write(chunk)
        sha256 = digest.hexdigest()
        if size == 0:
            raise ProblemException(422, "EMPTY_FILE", "文件内容为空", "请选择包含实际内容的文件。")
        relative_path = f"{sha256[:2]}/{sha256}"
        final_path = resolve_blob_path(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if not final_path.exists():
            os.replace(temporary_name, final_path)
            temporary_name = ""
        mime = upload.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        with db_runtime.write_lock:
            # 与事项流转及其他上传共用进程写锁，并在落库前重新校验，
            # 防止并发请求同时形成两个“最终版本”。
            db.refresh(task)
            if task.status == TaskStatus.ARCHIVED:
                raise ProblemException(
                    409,
                    "TASK_ARCHIVED_IMMUTABLE",
                    "归档事项不能修改材料",
                    "如确需补充，请由主办人说明原因并重新打开事项。",
                )
            existing_final_id = db.scalar(
                select(AttachmentVersion.id)
                .where(
                    AttachmentVersion.material_item_id == material.id,
                    AttachmentVersion.is_final.is_(True),
                )
                .limit(1)
            )
            if existing_final_id:
                raise ProblemException(
                    409,
                    "FINAL_VERSION_LOCKED",
                    "最终版本已锁定",
                    "该材料已有最终版本，系统不会静默替换；如需更正，请由管理员按更正流程处理。",
                )
            if normalized_upload_id:
                existing = db.scalar(
                    select(AttachmentVersion).where(
                        AttachmentVersion.client_upload_id == normalized_upload_id
                    )
                )
                if existing:
                    if existing.material_item_id == material.id:
                        return existing
                    raise ProblemException(
                        409,
                        "UPLOAD_ID_CONFLICT",
                        "上传请求已被使用",
                        "系统检测到重复的上传请求，请重新选择该文件。",
                    )
            blob = db.get(FileBlob, sha256)
            if not blob:
                blob = FileBlob(
                    sha256=sha256,
                    relative_path=relative_path,
                    size_bytes=size,
                    mime_type=mime,
                    original_name=original_name,
                )
                db.add(blob)
                # AttachmentVersion 仅保存哈希外键，没有 ORM relationship；
                # 显式刷新可保证 SQLite 在插入版本前已存在文件实体。
                db.flush()
            latest = db.scalar(
                select(func.max(AttachmentVersion.version_no)).where(
                    AttachmentVersion.material_item_id == material.id
                )
            )
            version = AttachmentVersion(
                material_item_id=material.id,
                blob_sha256=sha256,
                version_no=(latest or 0) + 1,
                stage=stage,
                is_final=is_final,
                uploaded_by=actor.id,
                note=note,
                display_name=original_name,
                client_upload_id=normalized_upload_id,
            )
            db.add(version)
            db.flush()
            task.version += 1
            task.updated_by = actor.id
            write_audit(
                db,
                actor,
                "attachment.upload",
                "attachment",
                version.id,
                {
                    "task_id": task.id,
                    "material_id": material.id,
                    "sha256": sha256,
                    "is_final": is_final,
                    "name": original_name,
                },
                ip,
            )
            record_system_entry(
                db,
                actor,
                f"上传材料：{original_name}",
                f"关联事项：{task.title}"
                + ("；已确认为最终稿" if is_final else ""),
                task_id=task.id,
                event_code="material.finalized" if is_final else "material.uploaded",
                event_data={
                    "material_stage": stage.value,
                    "is_final": is_final,
                    "filename": original_name,
                },
            )
            emit_event(
                db,
                "attachment.added",
                task.id,
                {"material_id": material.id, "is_final": is_final},
            )
            db.commit()
            db.refresh(version)
            return version
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _require_attachment_reason(reason: str) -> str:
    normalized = reason.strip()
    if len(normalized) < 2:
        raise ProblemException(
            422,
            "ATTACHMENT_DELETE_REASON_REQUIRED",
            "需要填写删除原因",
            "请至少填写两个字，方便日后审计和恢复。",
        )
    return normalized[:2_000]


def _task_allows_attachment_recovery(task: Task, actor: User) -> None:
    if task.status in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED} and actor.role != UserRole.ADMIN:
        raise ProblemException(
            409,
            "TASK_REOPEN_REQUIRED",
            "事项已办结，不能直接修改材料",
            "请由主办人重新打开事项，或联系管理员处理。",
        )


def delete_attachment_version(
    db: Session,
    task: Task,
    material: MaterialItem,
    version: AttachmentVersion,
    actor: User,
    reason: str,
    *,
    expected_task_version: int,
    ip: str = "",
) -> AttachmentVersion:
    normalized_reason = _require_attachment_reason(reason)
    _task_allows_attachment_recovery(task, actor)
    manager = can_manage_task(db, task, actor)
    if not manager and not (
        can_edit_task(db, task, actor)
        and version.uploaded_by == actor.id
        and not version.is_final
    ):
        raise ProblemException(
            403,
            "ATTACHMENT_DELETE_DENIED",
            "无权删除该文件",
            "上传人只能删除自己的非终稿；终稿由事项负责人或管理员处理。",
        )
    if task.version != expected_task_version:
        raise ProblemException(409, "VERSION_CONFLICT", "事项已被他人更新", "请刷新后再删除。")
    if version.deleted_at is not None:
        return version
    with db_runtime.write_lock:
        db.refresh(task)
        db.refresh(version)
        if task.version != expected_task_version:
            raise ProblemException(409, "VERSION_CONFLICT", "事项已被他人更新", "请刷新后再删除。")
        if version.deleted_at is not None:
            return version
        was_final = bool(version.is_final)
        version.deleted_was_final = was_final
        version.is_final = False
        version.deleted_at = datetime.now(timezone.utc)
        version.deleted_by = actor.id
        version.delete_reason = normalized_reason
        version.purge_after = version.deleted_at + timedelta(
            days=get_settings().deleted_attachment_retention_days
        )
        material.version += 1
        task.version += 1
        task.updated_by = actor.id
        write_audit(
            db,
            actor,
            "attachment.delete",
            "attachment",
            version.id,
            {
                "task_id": task.id,
                "material_id": material.id,
                "was_final": was_final,
                "reason": normalized_reason,
                "purge_after": version.purge_after.isoformat(),
            },
            ip,
        )
        record_system_entry(
            db,
            actor,
            f"移入回收站：{version.display_name or material.name}",
            f"关联事项：{task.title}；保留 {get_settings().deleted_attachment_retention_days} 天。",
            task_id=task.id,
            event_code="material.deleted",
            event_data={"material_id": material.id, "version_id": version.id},
        )
        emit_event(db, "attachment.deleted", task.id, {"version_id": version.id})
        db.commit()
        db.refresh(version)
        return version


def restore_attachment_version(
    db: Session,
    task: Task,
    material: MaterialItem,
    version: AttachmentVersion,
    actor: User,
    *,
    expected_task_version: int,
    ip: str = "",
) -> AttachmentVersion:
    _task_allows_attachment_recovery(task, actor)
    manager = can_manage_task(db, task, actor)
    if not manager and not (
        can_edit_task(db, task, actor) and version.uploaded_by == actor.id
    ):
        raise ProblemException(403, "ATTACHMENT_RESTORE_DENIED", "无权恢复该文件", "请联系事项负责人。")
    if version.deleted_at is None:
        return version
    if task.version != expected_task_version:
        raise ProblemException(409, "VERSION_CONFLICT", "事项已被他人更新", "请刷新后再恢复。")
    with db_runtime.write_lock:
        db.refresh(task)
        db.refresh(version)
        if task.version != expected_task_version:
            raise ProblemException(409, "VERSION_CONFLICT", "事项已被他人更新", "请刷新后再恢复。")
        if version.deleted_at is None:
            return version
        restore_final = bool(version.deleted_was_final) and db.scalar(
            select(AttachmentVersion.id)
            .where(
                AttachmentVersion.material_item_id == material.id,
                AttachmentVersion.deleted_at.is_(None),
                AttachmentVersion.is_final.is_(True),
            )
            .limit(1)
        ) is None
        version.is_final = restore_final
        version.deleted_at = None
        version.deleted_by = None
        version.delete_reason = ""
        version.purge_after = None
        version.deleted_was_final = False
        material.version += 1
        task.version += 1
        task.updated_by = actor.id
        write_audit(
            db,
            actor,
            "attachment.restore",
            "attachment",
            version.id,
            {
                "task_id": task.id,
                "material_id": material.id,
                "restored_as_final": restore_final,
            },
            ip,
        )
        record_system_entry(
            db,
            actor,
            f"恢复文件：{version.display_name or material.name}",
            f"关联事项：{task.title}" + ("；已恢复为终稿" if restore_final else ""),
            task_id=task.id,
            event_code="material.restored",
            event_data={"material_id": material.id, "version_id": version.id},
        )
        emit_event(db, "attachment.restored", task.id, {"version_id": version.id})
        db.commit()
        db.refresh(version)
        return version


def purge_expired_deleted_attachments(
    db: Session, *, now: datetime | None = None
) -> dict[str, int]:
    """清理超过保留期的逻辑附件；被其他记录引用的 Blob 永不误删。"""

    current = now or datetime.now(timezone.utc)
    task_rows = list(
        db.scalars(
            select(AttachmentVersion).where(
                AttachmentVersion.deleted_at.is_not(None),
                AttachmentVersion.purge_after.is_not(None),
                AttachmentVersion.purge_after <= current,
            )
        ).all()
    )
    archive_rows = list(
        db.scalars(
            select(ArchiveAttachment).where(
                ArchiveAttachment.deleted_at.is_not(None),
                ArchiveAttachment.purge_after.is_not(None),
                ArchiveAttachment.purge_after <= current,
            )
        ).all()
    )
    candidate_hashes = {
        *(row.blob_sha256 for row in task_rows),
        *(row.blob_sha256 for row in archive_rows),
    }
    for row in [*task_rows, *archive_rows]:
        db.delete(row)
    db.flush()
    purged_blobs = 0
    for sha256 in candidate_hashes:
        task_reference = db.scalar(
            select(AttachmentVersion.id)
            .where(AttachmentVersion.blob_sha256 == sha256)
            .limit(1)
        )
        archive_reference = db.scalar(
            select(ArchiveAttachment.id)
            .where(ArchiveAttachment.blob_sha256 == sha256)
            .limit(1)
        )
        if task_reference or archive_reference:
            continue
        blob = db.get(FileBlob, sha256)
        if not blob:
            continue
        path = resolve_blob_path(blob.relative_path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # 文件仍被系统扫描器或备份程序占用时保留元数据，下次周期重试。
            continue
        db.delete(blob)
        purged_blobs += 1
    db.commit()
    return {
        "task_versions": len(task_rows),
        "archive_attachments": len(archive_rows),
        "blobs": purged_blobs,
    }


def rollback_attachment_version(
    db: Session,
    task: Task,
    material: MaterialItem,
    target: AttachmentVersion,
    actor: User,
    reason: str,
    *,
    ip: str = "",
    expected_task_version: int,
) -> AttachmentVersion:
    """引用旧 Blob 创建新终稿；保留全部历史，不复制、不覆盖原文件。"""

    normalized_reason = reason.strip()
    if len(normalized_reason) < 2:
        raise ProblemException(422, "ROLLBACK_REASON_REQUIRED", "需要填写回退原因", "请至少填写两个字。")
    if not can_manage_task(db, task, actor):
        raise ProblemException(403, "MATERIAL_ROLLBACK_DENIED", "无权回退材料", "仅事项主办人、创建人或管理员可以回退。")
    if task.version != expected_task_version:
        raise ProblemException(
            409,
            "VERSION_CONFLICT",
            "事项已被他人更新",
            "请刷新材料目录后重新确认回退版本。",
            extra={"current_version": task.version, "submitted_version": expected_task_version},
        )
    if task.status == TaskStatus.ARCHIVED:
        raise ProblemException(
            409,
            "TASK_ARCHIVED_IMMUTABLE",
            "归档事项不能回退材料",
            "请先说明原因并重新打开事项，再执行版本回退。",
        )
    if target.material_item_id != material.id:
        raise ProblemException(404, "ATTACHMENT_NOT_FOUND", "材料版本不存在", "所选版本不属于当前材料项。")
    blob = db.get(FileBlob, target.blob_sha256)
    if not blob or not resolve_blob_path(blob.relative_path).is_file():
        raise ProblemException(410, "ATTACHMENT_MISSING", "旧版附件文件缺失", "请先从备份恢复该版本，再执行回退。")

    with db_runtime.write_lock:
        db.refresh(task)
        db.refresh(target)
        if task.version != expected_task_version:
            raise ProblemException(
                409,
                "VERSION_CONFLICT",
                "事项已被他人更新",
                "请刷新材料目录后重新确认回退版本。",
                extra={"current_version": task.version, "submitted_version": expected_task_version},
            )
        if task.status == TaskStatus.ARCHIVED:
            raise ProblemException(
                409,
                "TASK_ARCHIVED_IMMUTABLE",
                "归档事项不能回退材料",
                "请先说明原因并重新打开事项，再执行版本回退。",
            )
        current_final = db.scalar(
            select(AttachmentVersion)
            .where(
                AttachmentVersion.material_item_id == material.id,
                AttachmentVersion.is_final.is_(True),
            )
            .limit(1)
        )
        if current_final and current_final.id == target.id:
            raise ProblemException(409, "ATTACHMENT_ALREADY_FINAL", "所选版本已是最终版", "请选择更早的版本。")
        latest = db.scalar(
            select(func.max(AttachmentVersion.version_no)).where(
                AttachmentVersion.material_item_id == material.id
            )
        ) or 0
        if current_final:
            current_final.is_final = False
            db.flush()
        restored = AttachmentVersion(
            material_item_id=material.id,
            blob_sha256=target.blob_sha256,
            version_no=latest + 1,
            stage=MaterialStage.SUBMITTED,
            is_final=True,
            uploaded_by=actor.id,
            note=f"回退至 v{target.version_no}：{normalized_reason}",
            display_name=target.display_name or blob.original_name,
        )
        db.add(restored)
        material.version += 1
        task.version += 1
        task.updated_by = actor.id
        db.flush()
        write_audit(
            db,
            actor,
            "attachment.rollback",
            "attachment",
            restored.id,
            {
                "task_id": task.id,
                "material_id": material.id,
                "target_version_id": target.id,
                "target_version_no": target.version_no,
                "previous_final_id": current_final.id if current_final else None,
                "new_version_no": restored.version_no,
                "reason": normalized_reason,
            },
            ip,
        )
        record_system_entry(
            db,
            actor,
            f"回退材料：{material.name}",
            f"引用 v{target.version_no} 形成新终稿 v{restored.version_no}；原因：{normalized_reason}",
            task_id=task.id,
            event_code="material.rolled_back",
            event_data={
                "material_id": material.id,
                "target_version_no": target.version_no,
                "new_version_no": restored.version_no,
            },
        )
        emit_event(
            db,
            "attachment.rolled_back",
            task.id,
            {
                "material_id": material.id,
                "target_version_id": target.id,
                "new_version_id": restored.id,
            },
        )
        db.commit()
        db.refresh(restored)
        return restored
