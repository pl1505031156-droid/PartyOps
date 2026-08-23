"""“今日”工作域聚合接口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_session
from ..enums import PeriodReportStatus, TaskStatus
from ..models import (
    AttachmentVersion,
    BackupRun,
    BusinessDocument,
    BusinessMeeting,
    Device,
    MaterialItem,
    MeetingAction,
    PartyDevelopmentCase,
    PartyDevelopmentMilestone,
    PeriodReport,
    RecurrenceRule,
    Task,
    Transfer,
    User,
    WorkspaceFile,
    utcnow,
)
from ..security import get_current_user
from ..task_service import dashboard, visible_tasks
from .router_utils import aware_utc


router = APIRouter(tags=["today"])
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
OPEN_STATUSES = {
    TaskStatus.PENDING_RECEIPT,
    TaskStatus.PENDING_BREAKDOWN,
    TaskStatus.IN_PROGRESS,
    TaskStatus.WAITING_FEEDBACK,
    TaskStatus.PENDING_REVIEW,
    TaskStatus.RETURNED,
}
PARTY_LIFE_QUARTER_TARGETS = {
    "party_member_meeting": 1,
    "branch_members": 3,
    "party_group": 3,
}
SPECIALIZED_MEETING_TYPES = {
    *PARTY_LIFE_QUARTER_TARGETS,
    "party_class",
    "study_group",
}


def _task_summary(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "priority": task.priority.value,
        "owner_id": task.owner_id,
        "formal_due_at": task.formal_due_at,
        "internal_due_at": task.internal_due_at,
        "planned_start_at": task.planned_start_at,
        "planned_end_at": task.planned_end_at,
        "work_area": task.work_area,
        "route": f"/tasks/{task.id}",
    }


@router.get("/today", response_model=dict)
def today(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    now = utcnow()
    local_now = now.astimezone(LOCAL_TIMEZONE)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = (today_start - timedelta(days=today_start.weekday())).astimezone(
        timezone.utc
    )
    week_end = week_start + timedelta(days=7)
    next_week_end = week_end + timedelta(days=7)
    tasks = visible_tasks(db, user)

    def in_window(value: datetime | None, start: datetime, end: datetime) -> bool:
        aware = aware_utc(value)
        return bool(aware and start <= aware < end)

    today_tasks: list[Task] = []
    overdue: list[Task] = []
    review_feedback: list[Task] = []
    completed_week: list[Task] = []
    next_week: list[Task] = []
    utc_today_start = today_start.astimezone(timezone.utc)
    utc_today_end = today_end.astimezone(timezone.utc)
    for task in tasks:
        dates = (
            task.internal_due_at,
            task.formal_due_at,
            task.planned_start_at,
            task.planned_end_at,
        )
        if task.status in OPEN_STATUSES and any(
            in_window(value, utc_today_start, utc_today_end) for value in dates
        ):
            today_tasks.append(task)
        due = aware_utc(task.internal_due_at or task.formal_due_at)
        if task.status in OPEN_STATUSES and due and due < now:
            overdue.append(task)
        if task.status in {TaskStatus.PENDING_REVIEW, TaskStatus.WAITING_FEEDBACK}:
            review_feedback.append(task)
        if in_window(task.completed_at, week_start, week_end):
            completed_week.append(task)
        planned = task.planned_start_at or task.planned_end_at or task.internal_due_at
        if task.status in OPEN_STATUSES and in_window(planned, week_end, next_week_end):
            next_week.append(task)

    incomplete_materials = 0
    for task in tasks:
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED}:
            continue
        materials = db.scalars(
            select(MaterialItem).where(
                MaterialItem.task_id == task.id,
                MaterialItem.required.is_(True),
                MaterialItem.not_applicable.is_(False),
            )
        ).all()
        for material in materials:
            if not db.scalar(
                select(AttachmentVersion.id).where(
                    AttachmentVersion.material_item_id == material.id,
                    AttachmentVersion.is_final.is_(True),
                )
            ):
                incomplete_materials += 1

    recent_files = db.scalars(
        select(WorkspaceFile)
        .where(WorkspaceFile.in_scope.is_(True))
        .order_by(WorkspaceFile.last_seen_at.desc())
        .limit(8)
    ).all()
    pending_transfers = db.scalars(
        select(Transfer)
        .where(
            Transfer.requested_by == user.id,
            Transfer.status.in_(["awaiting_approval", "transferring", "failed"]),
        )
        .order_by(Transfer.updated_at.desc())
        .limit(8)
    ).all()
    settings = get_settings()
    devices = db.scalars(select(Device).where(Device.active.is_(True))).all()
    device_alerts = [
        {
            "id": item.id,
            "name": item.name,
            "status": item.status.value,
            "app_version": item.app_version,
            "reason": "版本不一致"
            if item.app_version and item.app_version != settings.app_version
            else "设备离线",
            "route": "/fleet",
        }
        for item in devices
        if item.status.value != "online"
        or (item.app_version and item.app_version != settings.app_version)
    ]
    latest_backup = db.scalar(
        select(BackupRun)
        .where(BackupRun.status == "completed")
        .order_by(BackupRun.completed_at.desc())
    )
    backup_stale = not latest_backup or (
        now
        - (
            aware_utc(latest_backup.completed_at)
            or aware_utc(latest_backup.created_at)
        )
    ) > timedelta(hours=36)
    recurrence_anomalies = db.scalar(
        select(func.count(RecurrenceRule.id)).where(
            RecurrenceRule.active.is_(True),
            RecurrenceRule.next_run_at < now - timedelta(days=1),
        )
    ) or 0
    report_drafts = db.scalar(
        select(func.count(PeriodReport.id)).where(
            PeriodReport.status == PeriodReportStatus.DRAFT
        )
    ) or 0
    quarter_start_month = ((local_now.month - 1) // 3) * 3 + 1
    quarter_start = local_now.replace(
        month=quarter_start_month,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    if quarter_start_month == 10:
        quarter_end = quarter_start.replace(year=quarter_start.year + 1, month=1)
    else:
        quarter_end = quarter_start.replace(month=quarter_start_month + 3)
    quarter_start_utc = quarter_start.astimezone(timezone.utc)
    quarter_end_utc = quarter_end.astimezone(timezone.utc)
    specialized_meetings = db.scalars(
        select(BusinessMeeting).where(
            BusinessMeeting.meeting_type.in_(SPECIALIZED_MEETING_TYPES)
        )
    ).all()
    quarter_counts = {key: 0 for key in SPECIALIZED_MEETING_TYPES}
    pending_archives = 0
    specialized_ids: list[str] = []
    for meeting in specialized_meetings:
        specialized_ids.append(meeting.id)
        scheduled = aware_utc(meeting.scheduled_at)
        if (
            meeting.status != "cancelled"
            and scheduled
            and quarter_start_utc <= scheduled < quarter_end_utc
        ):
            quarter_counts[meeting.meeting_type] += 1
        if meeting.status == "completed" and not db.scalar(
            select(BusinessDocument.id).where(
                BusinessDocument.meeting_id == meeting.id
            )
        ):
            pending_archives += 1
    party_life_recorded = sum(
        min(quarter_counts[key], target)
        for key, target in PARTY_LIFE_QUARTER_TARGETS.items()
    )
    party_life_expected = sum(PARTY_LIFE_QUARTER_TARGETS.values())
    study_center_recorded = min(quarter_counts["study_group"], 1)
    overdue_actions = 0
    if specialized_ids:
        actions = db.scalars(
            select(MeetingAction).where(MeetingAction.meeting_id.in_(specialized_ids))
        ).all()
        overdue_actions = sum(
            1
            for action in actions
            if action.status not in {"completed", "cancelled"}
            and (aware_utc(action.due_at) or now) < now
            and action.due_at is not None
        )
    reminder_limit = now + timedelta(days=30)
    development_reminders = 0
    active_case_ids = list(
        db.scalars(
            select(PartyDevelopmentCase.id).where(
                PartyDevelopmentCase.status == "active"
            )
        ).all()
    )
    if active_case_ids:
        milestones = db.scalars(
            select(PartyDevelopmentMilestone).where(
                PartyDevelopmentMilestone.case_id.in_(active_case_ids),
                PartyDevelopmentMilestone.actual_at.is_(None),
            )
        ).all()
        development_reminders = sum(
            1
            for milestone in milestones
            if (aware_utc(milestone.adjusted_at or milestone.planned_at))
            and aware_utc(milestone.adjusted_at or milestone.planned_at) <= reminder_limit
        )
    return {
        "updated_at": now,
        "dashboard": dashboard(db, user).model_dump(mode="json"),
        "today_tasks": [_task_summary(item) for item in today_tasks[:20]],
        "overdue_tasks": [_task_summary(item) for item in overdue[:20]],
        "pending_review_feedback": [
            _task_summary(item) for item in review_feedback[:20]
        ],
        "completed_this_week": [
            _task_summary(item) for item in completed_week[:30]
        ],
        "next_week_plan": [_task_summary(item) for item in next_week[:30]],
        "recent_files": [
            {
                "id": item.id,
                "name": item.name,
                "extension": item.extension,
                "availability": item.availability.value,
                "route": f"/workspace?file={item.id}",
            }
            for item in recent_files
        ],
        "pending_transfers": [
            {
                "id": item.id,
                "name": item.original_name,
                "status": item.status.value,
                "route": "/fleet?tab=transfers",
            }
            for item in pending_transfers
        ],
        "risks": {
            "incomplete_materials": incomplete_materials,
            "recurrence_anomalies": recurrence_anomalies,
            "draft_reports": report_drafts,
            "backup_stale": backup_stale,
            "device_alerts": device_alerts,
        },
        "party_work": {
            "quarter": (local_now.month - 1) // 3 + 1,
            "party_life_expected": party_life_expected,
            "party_life_recorded": party_life_recorded,
            "party_life_remaining": max(0, party_life_expected - party_life_recorded),
            "study_center_expected": 1,
            "study_center_recorded": study_center_recorded,
            "study_center_remaining": max(0, 1 - study_center_recorded),
            "pending_archives": pending_archives,
            "overdue_actions": overdue_actions,
            "development_reminders": development_reminders,
        },
    }
