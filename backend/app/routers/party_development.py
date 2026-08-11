"""党员发展规则计算、Word 导出和单位补充材料管理。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..audit import write_audit
from ..config import get_settings
from ..database import get_session
from ..models import PartyDevelopmentMaterial, PartyDevelopmentProfile, User, WorkCalendarEntry
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


@router.get("/admin/party-development/profiles", response_model=list[PartyDevelopmentProfileOut])
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


@router.delete("/admin/party-development/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    profile = _profile(db, profile_id)
    _check_version(profile, if_match)
    detail = {"active": profile.active}
    db.delete(profile)
    write_audit(db, admin, "party_development.profile_deleted", "party_development_profile", profile_id, detail, client_ip(request))
    db.commit()


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
