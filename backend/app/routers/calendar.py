"""统一工作日历接口。"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..calendar_service import calendar_events, preference_for
from ..database import get_session
from ..enums import CalendarEventType
from ..models import User, WorkCalendarEntry
from ..problems import ProblemException
from ..schemas import (
    CalendarEventOut,
    CalendarPreferenceOut,
    CalendarPreferencePatch,
    WorkCalendarEntryOut,
    WorkCalendarImport,
)
from ..security import get_current_user, require_admin
from .router_utils import aware_utc, parse_if_match


router = APIRouter(tags=["calendar"])


@router.get("/calendar/events", response_model=list[CalendarEventOut])
def list_calendar_events(
    start: datetime = Query(),
    end: datetime = Query(),
    event_type: list[CalendarEventType] = Query(default=[]),
    owner_id: list[str] = Query(default=[]),
    work_area: list[str] = Query(default=[]),
    topic_id: list[str] = Query(default=[]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, object]]:
    aware_start = aware_utc(start)
    aware_end = aware_utc(end)
    if aware_end <= aware_start:
        raise ProblemException(
            422, "CALENDAR_RANGE_INVALID", "日历范围无效", "结束时间必须晚于开始时间。"
        )
    if aware_end - aware_start > timedelta(days=370):
        raise ProblemException(
            422, "CALENDAR_RANGE_TOO_LARGE", "日历范围过大", "单次最多查询 370 天。"
        )
    return calendar_events(
        db,
        user,
        start,
        end,
        event_types=set(event_type) or None,
        owner_ids=set(owner_id) or None,
        work_areas=set(work_area) or None,
        topic_ids=set(topic_id) or None,
    )


@router.get("/calendar/preferences", response_model=CalendarPreferenceOut)
def get_calendar_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> object:
    item = preference_for(db, user)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/calendar/preferences", response_model=CalendarPreferenceOut)
def patch_calendar_preferences(
    payload: CalendarPreferencePatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> object:
    item = preference_for(db, user)
    if item.version != parse_if_match(if_match):
        raise ProblemException(
            409, "VERSION_CONFLICT", "日历设置已变化", "请刷新后重试。"
        )
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        if field == "visible_event_types" and value is not None:
            value = [entry.value for entry in value]
        setattr(item, field, value)
    item.version += 1
    db.commit()
    db.refresh(item)
    return item


@router.get("/calendar/workdays", response_model=list[WorkCalendarEntryOut])
def list_calendar_workdays(
    year: int | None = Query(default=None, ge=1900, le=2200),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkCalendarEntry]:
    statement = select(WorkCalendarEntry).order_by(WorkCalendarEntry.date_key)
    if year:
        statement = statement.where(WorkCalendarEntry.date_key.like(f"{year:04d}-%"))
    return list(db.scalars(statement).all())


@router.post("/calendar/workdays/import", response_model=list[WorkCalendarEntryOut])
def import_calendar_workdays(
    payload: WorkCalendarImport,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[WorkCalendarEntry]:
    result: list[WorkCalendarEntry] = []
    for value in payload.items:
        item = db.scalar(
            select(WorkCalendarEntry).where(
                WorkCalendarEntry.date_key == value.date_key,
                WorkCalendarEntry.kind == value.kind,
            )
        )
        if item is None:
            item = WorkCalendarEntry(owner_id=admin.id, **value.model_dump())
            db.add(item)
        else:
            item.title = value.title
            item.is_workday = value.is_workday
            item.note = value.note
            item.version += 1
        result.append(item)
    write_audit(
        db,
        admin,
        "calendar.workdays_import",
        "work_calendar",
        None,
        {"count": len(result)},
    )
    db.commit()
    for item in result:
        db.refresh(item)
    return result
