"""依据时限和办理状态生成持久化提醒。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import emit_event
from .enums import TaskStatus
from .models import (
    Notification,
    PartyDevelopmentCase,
    PartyDevelopmentMilestone,
    ReminderPreference,
    Task,
    User,
    utcnow,
)
from .task_service import required_materials_missing, visible_tasks

LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def _quiet_minutes(value: object, fallback: str) -> int:
    """解析历史免打扰值；非法旧数据回退默认值，不把调度器拖成 500。"""

    try:
        parts = str(value).split(":")
        if len(parts) != 2:
            raise ValueError
        hour, minute = (int(item) for item in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour * 60 + minute
    except (TypeError, ValueError):
        hour, minute = (int(item) for item in fallback.split(":"))
        return hour * 60 + minute


def add_notification(
    db: Session,
    *,
    user_id: str,
    notification_type: str,
    title: str,
    body: str,
    entity_type: str,
    entity_id: str | None,
    dedupe_key: str,
) -> bool:
    if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key)):
        return False
    item = Notification(
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=entity_id,
        dedupe_key=dedupe_key,
    )
    db.add(item)
    db.flush()
    emit_event(
        db,
        "notification.created",
        item.id,
        {"user_id": user_id, "entity_type": entity_type, "entity_id": entity_id, "type": notification_type},
    )
    return True


def desktop_notifications_allowed(
    preference: ReminderPreference | None,
    now: datetime | None = None,
) -> bool:
    """判断当前是否允许桌面弹窗；持久化提醒本身不受免打扰影响。"""

    if preference and (not preference.enabled or not preference.desktop_enabled):
        return False
    local_now = (now or utcnow()).astimezone(LOCAL_TIMEZONE)
    start_text = preference.quiet_start if preference else "22:00"
    end_text = preference.quiet_end if preference else "07:30"
    start = _quiet_minutes(start_text, "22:00")
    end = _quiet_minutes(end_text, "07:30")
    if start == end:
        return True
    current = local_now.hour * 60 + local_now.minute
    quiet = (
        start <= current < end
        if start < end
        else current >= start or current < end
    )
    return not quiet


def _add_once(
    db: Session,
    user: User,
    task: Task,
    notification_type: str,
    title: str,
    body: str,
    dedupe_key: str,
) -> bool:
    return add_notification(
        db,
        user_id=user.id,
        notification_type=notification_type,
        title=title,
        body=body,
        entity_type="task",
        entity_id=task.id,
        dedupe_key=dedupe_key,
    )


def _upsert_task_notice(
    db: Session,
    user: User,
    task: Task,
    notification_type: str,
    title: str,
    body: str,
    dedupe_key: str,
    now: datetime,
) -> bool:
    """更新当前提醒，不让事项改期后残留旧时间通知。"""

    existing = db.scalar(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    )
    if existing and existing.revoked_at is None and existing.read_at is None:
        changed = existing.title != title or existing.body != body
        existing.title = title
        existing.body = body
        existing.created_at = now
        existing.updated_at = now
        if changed:
            emit_event(
                db,
                "notification.updated",
                existing.id,
                {"user_id": user.id, "entity_type": "task", "entity_id": task.id},
            )
        return changed
    if existing:
        # 唯一键继续代表“当前通知”。历史行改名后仍保留已读审计。
        existing.revoked_at = existing.revoked_at or now
        existing.dedupe_key = f"{dedupe_key}:history:{existing.id}"
    return _add_once(
        db,
        user,
        task,
        notification_type,
        title,
        body,
        dedupe_key,
    )


def _revoke_stale_task_notices(
    db: Session,
    user: User,
    task: Task,
    desired_keys: set[str],
    now: datetime,
) -> int:
    revoked = 0
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id,
            Notification.entity_type == "task",
            Notification.entity_id == task.id,
            Notification.notification_type.in_(["deadline", "overdue"]),
            Notification.revoked_at.is_(None),
        )
    ).all()
    for row in rows:
        if row.dedupe_key in desired_keys:
            continue
        row.revoked_at = now
        row.updated_at = now
        revoked += 1
        emit_event(
            db,
            "notification.revoked",
            row.id,
            {"user_id": user.id, "entity_type": "task", "entity_id": task.id},
        )
    return revoked


def reconcile_task_deadline_notifications(
    db: Session,
    task: Task,
    *,
    now: datetime | None = None,
) -> int:
    """在事项更新事务后立即把截止通知收敛到当前期望状态。"""

    current = now or utcnow()
    today = current.astimezone(LOCAL_TIMEZONE).date()
    changed = 0
    users = db.scalars(select(User).where(User.active.is_(True))).all()
    for user in users:
        if all(visible.id != task.id for visible in visible_tasks(db, user)):
            continue
        preference = db.get(ReminderPreference, user.id)
        desired: set[str] = set()
        due = task.internal_due_at or task.formal_due_at
        if due and task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED}:
            aware_due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
            due_local = aware_due.astimezone(LOCAL_TIMEZONE)
            remaining = (due_local.date() - today).days
            days = (
                preference.reminder_days
                if preference and preference.reminder_days
                else [preference.advance_days if preference else 3, 0]
            )
            if not preference or preference.enabled:
                if remaining in days:
                    key = f"{user.id}:{task.id}:deadline:{remaining}"
                    desired.add(key)
                    label = "今天截止" if remaining == 0 else f"{remaining}天后截止"
                    changed += _upsert_task_notice(
                        db,
                        user,
                        task,
                        "deadline",
                        f"{label}：{task.title}",
                        f"内部节点：{due_local:%Y-%m-%d %H:%M}",
                        key,
                        current,
                    )
                elif remaining < 0 and (not preference or preference.remind_overdue):
                    key = f"{user.id}:{task.id}:overdue"
                    desired.add(key)
                    changed += _upsert_task_notice(
                        db,
                        user,
                        task,
                        "overdue",
                        f"事项已逾期：{task.title}",
                        f"已逾期 {-remaining} 天，请更新状态或计划。",
                        key,
                        current,
                    )
        changed += _revoke_stale_task_notices(db, user, task, desired, current)
    return changed


def _refresh_daily(
    db: Session,
    user: User,
    task: Task,
    notification_type: str,
    title: str,
    body: str,
    dedupe_key: str,
    now: datetime,
) -> bool:
    """每天至多刷新同一业务提醒一行，避免持续逾期造成通知表单调增长。"""

    existing = db.scalar(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    )
    if existing is None:
        return _add_once(
            db,
            user,
            task,
            notification_type,
            title,
            body,
            dedupe_key,
        )
    created_at = existing.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at.astimezone(LOCAL_TIMEZONE).date() >= now.astimezone(LOCAL_TIMEZONE).date():
        return False
    existing.title = title
    existing.body = body
    existing.read_at = None
    existing.created_at = now
    emit_event(
        db,
        "notification.refreshed",
        existing.id,
        {
            "user_id": user.id,
            "entity_type": "task",
            "entity_id": task.id,
            "type": notification_type,
        },
    )
    return True


def refresh_notifications(db: Session) -> int:
    """生成到期、逾期、审核、反馈和材料缺项提醒，使用去重键避免刷屏。"""

    now = utcnow()
    created = 0
    # 调度器只承担漏算修复；正常的事项改期由 PATCH 接口立即重算。这里仍按
    # 全量期望状态收敛，保证进程在接口提交与提醒提交之间退出后不会永久漏算。
    for task in db.scalars(select(Task)).all():
        created += reconcile_task_deadline_notifications(db, task, now=now)
    users = db.scalars(select(User).where(User.active.is_(True))).all()
    for user in users:
        preference = db.get(ReminderPreference, user.id)
        if preference and not preference.enabled:
            continue
        for task in visible_tasks(db, user):
            if task.status == TaskStatus.ARCHIVED:
                continue
            if (
                task.status == TaskStatus.PENDING_REVIEW
                and task.reviewer_id == user.id
                and (not preference or preference.remind_review)
            ):
                created += _add_once(
                    db,
                    user,
                    task,
                    "review",
                    f"待你审核：{task.title}",
                    "事项已提交审核，请及时处理。",
                    f"{user.id}:{task.id}:review:{task.version}",
                )
            if (
                task.status == TaskStatus.WAITING_FEEDBACK
                and task.owner_id == user.id
                and (not preference or preference.remind_feedback)
            ):
                created += _add_once(
                    db,
                    user,
                    task,
                    "feedback",
                    f"等待反馈：{task.title}",
                    "事项仍在等待外部反馈，请检查是否需要催办。",
                    f"{user.id}:{task.id}:feedback:{task.version}",
                )
            if (
                task.status == TaskStatus.COMPLETED
                and (not preference or preference.remind_materials)
                and required_materials_missing(db, task.id)
            ):
                created += _add_once(
                    db,
                    user,
                    task,
                    "materials",
                    f"材料仍有缺项：{task.title}",
                    "事项已完成，但归档前仍需补齐必备材料或说明不适用原因。",
                    f"{user.id}:{task.id}:materials:{task.version}",
                )
    created += refresh_party_development_notifications(db, now=now)
    return created


def refresh_party_development_notifications(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    """按持久化节点生成到期提醒；人工调整日期会更新当前未读提醒。"""

    current = now or utcnow()
    today = current.astimezone(LOCAL_TIMEZONE).date()
    changed = 0
    recipients = db.scalars(select(User).where(User.active.is_(True))).all()
    cases = {
        item.id: item
        for item in db.scalars(
            select(PartyDevelopmentCase).where(PartyDevelopmentCase.status == "active")
        ).all()
    }
    if not cases:
        return 0
    for milestone in db.scalars(
        select(PartyDevelopmentMilestone).where(
            PartyDevelopmentMilestone.case_id.in_(cases),
            PartyDevelopmentMilestone.actual_at.is_(None),
        )
    ).all():
        target = milestone.adjusted_at or milestone.planned_at or milestone.legal_deadline_at
        if not target:
            continue
        aware_target = target if target.tzinfo else target.replace(tzinfo=timezone.utc)
        remaining = (aware_target.astimezone(LOCAL_TIMEZONE).date() - today).days
        window = "overdue" if remaining < 0 else str(remaining)
        should_notify = remaining < 0 or remaining in (milestone.reminder_days or [60, 30, 14, 7, 1, 0])
        case = cases[milestone.case_id]
        for user in recipients:
            prefix = f"{user.id}:party-development:{case.id}:{milestone.id}:"
            desired = f"{prefix}{window}" if should_notify else ""
            for old in db.scalars(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.entity_type == "party_development_case",
                    Notification.entity_id == case.id,
                    Notification.dedupe_key.like(f"{prefix}%"),
                    Notification.revoked_at.is_(None),
                )
            ).all():
                if old.dedupe_key != desired:
                    old.revoked_at = current
                    old.updated_at = current
                    changed += 1
            if not should_notify:
                continue
            label = f"已逾期 {-remaining} 天" if remaining < 0 else ("今天到期" if remaining == 0 else f"还有 {remaining} 天")
            title = f"党员发展节点{label}：{case.name}"
            body = f"{milestone.milestone_type}，计划日期 {aware_target.astimezone(LOCAL_TIMEZONE):%Y-%m-%d}。请按组织程序核对材料和会议安排。"
            existing = db.scalar(select(Notification).where(Notification.dedupe_key == desired))
            if existing and existing.revoked_at is None and existing.read_at is None:
                if existing.title != title or existing.body != body:
                    existing.title = title
                    existing.body = body
                    existing.updated_at = current
                    changed += 1
            elif not existing:
                changed += add_notification(
                    db,
                    user_id=user.id,
                    notification_type="party_development",
                    title=title,
                    body=body,
                    entity_type="party_development_case",
                    entity_id=case.id,
                    dedupe_key=desired,
                )
    return changed
