"""周期规则预览、跳过和临时改期接口。"""

from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..database import get_session
from ..enums import ObjectType, RecurrenceExceptionAction, UserRole
from ..models import ActivityEvent, RecurrenceException, RecurrenceRule, User
from ..problems import ProblemException
from ..recurrence import as_utc, preview_occurrences
from ..schemas import RecurrenceExceptionCreate, RecurrenceExceptionOut
from ..security import get_current_user, require_admin
from .router_utils import client_ip, parse_if_match

router = APIRouter(tags=["recurrence-exceptions"])


@router.get("/recurrences/{rule_id}/preview", response_model=typing.List[dict])
def get_recurrence_preview(
    rule_id: str,
    count: int = Query(default=12, ge=1, le=60),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, object]]:
    rule = db.get(RecurrenceRule, rule_id)
    if not rule or (user.role != UserRole.ADMIN and rule.owner_id != user.id):
        raise ProblemException(
            404, "RECURRENCE_NOT_FOUND", "周期规则不存在", "未找到该周期规则。"
        )
    return preview_occurrences(db, rule, count=count)


@router.post(
    "/recurrences/{rule_id}/exceptions",
    response_model=RecurrenceExceptionOut,
    status_code=201,
)
def create_recurrence_exception(
    rule_id: str,
    payload: RecurrenceExceptionCreate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> RecurrenceException:
    rule = db.get(RecurrenceRule, rule_id)
    if not rule:
        raise ProblemException(
            404, "RECURRENCE_NOT_FOUND", "周期规则不存在", "未找到该周期规则。"
        )
    if rule.version != parse_if_match(if_match):
        raise ProblemException(
            409, "VERSION_CONFLICT", "周期规则已变化", "请刷新后重试。"
        )
    if (
        payload.action == RecurrenceExceptionAction.RESCHEDULE
        and payload.rescheduled_at is None
    ):
        raise ProblemException(
            422, "RESCHEDULE_TIME_REQUIRED", "缺少改期时间", "改期必须选择新的办理日期。"
        )
    occurrence_at = as_utc(payload.occurrence_at)
    rescheduled_at = as_utc(payload.rescheduled_at) if payload.rescheduled_at else None
    existing = db.scalar(
        select(RecurrenceException).where(
            RecurrenceException.rule_id == rule.id,
            RecurrenceException.occurrence_at == occurrence_at,
        )
    )
    if existing:
        raise ProblemException(
            409, "RECURRENCE_EXCEPTION_EXISTS", "该次计划已有例外", "请刷新周期预览。"
        )
    item = RecurrenceException(
        rule_id=rule.id,
        created_by=admin.id,
        occurrence_at=occurrence_at,
        action=payload.action,
        rescheduled_at=rescheduled_at,
        reason=payload.reason,
    )
    db.add(item)
    rule.version += 1
    db.add(
        ActivityEvent(
            object_type=ObjectType.TASK,
            object_id=rule.last_task_id or rule.id,
            event_code="recurrence.exception",
            actor_id=admin.id,
            event_data={
                "rule_id": rule.id,
                "action": payload.action.value,
                "reason": payload.reason,
            },
        )
    )
    write_audit(
        db,
        admin,
        "recurrence.exception_create",
        "recurrence",
        rule.id,
        {"action": payload.action.value},
        client_ip(request),
    )
    db.commit()
    db.refresh(item)
    return item
