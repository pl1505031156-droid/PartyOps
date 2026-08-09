"""附件落盘、去重、版本与安全路径处理。"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import emit_event, write_audit
from .config import get_settings
from .database import db_runtime
from .enums import MaterialStage, Sensitivity, TaskStatus
from .models import AttachmentVersion, FileBlob, MaterialItem, Task, User
from .problems import ProblemException
from .task_service import can_edit_task
from .work_journal import record_system_entry


def _safe_original_name(filename: str | None) -> str:
    name = Path(filename or "未命名文件").name.strip()
    return name[:255] or "未命名文件"


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
) -> AttachmentVersion:
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
