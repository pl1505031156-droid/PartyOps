"""东方主题上下文、用户偏好与管理员默认设置接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from ..appearance import (
    GLOBAL_APPEARANCE_KEY,
    effective_season,
    ensure_user_appearance,
    global_appearance,
)
from ..audit import emit_event, write_audit
from ..database import get_session
from ..models import SystemSetting, User, UserAppearancePreference
from ..problems import ProblemException
from ..schemas import (
    AdminAppearanceOut,
    AdminAppearancePatch,
    AppearanceContextOut,
    UserAppearanceOut,
    UserAppearancePatch,
)
from ..security import get_current_user, get_current_user_optional, require_admin
from .router_utils import client_ip, parse_if_match


router = APIRouter(tags=["appearance"])


@router.get("/appearance/context", response_model=AppearanceContextOut)
def get_appearance_context(
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_session),
) -> AppearanceContextOut:
    config = global_appearance(db)
    preference = db.get(UserAppearancePreference, user.id) if user else None
    return AppearanceContextOut(
        effective_season=effective_season(config, preference),
        art_level=(preference.art_level.value if preference else config["default_art_level"]),
        reduce_motion=(preference.reduce_motion if preference else bool(config["default_reduce_motion"])),
        theme_mode=config["theme_mode"],
    )


@router.get("/me/appearance", response_model=UserAppearanceOut)
def get_my_appearance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserAppearanceOut:
    preference = ensure_user_appearance(db, user)
    db.commit()
    db.refresh(preference)
    return preference


@router.patch("/me/appearance", response_model=UserAppearanceOut)
def patch_my_appearance(
    payload: UserAppearancePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> UserAppearanceOut:
    preference = ensure_user_appearance(db, user)
    if preference.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "外观偏好已更新", "请刷新后重试。")
    preference.art_level = payload.art_level
    preference.reduce_motion = payload.reduce_motion
    preference.theme_override = payload.theme_override
    preference.version += 1
    write_audit(db, user, "appearance.user_update", "user_appearance", user.id, {
        "art_level": payload.art_level.value,
        "reduce_motion": payload.reduce_motion,
        "theme_override": payload.theme_override.value if payload.theme_override else None,
    }, client_ip(request))
    emit_event(db, "appearance.updated", user.id, {})
    db.commit()
    db.refresh(preference)
    return preference


@router.get("/admin/appearance", response_model=AdminAppearanceOut)
def get_admin_appearance(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AdminAppearanceOut:
    return AdminAppearanceOut.model_validate(global_appearance(db))


@router.patch("/admin/appearance", response_model=AdminAppearanceOut)
def patch_admin_appearance(
    payload: AdminAppearancePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AdminAppearanceOut:
    current = global_appearance(db)
    if current["version"] != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "全局主题设置已更新", "请刷新后重试。")
    value = {
        "theme_mode": payload.theme_mode,
        "fixed_theme": payload.fixed_theme.value,
        "default_art_level": payload.default_art_level.value,
        "default_reduce_motion": payload.default_reduce_motion,
        "version": current["version"] + 1,
    }
    setting = db.get(SystemSetting, GLOBAL_APPEARANCE_KEY)
    if setting:
        setting.value = value
    else:
        db.add(SystemSetting(key=GLOBAL_APPEARANCE_KEY, value=value))
    write_audit(db, admin, "appearance.admin_update", "system_setting", GLOBAL_APPEARANCE_KEY, value, client_ip(request))
    emit_event(db, "appearance.global_updated", GLOBAL_APPEARANCE_KEY, {})
    db.commit()
    return AdminAppearanceOut.model_validate(value)
