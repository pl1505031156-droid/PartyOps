"""周期报告投影检查点、增量同步与人工重建。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .enums import PeriodReportStatus
from .models import EventOutbox, PeriodReport, ProjectionCheckpoint, Task, User, utcnow
from .reports import auto_fill_report, ensure_period_reports


REPORT_PROJECTION = "period_reports"
PROJECTION_RETRY_BASE_SECONDS = 60
PROJECTION_RETRY_MAX_SECONDS = 6 * 60 * 60


def _checkpoint(db: Session) -> ProjectionCheckpoint:
    checkpoint = db.get(ProjectionCheckpoint, REPORT_PROJECTION)
    if checkpoint is None:
        checkpoint = ProjectionCheckpoint(name=REPORT_PROJECTION)
        db.add(checkpoint)
        db.flush()
    return checkpoint


def _retry_delay_seconds(failed_count: int) -> int:
    """返回持久化指数退避时长，避免确定性故障形成每分钟重试风暴。"""

    exponent = max(0, min(8, failed_count - 1))
    return min(
        PROJECTION_RETRY_MAX_SECONDS,
        PROJECTION_RETRY_BASE_SECONDS * (2**exponent),
    )


def _backoff_active(checkpoint: ProjectionCheckpoint, now: datetime) -> bool:
    if checkpoint.status != "failed" or not checkpoint.last_run_at:
        return False
    last_run = checkpoint.last_run_at
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    return now < last_run + timedelta(seconds=_retry_delay_seconds(checkpoint.failed_count))


def _task_anchors(task: Task | None) -> list[datetime]:
    if task is None:
        return []
    values = (
        task.completed_at,
        task.planned_start_at,
        task.planned_end_at,
        task.internal_due_at,
        task.formal_due_at,
    )
    return [value for value in values if value is not None]


def _sync_reports(
    db: Session,
    user: User,
    anchors: list[datetime],
) -> tuple[int, int]:
    created = 0
    changed = 0
    seen: set[str] = set()
    for anchor in [utcnow(), *anchors]:
        reports, created_count, _ = ensure_period_reports(db, user, anchor)
        created += created_count
        seen.update(item.id for item in reports)
    for report in db.scalars(
        select(PeriodReport).where(PeriodReport.status == PeriodReportStatus.DRAFT)
    ).all():
        delta = auto_fill_report(db, report, user)
        if delta:
            report.version += 1
            report.updated_by = user.id
            changed += delta
        seen.add(report.id)
    return created, changed


def process_report_projection(db: Session, user: User) -> ProjectionCheckpoint:
    """消费任务事件并同步草稿周期报告；重复调用不会重复生成条目。"""

    checkpoint = _checkpoint(db)
    now = utcnow()
    if _backoff_active(checkpoint, now):
        return checkpoint
    events = db.scalars(
        select(EventOutbox)
        .where(
            EventOutbox.id > checkpoint.last_event_id,
            EventOutbox.event_type.like("task.%"),
        )
        .order_by(EventOutbox.id)
        .limit(2_000)
    ).all()
    if not events:
        checkpoint.status = "idle"
        checkpoint.failed_count = 0
        checkpoint.last_error = ""
        return checkpoint
    checkpoint.status = "running"
    checkpoint.last_error = ""
    anchors: list[datetime] = []
    for event in events:
        anchors.extend(_task_anchors(db.get(Task, event.entity_id)) if event.entity_id else [])
    try:
        _sync_reports(db, user, anchors)
    except Exception as exc:
        # 单次投影失败不能污染业务事务，也不能吞掉问题。回滚本次投影后
        # 保留检查点，下一轮会从同一事件继续；管理员可在诊断页看到失败。
        db.rollback()
        checkpoint = _checkpoint(db)
        checkpoint.status = "failed"
        checkpoint.failed_count += 1
        checkpoint.last_error = f"周期汇总投影失败：{type(exc).__name__}"
        checkpoint.last_run_at = utcnow()
        checkpoint.updated_at = utcnow()
        return checkpoint
    checkpoint.last_event_id = events[-1].id
    checkpoint.processed_count += len(events)
    checkpoint.status = "idle"
    checkpoint.failed_count = 0
    checkpoint.last_run_at = utcnow()
    checkpoint.last_error = ""
    checkpoint.updated_at = utcnow()
    return checkpoint


def rebuild_report_projection(db: Session, user: User) -> ProjectionCheckpoint:
    """重建全部草稿报告投影；发布快照保持不变。"""

    checkpoint = _checkpoint(db)
    checkpoint.status = "rebuilding"
    checkpoint.last_error = ""
    anchors: list[datetime] = []
    for task in db.scalars(select(Task).where(Task.deleted_at.is_(None))).all():
        anchors.extend(_task_anchors(task))
    created, changed = _sync_reports(db, user, anchors)
    checkpoint.last_event_id = int(
        db.scalar(select(func.max(EventOutbox.id))) or 0
    )
    checkpoint.processed_count += created + changed
    checkpoint.status = "idle"
    checkpoint.failed_count = 0
    checkpoint.last_run_at = datetime.now(timezone.utc)
    checkpoint.updated_at = utcnow()
    return checkpoint
