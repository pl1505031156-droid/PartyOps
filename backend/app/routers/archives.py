"""重要档案中心 API。

档案记录和扫描件与任务材料分开建模，但复用同一受管附件库、全文检索、
审计和备份链路。档案正文默认由管理员录入和修订，协同人员按类别权限
查看或下载；任何删除都以作废和历史留痕替代。
"""

from __future__ import annotations

import typing

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..archive_exporting import export_archive_package
from ..archive_service import (
    archive_permissions,
    archive_attachment_path,
    can_contribute_category,
    can_download_category,
    can_view_category,
    category_for_record,
    fts_search,
    index_archive_attachment,
    next_sequence,
    record_snapshot,
    refresh_search_index,
    save_archive_upload,
    safe_archive_name,
    validate_custom_fields,
    validate_record_mode,
)
from ..audit import emit_event, write_audit
from ..config import get_settings
from ..database import db_runtime, get_session
from ..device_versions import request_device
from ..enums import ArchiveAccessMode, ArchiveAttachmentStatus, ArchiveRecordMode, ArchiveRecordStatus, UserRole
from ..models import (
    ArchiveAccessGrant,
    ArchiveAttachment,
    ArchiveCategory,
    ArchiveLink,
    ArchiveRecord,
    ArchiveRecordRevision,
    Device,
    FileBlob,
    KnowledgeEntry,
    PeriodReport,
    Task,
    User,
    WorkJournalEntry,
    utcnow,
)
from ..problems import ProblemException
from ..schemas import (
    ArchiveAccessGrantCreate,
    ArchiveAccessGrantOut,
    ArchiveAccessGrantPatch,
    ArchiveAccessPatch,
    ArchiveAction,
    ArchiveAttachmentOut,
    ArchiveCategoryCreate,
    ArchiveCategoryOut,
    ArchiveCategoryPatch,
    ArchiveLinkCreate,
    ArchiveRecordCreate,
    ArchiveRecordOut,
    ArchiveRecordPatch,
    ArchiveRevisionOut,
)
from ..security import get_current_user, require_admin
from ..storage import normalize_client_upload_id


router = APIRouter(tags=["important-archives"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def _request_device_id(request: Request, db: Session) -> str | None:
    device = request_device(request, db)
    return device.id if device else None


def _category(
    db: Session,
    category_id: str,
    user: User,
    *,
    include_inactive: bool = False,
    device_id: str | None = None,
) -> ArchiveCategory:
    category = db.get(ArchiveCategory, category_id)
    if not category or (not include_inactive and not category.active):
        raise ProblemException(404, "ARCHIVE_CATEGORY_NOT_FOUND", "档案类别不存在", "未找到可用档案类别。")
    if user.role.value != "admin" and not can_view_category(
        db, category, user, device_id
    ):
        raise ProblemException(403, "ARCHIVE_ACCESS_DENIED", "无权查看该档案类别", "请联系管理员开通类别权限。")
    return category


def _category_out(
    db: Session,
    category: ArchiveCategory,
    user: User,
    device_id: str | None = None,
) -> ArchiveCategoryOut:
    return ArchiveCategoryOut.model_validate(category).model_copy(
        update={"permissions": archive_permissions(db, category, user, device_id)}
    )


def _validate_field_schema(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("key", "")).strip()
        if not key or key in seen:
            raise ProblemException(422, "ARCHIVE_FIELD_SCHEMA_INVALID", "字段模板无效", "字段键不能为空且不能重复。")
        seen.add(key)
        field_type = str(item.get("type", "text"))
        options = [str(value).strip() for value in item.get("options", []) if str(value).strip()]
        if field_type == "select" and not options:
            raise ProblemException(422, "ARCHIVE_FIELD_SCHEMA_INVALID", "字段模板无效", "下拉字段至少需要一个选项。")
        normalized.append(
            {
                "key": key,
                "label": str(item.get("label", key)).strip()[:80],
                "type": field_type,
                "required": bool(item.get("required", False)),
                "options": list(dict.fromkeys(options))[:50],
            }
        )
    return normalized


def _attachment_out(db: Session, attachment: ArchiveAttachment) -> ArchiveAttachmentOut:
    blob = db.get(FileBlob, attachment.blob_sha256)
    if not blob:
        raise ProblemException(410, "ARCHIVE_ATTACHMENT_MISSING", "扫描件已缺失", "受管附件实体不存在。")
    return ArchiveAttachmentOut(
        id=attachment.id,
        record_id=attachment.record_id,
        blob_sha256=attachment.blob_sha256,
        version_no=attachment.version_no,
        display_name=attachment.display_name,
        note=attachment.note,
        status=attachment.status,
        ocr_text=attachment.ocr_text,
        uploaded_by=attachment.uploaded_by,
        size_bytes=blob.size_bytes,
        mime_type=blob.mime_type,
        created_at=attachment.created_at,
        updated_at=attachment.updated_at,
        deleted_at=attachment.deleted_at,
        deleted_by=attachment.deleted_by,
        delete_reason=attachment.delete_reason,
        purge_after=attachment.purge_after,
    )


def _duplicate_warnings(
    db: Session,
    record: ArchiveRecord,
    category: ArchiveCategory,
) -> list[str]:
    if category.record_mode != ArchiveRecordMode.PERSON_YEAR or not record.person_name:
        return []
    statement = select(ArchiveRecord).where(
        ArchiveRecord.id != record.id,
        ArchiveRecord.category_id == record.category_id,
        ArchiveRecord.archive_year == record.archive_year,
        ArchiveRecord.status != ArchiveRecordStatus.VOIDED,
        ArchiveRecord.person_name == record.person_name,
    )
    if db.scalar(statement):
        return [f"{record.archive_year} 年“{record.person_name}”已有同名年度考核记录，请确认是否为同一人员。"]
    return []


def _record_out(
    db: Session,
    record: ArchiveRecord,
    user: User,
    *,
    include_attachments: bool = False,
    device_id: str | None = None,
) -> ArchiveRecordOut:
    category = category_for_record(db, record)
    attachment_rows = list(
        db.scalars(
            select(ArchiveAttachment)
            .where(ArchiveAttachment.record_id == record.id)
            .order_by(ArchiveAttachment.version_no)
        ).all()
    )
    attachments = [item for item in attachment_rows if item.deleted_at is None]
    deleted_attachments = [item for item in attachment_rows if item.deleted_at is not None]
    links = [
        {
            "id": item.id,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "relation": item.relation,
        }
        for item in db.scalars(
            select(ArchiveLink)
            .where(ArchiveLink.record_id == record.id)
            .order_by(ArchiveLink.created_at)
        ).all()
    ]
    return ArchiveRecordOut(
        id=record.id,
        category_id=record.category_id,
        archive_year=record.archive_year,
        sequence_no=record.sequence_no,
        document_no=record.document_no,
        title=record.title,
        summary=record.summary,
        involved_persons=record.involved_persons or [],
        source_unit=record.source_unit,
        document_date=record.document_date,
        person_name=record.person_name,
        person_identifier=record.person_identifier,
        personnel_type=record.personnel_type,
        organization=record.organization,
        assessment_result=record.assessment_result,
        tags=record.tags or [],
        custom_fields=record.custom_fields or {},
        status=record.status,
        void_reason=record.void_reason,
        version=record.version,
        created_by=record.created_by,
        updated_by=record.updated_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
        attachment_count=len(attachments),
        indexed_attachment_count=sum(
            item.status == ArchiveAttachmentStatus.INDEXED for item in attachments
        ),
        attachments=[_attachment_out(db, item) for item in attachments]
        if include_attachments
        else [],
        deleted_attachments=[_attachment_out(db, item) for item in deleted_attachments]
        if include_attachments
        else [],
        duplicate_warnings=_duplicate_warnings(db, record, category),
        links=links,
        permissions=archive_permissions(db, category, user, device_id),
    )


def _assert_unique(
    db: Session,
    category: ArchiveCategory,
    *,
    archive_year: int,
    sequence_no: int,
    document_no: str,
    person_identifier: str,
    exclude_id: str | None = None,
) -> None:
    base = [
        ArchiveRecord.category_id == category.id,
        ArchiveRecord.archive_year == archive_year,
    ]
    if exclude_id:
        base.append(ArchiveRecord.id != exclude_id)
    if db.scalar(select(ArchiveRecord.id).where(*base, ArchiveRecord.sequence_no == sequence_no)):
        raise ProblemException(409, "ARCHIVE_SEQUENCE_EXISTS", "档案序号重复", "同一年度同一类别的序号不能重复。")
    if document_no.strip() and db.scalar(select(ArchiveRecord.id).where(*base, ArchiveRecord.document_no == document_no.strip())):
        raise ProblemException(409, "ARCHIVE_DOCUMENT_NO_EXISTS", "文号重复", "同一年度同一类别的文号不能重复。")
    if (
        category.record_mode == ArchiveRecordMode.PERSON_YEAR
        and person_identifier.strip()
        and db.scalar(
            select(ArchiveRecord.id).where(
                *base,
                ArchiveRecord.person_identifier == person_identifier.strip(),
            )
        )
    ):
        raise ProblemException(409, "ARCHIVE_PERSON_EXISTS", "人员年度档案重复", "同一类别、同一年度和人员编号只能保存一档。")


def _add_revision(
    db: Session,
    record: ArchiveRecord,
    actor: User,
    change_note: str,
) -> None:
    revision_no = (
        db.scalar(
            select(func.max(ArchiveRecordRevision.revision_no)).where(
                ArchiveRecordRevision.record_id == record.id
            )
        )
        or 0
    ) + 1
    db.add(
        ArchiveRecordRevision(
            record_id=record.id,
            revision_no=revision_no,
            snapshot=record_snapshot(record),
            change_note=change_note,
            created_by=actor.id,
        )
    )


@router.get("/archives/categories", response_model=typing.List[ArchiveCategoryOut])
def list_archive_categories(
    request: Request,
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArchiveCategoryOut]:
    device_id = _request_device_id(request, db)
    statement = select(ArchiveCategory).order_by(ArchiveCategory.built_in.desc(), ArchiveCategory.name)
    categories = db.scalars(statement).all()
    return [
        _category_out(db, item, user, device_id)
        for item in categories
        if (user.role.value == "admin" and (include_inactive or item.active))
        or (item.active and can_view_category(db, item, user, device_id))
    ]


@router.post("/archives/categories", response_model=ArchiveCategoryOut, status_code=201)
def create_archive_category(
    payload: ArchiveCategoryCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveCategoryOut:
    if db.scalar(select(ArchiveCategory.id).where(or_(ArchiveCategory.name == payload.name.strip(), ArchiveCategory.code == payload.code))):
        raise ProblemException(409, "ARCHIVE_CATEGORY_EXISTS", "档案类别已存在", "类别名称或编码不能重复。")
    category = ArchiveCategory(
        name=payload.name.strip(),
        code=payload.code,
        description=payload.description,
        record_mode=payload.record_mode,
        field_schema=_validate_field_schema([item.model_dump() for item in payload.field_schema]),
        directory_pattern=payload.directory_pattern,
        access_mode=payload.access_mode,
        allow_device_access=payload.allow_device_access,
        created_by=admin.id,
    )
    db.add(category)
    write_audit(db, admin, "archive.category_create", "archive_category", category.id, {"code": category.code}, client_ip(request))
    db.commit()
    db.refresh(category)
    return _category_out(db, category, admin)


@router.patch("/archives/categories/{category_id}", response_model=ArchiveCategoryOut)
def patch_archive_category(
    category_id: str,
    payload: ArchiveCategoryPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveCategoryOut:
    category = _category(db, category_id, admin, include_inactive=True)
    if category.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案类别已更新", "请刷新类别模板后重试。")
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if field == "field_schema" and value is not None:
            value = _validate_field_schema([item.model_dump() for item in value])
        if field == "name" and value:
            value = value.strip()
        setattr(category, field, value)
    category.version += 1
    write_audit(db, admin, "archive.category_update", "archive_category", category.id, {"version": category.version}, client_ip(request))
    db.commit()
    db.refresh(category)
    return _category_out(db, category, admin)


@router.patch("/archives/categories/{category_id}/access", response_model=ArchiveCategoryOut)
def patch_archive_category_access(
    category_id: str,
    payload: ArchiveAccessPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveCategoryOut:
    category = _category(db, category_id, admin, include_inactive=True)
    if category.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案类别权限已更新", "请刷新后重试。")
    category.access_mode = payload.access_mode
    category.allow_device_access = payload.allow_device_access
    category.version += 1
    write_audit(db, admin, "archive.category_access_update", "archive_category", category.id, {"access_mode": payload.access_mode.value}, client_ip(request))
    db.commit()
    db.refresh(category)
    return _category_out(db, category, admin)


@router.post("/archives/categories/{category_id}/grants", response_model=dict, status_code=201)
def create_archive_category_grant(
    category_id: str,
    payload: ArchiveAccessGrantCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    category = _category(db, category_id, admin, include_inactive=True)
    if (payload.user_id is None) == (payload.device_id is None):
        raise ProblemException(422, "ARCHIVE_GRANT_TARGET_INVALID", "授权对象无效", "必须且只能选择一个人员或设备。")
    if payload.user_id and not db.get(User, payload.user_id):
        raise ProblemException(404, "USER_NOT_FOUND", "人员不存在", "未找到授权人员。")
    if payload.device_id and not db.get(Device, payload.device_id):
        raise ProblemException(404, "DEVICE_NOT_FOUND", "协同电脑不存在", "未找到授权设备。")
    grant = db.scalar(
        select(ArchiveAccessGrant).where(
            ArchiveAccessGrant.category_id == category.id,
            ArchiveAccessGrant.user_id == payload.user_id,
            ArchiveAccessGrant.device_id == payload.device_id,
        )
    )
    if grant:
        grant.active = True
        grant.can_view = payload.can_view
        grant.can_download = payload.can_download
        grant.can_contribute = payload.can_contribute
        grant.version += 1
    else:
        grant = ArchiveAccessGrant(
            category_id=category.id,
            created_by=admin.id,
            **payload.model_dump(),
        )
        db.add(grant)
    write_audit(db, admin, "archive.category_grant", "archive_category", category.id, {"grant_id": grant.id}, client_ip(request))
    db.commit()
    db.refresh(grant)
    return {
        "id": grant.id,
        "category_id": category.id,
        "user_id": grant.user_id,
        "device_id": grant.device_id,
        "can_view": grant.can_view,
        "can_download": grant.can_download,
        "can_contribute": grant.can_contribute,
        "active": grant.active,
        "version": grant.version,
    }


@router.get(
    "/archives/categories/{category_id}/grants",
    response_model=typing.List[ArchiveAccessGrantOut],
)
def list_archive_category_grants(
    category_id: str,
    include_inactive: bool = False,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[ArchiveAccessGrant]:
    _category(db, category_id, admin, include_inactive=True)
    statement = select(ArchiveAccessGrant).where(
        ArchiveAccessGrant.category_id == category_id
    )
    if not include_inactive:
        statement = statement.where(ArchiveAccessGrant.active.is_(True))
    return list(db.scalars(statement.order_by(ArchiveAccessGrant.created_at)).all())


@router.patch(
    "/archives/categories/{category_id}/grants/{grant_id}",
    response_model=ArchiveAccessGrantOut,
)
def patch_archive_category_grant(
    category_id: str,
    grant_id: str,
    payload: ArchiveAccessGrantPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveAccessGrant:
    _category(db, category_id, admin, include_inactive=True)
    grant = db.get(ArchiveAccessGrant, grant_id)
    if not grant or grant.category_id != category_id:
        raise ProblemException(404, "ARCHIVE_GRANT_NOT_FOUND", "授权不存在", "未找到该档案授权。")
    if grant.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案授权已更新", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(grant, field, getattr(payload, field))
    grant.version += 1
    write_audit(
        db,
        admin,
        "archive.category_grant_update",
        "archive_access_grant",
        grant.id,
        {"category_id": category_id, "version": grant.version},
        client_ip(request),
    )
    db.commit()
    db.refresh(grant)
    return grant


@router.get("/archives/years", response_model=dict)
def list_archive_years(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    device_id = _request_device_id(request, db)
    categories = [
        item
        for item in db.scalars(select(ArchiveCategory).where(ArchiveCategory.active.is_(True))).all()
        if can_view_category(db, item, user, device_id)
    ]
    summaries: list[dict[str, Any]] = []
    for year in db.scalars(
        select(ArchiveRecord.archive_year)
        .join(ArchiveCategory, ArchiveCategory.id == ArchiveRecord.category_id)
        .where(ArchiveCategory.active.is_(True))
        .distinct()
        .order_by(ArchiveRecord.archive_year.desc())
    ).all():
        category_rows: list[dict[str, Any]] = []
        for category in categories:
            records = db.scalars(
                select(ArchiveRecord).where(
                    ArchiveRecord.category_id == category.id,
                    ArchiveRecord.archive_year == year,
                    ArchiveRecord.status != ArchiveRecordStatus.VOIDED,
                )
            ).all()
            if not records:
                continue
            record_ids = [record.id for record in records]
            attachment_count = db.scalar(
                select(func.count(ArchiveAttachment.id)).where(
                    ArchiveAttachment.record_id.in_(record_ids),
                    ArchiveAttachment.status != ArchiveAttachmentStatus.VOIDED,
                )
            ) or 0
            missing_count = len(records) - len(
                {
                    item.record_id
                    for item in db.scalars(
                        select(ArchiveAttachment).where(
                            ArchiveAttachment.record_id.in_(record_ids),
                            ArchiveAttachment.status != ArchiveAttachmentStatus.VOIDED,
                        )
                    ).all()
                }
            )
            category_rows.append(
                {
                    "id": category.id,
                    "name": category.name,
                    "record_count": len(records),
                    "attachment_count": int(attachment_count),
                    "missing_attachment_count": missing_count,
                    "last_updated": max(record.updated_at for record in records),
                }
            )
        if category_rows:
            summaries.append({"year": year, "categories": category_rows})
    return {"years": summaries}


@router.get("/archives/records", response_model=typing.List[ArchiveRecordOut])
def list_archive_records(
    request: Request,
    archive_year: int | None = Query(default=None, ge=1_000, le=9_999),
    category_id: str | None = None,
    keyword: str = Query(default="", max_length=200),
    person: str = Query(default="", max_length=120),
    document_no: str = Query(default="", max_length=160),
    status: ArchiveRecordStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100_000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArchiveRecordOut]:
    device_id = _request_device_id(request, db)
    visible_categories = [
        item
        for item in db.scalars(select(ArchiveCategory).where(ArchiveCategory.active.is_(True))).all()
        if can_view_category(db, item, user, device_id)
    ]
    visible_ids = {item.id for item in visible_categories}
    if category_id and category_id not in visible_ids:
        return []
    statement = select(ArchiveRecord).where(ArchiveRecord.category_id.in_(visible_ids))
    if archive_year is not None:
        statement = statement.where(ArchiveRecord.archive_year == archive_year)
    if category_id:
        statement = statement.where(ArchiveRecord.category_id == category_id)
    if status:
        statement = statement.where(ArchiveRecord.status == status)
    else:
        statement = statement.where(ArchiveRecord.status != ArchiveRecordStatus.VOIDED)
    if person.strip():
        statement = statement.where(ArchiveRecord.search_text.contains(person.strip()))
    if document_no.strip():
        statement = statement.where(ArchiveRecord.document_no.contains(document_no.strip()))
    if keyword.strip():
        matched = fts_search(db, keyword, limit=2_000)
        if matched:
            statement = statement.where(ArchiveRecord.id.in_(matched))
        else:
            statement = statement.where(ArchiveRecord.search_text.contains(keyword.strip()))
    records = db.scalars(
        statement.order_by(ArchiveRecord.archive_year.desc(), ArchiveRecord.sequence_no, ArchiveRecord.created_at)
        .offset(offset)
        .limit(limit)
    ).all()
    return [_record_out(db, record, user, device_id=device_id) for record in records]


@router.get("/archives/records/{record_id}", response_model=ArchiveRecordOut)
def get_archive_record(
    record_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveRecordOut:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, device_id=device_id)
    if not can_view_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_ACCESS_DENIED", "无权查看该档案", "请联系管理员。")
    return _record_out(db, record, user, include_attachments=True, device_id=device_id)


@router.post("/archives/records", response_model=ArchiveRecordOut, status_code=201)
def create_archive_record(
    payload: ArchiveRecordCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveRecordOut:
    device_id = _request_device_id(request, db)
    category = _category(db, payload.category_id, user, device_id=device_id)
    if not can_contribute_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_CONTRIBUTE_DENIED", "无权录入该类档案", "请联系管理员开通档案贡献权限。")
    values = payload.model_dump()
    values["custom_fields"] = validate_custom_fields(category, values.get("custom_fields") or {})
    validate_record_mode(category, values)
    with db_runtime.write_lock:
        sequence = next_sequence(db, category.id, payload.archive_year, payload.sequence_no)
        _assert_unique(
            db,
            category,
            archive_year=payload.archive_year,
            sequence_no=sequence,
            document_no=payload.document_no,
            person_identifier=payload.person_identifier,
        )
        values["sequence_no"] = sequence
        record = ArchiveRecord(
            **{key: value for key, value in values.items() if key != "category_id"},
            category_id=category.id,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(record)
        db.flush()
        refresh_search_index(db, record.id)
        db.add(
            ArchiveRecordRevision(
                record_id=record.id,
                revision_no=1,
                snapshot=record_snapshot(record),
                change_note="创建档案",
                created_by=user.id,
            )
        )
        write_audit(db, user, "archive.record_create", "archive_record", record.id, {"category_id": category.id, "archive_year": record.archive_year, "device_id": device_id}, client_ip(request))
        emit_event(db, "archive.record_created", record.id, {"category_id": category.id, "archive_year": record.archive_year})
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ProblemException(
                409,
                "ARCHIVE_RECORD_CONFLICT",
                "档案目录发生并发冲突",
                "同一年度的序号或档案标识已被占用，请刷新后重试。",
            ) from exc
    db.refresh(record)
    return _record_out(db, record, user, include_attachments=True, device_id=device_id)


@router.patch("/archives/records/{record_id}", response_model=ArchiveRecordOut)
def patch_archive_record(
    record_id: str,
    payload: ArchiveRecordPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveRecordOut:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, include_inactive=True, device_id=device_id)
    if not can_contribute_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_CONTRIBUTE_DENIED", "无权修订该类档案", "请联系管理员开通档案贡献权限。")
    if record.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案已被更新", "请刷新后查看最新内容。")
    values = payload.model_dump(exclude={"change_note"}, exclude_unset=True)
    merged_custom = dict(record.custom_fields or {})
    if "custom_fields" in values:
        merged_custom.update(values["custom_fields"] or {})
    values["custom_fields"] = validate_custom_fields(
        category,
        merged_custom,
        legacy_values=record.custom_fields or {},
    )
    merged_for_validation = record_snapshot(record)
    merged_for_validation.update(values)
    validate_record_mode(category, merged_for_validation)
    archive_year = int(merged_for_validation.get("archive_year", record.archive_year))
    sequence_no = int(merged_for_validation.get("sequence_no", record.sequence_no))
    _assert_unique(
        db,
        category,
        archive_year=archive_year,
        sequence_no=sequence_no,
        document_no=str(merged_for_validation.get("document_no", record.document_no)),
        person_identifier=str(merged_for_validation.get("person_identifier", record.person_identifier)),
        exclude_id=record.id,
    )
    _add_revision(db, record, user, payload.change_note or "人工修订")
    for field, value in values.items():
        setattr(record, field, value)
    record.updated_by = user.id
    record.version += 1
    refresh_search_index(db, record.id)
    write_audit(db, user, "archive.record_update", "archive_record", record.id, {"version": record.version, "change_note": payload.change_note, "device_id": device_id}, client_ip(request))
    emit_event(db, "archive.record_updated", record.id, {"version": record.version})
    db.commit()
    db.refresh(record)
    return _record_out(db, record, user, include_attachments=True, device_id=device_id)


@router.post("/archives/records/{record_id}/void", response_model=ArchiveRecordOut)
def void_archive_record(
    record_id: str,
    payload: ArchiveAction,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveRecordOut:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    if record.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案已被更新", "请刷新后重试。")
    _add_revision(db, record, admin, f"作废：{payload.reason}")
    record.status = ArchiveRecordStatus.VOIDED
    record.void_reason = payload.reason
    record.voided_at = utcnow()
    record.updated_by = admin.id
    record.version += 1
    refresh_search_index(db, record.id)
    write_audit(db, admin, "archive.record_void", "archive_record", record.id, {"reason": payload.reason}, client_ip(request))
    emit_event(db, "archive.record_voided", record.id, {"reason": payload.reason})
    db.commit()
    db.refresh(record)
    return _record_out(db, record, admin, include_attachments=True)


@router.post("/archives/records/{record_id}/restore", response_model=ArchiveRecordOut)
def restore_archive_record(
    record_id: str,
    payload: ArchiveAction,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveRecordOut:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    if record.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案已被更新", "请刷新后重试。")
    category = category_for_record(db, record)
    _assert_unique(
        db,
        category,
        archive_year=record.archive_year,
        sequence_no=record.sequence_no,
        document_no=record.document_no,
        person_identifier=record.person_identifier,
        exclude_id=record.id,
    )
    _add_revision(db, record, admin, f"恢复：{payload.reason}")
    record.status = ArchiveRecordStatus.ACTIVE
    record.void_reason = ""
    record.voided_at = None
    record.updated_by = admin.id
    record.version += 1
    refresh_search_index(db, record.id)
    write_audit(db, admin, "archive.record_restore", "archive_record", record.id, {"reason": payload.reason}, client_ip(request))
    emit_event(db, "archive.record_restored", record.id, {})
    db.commit()
    db.refresh(record)
    return _record_out(db, record, admin, include_attachments=True)


@router.post("/archives/records/{record_id}/attachments", response_model=ArchiveAttachmentOut, status_code=201)
async def upload_archive_attachment(
    record_id: str,
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    note: str = Form(""),
    client_upload_id: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveAttachmentOut:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    category = category_for_record(db, record)
    device_id = _request_device_id(request, db)
    if not can_contribute_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_UPLOAD_DENIED", "无权上传扫描件", "请联系管理员。")
    normalized_upload_id = normalize_client_upload_id(client_upload_id)
    if normalized_upload_id:
        existing = db.scalar(
            select(ArchiveAttachment).where(
                ArchiveAttachment.client_upload_id == normalized_upload_id
            )
        )
        if existing:
            if existing.record_id != record.id:
                raise ProblemException(409, "UPLOAD_ID_CONFLICT", "上传请求已被使用", "请重新选择该文件后再试。")
            return _attachment_out(db, existing)
    try:
        with db_runtime.write_lock:
            attachment = await save_archive_upload(
                db,
                record,
                file,
                user,
                note,
                client_upload_id=normalized_upload_id,
            )
            record.version += 1
            record.updated_by = user.id
            write_audit(db, user, "archive.attachment_upload", "archive_attachment", attachment.id, {"record_id": record.id, "sha256": attachment.blob_sha256, "name": attachment.display_name, "device_id": device_id}, client_ip(request))
            emit_event(db, "archive.attachment_added", record.id, {"attachment_id": attachment.id})
            db.commit()
            db.refresh(attachment)
    except BaseException:
        # 异步读取文件失败时在当前执行线程释放事务锁，避免依赖清理阶段
        # 把可读的中文业务错误覆盖成 500。
        db.rollback()
        raise
    background.add_task(index_archive_attachment, attachment.id)
    return _attachment_out(db, attachment)


@router.get("/archives/records/{record_id}/attachments", response_model=typing.List[ArchiveAttachmentOut])
def list_archive_attachments(
    record_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
    include_deleted: bool = Query(default=False),
) -> list[ArchiveAttachmentOut]:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, device_id=device_id)
    if not can_view_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_ACCESS_DENIED", "无权查看扫描件", "请联系管理员。")
    query = select(ArchiveAttachment).where(ArchiveAttachment.record_id == record.id)
    if not include_deleted:
        query = query.where(ArchiveAttachment.deleted_at.is_(None))
    return [
        _attachment_out(db, item)
        for item in db.scalars(
            query.order_by(ArchiveAttachment.version_no)
        ).all()
    ]


@router.get("/archives/attachments/{attachment_id}/download")
def download_archive_attachment(
    attachment_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    attachment, blob, path = archive_attachment_path(db, attachment_id)
    if getattr(attachment, "deleted_at", None) is not None:
        raise ProblemException(410, "ARCHIVE_ATTACHMENT_IN_RECYCLE_BIN", "扫描件已移入回收站", "请先恢复扫描件再下载。")
    if attachment.status == ArchiveAttachmentStatus.VOIDED:
        raise ProblemException(410, "ARCHIVE_ATTACHMENT_VOIDED", "扫描件已作废", "请查看有效版本。")
    record = db.get(ArchiveRecord, attachment.record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到所属档案。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, device_id=device_id)
    if not can_download_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_DOWNLOAD_DENIED", "无权下载扫描件", "请联系管理员。")
    if not path.exists():
        raise ProblemException(410, "ARCHIVE_ATTACHMENT_MISSING", "扫描件已缺失", "备份恢复后请检查附件完整性。")
    write_audit(db, user, "archive.attachment_download", "archive_attachment", attachment.id, {"record_id": record.id, "sha256": blob.sha256}, client_ip(request))
    db.commit()
    return FileResponse(path, media_type=blob.mime_type, filename=safe_archive_name(attachment.display_name, 255))


def _can_recycle_archive_attachment(
    db: Session,
    category: ArchiveCategory,
    attachment: ArchiveAttachment,
    user: User,
    device_id: str | None,
) -> bool:
    return user.role == UserRole.ADMIN or (
        attachment.uploaded_by == user.id
        and can_contribute_category(db, category, user, device_id)
    )


@router.delete("/archives/attachments/{attachment_id}", response_model=ArchiveAttachmentOut)
def delete_archive_attachment(
    attachment_id: str,
    request: Request,
    reason: str = Query(min_length=2, max_length=2_000),
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveAttachmentOut:
    attachment, _blob, _path = archive_attachment_path(db, attachment_id)
    record = db.get(ArchiveRecord, attachment.record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到所属档案。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, device_id=device_id)
    if not _can_recycle_archive_attachment(db, category, attachment, user, device_id):
        raise ProblemException(403, "ARCHIVE_ATTACHMENT_DELETE_DENIED", "无权删除扫描件", "上传人或管理员可以处理该文件。")
    if record.status != ArchiveRecordStatus.ACTIVE and user.role != UserRole.ADMIN:
        raise ProblemException(409, "ARCHIVE_RECORD_RESTORE_REQUIRED", "档案当前不能修改", "请先由管理员恢复档案。")
    if record.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案扫描件已更新", "请刷新后重试。")
    if attachment.deleted_at is not None:
        return _attachment_out(db, attachment)
    attachment.deleted_at = utcnow()
    attachment.deleted_by = user.id
    attachment.delete_reason = reason.strip()
    attachment.purge_after = attachment.deleted_at + timedelta(
        days=get_settings().deleted_attachment_retention_days
    )
    record.version += 1
    record.updated_by = user.id
    refresh_search_index(db, record.id)
    write_audit(
        db,
        user,
        "archive.attachment_delete",
        "archive_attachment",
        attachment.id,
        {
            "record_id": record.id,
            "reason": attachment.delete_reason,
            "purge_after": attachment.purge_after.isoformat(),
        },
        client_ip(request),
    )
    emit_event(db, "archive.attachment_deleted", record.id, {"attachment_id": attachment.id})
    db.commit()
    db.refresh(attachment)
    return _attachment_out(db, attachment)


@router.post("/archives/attachments/{attachment_id}/restore", response_model=ArchiveAttachmentOut)
def restore_archive_attachment(
    attachment_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveAttachmentOut:
    attachment, _blob, _path = archive_attachment_path(db, attachment_id)
    record = db.get(ArchiveRecord, attachment.record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到所属档案。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, device_id=device_id)
    if not _can_recycle_archive_attachment(db, category, attachment, user, device_id):
        raise ProblemException(403, "ARCHIVE_ATTACHMENT_RESTORE_DENIED", "无权恢复扫描件", "上传人或管理员可以恢复该文件。")
    if record.status != ArchiveRecordStatus.ACTIVE and user.role != UserRole.ADMIN:
        raise ProblemException(409, "ARCHIVE_RECORD_RESTORE_REQUIRED", "档案当前不能修改", "请先由管理员恢复档案。")
    if record.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案扫描件已更新", "请刷新后重试。")
    if attachment.deleted_at is None:
        return _attachment_out(db, attachment)
    attachment.deleted_at = None
    attachment.deleted_by = None
    attachment.delete_reason = ""
    attachment.purge_after = None
    record.version += 1
    record.updated_by = user.id
    refresh_search_index(db, record.id)
    write_audit(
        db,
        user,
        "archive.attachment_restore",
        "archive_attachment",
        attachment.id,
        {"record_id": record.id},
        client_ip(request),
    )
    emit_event(db, "archive.attachment_restored", record.id, {"attachment_id": attachment.id})
    db.commit()
    db.refresh(attachment)
    return _attachment_out(db, attachment)


@router.post("/archives/attachments/{attachment_id}/void", response_model=ArchiveAttachmentOut)
def void_archive_attachment(
    attachment_id: str,
    payload: ArchiveAction,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ArchiveAttachmentOut:
    attachment, _blob, _path = archive_attachment_path(db, attachment_id)
    record = db.get(ArchiveRecord, attachment.record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到所属档案。")
    if record.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "档案扫描件已更新", "请刷新后重试。")
    if attachment.status == ArchiveAttachmentStatus.VOIDED:
        return _attachment_out(db, attachment)
    attachment.status = ArchiveAttachmentStatus.VOIDED
    attachment.note = f"{attachment.note}\n作废原因：{payload.reason}".strip()
    record.version += 1
    record.updated_by = admin.id
    refresh_search_index(db, record.id)
    write_audit(
        db,
        admin,
        "archive.attachment_void",
        "archive_attachment",
        attachment.id,
        {"record_id": record.id, "reason": payload.reason},
        client_ip(request),
    )
    emit_event(db, "archive.attachment_voided", record.id, {"attachment_id": attachment.id})
    db.commit()
    db.refresh(attachment)
    return _attachment_out(db, attachment)


@router.get("/archives/records/{record_id}/history", response_model=typing.List[ArchiveRevisionOut])
def archive_history(
    record_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArchiveRecordRevision]:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    _category(db, record.category_id, user, device_id=_request_device_id(request, db))
    return list(
        db.scalars(
            select(ArchiveRecordRevision)
            .where(ArchiveRecordRevision.record_id == record.id)
            .order_by(ArchiveRecordRevision.revision_no.desc())
        ).all()
    )


@router.post("/archives/records/{record_id}/links", response_model=ArchiveRecordOut, status_code=201)
def link_archive_record(
    record_id: str,
    payload: ArchiveLinkCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveRecordOut:
    record = db.get(ArchiveRecord, record_id)
    if not record:
        raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到档案记录。")
    device_id = _request_device_id(request, db)
    category = _category(db, record.category_id, user, device_id=device_id)
    if not can_contribute_category(db, category, user, device_id):
        raise ProblemException(403, "ARCHIVE_CONTRIBUTE_DENIED", "无权关联该类档案", "请联系管理员开通档案贡献权限。")
    models = {
        "task": Task,
        "report": PeriodReport,
        "journal": WorkJournalEntry,
        "knowledge": KnowledgeEntry,
    }
    target_model = models[payload.entity_type]
    if not db.get(target_model, payload.entity_id):
        raise ProblemException(404, "ARCHIVE_LINK_TARGET_NOT_FOUND", "关联对象不存在", "未找到可关联对象。")
    existing = db.scalar(
        select(ArchiveLink).where(
            ArchiveLink.record_id == record.id,
            ArchiveLink.entity_type == payload.entity_type,
            ArchiveLink.entity_id == payload.entity_id,
            ArchiveLink.relation == payload.relation,
        )
    )
    if not existing:
        db.add(ArchiveLink(record_id=record.id, created_by=user.id, **payload.model_dump()))
        record.version += 1
        record.updated_by = user.id
        write_audit(db, user, "archive.link_create", "archive_record", record.id, {**payload.model_dump(), "device_id": device_id}, client_ip(request))
        db.commit()
    return _record_out(db, record, user, include_attachments=True, device_id=device_id)


@router.get("/archives/search", response_model=typing.List[ArchiveRecordOut])
def search_archives(
    request: Request,
    keyword: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArchiveRecordOut]:
    device_id = _request_device_id(request, db)
    ids = fts_search(db, keyword, limit=limit * 5)
    statement = select(ArchiveRecord)
    if ids:
        statement = statement.where(ArchiveRecord.id.in_(ids))
    else:
        statement = statement.where(ArchiveRecord.search_text.contains(keyword.strip()))
    records = db.scalars(
        statement.where(ArchiveRecord.status != ArchiveRecordStatus.VOIDED)
        .order_by(ArchiveRecord.updated_at.desc())
        .limit(limit)
    ).all()
    visible: list[ArchiveRecordOut] = []
    for record in records:
        category = category_for_record(db, record)
        if can_view_category(db, category, user, device_id):
            visible.append(_record_out(db, record, user, device_id=device_id))
    return visible


@router.get("/archives/export")
def export_archives(
    request: Request,
    archive_year: int = Query(ge=1_000, le=9_999),
    category_id: str | None = None,
    keyword: str = Query(default="", max_length=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    device_id = _request_device_id(request, db)
    if category_id:
        _category(db, category_id, user, device_id=device_id)
    path = export_archive_package(db, user, archive_year, category_id, keyword, device_id)
    write_audit(db, user, "archive.export", "archive_export", path.name, {"archive_year": archive_year, "category_id": category_id}, client_ip(request))
    db.commit()
    return FileResponse(path, media_type="application/zip", filename=path.name)
