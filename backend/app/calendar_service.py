"""统一工作日历投影。"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import CalendarEventType, ObjectType, TaskStatus, UserRole
from .models import (
    CalendarPreference,
    Notification,
    ObjectLink,
    PeriodReport,
    RecurrenceRule,
    Task,
    User,
    WorkCalendarEntry,
)
from .task_service import visible_tasks

LOCAL_TIMEZONE = timezone(timedelta(hours=8))
OPEN_STATUSES = {
    TaskStatus.PENDING_RECEIPT,
    TaskStatus.PENDING_BREAKDOWN,
    TaskStatus.IN_PROGRESS,
    TaskStatus.WAITING_FEEDBACK,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.RETURNED,
}


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def preference_for(db: Session, user: User) -> CalendarPreference:
    item = db.scalar(
        select(CalendarPreference).where(CalendarPreference.user_id == user.id)
    )
    if item is None:
        item = CalendarPreference(
            user_id=user.id,
            visible_event_types=[value.value for value in CalendarEventType],
        )
        db.add(item)
        db.flush()
    return item


def _topic_ids(db: Session, task_ids: list[str]) -> dict[str, list[str]]:
    result = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return result
    links = db.scalars(
        select(ObjectLink).where(
            ObjectLink.source_type == ObjectType.TOPIC,
            ObjectLink.target_type == ObjectType.TASK,
            ObjectLink.target_id.in_(task_ids),
        )
    ).all()
    for link in links:
        result.setdefault(link.target_id, []).append(link.source_id)
    return result


def calendar_events(
    db: Session,
    user: User,
    start: datetime,
    end: datetime,
    *,
    event_types: set[CalendarEventType] | None = None,
    owner_ids: set[str] | None = None,
    work_areas: set[str] | None = None,
    topic_ids: set[str] | None = None,
) -> list[dict[str, object]]:
    start, end = aware(start), aware(end)
    allowed = event_types or set(CalendarEventType)
    tasks = visible_tasks(db, user)
    topics = _topic_ids(db, [task.id for task in tasks])
    result: list[dict[str, object]] = []

    def include_task(task: Task) -> bool:
        if owner_ids and task.owner_id not in owner_ids:
            return False
        if work_areas and task.work_area not in work_areas:
            return False
        return not topic_ids or bool(topic_ids & set(topics.get(task.id, [])))

    def append_task_event(
        task: Task,
        kind: CalendarEventType,
        value: datetime | None,
        label: str,
        *,
        editable: bool,
    ) -> None:
        if kind not in allowed or not value or not include_task(task):
            return
        instant = aware(value)
        if not start <= instant < end:
            return
        result.append(
            {
                "id": f"{kind.value}:{task.id}:{label}",
                "event_type": kind,
                "title": f"{label}：{task.title}",
                "start_at": instant,
                "all_day": False,
                "object_type": ObjectType.TASK,
                "object_id": task.id,
                "route": f"/tasks/{task.id}",
                "status": task.status.value,
                "owner_id": task.owner_id,
                "work_area": task.work_area,
                "topic_ids": topics.get(task.id, []),
                "editable": editable,
                "metadata": {
                    "date_kind": label,
                    "priority": task.priority.value,
                    "open": task.status in OPEN_STATUSES,
                },
            }
        )

    for task in tasks:
        append_task_event(
            task, CalendarEventType.TASK_DUE, task.formal_due_at, "正式截止", editable=False
        )
        append_task_event(
            task, CalendarEventType.TASK_PLAN, task.internal_due_at, "内部节点", editable=True
        )
        append_task_event(
            task, CalendarEventType.TASK_PLAN, task.planned_start_at, "计划开始", editable=True
        )
        append_task_event(
            task, CalendarEventType.TASK_PLAN, task.planned_end_at, "计划完成", editable=True
        )

    if CalendarEventType.RECURRENCE in allowed:
        statement = select(RecurrenceRule).where(
            RecurrenceRule.active.is_(True),
            RecurrenceRule.next_run_at >= start,
            RecurrenceRule.next_run_at < end,
        )
        if user.role != UserRole.ADMIN:
            statement = statement.where(RecurrenceRule.owner_id == user.id)
        for rule in db.scalars(statement).all():
            if owner_ids and rule.owner_id not in owner_ids:
                continue
            result.append(
                {
                    "id": f"recurrence:{rule.id}",
                    "event_type": CalendarEventType.RECURRENCE,
                    "title": f"周期生成：{rule.name}",
                    "start_at": aware(rule.next_run_at),
                    "all_day": False,
                    "object_type": None,
                    "object_id": rule.id,
                    "route": "/templates",
                    "status": "paused" if rule.paused_until else "active",
                    "owner_id": rule.owner_id,
                    "work_area": "",
                    "topic_ids": [],
                    "editable": False,
                    "metadata": {"kind": rule.kind.value},
                }
            )

    if CalendarEventType.REPORT_BOUNDARY in allowed:
        reports = db.scalars(
            select(PeriodReport).where(
                PeriodReport.end_at >= start,
                PeriodReport.end_at < end,
            )
        ).all()
        for report in reports:
            result.append(
                {
                    "id": f"report:{report.id}",
                    "event_type": CalendarEventType.REPORT_BOUNDARY,
                    "title": f"汇总节点：{report.title}",
                    "start_at": aware(report.end_at),
                    "all_day": False,
                    "object_type": ObjectType.PERIOD_REPORT,
                    "object_id": report.id,
                    "route": f"/reports?report={report.id}",
                    "status": report.status.value,
                    "owner_id": report.updated_by,
                    "work_area": "",
                    "topic_ids": [],
                    "editable": False,
                    "metadata": {"period_type": report.period_type.value},
                }
            )

    if CalendarEventType.REMINDER in allowed:
        for item in db.scalars(
            select(Notification).where(
                Notification.user_id == user.id,
                Notification.created_at >= start,
                Notification.created_at < end,
            )
        ).all():
            result.append(
                {
                    "id": f"reminder:{item.id}",
                    "event_type": CalendarEventType.REMINDER,
                    "title": item.title,
                    "start_at": aware(item.created_at),
                    "all_day": False,
                    "object_type": ObjectType.TASK
                    if item.entity_type == "task"
                    else None,
                    "object_id": item.entity_id,
                    "route": f"/tasks/{item.entity_id}"
                    if item.entity_type == "task" and item.entity_id
                    else "/",
                    "status": "read" if item.read_at else "unread",
                    "owner_id": user.id,
                    "work_area": "",
                    "topic_ids": [],
                    "editable": False,
                    "metadata": {},
                }
            )

    for item in db.scalars(select(WorkCalendarEntry)).all():
        event_type = (
            CalendarEventType.ADJUSTED_WORKDAY
            if item.is_workday
            else CalendarEventType.HOLIDAY
        )
        if event_type not in allowed:
            continue
        local_start = datetime.combine(
            datetime.fromisoformat(item.date_key).date(),
            time.min,
            tzinfo=LOCAL_TIMEZONE,
        ).astimezone(timezone.utc)
        if not start <= local_start < end:
            continue
        result.append(
            {
                "id": f"workday:{item.id}",
                "event_type": event_type,
                "title": item.title,
                "start_at": local_start,
                "end_at": local_start + timedelta(days=1),
                "all_day": True,
                "object_type": None,
                "object_id": item.id,
                "route": "/calendar",
                "status": item.kind,
                "owner_id": item.owner_id,
                "work_area": "",
                "topic_ids": [],
                "editable": user.role == UserRole.ADMIN,
                "metadata": {"note": item.note},
            }
        )
    return sorted(result, key=lambda item: (item["start_at"], str(item["title"])))
