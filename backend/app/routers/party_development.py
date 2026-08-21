"""党员发展规则计算、Word 导出和单位补充材料管理。"""

from __future__ import annotations

import io
import typing

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from docx import Document
from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..audit import write_audit
from ..config import get_settings
from ..database import get_session
from ..models import (
    PartyDevelopmentCase,
    PartyDevelopmentMaterial,
    PartyDevelopmentMilestone,
    PartyDevelopmentProfile,
    User,
    WorkCalendarEntry,
    utcnow,
)
from ..party_development import (
    calculate_party_development,
    ensure_reference_profile,
    export_result_docx,
    profile_to_dict,
    rule_metadata,
    safe_person_filename,
    supplemental_materials,
)
from ..problems import ProblemException
from ..schemas import (
    PartyDevelopmentCalculateRequest,
    PartyDevelopmentActualDates,
    PartyDevelopmentCaseCreate,
    PartyDevelopmentCasePatch,
    PartyDevelopmentMilestonePatch,
    PartyDevelopmentMaterialInput,
    PartyDevelopmentProfileCreate,
    PartyDevelopmentProfileOut,
    PartyDevelopmentProfilePatch,
    PartyDevelopmentResultOut,
)
from ..security import get_current_user, require_admin
from .router_utils import client_ip, parse_if_match


router = APIRouter(tags=["party-development"])
settings = get_settings()


def _as_datetime(value: date | None) -> datetime | None:
    return datetime.combine(value, time.min, tzinfo=timezone.utc) if value else None


def _as_date(value: datetime | None) -> date | None:
    return value.date() if value else None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _case_payload(item: PartyDevelopmentCase) -> PartyDevelopmentCalculateRequest:
    return PartyDevelopmentCalculateRequest(
        name=item.name,
        application_date=item.application_at.date(),
        actual_dates=PartyDevelopmentActualDates(
            activist_date=_as_date(item.activist_at),
            development_object_date=_as_date(item.development_object_at),
            branch_acceptance_date=_as_date(item.probationary_at),
            transition_approval_date=_as_date(item.converted_at),
        ),
    )


def _case_out(db: Session, item: PartyDevelopmentCase) -> dict[str, typing.Any]:
    milestones = db.scalars(
        select(PartyDevelopmentMilestone)
        .where(PartyDevelopmentMilestone.case_id == item.id)
        .order_by(PartyDevelopmentMilestone.planned_at, PartyDevelopmentMilestone.milestone_type)
    ).all()
    return {
        "id": item.id,
        "party_committee": item.party_committee,
        "party_branch": item.party_branch,
        "name": item.name,
        "gender": item.gender,
        "ethnicity": item.ethnicity,
        "birth_date": item.birth_date,
        "education": item.education,
        "application_at": item.application_at,
        "activist_at": item.activist_at,
        "training_contacts": item.training_contacts,
        "introducers": item.introducers,
        "development_object_at": item.development_object_at,
        "probationary_at": item.probationary_at,
        "converted_at": item.converted_at,
        "stage": item.stage,
        "status": item.status,
        "rule_version": item.rule_version,
        "version": item.version,
        "milestones": [
            {
                "id": row.id,
                "milestone_type": row.milestone_type,
                "actual_at": row.actual_at,
                "legal_earliest_at": row.legal_earliest_at,
                "legal_deadline_at": row.legal_deadline_at,
                "planned_at": row.planned_at,
                "adjusted_at": row.adjusted_at,
                "rule_version": row.rule_version,
                "legal_basis": row.legal_basis,
                "plan_kind": row.plan_kind,
                "reminder_days": row.reminder_days,
                "version": row.version,
            }
            for row in milestones
        ],
    }


def _profile(db: Session, profile_id: str) -> PartyDevelopmentProfile:
    profile = db.get(PartyDevelopmentProfile, profile_id)
    if not profile:
        raise ProblemException(404, "PARTY_DEVELOPMENT_PROFILE_NOT_FOUND", "补充材料模板不存在", "请刷新后重试。")
    return profile


def _check_version(profile: PartyDevelopmentProfile, if_match: str | None) -> None:
    if profile.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "补充材料模板已更新", "请刷新后重试。")


def _add_items(
    db: Session,
    profile: PartyDevelopmentProfile,
    items: list[PartyDevelopmentMaterialInput],
    admin: User,
) -> None:
    identities: set[tuple[str, str]] = set()
    for item in items:
        name = item.name.strip()
        identity = (item.phase, name.casefold())
        if identity in identities:
            raise ProblemException(422, "MATERIAL_DUPLICATE", "补充材料重复", f"{name} 在同一阶段重复。")
        identities.add(identity)
        db.add(PartyDevelopmentMaterial(
            profile_id=profile.id,
            phase=item.phase,
            name=name,
            responsible_party=item.responsible_party.strip(),
            guidance=item.guidance.strip(),
            required=item.required,
            enabled=item.enabled,
            sort_order=item.sort_order,
            created_by=admin.id,
        ))


@router.get("/party-development/rules/current", response_model=dict)
def current_rules(_user: User = Depends(get_current_user)) -> dict:
    return rule_metadata()


@router.post("/party-development/calculate", response_model=PartyDevelopmentResultOut)
def calculate(
    payload: PartyDevelopmentCalculateRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PartyDevelopmentResultOut:
    entries = db.scalars(select(WorkCalendarEntry)).all()
    return calculate_party_development(
        payload,
        entries,
        supplemental_materials(db, payload.profile_ids),
    )


@router.get("/party-development/cases", response_model=typing.List[dict])
def list_cases(
    party_committee: str = "",
    party_branch: str = "",
    case_status: str = "active",
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, typing.Any]]:
    query = select(PartyDevelopmentCase)
    if party_committee:
        query = query.where(PartyDevelopmentCase.party_committee == party_committee)
    if party_branch:
        query = query.where(PartyDevelopmentCase.party_branch == party_branch)
    if case_status:
        query = query.where(PartyDevelopmentCase.status == case_status)
    return [_case_out(db, item) for item in db.scalars(query.order_by(PartyDevelopmentCase.name)).all()]


@router.post("/party-development/cases", response_model=dict, status_code=201)
def create_case(
    payload: PartyDevelopmentCaseCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    item = PartyDevelopmentCase(
        party_committee=payload.party_committee.strip(),
        party_branch=payload.party_branch.strip(),
        name=payload.name.strip(),
        gender=payload.gender.strip(),
        ethnicity=payload.ethnicity.strip(),
        birth_date=_as_datetime(payload.birth_date),
        education=payload.education.strip(),
        application_at=_as_datetime(payload.application_date),
        activist_at=_as_datetime(payload.activist_date),
        training_contacts=[value.strip() for value in payload.training_contacts if value.strip()],
        introducers=[value.strip() for value in payload.introducers if value.strip()],
        development_object_at=_as_datetime(payload.development_object_date),
        probationary_at=_as_datetime(payload.probationary_date),
        converted_at=_as_datetime(payload.converted_date),
        rule_version=str(rule_metadata()["version"]),
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    write_audit(db, user, "party_development.case_create", "party_development_case", item.id, {"party_branch": item.party_branch}, client_ip(request))
    db.commit()
    return _case_out(db, item)


@router.patch("/party-development/cases/{case_id}", response_model=dict)
def patch_case(
    case_id: str,
    payload: PartyDevelopmentCasePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    item = db.get(PartyDevelopmentCase, case_id)
    if not item:
        raise ProblemException(404, "PARTY_DEVELOPMENT_CASE_NOT_FOUND", "发展档案不存在", "请刷新后重试。")
    if item.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "发展档案已更新", "请刷新后重试。")
    changes = payload.model_dump(exclude_unset=True)
    date_fields = {
        "birth_date": "birth_date",
        "activist_date": "activist_at",
        "development_object_date": "development_object_at",
        "probationary_date": "probationary_at",
        "converted_date": "converted_at",
    }
    for incoming, stored in date_fields.items():
        if incoming in changes:
            setattr(item, stored, _as_datetime(changes.pop(incoming)))
    for key, value in changes.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    item.version += 1
    write_audit(db, user, "party_development.case_update", "party_development_case", item.id, {"fields": sorted(payload.model_fields_set)}, client_ip(request))
    db.commit()
    return _case_out(db, item)


@router.post("/party-development/cases/{case_id}/generate-milestones", response_model=dict)
def generate_case_milestones(
    case_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    """生成法定边界和参考计划，绝不把计算结果写入 actual_at。"""

    item = db.get(PartyDevelopmentCase, case_id)
    if not item:
        raise ProblemException(404, "PARTY_DEVELOPMENT_CASE_NOT_FOUND", "发展档案不存在", "请刷新后重试。")
    result = calculate_party_development(
        _case_payload(item),
        db.scalars(select(WorkCalendarEntry)).all(),
        supplemental_materials(db, []),
    )
    actual_by_key = {
        "application": item.application_at,
        "activist_date": item.activist_at,
        "development_object_date": item.development_object_at,
        "branch_acceptance": item.probationary_at,
        "transition_approval_deadline": item.converted_at,
    }
    previous = {
        row.milestone_type: row
        for row in db.scalars(select(PartyDevelopmentMilestone).where(PartyDevelopmentMilestone.case_id == item.id)).all()
    }
    generated: set[str] = set()
    for node in result.nodes:
        if node.date is None and node.key not in actual_by_key:
            continue
        generated.add(node.key)
        row = previous.get(node.key)
        is_new = row is None
        if is_new:
            row = PartyDevelopmentMilestone(case_id=item.id, milestone_type=node.key, version=1)
            db.add(row)
        row.actual_at = actual_by_key.get(node.key)
        row.legal_earliest_at = _as_datetime(node.date) if node.date_kind == "earliest" else None
        row.legal_deadline_at = _as_datetime(node.end_date or node.date) if node.date_kind in {"deadline", "workday_window"} else None
        row.planned_at = _as_datetime(node.date) if node.date_kind in {"window", "earliest", "deadline", "workday_window"} else None
        row.rule_version = result.rule_version
        row.legal_basis = f"{node.article}：{node.basis}"
        row.plan_kind = "legal" if node.date_kind in {"earliest", "deadline", "workday_window"} else "reference"
        if not is_new:
            row.version += 1
    item.rule_version = result.rule_version
    item.version += 1
    write_audit(db, user, "party_development.milestones_generate", "party_development_case", item.id, {"rule_version": result.rule_version, "count": len(generated)}, client_ip(request))
    db.commit()
    return _case_out(db, item)


@router.patch("/party-development/milestones/{milestone_id}", response_model=dict)
def patch_milestone(
    milestone_id: str,
    payload: PartyDevelopmentMilestonePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    row = db.get(PartyDevelopmentMilestone, milestone_id)
    if not row:
        raise ProblemException(404, "PARTY_DEVELOPMENT_MILESTONE_NOT_FOUND", "节点不存在", "请刷新后重试。")
    if row.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "节点已更新", "请刷新后重试。")
    if "actual_date" in payload.model_fields_set:
        row.actual_at = _as_datetime(payload.actual_date)
    if "adjusted_date" in payload.model_fields_set:
        row.adjusted_at = _as_datetime(payload.adjusted_date)
    if payload.reminder_days is not None:
        if any(value < 0 or value > 3650 for value in payload.reminder_days):
            raise ProblemException(422, "REMINDER_DAYS_INVALID", "提醒天数无效", "提醒天数必须在 0 到 3650 天之间。")
        row.reminder_days = sorted(set(payload.reminder_days), reverse=True)
    row.version += 1
    write_audit(db, user, "party_development.milestone_update", "party_development_milestone", row.id, {"fields": sorted(payload.model_fields_set)}, client_ip(request))
    db.commit()
    return _case_out(db, db.get(PartyDevelopmentCase, row.case_id))


@router.get("/party-development/statistics", response_model=dict)
def development_statistics(
    party_committee: str = "",
    party_branch: str = "",
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    query = select(PartyDevelopmentCase).where(PartyDevelopmentCase.status == "active")
    if party_committee:
        query = query.where(PartyDevelopmentCase.party_committee == party_committee)
    if party_branch:
        query = query.where(PartyDevelopmentCase.party_branch == party_branch)
    cases = db.scalars(query).all()
    stage_counts: dict[str, int] = {}
    for item in cases:
        stage_counts[item.stage] = stage_counts.get(item.stage, 0) + 1
    case_ids = [item.id for item in cases]
    milestones = db.scalars(select(PartyDevelopmentMilestone).where(PartyDevelopmentMilestone.case_id.in_(case_ids))).all() if case_ids else []
    now = utcnow()
    targets = [
        (row, _aware(row.adjusted_at or row.planned_at or row.legal_deadline_at))
        for row in milestones
    ]
    upcoming = sum(1 for row, target in targets if not row.actual_at and target and now <= target <= now + timedelta(days=60))
    overdue = sum(1 for row, target in targets if not row.actual_at and target and target < now)
    return {"total": len(cases), "stage_counts": stage_counts, "upcoming_60_days": upcoming, "overdue": overdue}


def _export_cases_query(party_committee: str, party_branch: str):
    query = select(PartyDevelopmentCase).where(PartyDevelopmentCase.status == "active")
    if party_committee:
        query = query.where(PartyDevelopmentCase.party_committee == party_committee)
    if party_branch:
        query = query.where(PartyDevelopmentCase.party_branch == party_branch)
    return query.order_by(PartyDevelopmentCase.party_committee, PartyDevelopmentCase.party_branch, PartyDevelopmentCase.name)


def _display_date(value: datetime | None) -> str:
    return value.date().isoformat() if value else ""


def _development_export_rows(cases: list[PartyDevelopmentCase]) -> list[list[str]]:
    return [
        [
            item.party_committee,
            item.party_branch,
            item.name,
            item.gender,
            item.ethnicity,
            _display_date(item.birth_date),
            item.education,
            _display_date(item.application_at),
            _display_date(item.activist_at),
            "、".join(item.training_contacts or []),
            "、".join(item.introducers or []),
            _display_date(item.development_object_at),
            _display_date(item.probationary_at),
            _display_date(item.converted_at),
            item.stage,
        ]
        for item in cases
    ]


DEVELOPMENT_EXPORT_HEADERS = [
    "所属党委",
    "所属党支部",
    "姓名",
    "性别",
    "民族",
    "出生年月日",
    "文化程度",
    "提交入党申请时间",
    "列为积极分子时间",
    "培养联系人",
    "入党介绍人",
    "列为发展对象时间",
    "列为预备党员时间",
    "预备党员转正时间",
    "当前阶段",
]


@router.get("/party-development/cases/export.docx")
def export_development_cases_docx(
    request: Request,
    party_committee: str = "",
    party_branch: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """导出当前筛选范围内的发展党员档案汇总表。"""

    cases = list(db.scalars(_export_cases_query(party_committee, party_branch)).all())
    document = Document()
    document.add_heading("党员发展情况统计表", level=1)
    document.add_paragraph(f"档案人数：{len(cases)} 人；规则版本：{rule_metadata()['version']}")
    table = document.add_table(rows=1, cols=len(DEVELOPMENT_EXPORT_HEADERS))
    table.style = "Table Grid"
    for index, heading in enumerate(DEVELOPMENT_EXPORT_HEADERS):
        table.rows[0].cells[index].text = heading
    for row in _development_export_rows(cases):
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = value
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    write_audit(db, user, "party_development.cases_exported", "party_development_case", None, {"format": "docx", "count": len(cases)}, client_ip(request))
    db.commit()
    filename = quote("党员发展情况统计表.docx")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/party-development/cases/export.xlsx")
def export_development_cases_xlsx(
    request: Request,
    party_committee: str = "",
    party_branch: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """导出可继续筛选、统计的发展党员档案工作簿。"""

    cases = list(db.scalars(_export_cases_query(party_committee, party_branch)).all())
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "党员发展情况"
    sheet.append(DEVELOPMENT_EXPORT_HEADERS)
    for row in _development_export_rows(cases):
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(28, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    write_audit(db, user, "party_development.cases_exported", "party_development_case", None, {"format": "xlsx", "count": len(cases)}, client_ip(request))
    db.commit()
    filename = quote("党员发展情况统计表.xlsx")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _delete_temporary_export(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # 失败时不影响已经完成的下载；诊断清理会处理 exports 中的旧临时文件。
        pass


@router.post("/party-development/export.docx")
def export_docx(
    payload: PartyDevelopmentCalculateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    entries = db.scalars(select(WorkCalendarEntry)).all()
    result = calculate_party_development(
        payload,
        entries,
        supplemental_materials(db, payload.profile_ids),
    )
    path = export_result_docx(result, settings.exports_dir)
    write_audit(
        db,
        user,
        "party_development.exported",
        "party_development_calculation",
        None,
        {"rule_version": result.rule_version, "node_count": len(result.nodes), "warning_count": len(result.warnings)},
        client_ip(request),
    )
    db.commit()
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{safe_person_filename(payload.name)}-党员发展时间节点.docx",
        background=BackgroundTask(_delete_temporary_export, path),
    )


@router.get("/admin/party-development/profiles", response_model=typing.List[PartyDevelopmentProfileOut])
def list_profiles(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[dict]:
    ensure_reference_profile(db, admin)
    db.commit()
    profiles = db.scalars(
        select(PartyDevelopmentProfile).order_by(PartyDevelopmentProfile.created_at, PartyDevelopmentProfile.name)
    ).all()
    return [profile_to_dict(db, profile) for profile in profiles]


@router.post(
    "/admin/party-development/profiles",
    response_model=PartyDevelopmentProfileOut,
    status_code=status.HTTP_201_CREATED,
)
def create_profile(
    payload: PartyDevelopmentProfileCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    profile = PartyDevelopmentProfile(
        name=payload.name.strip(),
        description=payload.description.strip(),
        source_label=payload.source_label.strip(),
        active=payload.active,
        created_by=admin.id,
    )
    db.add(profile)
    try:
        db.flush()
        _add_items(db, profile, payload.items, admin)
        write_audit(db, admin, "party_development.profile_created", "party_development_profile", profile.id, {
            "active": profile.active, "item_count": len(payload.items),
        }, client_ip(request))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ProblemException(409, "PROFILE_NAME_EXISTS", "模板名称已存在", "请换一个模板名称。") from exc
    db.refresh(profile)
    return profile_to_dict(db, profile)


@router.patch("/admin/party-development/profiles/{profile_id}", response_model=PartyDevelopmentProfileOut)
def patch_profile(
    profile_id: str,
    payload: PartyDevelopmentProfilePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    profile = _profile(db, profile_id)
    _check_version(profile, if_match)
    changes = payload.model_dump(exclude_unset=True)
    for field in ("name", "description", "source_label"):
        if field in changes:
            changes[field] = changes[field].strip()
    for key, value in changes.items():
        setattr(profile, key, value)
    profile.version += 1
    write_audit(db, admin, "party_development.profile_updated", "party_development_profile", profile.id, {
        "fields": sorted(changes), "active": profile.active,
    }, client_ip(request))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ProblemException(409, "PROFILE_NAME_EXISTS", "模板名称已存在", "请换一个模板名称。") from exc
    db.refresh(profile)
    return profile_to_dict(db, profile)


@router.delete(
    "/admin/party-development/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_profile(
    profile_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Response:
    profile = _profile(db, profile_id)
    _check_version(profile, if_match)
    detail = {"active": profile.active}
    db.delete(profile)
    write_audit(db, admin, "party_development.profile_deleted", "party_development_profile", profile_id, detail, client_ip(request))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/admin/party-development/profiles/{profile_id}/items", response_model=PartyDevelopmentProfileOut)
def replace_profile_items(
    profile_id: str,
    payload: list[PartyDevelopmentMaterialInput],
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    if len(payload) > 200:
        raise ProblemException(422, "TOO_MANY_MATERIALS", "补充材料过多", "单个模板最多保存 200 条材料。")
    profile = _profile(db, profile_id)
    _check_version(profile, if_match)
    db.execute(delete(PartyDevelopmentMaterial).where(PartyDevelopmentMaterial.profile_id == profile.id))
    _add_items(db, profile, payload, admin)
    profile.version += 1
    write_audit(db, admin, "party_development.materials_replaced", "party_development_profile", profile.id, {
        "item_count": len(payload),
    }, client_ip(request))
    db.commit()
    db.refresh(profile)
    return profile_to_dict(db, profile)
