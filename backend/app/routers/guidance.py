"""用户引导进度与管理员投影维护接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..database import db_runtime, get_session
from ..models import OnboardingProgress, ProjectionCheckpoint, User
from ..problems import ProblemException
from ..projections import rebuild_report_projection
from ..schemas import (
    OnboardingProgressOut,
    OnboardingProgressPatch,
    ProjectionCheckpointOut,
)
from ..security import get_current_user, require_admin
from .router_utils import client_ip, parse_if_match


router = APIRouter(tags=["guidance-and-projections"])
ONBOARDING_STEPS = [
    {"key": "profile", "title": "确认账号和提醒时间", "route": "/settings"},
    {"key": "first_task", "title": "建立第一项工作", "route": "/tasks"},
    {"key": "calendar", "title": "查看本周工作日历", "route": "/calendar"},
    {"key": "workspace", "title": "接入只读原始文件夹", "route": "/workspace"},
    {"key": "report", "title": "检查本周完成和下周计划", "route": "/reports"},
    {"key": "start", "title": "掌握第一次使用流程", "route": "/help"},
    {"key": "inbox", "title": "掌握快速收件建档", "route": "/help"},
    {"key": "daily", "title": "掌握每日使用流程", "route": "/help"},
    {"key": "recurrence", "title": "掌握长期周期事项", "route": "/help"},
    {"key": "files", "title": "掌握文件中心与版本", "route": "/help"},
    {"key": "reports", "title": "掌握周期报告与交接", "route": "/help"},
    {"key": "archives", "title": "掌握重要档案归档", "route": "/help"},
    {"key": "devices", "title": "掌握多设备协同", "route": "/help"},
    {"key": "ai", "title": "掌握 AI 安全边界", "route": "/help"},
    {"key": "admin", "title": "掌握管理员维护", "route": "/help"},
]


@router.get("/me/onboarding", response_model=OnboardingProgressOut)
def get_onboarding(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> object:
    item = db.scalar(
        select(OnboardingProgress).where(OnboardingProgress.user_id == user.id)
    )
    if item is None:
        item = OnboardingProgress(user_id=user.id)
        db.add(item)
        db.commit()
        db.refresh(item)
    return OnboardingProgressOut.model_validate(item).model_copy(
        update={"steps": ONBOARDING_STEPS}
    )


@router.patch("/me/onboarding", response_model=OnboardingProgressOut)
def patch_onboarding(
    payload: OnboardingProgressPatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> object:
    item = db.scalar(
        select(OnboardingProgress).where(OnboardingProgress.user_id == user.id)
    )
    if item is None:
        item = OnboardingProgress(user_id=user.id)
        db.add(item)
        db.flush()
    if item.version != parse_if_match(if_match):
        raise ProblemException(
            409, "VERSION_CONFLICT", "引导进度已变化", "请刷新后重试。"
        )
    if payload.completed_steps is not None:
        allowed = {step["key"] for step in ONBOARDING_STEPS}
        item.completed_steps = sorted(
            allowed & {value for value in payload.completed_steps if value}
        )
    if payload.dismissed is not None:
        item.dismissed = payload.dismissed
    item.version += 1
    db.commit()
    db.refresh(item)
    return OnboardingProgressOut.model_validate(item).model_copy(
        update={"steps": ONBOARDING_STEPS}
    )


@router.get(
    "/admin/projections/status",
    response_model=list[ProjectionCheckpointOut],
)
def projection_status(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[ProjectionCheckpoint]:
    return list(
        db.scalars(
            select(ProjectionCheckpoint).order_by(ProjectionCheckpoint.name)
        ).all()
    )


@router.post(
    "/admin/projections/rebuild",
    response_model=ProjectionCheckpointOut,
)
def rebuild_projections(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ProjectionCheckpoint:
    with db_runtime.write_lock:
        checkpoint = rebuild_report_projection(db, admin)
        write_audit(
            db,
            admin,
            "projection.rebuild",
            "projection",
            checkpoint.name,
            {"processed_count": checkpoint.processed_count},
            client_ip(request),
        )
        db.commit()
        db.refresh(checkpoint)
        return checkpoint
