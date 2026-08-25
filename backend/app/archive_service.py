"""重要档案中心的校验、权限、扫描件和全文索引服务。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .database import db_runtime
from .enums import (
    ArchiveAccessMode,
    ArchiveAttachmentStatus,
    ArchiveRecordMode,
    UserRole,
)
from .intake import extract_path_text
from .models import (
    ArchiveAccessGrant,
    ArchiveAttachment,
    ArchiveCategory,
    ArchiveRecord,
    FileBlob,
    User,
)
from .problems import ProblemException
from .storage import normalize_client_upload_id, resolve_blob_path

ALLOWED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".docx",
    ".xlsx",
    ".xlsm",
    ".txt",
    ".csv",
}
BLOCKED_SUFFIXES = {
    ".sh",
    ".desktop",
    ".so",
    ".deb",
    ".appimage",
    ".exe",
    ".bat",
    ".cmd",
    ".dll",
    ".js",
    ".py",
}


def safe_archive_name(value: str, maximum: int = 100) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return (cleaned[:maximum] or "未命名档案")


def category_for_record(db: Session, record: ArchiveRecord) -> ArchiveCategory:
    category = db.get(ArchiveCategory, record.category_id)
    if not category or not category.active:
        raise ProblemException(
            410, "ARCHIVE_CATEGORY_DISABLED", "档案类别已停用", "请联系管理员。"
        )
    return category


def archive_permissions(
    db: Session,
    category: ArchiveCategory,
    user: User,
    device_id: str | None = None,
) -> dict[str, bool]:
    """返回当前人员与设备上下文下的档案能力。

    设备授权与人员授权取并集；`allow_device_access` 关闭时，普通人员从
    协同电脑进入将看不到该类别。管理员仍可在任意已纳管电脑上处理治理
    工作，避免错误配置后无法恢复。
    """

    if user.role == UserRole.ADMIN:
        return {
            "view": True,
            "download": True,
            "contribute": True,
            "manage": True,
            "void": True,
        }
    denied = {
        "view": False,
        "download": False,
        "contribute": False,
        "manage": False,
        "void": False,
    }
    if device_id and not category.allow_device_access:
        return denied
    if category.access_mode == ArchiveAccessMode.ADMINS_ONLY:
        return denied
    if category.access_mode == ArchiveAccessMode.ALL_USERS:
        return {
            "view": True,
            "download": True,
            "contribute": True,
            "manage": False,
            "void": False,
        }
    targets = [ArchiveAccessGrant.user_id == user.id]
    if device_id:
        targets.append(ArchiveAccessGrant.device_id == device_id)
    grants = db.scalars(
        select(ArchiveAccessGrant).where(
            ArchiveAccessGrant.category_id == category.id,
            ArchiveAccessGrant.active.is_(True),
            or_(*targets),
        )
    ).all()
    can_view = any(item.can_view for item in grants)
    return {
        "view": can_view,
        "download": can_view and any(item.can_download for item in grants),
        "contribute": can_view and any(item.can_contribute for item in grants),
        "manage": False,
        "void": False,
    }


def can_view_category(
    db: Session,
    category: ArchiveCategory,
    user: User,
    device_id: str | None = None,
) -> bool:
    return archive_permissions(db, category, user, device_id)["view"]


def can_download_category(
    db: Session,
    category: ArchiveCategory,
    user: User,
    device_id: str | None = None,
) -> bool:
    return archive_permissions(db, category, user, device_id)["download"]


def can_contribute_category(
    db: Session,
    category: ArchiveCategory,
    user: User,
    device_id: str | None = None,
) -> bool:
    return archive_permissions(db, category, user, device_id)["contribute"]


def validate_custom_fields(
    category: ArchiveCategory,
    values: dict[str, Any],
    *,
    legacy_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definitions = {
        str(item.get("key")): item
        for item in (category.field_schema or [])
        if item.get("key")
    }
    legacy = legacy_values or {}
    unknown = sorted(
        key for key in set(values) - set(definitions) if key not in legacy
    )
    if unknown:
        raise ProblemException(
            422,
            "ARCHIVE_FIELD_UNKNOWN",
            "存在未定义字段",
            "请刷新档案类别模板后重新保存。",
            fields={
                f"custom_fields.{key}": "该字段已不在当前档案类别模板中"
                for key in unknown
            },
        )
    # 管理员调整模板不能把既有档案锁死。已存在于旧记录、但已从模板
    # 删除的键只读保留；新建或手工注入的未知键仍按错误拒绝。
    normalized: dict[str, Any] = {
        key: values[key]
        for key in set(values) - set(definitions)
        if key in legacy
    }
    for key, definition in definitions.items():
        value = values.get(key)
        if value in (None, ""):
            if definition.get("required"):
                raise ProblemException(
                    422,
                    "ARCHIVE_FIELD_REQUIRED",
                    "档案字段未填写",
                    f"请填写“{definition.get('label', key)}”。",
                    fields={f"custom_fields.{key}": "此项为必填字段"},
                )
            continue
        field_type = definition.get("type", "text")
        if field_type == "number":
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ProblemException(
                    422,
                    "ARCHIVE_FIELD_INVALID",
                    "档案字段格式不正确",
                    f"“{definition.get('label', key)}”必须是数字。",
                    fields={f"custom_fields.{key}": "请输入有效数字"},
                ) from exc
        elif field_type == "select":
            options = [str(item) for item in definition.get("options", [])]
            if str(value) not in options:
                raise ProblemException(
                    422,
                    "ARCHIVE_FIELD_INVALID",
                    "档案字段选项无效",
                    f"“{definition.get('label', key)}”不是允许的选项。",
                    fields={f"custom_fields.{key}": "请选择类别定义中的有效选项"},
                    extra={"options": options},
                )
        elif field_type == "date":
            value = str(value).strip()
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ProblemException(
                    422,
                    "ARCHIVE_FIELD_INVALID",
                    "档案字段日期不正确",
                    f"“{definition.get('label', key)}”必须使用有效日期。",
                    fields={f"custom_fields.{key}": "请选择有效日期"},
                ) from exc
        elif field_type in {"text", "textarea"}:
            value = str(value).strip()
        normalized[key] = value
    return normalized


def validate_record_mode(category: ArchiveCategory, values: dict[str, Any]) -> None:
    if category.record_mode == ArchiveRecordMode.PERSON_YEAR and not values.get(
        "person_name"
    ):
        raise ProblemException(
            422,
            "ARCHIVE_PERSON_REQUIRED",
            "缺少人员姓名",
            "一人一档类别必须填写人员姓名。",
            fields={"person_name": "一人一档类别必须填写人员姓名"},
        )
    for definition in category.field_schema or []:
        key = str(definition.get("key", ""))
        if key != "assessment_result":
            continue
        value = str(values.get(key, "") or "").strip()
        if not value and definition.get("required"):
            raise ProblemException(
                422,
                "ARCHIVE_FIELD_REQUIRED",
                "档案字段未填写",
                f"请填写“{definition.get('label', key)}”。",
                fields={"assessment_result": "此项为必填字段"},
            )
        options = [str(item) for item in definition.get("options", [])]
        if value and definition.get("type") == "select" and value not in options:
            raise ProblemException(
                422,
                "ARCHIVE_FIELD_INVALID",
                "档案字段选项无效",
                f"“{definition.get('label', key)}”不是允许的选项。",
                fields={"assessment_result": "请选择类别定义中的有效选项"},
                extra={"options": options},
            )


def next_sequence(
    db: Session,
    category_id: str,
    archive_year: int,
    requested: int | None,
) -> int:
    if requested is not None:
        return requested
    current = db.scalar(
        select(func.max(ArchiveRecord.sequence_no)).where(
            ArchiveRecord.category_id == category_id,
            ArchiveRecord.archive_year == archive_year,
        )
    )
    return int(current or 0) + 1


def record_snapshot(record: ArchiveRecord) -> dict[str, Any]:
    return {
        "category_id": record.category_id,
        "archive_year": record.archive_year,
        "sequence_no": record.sequence_no,
        "document_no": record.document_no,
        "title": record.title,
        "summary": record.summary,
        "involved_persons": record.involved_persons or [],
        "source_unit": record.source_unit,
        "document_date": record.document_date.isoformat()
        if record.document_date
        else None,
        "person_name": record.person_name,
        "person_identifier": record.person_identifier,
        "personnel_type": record.personnel_type,
        "organization": record.organization,
        "assessment_result": record.assessment_result,
        "tags": record.tags or [],
        "custom_fields": record.custom_fields or {},
        "status": record.status.value,
        "void_reason": record.void_reason,
        "version": record.version,
    }


def refresh_search_index(db: Session, record_id: str) -> None:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        return
    category = db.get(ArchiveCategory, record.category_id)
    attachment_names = db.scalars(
        select(ArchiveAttachment.display_name).where(
            ArchiveAttachment.record_id == record.id,
            ArchiveAttachment.status != ArchiveAttachmentStatus.VOIDED,
        )
    ).all()
    attachment_text = db.scalars(
        select(ArchiveAttachment.ocr_text).where(
            ArchiveAttachment.record_id == record.id,
            ArchiveAttachment.status != ArchiveAttachmentStatus.VOIDED,
        )
    ).all()
    record.search_text = " ".join(
        [
            category.name if category else "",
            record.document_no,
            record.title,
            record.summary,
            " ".join(record.involved_persons or []),
            record.source_unit,
            record.person_name,
            record.person_identifier,
            record.personnel_type,
            record.organization,
            record.assessment_result,
            " ".join(record.tags or []),
            json.dumps(record.custom_fields or {}, ensure_ascii=False),
            " ".join(attachment_names),
            " ".join(attachment_text),
        ]
    )[:500_000]
    db.execute(
        text("DELETE FROM archive_search_fts WHERE record_id = :record_id"),
        {"record_id": record.id},
    )
    db.execute(
        text(
            """
            INSERT INTO archive_search_fts(record_id, title, document_no, body)
            VALUES (:record_id, :title, :document_no, :body)
            """
        ),
        {
            "record_id": record.id,
            "title": record.title,
            "document_no": record.document_no,
            "body": record.search_text,
        },
    )


def archive_attachment_path(db: Session, attachment_id: str) -> tuple[ArchiveAttachment, FileBlob, Path]:
    row = db.execute(
        select(ArchiveAttachment, FileBlob)
        .join(FileBlob, FileBlob.sha256 == ArchiveAttachment.blob_sha256)
        .where(ArchiveAttachment.id == attachment_id)
    ).one_or_none()
    if not row:
        raise ProblemException(
            404, "ARCHIVE_ATTACHMENT_NOT_FOUND", "扫描件不存在", "未找到档案扫描件。"
        )
    attachment, blob = row
    path = resolve_blob_path(blob.relative_path)
    return attachment, blob, path


async def save_archive_upload(
    db: Session,
    record: ArchiveRecord,
    upload: UploadFile,
    actor: User,
    note: str = "",
    client_upload_id: str | None = None,
) -> ArchiveAttachment:
    normalized_upload_id = normalize_client_upload_id(client_upload_id)
    if normalized_upload_id:
        existing = db.scalar(
            select(ArchiveAttachment).where(
                ArchiveAttachment.client_upload_id == normalized_upload_id
            )
        )
        if existing:
            if existing.record_id == record.id:
                return existing
            raise ProblemException(
                409,
                "UPLOAD_ID_CONFLICT",
                "上传请求已被使用",
                "请重新选择该文件后再试。",
            )
    original_name = safe_archive_name(Path(upload.filename or "未命名文件").name, 255)
    suffix = Path(original_name).suffix.lower()
    if suffix in BLOCKED_SUFFIXES or suffix not in ALLOWED_SUFFIXES:
        raise ProblemException(
            415,
            "ARCHIVE_FILE_TYPE_NOT_ALLOWED",
            "扫描件格式不支持",
            "请上传 PDF、图片、Word、Excel 或文本文件。",
        )
    settings = get_settings()
    maximum = settings.max_upload_mb * 1024 * 1024
    incoming = settings.attachments_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="archive-upload-", dir=incoming)
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
                        "扫描件过大",
                        f"单个文件不得超过 {settings.max_upload_mb} MB。",
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
        mime = (
            upload.content_type
            or mimetypes.guess_type(original_name)[0]
            or "application/octet-stream"
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
            db.flush()
        latest = db.scalar(
            select(func.max(ArchiveAttachment.version_no)).where(
                ArchiveAttachment.record_id == record.id
            )
        )
        attachment = ArchiveAttachment(
            record_id=record.id,
            blob_sha256=sha256,
            version_no=int(latest or 0) + 1,
            display_name=original_name,
            note=note[:2_000],
            uploaded_by=actor.id,
            client_upload_id=normalized_upload_id,
        )
        db.add(attachment)
        db.flush()
        return attachment
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def index_archive_attachment(attachment_id: str) -> None:
    """后台读取 PDF/图片/Office 正文，并刷新档案全文索引。"""

    with db_runtime.session_factory() as db:
        attachment, blob, path = archive_attachment_path(db, attachment_id)
        if attachment.deleted_at is not None:
            return
        if not path.exists():
            attachment.status = ArchiveAttachmentStatus.OCR_ERROR
            attachment.ocr_text = ""
            db.commit()
            return
        extracted, warnings = extract_path_text(path, original_name=attachment.display_name)
        attachment.ocr_text = extracted[:200_000]
        attachment.status = (
            ArchiveAttachmentStatus.OCR_ERROR
            if warnings and not extracted.strip()
            else ArchiveAttachmentStatus.INDEXED
        )
        refresh_search_index(db, attachment.record_id)
        db.commit()


def fts_search(db: Session, keyword: str, limit: int = 100) -> list[str]:
    normalized = " ".join(part for part in re.split(r"\s+", keyword.strip()) if part)
    if not normalized:
        return []
    # FTS5 操作符和引号来自用户输入时不作为查询语法执行，统一按短语匹配。
    query = '"' + normalized.replace('"', '""') + '"'
    rows = db.execute(
        text(
            """
            SELECT record_id FROM archive_search_fts
            WHERE archive_search_fts MATCH :query
            LIMIT :limit
            """
        ),
        {"query": query, "limit": limit},
    ).all()
    return [str(row[0]) for row in rows]
