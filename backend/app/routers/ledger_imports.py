"""通用台账导入 API。

导入分为检查、映射、全量校验、提交和撤销五个显式状态；协同用户与主机
用户使用同一业务权限，设备类型不参与硬编码授权。
"""

# FastAPI 使用声明式依赖和表单字段作为函数默认值；这是框架规定的签名。
# ruff: noqa: B008

from __future__ import annotations

import re
import typing
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..archive_service import (
    can_contribute_category,
    next_sequence,
    record_snapshot,
    refresh_search_index,
    validate_custom_fields,
    validate_record_mode,
)
from ..audit import emit_event, write_audit
from ..database import db_runtime, get_session
from ..device_versions import request_device
from ..enums import ArchiveRecordStatus
from ..ledger_imports import (
    ARCHIVE_FIELDS,
    MAX_FILE_BYTES,
    PARTY_FIELDS,
    PARTY_PROGRESS_FIELDS,
    delete_stage,
    detect_header_row,
    fields_for_target,
    formula_like,
    load_stage,
    mapped_rows,
    parse_date,
    parse_list,
    parse_table,
    profile_sheet,
    safe_field_key,
    save_stage,
    validate_filename,
)
from ..models import (
    ArchiveCategory,
    ArchiveRecord,
    ArchiveRecordRevision,
    LedgerImportChange,
    LedgerImportJob,
    LedgerImportMappingTemplate,
    PartyDevelopmentCase,
    PartyDevelopmentFieldDefinition,
    PartyDevelopmentProgressEvent,
    User,
    utcnow,
)
from ..party_development import ensure_reference_plan_profile, rule_metadata
from ..problems import ProblemException
from ..schemas import (
    LedgerImportCommitRequest,
    LedgerImportMappingPatch,
    LedgerImportProfilePatch,
    LedgerImportTemplateCreate,
    LedgerImportTemplatePatch,
    LedgerImportUndoRequest,
    LedgerImportValidateRequest,
)
from ..security import get_current_user

UTC = timezone.utc
from .router_utils import client_ip

router = APIRouter(tags=["ledger-imports"])

CORE_PARTY_KEYS = {item.key for item in PARTY_FIELDS}
CORE_ARCHIVE_KEYS = {item.key for item in ARCHIVE_FIELDS}
PARTY_DATE_COLUMNS = {
    "birth_date": "birth_date",
    "application_date": "application_at",
    "activist_date": "activist_at",
    "development_object_date": "development_object_at",
    "probationary_date": "probationary_at",
    "converted_date": "converted_at",
}
ALLOWED_DUPLICATE_ACTIONS = {"new", "skip", "fill"}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _date_time(value: date | None) -> datetime | None:
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC) if value else None


def _job_out(job: LedgerImportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "target_type": job.target_type,
        "target_id": job.target_id,
        "source_format": job.source_format,
        "sheet_name": job.sheet_name,
        "header_row": job.header_row,
        "status": job.status,
        "mapping": job.mapping,
        "profile": job.profile,
        "validation": job.validation,
        "total_rows": job.total_rows,
        "valid_rows": job.valid_rows,
        "warning_rows": job.warning_rows,
        "error_rows": job.error_rows,
        "expires_at": job.expires_at,
        "committed_at": job.committed_at,
        "undone_at": job.undone_at,
        "version": job.version,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _template_out(item: LedgerImportMappingTemplate) -> dict[str, Any]:
    return {
        "id": item.id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "name": item.name,
        "header_signature": item.header_signature,
        "mapping": item.mapping,
        "active": item.active,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _device_id(request: Request, db: Session) -> str | None:
    device = request_device(request, db)
    return device.id if device else None


def _target_fields(
    db: Session, target_type: str, target_id: str | None
) -> list[Any]:
    if target_type == "party_development":
        definitions = db.scalars(
            select(PartyDevelopmentFieldDefinition).where(
                PartyDevelopmentFieldDefinition.active.is_(True)
            )
        ).all()
        custom = [
            {
                "key": item.key,
                "label": item.label,
                "type": item.field_type,
                "aliases": item.aliases,
                "required": item.required,
            }
            for item in definitions
        ]
        return fields_for_target(target_type, custom)
    if target_type != "archive" or not target_id:
        raise ProblemException(
            422,
            "LEDGER_TARGET_INVALID",
            "归档目标无效",
            "重要档案导入必须选择具体类别。",
        )
    category = db.get(ArchiveCategory, target_id)
    if not category or not category.active:
        raise ProblemException(
            404,
            "ARCHIVE_CATEGORY_NOT_FOUND",
            "档案类别不存在",
            "请选择仍在使用的档案类别。",
        )
    return fields_for_target(target_type, category.field_schema or [])


def _ensure_target_permission(
    db: Session,
    request: Request,
    user: User,
    target_type: str,
    target_id: str | None,
) -> ArchiveCategory | None:
    if target_type == "party_development":
        return None
    if target_type != "archive" or not target_id:
        raise ProblemException(
            422,
            "LEDGER_TARGET_INVALID",
            "归档目标无效",
            "重要档案导入必须选择具体类别。",
        )
    category = db.get(ArchiveCategory, target_id)
    if not category or not category.active:
        raise ProblemException(404, "ARCHIVE_CATEGORY_NOT_FOUND", "档案类别不存在", "请选择仍在使用的档案类别。")
    if not can_contribute_category(db, category, user, _device_id(request, db)):
        raise ProblemException(
            403,
            "LEDGER_TARGET_CONTRIBUTE_DENIED",
            "无权导入该类档案",
            "请联系管理员开通该类别的新增权限。",
        )
    return category


def _job(db: Session, job_id: str, user: User) -> LedgerImportJob:
    item = db.get(LedgerImportJob, job_id)
    if not item or item.created_by != user.id:
        raise ProblemException(404, "LEDGER_JOB_NOT_FOUND", "导入任务不存在", "请重新选择台账文件。")
    if _aware(item.expires_at) <= utcnow() and item.status not in {"committed", "undone"}:
        delete_stage(item.id)
        item.status = "expired"
        db.commit()
        raise ProblemException(410, "LEDGER_JOB_EXPIRED", "导入任务已过期", "暂存数据已清理，请重新选择文件。")
    return item


def _assert_version(job: LedgerImportJob, version: int) -> None:
    if job.version != version:
        raise ProblemException(409, "VERSION_CONFLICT", "导入任务已更新", "请刷新当前步骤后重试。")


async def _bounded_upload(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, MAX_FILE_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise ProblemException(413, "LEDGER_FILE_TOO_LARGE", "台账过大", "单个台账文件不能超过 50 MiB。")
    return b"".join(chunks)


def _mapping_payload(payload: LedgerImportMappingPatch, fields: list[Any]) -> list[dict[str, Any]]:
    allowed = {item.key: item for item in fields}
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in payload.mappings:
        source_key = re.sub(r"\s+", "", item.source_column.strip().lower())
        if source_key in seen_sources:
            raise ProblemException(422, "LEDGER_MAPPING_SOURCE_DUPLICATE", "源字段重复", f"“{item.source_column}”只能映射一次。")
        seen_sources.add(source_key)
        values = item.model_dump()
        if item.action == "ignore":
            values["target_field"] = None
            result.append(values)
            continue
        if item.action == "create":
            if not item.confirmed or not item.create_label or not item.create_type:
                raise ProblemException(422, "LEDGER_NEW_FIELD_CONFIRM_REQUIRED", "新增字段尚未确认", "请确认字段名称、类型和影响范围。")
            values["target_field"] = safe_field_key(item.create_label)
        elif item.target_field not in allowed:
            raise ProblemException(422, "LEDGER_MAPPING_TARGET_INVALID", "目标字段无效", f"“{item.target_field or ''}”不是当前模块字段。")
        if values["target_field"] in seen_targets:
            raise ProblemException(422, "LEDGER_MAPPING_TARGET_DUPLICATE", "目标字段重复", "一个目标字段只能接收一列数据。")
        if not item.confirmed:
            raise ProblemException(422, "LEDGER_MAPPING_CONFIRM_REQUIRED", "字段映射尚未确认", f"请确认“{item.source_column}”的字段映射。")
        seen_targets.add(str(values["target_field"]))
        result.append(values)
    return result


def _field_choices(fields: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "key": item.key,
            "label": item.label,
            "field_type": item.field_type,
            "required": item.required,
        }
        for item in fields
    ]


@router.post("/ledger-imports/inspect", response_model=dict, status_code=201)
async def inspect_ledger(
    request: Request,
    target_type: str = Form(...),
    target_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """读取并剖析台账；原始文件只在请求内存中存在。"""

    _ensure_target_permission(db, request, user, target_type, target_id)
    fields = _target_fields(db, target_type, target_id)
    suffix = validate_filename(file.filename or "")
    data = await _bounded_upload(file)
    sheets, metadata = parse_table(data, suffix)
    visible_names = [item["name"] for item in metadata if not item["hidden"]]
    chosen = visible_names[0] if visible_names else metadata[0]["name"]
    header_row = detect_header_row(sheets[chosen])
    profile = profile_sheet(sheets[chosen], header_row, fields)
    now = utcnow()
    job = LedgerImportJob(
        target_type=target_type,
        target_id=target_id,
        source_format=suffix.lstrip("."),
        sheet_name=chosen,
        header_row=header_row,
        status="inspected",
        profile={
            "sheets": metadata,
            "selected": profile,
            "available_fields": _field_choices(fields),
        },
        total_rows=profile["total_rows"],
        expires_at=now + timedelta(hours=24),
        created_by=user.id,
    )
    db.add(job)
    db.flush()
    try:
        save_stage(job.id, sheets)
        write_audit(
            db,
            user,
            "ledger_import.inspected",
            "ledger_import_job",
            job.id,
            {"target_type": target_type, "target_id": target_id, "format": job.source_format, "sheet_count": len(metadata), "row_count": job.total_rows},
            client_ip(request),
        )
        db.commit()
    except Exception:
        db.rollback()
        delete_stage(job.id)
        raise
    return _job_out(job)


@router.patch("/ledger-imports/{job_id}/profile", response_model=dict)
def patch_profile(
    job_id: str,
    payload: LedgerImportProfilePatch,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    job = _job(db, job_id, user)
    _assert_version(job, payload.version)
    if job.status in {"committed", "undone"}:
        raise ProblemException(409, "LEDGER_JOB_FINALIZED", "导入任务已结束", "已提交或撤销的任务不能重新识别表头。")
    _ensure_target_permission(db, request, user, job.target_type, job.target_id)
    fields = _target_fields(db, job.target_type, job.target_id)
    rows = load_stage(job.id).get(payload.sheet_name)
    if rows is None:
        raise ProblemException(422, "LEDGER_SHEET_NOT_FOUND", "工作表不存在", "请重新选择工作表。")
    profile = profile_sheet(rows, payload.header_row, fields)
    job.sheet_name = payload.sheet_name
    job.header_row = payload.header_row
    job.profile = {
        **(job.profile or {}),
        "selected": profile,
        "available_fields": _field_choices(fields),
    }
    job.mapping = {}
    job.validation = {}
    job.status = "inspected"
    job.total_rows = profile["total_rows"]
    job.version += 1
    write_audit(db, user, "ledger_import.profile_updated", "ledger_import_job", job.id, {"sheet_name": job.sheet_name, "header_row": job.header_row}, client_ip(request))
    db.commit()
    return _job_out(job)


@router.patch("/ledger-imports/{job_id}/mapping", response_model=dict)
def patch_mapping(
    job_id: str,
    payload: LedgerImportMappingPatch,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    job = _job(db, job_id, user)
    _assert_version(job, payload.version)
    if job.status in {"committed", "undone"}:
        raise ProblemException(409, "LEDGER_JOB_FINALIZED", "导入任务已结束", "已提交或撤销的任务不能修改映射。")
    _ensure_target_permission(db, request, user, job.target_type, job.target_id)
    fields = _target_fields(db, job.target_type, job.target_id)
    rows_by_sheet = load_stage(job.id)
    rows = rows_by_sheet.get(payload.sheet_name)
    if rows is None:
        raise ProblemException(422, "LEDGER_SHEET_NOT_FOUND", "工作表不存在", "请重新选择工作表。")
    profile = profile_sheet(rows, payload.header_row, fields)
    if profile["duplicate_headers"]:
        raise ProblemException(422, "LEDGER_DUPLICATE_HEADERS", "表头存在重名列", "请先在源台账中为重复列设置不同名称。")
    mapping = _mapping_payload(payload, fields)
    job.sheet_name = payload.sheet_name
    job.header_row = payload.header_row
    job.mapping = {"columns": mapping, "header_signature": profile["header_signature"]}
    job.profile = {**(job.profile or {}), "selected": profile}
    job.status = "mapped"
    job.total_rows = profile["total_rows"]
    job.validation = {}
    job.version += 1
    write_audit(db, user, "ledger_import.mapping_confirmed", "ledger_import_job", job.id, {"mapped": sum(item["action"] != "ignore" for item in mapping), "created_fields": sum(item["action"] == "create" for item in mapping)}, client_ip(request))
    db.commit()
    return _job_out(job)


def _normalize_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _validate_mapped_value(field_type: str, value: Any, label: str) -> Any:
    if value in (None, ""):
        return None
    if formula_like(value):
        raise ValueError(f"{label}包含公式样式内容")
    if field_type == "date":
        return parse_date(value, label).isoformat()
    if field_type == "number":
        number = float(value)
        return int(number) if number.is_integer() else number
    if field_type == "list":
        return parse_list(value)
    return _normalize_text(value, 20_000)


def _party_duplicate(db: Session, values: dict[str, Any]) -> tuple[PartyDevelopmentCase | None, str]:
    name = str(values.get("name") or "").strip()
    committee = str(values.get("party_committee") or "").strip()
    branch = str(values.get("party_branch") or "").strip()
    if not name or not committee or not branch:
        return None, "none"
    query = select(PartyDevelopmentCase).where(
        PartyDevelopmentCase.status != "void",
        PartyDevelopmentCase.name == name,
        PartyDevelopmentCase.party_committee == committee,
        PartyDevelopmentCase.party_branch == branch,
    )
    birth = values.get("birth_date")
    if birth:
        query = query.where(PartyDevelopmentCase.birth_date == _date_time(date.fromisoformat(str(birth))))
        matches = db.scalars(query).all()
        return (matches[0], "exact") if len(matches) == 1 else (None, "conflict" if len(matches) > 1 else "none")
    matches = db.scalars(query).all()
    return (matches[0], "possible") if len(matches) == 1 else (None, "conflict" if len(matches) > 1 else "none")


def _archive_duplicate(db: Session, category_id: str, values: dict[str, Any]) -> tuple[ArchiveRecord | None, str]:
    year = int(values.get("archive_year") or 0)
    document_no = str(values.get("document_no") or "").strip()
    person_identifier = str(values.get("person_identifier") or "").strip()
    if document_no:
        matches = db.scalars(select(ArchiveRecord).where(ArchiveRecord.category_id == category_id, ArchiveRecord.archive_year == year, ArchiveRecord.document_no == document_no, ArchiveRecord.status != ArchiveRecordStatus.VOIDED)).all()
        return (matches[0], "exact") if len(matches) == 1 else (None, "conflict" if len(matches) > 1 else "none")
    if person_identifier:
        matches = db.scalars(select(ArchiveRecord).where(ArchiveRecord.category_id == category_id, ArchiveRecord.archive_year == year, ArchiveRecord.person_identifier == person_identifier, ArchiveRecord.status != ArchiveRecordStatus.VOIDED)).all()
        return (matches[0], "exact") if len(matches) == 1 else (None, "conflict" if len(matches) > 1 else "none")
    return None, "none"


def _validated_rows(
    db: Session, job: LedgerImportJob, row_actions: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mappings = list((job.mapping or {}).get("columns") or [])
    if not mappings:
        raise ProblemException(409, "LEDGER_MAPPING_REQUIRED", "尚未完成字段映射", "请先完成字段映射。")
    fields = _target_fields(db, job.target_type, job.target_id)
    field_by_key = {item.key: item for item in fields}
    required = {item.key for item in fields if item.required and item.key in (CORE_PARTY_KEYS if job.target_type == "party_development" else CORE_ARCHIVE_KEYS)}
    rows = mapped_rows(job.id, job.sheet_name, job.header_row, mappings)
    checked: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    errors = warnings = 0
    for row in rows:
        normalized: dict[str, Any] = {}
        row_errors: list[str] = []
        for key, value in row["values"].items():
            spec = field_by_key.get(key)
            mapping = next((item for item in mappings if item.get("target_field") == key), None)
            field_type = str(mapping.get("create_type")) if mapping and mapping.get("action") == "create" else (spec.field_type if spec else "text")
            label = str(mapping.get("create_label")) if mapping and mapping.get("action") == "create" else (spec.label if spec else key)
            try:
                normalized[key] = _validate_mapped_value(field_type, value, label)
            except (TypeError, ValueError, OverflowError) as exc:
                row_errors.append(str(exc))
        missing = [field_by_key[key].label for key in required if normalized.get(key) in (None, "", [])]
        if missing:
            row_errors.append("缺少必填字段：" + "、".join(missing))
        matched: PartyDevelopmentCase | ArchiveRecord | None
        if job.target_type == "party_development":
            matched, confidence = _party_duplicate(db, normalized)
        else:
            matched, confidence = _archive_duplicate(db, str(job.target_id), normalized)
        action = str(row_actions.get(str(row["row_number"]), "")).strip()
        if confidence in {"exact", "possible", "conflict"} and not action:
            row_errors.append("检测到可能重复记录，必须逐项选择处理方式")
        if action and action not in ALLOWED_DUPLICATE_ACTIONS and not action.startswith("update:"):
            row_errors.append("重复处理方式无效")
        if action.startswith("update:"):
            update_fields = {item for item in action[7:].split(",") if item}
            if not update_fields or not update_fields.issubset(normalized):
                row_errors.append("请选择本次允许更新的字段")
        if row_errors:
            errors += 1
            if len(issues) < 1_000:
                issues.append({"row_number": row["row_number"], "level": "error", "messages": row_errors})
        elif confidence == "possible":
            warnings += 1
            if len(issues) < 1_000:
                issues.append({"row_number": row["row_number"], "level": "warning", "messages": ["姓名和组织相同但缺少出生日期，请核对重复记录。"]})
        checked.append({**row, "values": normalized, "matched_id": matched.id if matched else None, "match_confidence": confidence, "action": action or "new"})
    summary = {"checked_rows": len(rows), "valid_rows": len(rows) - errors, "warning_rows": warnings, "error_rows": errors, "issues": issues, "issues_truncated": max(0, errors + warnings - len(issues))}
    return checked, summary


@router.post("/ledger-imports/{job_id}/validate", response_model=dict)
def validate_import(
    job_id: str,
    payload: LedgerImportValidateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    job = _job(db, job_id, user)
    _assert_version(job, payload.version)
    _ensure_target_permission(db, request, user, job.target_type, job.target_id)
    _, summary = _validated_rows(db, job, payload.row_actions)
    job.validation = {**summary, "row_actions": payload.row_actions}
    job.valid_rows = summary["valid_rows"]
    job.warning_rows = summary["warning_rows"]
    job.error_rows = summary["error_rows"]
    job.status = "validated" if not summary["error_rows"] else "validation_failed"
    job.version += 1
    write_audit(db, user, "ledger_import.validated", "ledger_import_job", job.id, {key: summary[key] for key in ("checked_rows", "valid_rows", "warning_rows", "error_rows")}, client_ip(request))
    db.commit()
    return _job_out(job)


def _party_snapshot(item: PartyDevelopmentCase) -> dict[str, Any]:
    return {
        "party_committee": item.party_committee,
        "party_branch": item.party_branch,
        "name": item.name,
        "gender": item.gender,
        "ethnicity": item.ethnicity,
        "birth_date": item.birth_date.isoformat() if item.birth_date else None,
        "education": item.education,
        "application_at": item.application_at.isoformat(),
        "activist_at": item.activist_at.isoformat() if item.activist_at else None,
        "training_contacts": item.training_contacts or [],
        "introducers": item.introducers or [],
        "development_object_at": item.development_object_at.isoformat() if item.development_object_at else None,
        "probationary_at": item.probationary_at.isoformat() if item.probationary_at else None,
        "converted_at": item.converted_at.isoformat() if item.converted_at else None,
        "stage": item.stage,
        "status": item.status,
        "extra_fields": item.extra_fields or {},
        "version": item.version,
    }


def _next_stage(values: dict[str, Any]) -> str:
    if values.get("converted_date"):
        return "completed"
    if values.get("probationary_date") or values.get("branch_acceptance_date"):
        return "probationary"
    if values.get("development_object_date"):
        return "development_object"
    if values.get("activist_date"):
        return "activist"
    return "application"


def _selected_update_fields(action: str, values: dict[str, Any]) -> set[str]:
    if action == "fill":
        return set(values)
    if action.startswith("update:"):
        return {item for item in action[7:].split(",") if item in values}
    return set(values)


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _create_custom_fields(
    db: Session,
    job: LedgerImportJob,
    user: User,
    category: ArchiveCategory | None,
) -> list[str]:
    mappings = list((job.mapping or {}).get("columns") or [])
    created: list[str] = []
    if job.target_type == "party_development":
        existing = {
            item.key: item
            for item in db.scalars(select(PartyDevelopmentFieldDefinition)).all()
        }
        existing_labels = {
            re.sub(r"\s+", "", item.label).lower(): item
            for item in existing.values()
        }
        for mapping in mappings:
            if mapping.get("action") != "create":
                continue
            key = str(mapping["target_field"])
            label = str(mapping["create_label"]).strip()
            label_key = re.sub(r"\s+", "", label).lower()
            if key in existing:
                continue
            if label_key in existing_labels:
                raise ProblemException(409, "LEDGER_FIELD_LABEL_CONFLICT", "字段名称重复", f"“{label}”已存在，请映射到已有字段。")
            definition = PartyDevelopmentFieldDefinition(
                key=key,
                label=label,
                field_type=str(mapping["create_type"]),
                aliases=[str(mapping["source_column"])],
                created_by=user.id,
            )
            db.add(definition)
            existing[key] = definition
            existing_labels[label_key] = definition
            created.append(key)
        return created
    if category is None:
        raise ProblemException(422, "LEDGER_TARGET_INVALID", "归档目标无效", "请选择档案类别。")
    schema = [dict(item) for item in (category.field_schema or [])]
    keys = {str(item.get("key")) for item in schema}
    labels = {re.sub(r"\s+", "", str(item.get("label", ""))).lower() for item in schema}
    for mapping in mappings:
        if mapping.get("action") != "create":
            continue
        key = str(mapping["target_field"])
        label = str(mapping["create_label"]).strip()
        label_key = re.sub(r"\s+", "", label).lower()
        if key in keys:
            continue
        if label_key in labels:
            raise ProblemException(409, "LEDGER_FIELD_LABEL_CONFLICT", "字段名称重复", f"“{label}”已存在，请映射到已有字段。")
        schema.append({"key": key, "label": label, "type": str(mapping["create_type"]), "required": False, "options": [], "active": True, "created_by_import": job.id})
        keys.add(key)
        labels.add(label_key)
        created.append(key)
    if created:
        category.field_schema = schema
        category.version += 1
    return created


def _record_change(
    db: Session,
    job: LedgerImportJob,
    row_number: int,
    entity_type: str,
    entity_id: str,
    action: str,
    before: dict[str, Any],
    after_version: int,
    new_fields: list[str] | None = None,
) -> None:
    db.add(
        LedgerImportChange(
            job_id=job.id,
            row_number=row_number,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_snapshot=before,
            after_version=after_version,
            new_field_keys=new_fields or [],
        )
    )


def _party_event_fields() -> dict[str, str]:
    return {
        "application_date": "application",
        "activist_date": "activist_date",
        "development_object_date": "development_object_date",
        "probationary_date": "probationary_status",
        "converted_date": "transition_approval",
        **PARTY_PROGRESS_FIELDS,
    }


def _append_progress_events(
    db: Session,
    job: LedgerImportJob,
    item: PartyDevelopmentCase,
    row_number: int,
    values: dict[str, Any],
    selected: set[str],
    action: str,
    user: User,
) -> None:
    for source_key, milestone_type in _party_event_fields().items():
        if source_key not in selected or not values.get(source_key):
            continue
        actual_at = _date_time(date.fromisoformat(str(values[source_key])))
        previous = db.scalar(
            select(PartyDevelopmentProgressEvent)
            .where(
                PartyDevelopmentProgressEvent.case_id == item.id,
                PartyDevelopmentProgressEvent.milestone_type == milestone_type,
                PartyDevelopmentProgressEvent.status == "confirmed",
            )
            .order_by(PartyDevelopmentProgressEvent.created_at.desc())
        )
        if previous and previous.actual_at == actual_at:
            continue
        if previous and action == "fill":
            continue
        if previous:
            previous.status = "superseded"
            previous.version += 1
        event = PartyDevelopmentProgressEvent(
            case_id=item.id,
            milestone_type=milestone_type,
            actual_at=actual_at,
            evidence_note="由台账导入并经用户确认",
            source_entity_type="ledger_import",
            source_entity_id=job.id,
            supersedes_event_id=previous.id if previous else None,
            import_job_id=job.id,
            created_by=user.id,
        )
        db.add(event)
        db.flush()
        _record_change(
            db,
            job,
            row_number,
            "party_development_progress_event",
            event.id,
            "create",
            {"supersedes_event_id": previous.id if previous else None},
            event.version,
        )


def _apply_party_values(
    item: PartyDevelopmentCase,
    values: dict[str, Any],
    selected: set[str],
    *,
    fill_only: bool,
) -> None:
    text_fields = {
        "party_committee",
        "party_branch",
        "name",
        "gender",
        "ethnicity",
        "education",
    }
    list_fields = {"training_contacts", "introducers"}
    for key in text_fields & selected:
        current = getattr(item, key)
        if not fill_only or _is_empty(current):
            setattr(item, key, str(values.get(key) or "").strip())
    for key in list_fields & selected:
        current = getattr(item, key)
        if not fill_only or _is_empty(current):
            setattr(item, key, list(values.get(key) or []))
    for incoming, stored in PARTY_DATE_COLUMNS.items():
        if incoming not in selected:
            continue
        current = getattr(item, stored)
        if not fill_only or _is_empty(current):
            setattr(item, stored, _date_time(date.fromisoformat(str(values[incoming]))) if values.get(incoming) else None)
    extra = dict(item.extra_fields or {})
    for key in selected - CORE_PARTY_KEYS:
        if not fill_only or _is_empty(extra.get(key)):
            extra[key] = values.get(key)
    item.extra_fields = extra
    item.stage = _next_stage({**values, "activist_date": item.activist_at, "development_object_date": item.development_object_at, "probationary_date": item.probationary_at, "converted_date": item.converted_at})


def _create_or_update_party(
    db: Session,
    job: LedgerImportJob,
    row: dict[str, Any],
    new_fields: list[str],
    user: User,
) -> None:
    values = row["values"]
    action = row["action"]
    matched = db.get(PartyDevelopmentCase, row["matched_id"]) if row["matched_id"] else None
    if matched and action == "skip":
        return
    if matched and action != "new":
        item = matched
        before = _party_snapshot(item)
        selected = _selected_update_fields(action, values)
        _apply_party_values(item, values, selected, fill_only=action == "fill")
        item.version += 1
        change_action = "update"
    else:
        profile = ensure_reference_plan_profile(db, user)
        item = PartyDevelopmentCase(
            party_committee=str(values["party_committee"]),
            party_branch=str(values["party_branch"]),
            name=str(values["name"]),
            gender=str(values.get("gender") or ""),
            ethnicity=str(values.get("ethnicity") or ""),
            birth_date=_date_time(date.fromisoformat(str(values["birth_date"]))) if values.get("birth_date") else None,
            education=str(values.get("education") or ""),
            application_at=_date_time(date.fromisoformat(str(values["application_date"]))),
            activist_at=_date_time(date.fromisoformat(str(values["activist_date"]))) if values.get("activist_date") else None,
            training_contacts=list(values.get("training_contacts") or []),
            introducers=list(values.get("introducers") or []),
            development_object_at=_date_time(date.fromisoformat(str(values["development_object_date"]))) if values.get("development_object_date") else None,
            probationary_at=_date_time(date.fromisoformat(str(values["probationary_date"]))) if values.get("probationary_date") else None,
            converted_at=_date_time(date.fromisoformat(str(values["converted_date"]))) if values.get("converted_date") else None,
            stage=_next_stage(values),
            rule_version=str(rule_metadata()["version"]),
            planning_profile_id=profile.id,
            planning_profile_snapshot={"system_key": profile.system_key, "name": profile.name, "version": profile.version, "assumptions": dict(profile.assumptions), "captured_at": utcnow().isoformat()},
            extra_fields={key: value for key, value in values.items() if key not in CORE_PARTY_KEYS},
            import_batch_id=job.id,
            created_by=user.id,
        )
        db.add(item)
        db.flush()
        selected = set(values)
        before = {}
        change_action = "create"
    _append_progress_events(db, job, item, row["row_number"], values, selected, action, user)
    # 延迟导入可避免路由模块初始化形成循环；计划计算仍复用统一领域实现。
    from .party_development import _apply_reference_plan

    _apply_reference_plan(db, item, bump_case_version=False)
    _record_change(db, job, row["row_number"], "party_development_case", item.id, change_action, before, item.version, new_fields if change_action == "create" else [])


def _restoreable_archive_values(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_no": str(values.get("document_no") or ""),
        "title": str(values.get("title") or ""),
        "summary": str(values.get("summary") or ""),
        "involved_persons": list(values.get("involved_persons") or []),
        "source_unit": str(values.get("source_unit") or ""),
        "document_date": _date_time(date.fromisoformat(str(values["document_date"]))) if values.get("document_date") else None,
        "person_name": str(values.get("person_name") or ""),
        "person_identifier": str(values.get("person_identifier") or ""),
        "personnel_type": str(values.get("personnel_type") or ""),
        "organization": str(values.get("organization") or ""),
        "assessment_result": str(values.get("assessment_result") or ""),
        "tags": list(values.get("tags") or []),
    }


def _create_or_update_archive(
    db: Session,
    job: LedgerImportJob,
    category: ArchiveCategory,
    row: dict[str, Any],
    new_fields: list[str],
    user: User,
) -> None:
    values = row["values"]
    action = row["action"]
    matched = db.get(ArchiveRecord, row["matched_id"]) if row["matched_id"] else None
    if matched and action == "skip":
        return
    selected = _selected_update_fields(action, values)
    custom = {key: value for key, value in values.items() if key not in CORE_ARCHIVE_KEYS}
    if matched and action != "new":
        record = matched
        before = record_snapshot(record)
        incoming = _restoreable_archive_values(values)
        for key in selected & CORE_ARCHIVE_KEYS - {"archive_year"}:
            if key not in incoming:
                continue
            current = getattr(record, key)
            if action != "fill" or _is_empty(current):
                setattr(record, key, incoming[key])
        current_custom = dict(record.custom_fields or {})
        for key in selected - CORE_ARCHIVE_KEYS:
            if action != "fill" or _is_empty(current_custom.get(key)):
                current_custom[key] = custom.get(key)
        record.custom_fields = validate_custom_fields(category, current_custom, legacy_values=record.custom_fields or {})
        validate_record_mode(category, {**record_snapshot(record), "custom_fields": record.custom_fields})
        record.updated_by = user.id
        record.version += 1
        change_action = "update"
    else:
        year = int(values["archive_year"])
        incoming = _restoreable_archive_values(values)
        record = ArchiveRecord(
            category_id=category.id,
            archive_year=year,
            sequence_no=next_sequence(db, category.id, year, None),
            custom_fields=validate_custom_fields(category, custom),
            import_batch_id=job.id,
            created_by=user.id,
            updated_by=user.id,
            **incoming,
        )
        validate_record_mode(category, {**incoming, "custom_fields": record.custom_fields})
        db.add(record)
        db.flush()
        before = {}
        change_action = "create"
    refresh_search_index(db, record.id)
    revision_no = int(db.scalar(select(func.max(ArchiveRecordRevision.revision_no)).where(ArchiveRecordRevision.record_id == record.id)) or 0) + 1
    db.add(ArchiveRecordRevision(record_id=record.id, revision_no=revision_no, snapshot=record_snapshot(record), change_note=f"台账导入批次 {job.id[:8]}", created_by=user.id))
    _record_change(db, job, row["row_number"], "archive_record", record.id, change_action, before, record.version, new_fields if change_action == "create" else [])


@router.post("/ledger-imports/{job_id}/commit", response_model=dict)
def commit_import(
    job_id: str,
    payload: LedgerImportCommitRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    job = _job(db, job_id, user)
    _assert_version(job, payload.version)
    if job.status == "committed":
        return _job_out(job)
    if job.status == "undone":
        raise ProblemException(409, "LEDGER_JOB_FINALIZED", "导入任务已撤销", "已撤销任务不能再次提交。")
    category = _ensure_target_permission(db, request, user, job.target_type, job.target_id)
    if not payload.confirm_shared_storage:
        raise ProblemException(422, "LEDGER_SHARED_STORAGE_CONFIRM_REQUIRED", "尚未确认共享归档", "请确认台账内容将进入 PartyOps 主机并按权限共享。")
    mappings = list((job.mapping or {}).get("columns") or [])
    if any(item.get("action") == "create" for item in mappings) and not payload.confirm_new_fields:
        raise ProblemException(422, "LEDGER_NEW_FIELD_CONFIRM_REQUIRED", "尚未确认新增字段", "请确认新增字段的类型和模块影响范围。")
    row_actions = payload.row_actions or dict((job.validation or {}).get("row_actions") or {})
    checked, summary = _validated_rows(db, job, row_actions)
    if summary["error_rows"]:
        job.validation = {**summary, "row_actions": row_actions}
        job.status = "validation_failed"
        job.error_rows = summary["error_rows"]
        job.version += 1
        db.commit()
        raise ProblemException(409, "LEDGER_VALIDATION_FAILED", "台账仍有待处理问题", "请返回全量校验步骤处理错误和重复记录。", extra={"validation": summary, "job_version": job.version})
    with db_runtime.write_lock:
        new_fields = _create_custom_fields(db, job, user, category)
        for row in checked:
            if job.target_type == "party_development":
                _create_or_update_party(db, job, row, new_fields, user)
            elif category is not None:
                _create_or_update_archive(db, job, category, row, new_fields, user)
        job.validation = {**summary, "row_actions": row_actions}
        job.valid_rows = summary["valid_rows"]
        job.warning_rows = summary["warning_rows"]
        job.error_rows = 0
        job.status = "committed"
        job.committed_at = utcnow()
        job.version += 1
        write_audit(db, user, "ledger_import.committed", "ledger_import_job", job.id, {"target_type": job.target_type, "target_id": job.target_id, "rows": summary["valid_rows"], "new_fields": new_fields}, client_ip(request))
        emit_event(db, "ledger_import.committed", job.id, {"target_type": job.target_type, "rows": summary["valid_rows"]})
        db.commit()
    delete_stage(job.id)
    return _job_out(job)


def _restore_party(item: PartyDevelopmentCase, snapshot: dict[str, Any]) -> None:
    for key in ("party_committee", "party_branch", "name", "gender", "ethnicity", "education", "stage", "status"):
        setattr(item, key, snapshot[key])
    for key in ("birth_date", "application_at", "activist_at", "development_object_at", "probationary_at", "converted_at"):
        value = snapshot.get(key)
        setattr(item, key, datetime.fromisoformat(value) if value else None)
    item.training_contacts = list(snapshot.get("training_contacts") or [])
    item.introducers = list(snapshot.get("introducers") or [])
    item.extra_fields = dict(snapshot.get("extra_fields") or {})


def _restore_archive(item: ArchiveRecord, snapshot: dict[str, Any]) -> None:
    for key in ("archive_year", "sequence_no", "document_no", "title", "summary", "source_unit", "person_name", "person_identifier", "personnel_type", "organization", "assessment_result", "void_reason"):
        setattr(item, key, snapshot[key])
    item.involved_persons = list(snapshot.get("involved_persons") or [])
    item.tags = list(snapshot.get("tags") or [])
    item.custom_fields = dict(snapshot.get("custom_fields") or {})
    item.document_date = datetime.fromisoformat(snapshot["document_date"]) if snapshot.get("document_date") else None
    item.status = ArchiveRecordStatus(snapshot["status"])


def _cleanup_undone_fields(
    db: Session, job: LedgerImportJob, changes: list[LedgerImportChange]
) -> None:
    keys = {
        key
        for change in changes
        for key in (change.new_field_keys or [])
        if key
    }
    if not keys:
        return
    if job.target_type == "party_development":
        other_cases = db.scalars(
            select(PartyDevelopmentCase).where(
                or_(
                    PartyDevelopmentCase.import_batch_id.is_(None),
                    PartyDevelopmentCase.import_batch_id != job.id,
                )
            )
        ).all()
        for key in keys:
            definition = db.scalar(
                select(PartyDevelopmentFieldDefinition).where(
                    PartyDevelopmentFieldDefinition.key == key
                )
            )
            if not definition:
                continue
            if any(key in (item.extra_fields or {}) for item in other_cases):
                definition.active = False
                definition.version += 1
            else:
                db.delete(definition)
        return
    category = db.get(ArchiveCategory, job.target_id) if job.target_id else None
    if not category:
        return
    other_records = db.scalars(
        select(ArchiveRecord).where(
            ArchiveRecord.category_id == category.id,
            or_(
                ArchiveRecord.import_batch_id.is_(None),
                ArchiveRecord.import_batch_id != job.id,
            ),
        )
    ).all()
    schema: list[dict[str, Any]] = []
    changed = False
    for definition in category.field_schema or []:
        key = str(definition.get("key") or "")
        if key not in keys:
            schema.append(dict(definition))
            continue
        changed = True
        if any(key in (item.custom_fields or {}) for item in other_records):
            schema.append({**definition, "active": False})
    if changed:
        category.field_schema = schema
        category.version += 1


@router.post("/ledger-imports/{job_id}/undo", response_model=dict)
def undo_import(
    job_id: str,
    payload: LedgerImportUndoRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    job = _job(db, job_id, user)
    _assert_version(job, payload.version)
    if job.status == "undone":
        return _job_out(job)
    if job.status != "committed":
        raise ProblemException(409, "LEDGER_NOT_COMMITTED", "导入尚未提交", "只有已提交的导入批次可以撤销。")
    _ensure_target_permission(db, request, user, job.target_type, job.target_id)
    changes = db.scalars(select(LedgerImportChange).where(LedgerImportChange.job_id == job.id, LedgerImportChange.status == "active").order_by(LedgerImportChange.created_at.desc())).all()
    conflicts: list[dict[str, str]] = []
    for change in changes:
        if change.entity_type == "party_development_case":
            entity = db.get(PartyDevelopmentCase, change.entity_id)
        elif change.entity_type == "archive_record":
            entity = db.get(ArchiveRecord, change.entity_id)
        else:
            entity = db.get(PartyDevelopmentProgressEvent, change.entity_id)
        if not entity or entity.version != change.after_version:
            conflicts.append({"entity_type": change.entity_type, "entity_id": change.entity_id, "reason": "记录已被后续修改或不存在"})
        elif change.entity_type == "party_development_progress_event" and entity.status != "confirmed":
            conflicts.append({"entity_type": change.entity_type, "entity_id": change.entity_id, "reason": "真实进度已被纠正或作废"})
    if conflicts:
        raise ProblemException(409, "LEDGER_UNDO_CONFLICT", "导入内容已被后续编辑", "为避免覆盖后续人工修改，本批次不能自动撤销。", extra={"conflicts": conflicts[:200], "conflict_count": len(conflicts)})
    with db_runtime.write_lock:
        for change in changes:
            if change.entity_type == "party_development_case":
                item = db.get(PartyDevelopmentCase, change.entity_id)
                if change.action == "create":
                    item.status = "archived"
                else:
                    _restore_party(item, change.before_snapshot)
                item.version += 1
            elif change.entity_type == "archive_record":
                record = db.get(ArchiveRecord, change.entity_id)
                if change.action == "create":
                    record.status = ArchiveRecordStatus.VOIDED
                    record.void_reason = f"撤销台账导入批次 {job.id[:8]}"
                    record.voided_at = utcnow()
                else:
                    _restore_archive(record, change.before_snapshot)
                record.updated_by = user.id
                record.version += 1
                refresh_search_index(db, record.id)
            else:
                event = db.get(PartyDevelopmentProgressEvent, change.entity_id)
                event.status = "voided"
                event.voided_at = utcnow()
                event.version += 1
                superseded_id = str(change.before_snapshot.get("supersedes_event_id") or "")
                if superseded_id:
                    previous = db.get(PartyDevelopmentProgressEvent, superseded_id)
                    if previous and previous.status == "superseded":
                        previous.status = "confirmed"
                        previous.version += 1
            change.status = "reverted"
            change.reverted_at = utcnow()
        _cleanup_undone_fields(db, job, changes)
        job.status = "undone"
        job.undone_at = utcnow()
        job.version += 1
        write_audit(db, user, "ledger_import.undone", "ledger_import_job", job.id, {"change_count": len(changes)}, client_ip(request))
        emit_event(db, "ledger_import.undone", job.id, {"change_count": len(changes)})
        db.commit()
    return _job_out(job)


@router.get("/ledger-imports/templates", response_model=typing.List[dict])
def list_templates(
    target_type: str,
    target_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = db.scalars(select(LedgerImportMappingTemplate).where(LedgerImportMappingTemplate.created_by == user.id, LedgerImportMappingTemplate.target_type == target_type, LedgerImportMappingTemplate.target_id == target_id, LedgerImportMappingTemplate.active.is_(True)).order_by(LedgerImportMappingTemplate.updated_at.desc())).all()
    return [_template_out(item) for item in rows]


@router.post("/ledger-imports/templates", response_model=dict, status_code=201)
def create_template(
    payload: LedgerImportTemplateCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _ensure_target_permission(db, request, user, payload.target_type, payload.target_id)
    existing = db.scalar(select(LedgerImportMappingTemplate).where(LedgerImportMappingTemplate.created_by == user.id, LedgerImportMappingTemplate.target_type == payload.target_type, LedgerImportMappingTemplate.target_id == payload.target_id, LedgerImportMappingTemplate.header_signature == payload.header_signature))
    if existing:
        existing.name = payload.name.strip()
        existing.mapping = payload.mapping
        existing.active = True
        existing.version += 1
        item = existing
    else:
        item = LedgerImportMappingTemplate(**payload.model_dump(), created_by=user.id)
        db.add(item)
    write_audit(db, user, "ledger_import.template_saved", "ledger_import_mapping_template", item.id, {"target_type": item.target_type}, client_ip(request))
    db.commit()
    return _template_out(item)


@router.patch("/ledger-imports/templates/{template_id}", response_model=dict)
def patch_template(
    template_id: str,
    payload: LedgerImportTemplatePatch,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    item = db.get(LedgerImportMappingTemplate, template_id)
    if not item or item.created_by != user.id:
        raise ProblemException(404, "LEDGER_TEMPLATE_NOT_FOUND", "映射模板不存在", "请刷新后重试。")
    if item.version != payload.version:
        raise ProblemException(409, "VERSION_CONFLICT", "映射模板已更新", "请刷新后重试。")
    if payload.name is not None:
        item.name = payload.name.strip()
    if payload.active is not None:
        item.active = payload.active
    item.version += 1
    write_audit(db, user, "ledger_import.template_updated", "ledger_import_mapping_template", item.id, {"active": item.active}, client_ip(request))
    db.commit()
    return _template_out(item)


@router.delete(
    "/ledger-imports/templates/{template_id}",
    status_code=204,
    response_class=Response,
)
def delete_template(
    template_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    item = db.get(LedgerImportMappingTemplate, template_id)
    if not item or item.created_by != user.id:
        raise ProblemException(404, "LEDGER_TEMPLATE_NOT_FOUND", "映射模板不存在", "请刷新后重试。")
    item.active = False
    item.version += 1
    write_audit(db, user, "ledger_import.template_disabled", "ledger_import_mapping_template", item.id, {}, client_ip(request))
    db.commit()
    return Response(status_code=204)
