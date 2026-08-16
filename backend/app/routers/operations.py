"""周期报告、工作日志、归档模板与持久化通知。"""

from __future__ import annotations

import typing

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import emit_event, write_audit
from ..database import db_runtime, get_session
from ..enums import PeriodReportStatus, PeriodType, ReportSection, UserRole
from ..models import (
    ArchiveTemplate,
    Notification,
    PeriodReport,
    PeriodReportItem,
    ReportTemplate,
    Task,
    User,
    WorkJournalEntry,
    WorkJournalRevision,
    utcnow,
)
from ..problems import ProblemException
from ..reports import (
    auto_fill_report,
    ensure_period_reports,
    export_period_docx,
    export_period_xlsx,
    period_bounds,
    report_snapshot,
    report_to_out,
)
from ..schemas import (
    ArchiveTemplateCreate,
    ArchiveTemplateOut,
    NotificationOut,
    PeriodReportAction,
    PeriodReportCreate,
    PeriodReportItemCreate,
    PeriodReportItemOut,
    PeriodReportItemPatch,
    PeriodReportOut,
    PeriodReportPatch,
    ReportTemplateCreate,
    ReportTemplatePatch,
    ReportTemplateOut,
    WorkJournalCreate,
    WorkJournalOut,
    WorkJournalPatch,
    WorkJournalRevisionOut,
    serialize_api_datetime,
)
from ..security import get_current_user
from ..task_service import can_view_task
from ..work_journal import journal_to_out, record_system_entry


router = APIRouter(tags=["period-reports-journal"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def require_version(current: int, value: str | None) -> None:
    submitted = parse_version(value)
    if current != submitted:
        raise ProblemException(
            409,
            "VERSION_CONFLICT",
            "内容已被其他人更新",
            "请刷新后比较最新内容再提交。",
            extra={"current_version": current, "submitted_version": submitted},
        )


def require_report_draft(report: PeriodReport, operation: str) -> None:
    """报告发布后只能显式重新打开，禁止实时表静默漂移。"""

    if report.status == PeriodReportStatus.LOCKED:
        raise ProblemException(
            409,
            "REPORT_LOCKED",
            "报告已经锁定",
            f"请由管理员重新打开后{operation}。",
        )
    if report.status == PeriodReportStatus.PUBLISHED:
        raise ProblemException(
            409,
            "REPORT_PUBLISHED",
            "报告已经发布",
            f"发布快照不可直接改写，请由管理员重新打开后{operation}。",
        )


@router.get("/period-reports", response_model=typing.List[PeriodReportOut])
def list_period_reports(
    period_type: PeriodType | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[PeriodReportOut]:
    statement = select(PeriodReport)
    if period_type:
        statement = statement.where(PeriodReport.period_type == period_type)
    reports = db.scalars(
        statement.order_by(PeriodReport.start_at.desc()).limit(limit)
    ).all()
    return [report_to_out(db, report) for report in reports]


@router.post("/period-reports/ensure-current", response_model=typing.List[PeriodReportOut])
def ensure_current_period_reports(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[PeriodReportOut]:
    """建立当前四级周期目录，并把最新完成和计划事项同步到草稿。"""

    with db_runtime.write_lock:
        reports, created_count, added_count = ensure_period_reports(db, user)
        if created_count or added_count:
            write_audit(
                db,
                user,
                "period_report.auto_sync",
                "period_report",
                None,
                {
                    "created_reports": created_count,
                    "added_items": added_count,
                    "period_keys": [report.period_key for report in reports],
                },
                client_ip(request),
            )
            emit_event(
                db,
                "period_report.updated",
                reports[0].id,
                {"created_reports": created_count, "added_items": added_count},
            )
            db.commit()
        return [report_to_out(db, report) for report in reports]


@router.post("/period-reports", response_model=PeriodReportOut, status_code=201)
def create_period_report(
    payload: PeriodReportCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PeriodReportOut:
    key, default_title, start, end = period_bounds(payload.period_type, payload.anchor_at)
    template = db.get(ReportTemplate, payload.template_id) if payload.template_id else None
    if payload.template_id and (not template or not template.active):
        raise ProblemException(
            404,
            "REPORT_TEMPLATE_NOT_FOUND",
            "报告模板不存在",
            "请选择仍在使用的报告模板。",
        )
    if template and template.period_type != payload.period_type:
        raise ProblemException(
            422,
            "REPORT_TEMPLATE_PERIOD_MISMATCH",
            "模板周期不匹配",
            "请选择与报告周期一致的模板。",
        )
    existing = db.scalar(select(PeriodReport).where(PeriodReport.period_key == key))
    if existing:
        raise ProblemException(
            409,
            "PERIOD_REPORT_EXISTS",
            "该周期报告已存在",
            "请直接打开已有报告。",
            extra={"report_id": existing.id},
        )
    with db_runtime.write_lock:
        report = PeriodReport(
            period_type=payload.period_type,
            period_key=key,
            title=(payload.title or default_title).strip(),
            start_at=start,
            end_at=end,
            summary=payload.summary.strip(),
            snapshot={
                "design": {
                    "template_id": template.id,
                    "sections": list(template.sections),
                }
            }
            if template
            else {},
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(report)
        db.flush()
        if payload.auto_fill:
            auto_fill_report(db, report, user)
        write_audit(
            db,
            user,
            "period_report.create",
            "period_report",
            report.id,
            {"period_key": key},
            client_ip(request),
        )
        emit_event(db, "period_report.created", report.id, {"period_key": key})
        db.commit()
        db.refresh(report)
    return report_to_out(db, report)


@router.get("/period-reports/{report_id}", response_model=PeriodReportOut)
def get_period_report(
    report_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PeriodReportOut:
    report = db.get(PeriodReport, report_id)
    if not report:
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "周期报告不存在", "未找到该报告。")
    return report_to_out(db, report)


@router.patch("/period-reports/{report_id}", response_model=PeriodReportOut)
def patch_period_report(
    report_id: str,
    payload: PeriodReportPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PeriodReportOut:
    report = db.get(PeriodReport, report_id)
    if not report:
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "周期报告不存在", "未找到该报告。")
    require_version(report.version, if_match)
    require_report_draft(report, "修改")
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        setattr(report, field, value.strip() if isinstance(value, str) else value)
    report.version += 1
    report.updated_by = user.id
    record_system_entry(
        db,
        user,
        f"修改周期报告：{report.title}",
        "修改内容：" + "、".join(sorted(payload.model_fields_set)),
        report_id=report.id,
        event_code="report.updated",
        event_data={"fields": sorted(payload.model_fields_set)},
    )
    write_audit(
        db,
        user,
        "period_report.update",
        "period_report",
        report.id,
        {"fields": sorted(payload.model_fields_set), "version": report.version},
        client_ip(request),
    )
    emit_event(db, "period_report.updated", report.id, {"version": report.version})
    db.commit()
    db.refresh(report)
    return report_to_out(db, report)


@router.post("/period-reports/{report_id}/actions", response_model=PeriodReportOut)
def period_report_action(
    report_id: str,
    payload: PeriodReportAction,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PeriodReportOut:
    report = db.get(PeriodReport, report_id)
    if not report:
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "周期报告不存在", "未找到该报告。")
    require_version(report.version, if_match)
    if payload.action == "publish":
        require_report_draft(report, "重新发布")
        report.status = PeriodReportStatus.PUBLISHED
        report.published_at = utcnow()
        report.snapshot = report_snapshot(db, report)
    elif payload.action == "lock":
        if user.role != UserRole.ADMIN:
            raise ProblemException(403, "ADMIN_REQUIRED", "无权锁定", "仅管理员可以锁定报告。")
        report.status = PeriodReportStatus.LOCKED
        report.locked_at = utcnow()
        report.published_at = report.published_at or utcnow()
        report.snapshot = report_snapshot(db, report)
    else:
        if user.role != UserRole.ADMIN:
            raise ProblemException(403, "ADMIN_REQUIRED", "无权重新打开", "仅管理员可以重新打开报告。")
        report.status = PeriodReportStatus.DRAFT
        report.locked_at = None
    report.version += 1
    report.updated_by = user.id
    action_labels = {
        "publish": "发布周期报告",
        "lock": "锁定周期报告",
        "reopen": "重新打开周期报告",
    }
    record_system_entry(
        db,
        user,
        f"{action_labels.get(payload.action, '更新周期报告')}：{report.title}",
        payload.note.strip(),
        report_id=report.id,
        event_code=f"report.{payload.action}",
        event_data={
            "report_status": report.status.value,
            "note": payload.note.strip(),
        },
    )
    write_audit(
        db,
        user,
        f"period_report.{payload.action}",
        "period_report",
        report.id,
        {"note": payload.note, "version": report.version},
        client_ip(request),
    )
    emit_event(db, "period_report.updated", report.id, {"status": report.status.value})
    db.commit()
    db.refresh(report)
    return report_to_out(db, report)


@router.post(
    "/period-reports/{report_id}/items",
    response_model=PeriodReportItemOut,
    status_code=201,
)
def add_period_report_item(
    report_id: str,
    payload: PeriodReportItemCreate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PeriodReportItem:
    report = db.get(PeriodReport, report_id)
    if not report:
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "周期报告不存在", "未找到该报告。")
    require_version(report.version, if_match)
    require_report_draft(report, "新增条目")
    if payload.source_type == "task" and payload.source_id:
        task = db.get(Task, payload.source_id)
        if not task or not can_view_task(db, task, user):
            raise ProblemException(404, "TASK_NOT_FOUND", "关联事项不存在", "未找到可关联事项。")
    item = PeriodReportItem(
        report_id=report.id,
        created_by=user.id,
        **payload.model_dump(),
    )
    db.add(item)
    db.flush()
    report.version += 1
    report.updated_by = user.id
    write_audit(
        db,
        user,
        "period_report.item_add",
        "period_report_item",
        item.id,
        {"report_id": report.id, "section": item.section.value},
        client_ip(request),
    )
    emit_event(db, "period_report.updated", report.id, {"version": report.version})
    db.commit()
    db.refresh(item)
    return item


@router.patch("/period-reports/{report_id}/items/{item_id}", response_model=PeriodReportItemOut)
def patch_period_report_item(
    report_id: str,
    item_id: str,
    payload: PeriodReportItemPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> PeriodReportItem:
    report = db.get(PeriodReport, report_id)
    item = db.get(PeriodReportItem, item_id)
    if not report or not item or item.report_id != report.id:
        raise ProblemException(404, "REPORT_ITEM_NOT_FOUND", "报告条目不存在", "未找到该条目。")
    require_version(item.version, if_match)
    require_report_draft(report, "修改条目")
    for field in payload.model_fields_set:
        setattr(item, field, getattr(payload, field))
    item.version += 1
    report.version += 1
    report.updated_by = user.id
    write_audit(
        db,
        user,
        "period_report.item_update",
        "period_report_item",
        item.id,
        {"report_id": report.id, "version": item.version},
        client_ip(request),
    )
    emit_event(db, "period_report.updated", report.id, {"version": report.version})
    db.commit()
    db.refresh(item)
    return item


@router.delete("/period-reports/{report_id}/items/{item_id}", response_model=dict)
def delete_period_report_item(
    report_id: str,
    item_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    report = db.get(PeriodReport, report_id)
    item = db.get(PeriodReportItem, item_id)
    if not report or not item or item.report_id != report.id:
        raise ProblemException(404, "REPORT_ITEM_NOT_FOUND", "报告条目不存在", "未找到该条目。")
    require_version(item.version, if_match)
    require_report_draft(report, "删除条目")
    db.delete(item)
    report.version += 1
    report.updated_by = user.id
    write_audit(
        db,
        user,
        "period_report.item_delete",
        "period_report_item",
        item.id,
        {"report_id": report.id},
        client_ip(request),
    )
    emit_event(db, "period_report.updated", report.id, {"version": report.version})
    db.commit()
    return {"deleted": True, "id": item_id}


@router.get("/period-reports/{report_id}/export.docx")
def download_period_docx(
    report_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    report = db.get(PeriodReport, report_id)
    if not report:
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "周期报告不存在", "未找到该报告。")
    path = export_period_docx(db, report)
    return FileResponse(path, filename=path.name)


@router.get("/period-reports/{report_id}/export.xlsx")
def download_period_xlsx(
    report_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    report = db.get(PeriodReport, report_id)
    if not report:
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "周期报告不存在", "未找到该报告。")
    path = export_period_xlsx(db, report)
    return FileResponse(path, filename=path.name)


@router.get("/report-templates", response_model=typing.List[ReportTemplateOut])
def list_report_templates(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ReportTemplate]:
    return list(db.scalars(select(ReportTemplate).order_by(ReportTemplate.name)).all())


@router.post("/report-templates", response_model=ReportTemplateOut, status_code=201)
def create_report_template(
    payload: ReportTemplateCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ReportTemplate:
    if db.scalar(select(ReportTemplate).where(ReportTemplate.name == payload.name.strip())):
        raise ProblemException(409, "TEMPLATE_EXISTS", "模板名称已存在", "请使用其他名称。")
    template = ReportTemplate(
        name=payload.name.strip(),
        period_type=payload.period_type,
        description=payload.description.strip(),
        sections=[section.value for section in payload.sections],
        created_by=user.id,
    )
    db.add(template)
    db.flush()
    write_audit(db, user, "report_template.create", "report_template", template.id, {}, client_ip(request))
    db.commit()
    db.refresh(template)
    return template


@router.patch("/report-templates/{template_id}", response_model=ReportTemplateOut)
def patch_report_template(
    template_id: str,
    payload: ReportTemplatePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ReportTemplate:
    template = db.get(ReportTemplate, template_id)
    if not template:
        raise ProblemException(404, "REPORT_TEMPLATE_NOT_FOUND", "报告模板不存在", "未找到该报告模板。")
    if template.created_by != user.id and user.role != UserRole.ADMIN:
        raise ProblemException(403, "REPORT_TEMPLATE_EDIT_DENIED", "无权修改报告模板", "请由模板创建人或管理员操作。")
    if template.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "报告模板已变化", "请刷新后重试。")
    for field in payload.model_fields_set:
        value = getattr(payload, field)
        setattr(template, field, [item.value for item in value] if field == "sections" and value is not None else value)
    template.version += 1
    write_audit(db, user, "report_template.update", "report_template", template.id, {"fields": sorted(payload.model_fields_set)}, client_ip(request))
    db.commit()
    db.refresh(template)
    return template


@router.get("/archive-templates", response_model=typing.List[ArchiveTemplateOut])
def list_archive_templates(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArchiveTemplate]:
    return list(db.scalars(select(ArchiveTemplate).order_by(ArchiveTemplate.name)).all())


@router.post("/archive-templates", response_model=ArchiveTemplateOut, status_code=201)
def create_archive_template(
    payload: ArchiveTemplateCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArchiveTemplate:
    if db.scalar(select(ArchiveTemplate).where(ArchiveTemplate.name == payload.name.strip())):
        raise ProblemException(409, "TEMPLATE_EXISTS", "模板名称已存在", "请使用其他名称。")
    template = ArchiveTemplate(
        **payload.model_dump(),
        created_by=user.id,
    )
    db.add(template)
    db.flush()
    write_audit(db, user, "archive_template.create", "archive_template", template.id, {}, client_ip(request))
    db.commit()
    db.refresh(template)
    return template


def journal_visible(db: Session, entry: WorkJournalEntry, user: User) -> bool:
    if not entry.task_id:
        return True
    task = db.get(Task, entry.task_id)
    return bool(task and can_view_task(db, task, user))


@router.get("/work-journal", response_model=typing.List[WorkJournalOut])
def list_work_journal(
    task_id: str | None = None,
    created_by: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkJournalOut]:
    statement = select(WorkJournalEntry)
    if task_id:
        statement = statement.where(WorkJournalEntry.task_id == task_id)
    if created_by:
        statement = statement.where(WorkJournalEntry.created_by == created_by)
    if start_at:
        statement = statement.where(WorkJournalEntry.occurred_at >= start_at)
    if end_at:
        statement = statement.where(WorkJournalEntry.occurred_at < end_at)
    entries = db.scalars(
        statement.order_by(WorkJournalEntry.occurred_at.desc()).limit(limit)
    ).all()
    return [
        journal_to_out(db, entry)
        for entry in entries
        if journal_visible(db, entry, user)
    ]


@router.post("/work-journal", response_model=WorkJournalOut, status_code=201)
def create_work_journal(
    payload: WorkJournalCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkJournalOut:
    if payload.task_id:
        task = db.get(Task, payload.task_id)
        if not task or not can_view_task(db, task, user):
            raise ProblemException(404, "TASK_NOT_FOUND", "事项不存在", "未找到关联事项。")
    entry = WorkJournalEntry(
        entry_type="manual",
        title=payload.title.strip(),
        content=payload.content.strip(),
        occurred_at=payload.occurred_at or utcnow(),
        task_id=payload.task_id,
        file_id=payload.file_id,
        report_id=payload.report_id,
        immutable=False,
        created_by=user.id,
    )
    db.add(entry)
    db.flush()
    write_audit(db, user, "work_journal.create", "work_journal", entry.id, {}, client_ip(request))
    emit_event(db, "work_journal.created", entry.id, {"task_id": entry.task_id})
    db.commit()
    db.refresh(entry)
    return journal_to_out(db, entry)


@router.patch("/work-journal/{entry_id}", response_model=WorkJournalOut)
def patch_work_journal(
    entry_id: str,
    payload: WorkJournalPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkJournalOut:
    entry = db.get(WorkJournalEntry, entry_id)
    if not entry or not journal_visible(db, entry, user):
        raise ProblemException(404, "JOURNAL_NOT_FOUND", "工作日志不存在", "未找到该日志。")
    if entry.immutable:
        raise ProblemException(409, "JOURNAL_IMMUTABLE", "系统日志不可修改", "系统事件只允许追加。")
    if entry.created_by != user.id and user.role != UserRole.ADMIN:
        raise ProblemException(403, "JOURNAL_EDIT_DENIED", "无权修改", "只能修改自己记录的工作日志。")
    require_version(entry.version, if_match)
    revision_no = (
        db.scalar(
            select(func.max(WorkJournalRevision.revision_no)).where(
                WorkJournalRevision.entry_id == entry.id
            )
        )
        or 0
    ) + 1
    db.add(
        WorkJournalRevision(
            entry_id=entry.id,
            revision_no=revision_no,
            snapshot={
                "title": entry.title,
                "content": entry.content,
                "occurred_at": serialize_api_datetime(entry.occurred_at),
                "task_id": entry.task_id,
                "file_id": entry.file_id,
                "report_id": entry.report_id,
                "version": entry.version,
            },
            change_note=payload.change_note.strip(),
            created_by=user.id,
        )
    )
    for field in payload.model_fields_set - {"change_note"}:
        value = getattr(payload, field)
        setattr(entry, field, value.strip() if isinstance(value, str) else value)
    entry.version += 1
    write_audit(db, user, "work_journal.update", "work_journal", entry.id, {"version": entry.version}, client_ip(request))
    emit_event(db, "work_journal.updated", entry.id, {})
    db.commit()
    db.refresh(entry)
    return journal_to_out(db, entry)


@router.get(
    "/work-journal/{entry_id}/history",
    response_model=typing.List[WorkJournalRevisionOut],
)
def work_journal_history(
    entry_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkJournalRevision]:
    entry = db.get(WorkJournalEntry, entry_id)
    if not entry or not journal_visible(db, entry, user):
        raise ProblemException(
            404, "JOURNAL_NOT_FOUND", "工作日志不存在", "未找到该日志。"
        )
    return list(
        db.scalars(
            select(WorkJournalRevision)
            .where(WorkJournalRevision.entry_id == entry.id)
            .order_by(WorkJournalRevision.revision_no.desc())
        ).all()
    )


@router.get("/notifications", response_model=typing.List[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    notification_type: str | None = Query(default=None, max_length=48),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[Notification]:
    statement = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    if notification_type:
        statement = statement.where(Notification.notification_type == notification_type)
    return list(
        db.scalars(statement.order_by(Notification.created_at.desc()).limit(limit)).all()
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def read_notification(
    notification_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Notification:
    item = db.get(Notification, notification_id)
    if not item or item.user_id != user.id:
        raise ProblemException(404, "NOTIFICATION_NOT_FOUND", "提醒不存在", "未找到该提醒。")
    item.read_at = item.read_at or utcnow()
    db.commit()
    db.refresh(item)
    return item


@router.post("/notifications/read-all", response_model=dict)
def read_all_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    items = db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    ).all()
    now = utcnow()
    for item in items:
        item.read_at = now
    db.commit()
    return {"read": len(items)}
