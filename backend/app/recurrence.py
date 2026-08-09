"""模板实例化与周期事项生成。"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import RecurrenceKind
from .models import (
    MaterialItem,
    RecurrenceException,
    RecurrenceRule,
    Task,
    TaskStep,
    TaskTemplate,
    TemplateMaterial,
    TemplateStep,
    User,
    WorkCalendarEntry,
    utcnow,
)
from .problems import ProblemException
from .schemas import MaterialInput, StepInput, TaskCreate
from .task_service import create_task


def as_utc(value: datetime) -> datetime:
    """把 SQLite 取出的无时区值及带偏移值统一为 UTC。"""

    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def next_occurrence(rule: RecurrenceRule, base: datetime) -> datetime:
    if rule.kind == RecurrenceKind.MONTHLY:
        result = add_months(base, 1)
    elif rule.kind == RecurrenceKind.QUARTERLY:
        result = add_months(base, 3)
    elif rule.kind == RecurrenceKind.HALF_YEARLY:
        result = add_months(base, 6)
    elif rule.kind == RecurrenceKind.YEARLY:
        result = add_months(base, 12)
    else:
        result = base + timedelta(days=rule.custom_days or 1)
    config = rule.schedule_config or {}
    mode = str(config.get("mode", "same_day"))
    if mode == "day_of_month":
        day = max(1, min(31, int(config.get("day", result.day) or result.day)))
        result = result.replace(day=min(day, calendar.monthrange(result.year, result.month)[1]))
    elif mode in {"month_end", "quarter_end", "last_workday"}:
        result = result.replace(day=calendar.monthrange(result.year, result.month)[1])
    return result


def scheduled_occurrence(
    db: Session,
    rule: RecurrenceRule,
    occurrence_at: datetime,
) -> datetime:
    """把周期锚点转换为实际正式日期。

    ``next_run_at`` 始终保存稳定的周期锚点；“最后一个工作日”只在生成和
    预览时投影，避免反复调整后逐月漂移。
    """

    config = rule.schedule_config or {}
    mode = str(config.get("mode", "same_day"))
    policy = str(config.get("workday_policy", "unchanged"))
    if mode != "last_workday" and policy == "unchanged":
        return occurrence_at
    entries = {
        item.date_key: item.is_workday
        for item in db.scalars(select(WorkCalendarEntry)).all()
    }
    candidate = occurrence_at

    def is_workday(value: datetime) -> bool:
        aware_candidate = (
            value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        )
        local_date = aware_candidate.astimezone(
            timezone(timedelta(hours=8))
        ).date()
        explicit = entries.get(local_date.isoformat())
        return explicit is True or (explicit is None and local_date.weekday() < 5)

    direction = -1 if mode == "last_workday" or policy == "previous" else 1
    for _ in range(31):
        if is_workday(candidate):
            return candidate
        candidate += timedelta(days=direction)
    raise ProblemException(
        409,
        "WORK_CALENDAR_INVALID",
        "无法确定适用工作日",
        "请检查该月份的节假日和调休设置。",
    )


def preview_occurrences(
    db: Session,
    rule: RecurrenceRule,
    *,
    count: int = 12,
) -> list[dict[str, object]]:
    exceptions = {
        as_utc(item.occurrence_at): item
        for item in db.scalars(
            select(RecurrenceException).where(RecurrenceException.rule_id == rule.id)
        ).all()
    }
    value = as_utc(rule.next_run_at)
    result: list[dict[str, object]] = []
    for _ in range(max(1, min(count, 60))):
        exception = exceptions.get(value)
        scheduled = scheduled_occurrence(db, rule, value)
        effective = (
            exception.rescheduled_at
            if exception and exception.rescheduled_at
            else scheduled
        )
        result.append(
            {
                "occurrence_at": value,
                "effective_at": effective,
                "action": exception.action.value if exception else "",
                "reason": exception.reason if exception else "",
            }
        )
        value = next_occurrence(rule, value)
    return result


def adjusted_internal_due(
    db: Session,
    owner_id: str,
    formal_due_at: datetime,
    lead_days: int,
) -> datetime:
    """计算内部节点，并按个人工作日历向前调整到最近工作日。

    上级正式时限保持不变；周末默认不作为工作日，日历中显式标记的调休
    工作日或节假日优先于周末规则。
    """

    candidate = formal_due_at - timedelta(days=max(0, lead_days))
    entries = {
        item.date_key: item.is_workday
        for item in db.scalars(select(WorkCalendarEntry)).all()
    }
    for _ in range(366):
        aware_candidate = (
            candidate
            if candidate.tzinfo
            else candidate.replace(tzinfo=timezone.utc)
        )
        local_date = aware_candidate.astimezone(timezone(timedelta(hours=8))).date()
        explicit = entries.get(local_date.isoformat())
        if explicit is True or (explicit is None and local_date.weekday() < 5):
            return candidate
        candidate -= timedelta(days=1)
    raise ProblemException(
        409,
        "WORK_CALENDAR_INVALID",
        "工作日历无法计算内部节点",
        "请检查连续节假日设置是否正确。",
    )


def instantiate_template(
    db: Session,
    template: TaskTemplate,
    owner_id: str,
    actor: User,
    title: str | None = None,
    formal_due_at: datetime | None = None,
    internal_due_at: datetime | None = None,
    recurrence_rule: RecurrenceRule | None = None,
    previous_task: Task | None = None,
) -> object:
    steps = db.scalars(
        select(TemplateStep)
        .where(TemplateStep.template_id == template.id)
        .order_by(TemplateStep.sort_order)
    ).all()
    materials = db.scalars(
        select(TemplateMaterial).where(TemplateMaterial.template_id == template.id)
    ).all()
    if previous_task:
        previous_steps = db.scalars(
            select(TaskStep)
            .where(TaskStep.task_id == previous_task.id)
            .order_by(TaskStep.sort_order)
        ).all()
        previous_materials = db.scalars(
            select(MaterialItem)
            .where(MaterialItem.task_id == previous_task.id)
            .order_by(MaterialItem.created_at)
        ).all()
        if previous_steps:
            steps = previous_steps
        if previous_materials:
            materials = previous_materials
    payload = TaskCreate(
        title=title or template.name,
        description=previous_task.description if previous_task else template.description,
        task_type=template.task_type,
        owner_id=owner_id,
        formal_due_at=formal_due_at,
        internal_due_at=internal_due_at,
        template_id=template.id,
        recurrence_rule_id=recurrence_rule.id if recurrence_rule else None,
        category=template.category,
        experience_notes=(
            previous_task.experience_notes
            if previous_task
            else recurrence_rule.notes
            if recurrence_rule
            else ""
        ),
        contact_ids=(
            previous_task.contact_ids
            if previous_task
            else recurrence_rule.contact_ids
            if recurrence_rule
            else []
        ),
        steps=[StepInput(title=item.title, assignee_id=owner_id) for item in steps],
        materials=[
            MaterialInput(
                category=item.category,
                name=item.name,
                required=item.required,
            )
            for item in materials
        ],
    )
    return create_task(db, payload, actor)


def run_due_rules(db: Session, actor: User) -> list[str]:
    now = utcnow()
    rules = db.scalars(
        select(RecurrenceRule).where(RecurrenceRule.active.is_(True))
    ).all()
    created: list[str] = []
    comparable_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    for rule in rules:
        paused_until = (
            rule.paused_until.replace(tzinfo=timezone.utc)
            if rule.paused_until and rule.paused_until.tzinfo is None
            else rule.paused_until
        )
        if paused_until and paused_until > comparable_now:
            continue
        exceptions = {
            as_utc(item.occurrence_at): item
            for item in db.scalars(
                select(RecurrenceException).where(
                    RecurrenceException.rule_id == rule.id
                )
            ).all()
        }
        generated_for_rule = 0
        while generated_for_rule < 24:
            occurrence_at = as_utc(rule.next_run_at)
            if rule.end_at:
                end_at = (
                    rule.end_at.replace(tzinfo=timezone.utc)
                    if rule.end_at.tzinfo is None
                    else rule.end_at.astimezone(timezone.utc)
                )
                if occurrence_at > end_at:
                    rule.active = False
                    rule.version += 1
                    break
            if rule.max_occurrences and rule.occurrence_count >= rule.max_occurrences:
                rule.active = False
                rule.version += 1
                break
            exception = exceptions.get(occurrence_at)
            if exception and exception.action.value == "skip":
                rule.last_run_at = now
                rule.next_run_at = next_occurrence(rule, occurrence_at)
                rule.version += 1
                generated_for_rule += 1
                continue
            formal_due_at = (
                exception.rescheduled_at
                if exception and exception.rescheduled_at
                else scheduled_occurrence(db, rule, occurrence_at)
            )
            comparable_formal = (
                formal_due_at.replace(tzinfo=timezone.utc)
                if formal_due_at.tzinfo is None
                else formal_due_at.astimezone(timezone.utc)
            )
            internal_due_at = adjusted_internal_due(
                db,
                rule.owner_id,
                comparable_formal,
                rule.internal_lead_days,
            )
            comparable_internal_due = (
                internal_due_at
                if internal_due_at.tzinfo
                else internal_due_at.replace(tzinfo=timezone.utc)
            )
            if comparable_internal_due > comparable_now:
                break
            template = db.get(TaskTemplate, rule.template_id)
            if not template:
                rule.last_error = "周期模板不存在或已停用"
                rule.version += 1
                break
            existing = db.scalar(
                select(Task).where(
                    Task.recurrence_rule_id == rule.id,
                    Task.formal_due_at == comparable_formal,
                    Task.deleted_at.is_(None),
                )
            )
            if existing:
                rule.last_task_id = existing.id
            else:
                previous = db.get(Task, rule.last_task_id) if rule.last_task_id else None
                task = instantiate_template(
                    db,
                    template,
                    rule.owner_id,
                    actor,
                    title=f"{template.name}（{comparable_formal:%Y-%m}）",
                    formal_due_at=comparable_formal,
                    internal_due_at=internal_due_at,
                    recurrence_rule=rule,
                    previous_task=previous,
                )
                created.append(task.id)
                rule.last_task_id = task.id
                rule.occurrence_count += 1
            rule.last_run_at = now
            rule.last_error = ""
            rule.version += 1
            rule.next_run_at = next_occurrence(rule, occurrence_at)
            generated_for_rule += 1
        db.commit()
    return created
