"""高频工作流接口：工作台、批量处理、智能视图、专题空间、日历与交接包。"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
import difflib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..archive_service import can_view_category, fts_search as archive_fts_search
from ..config import get_settings
from ..database import db_runtime, get_session
from ..device_versions import request_device
from ..enums import ParticipantRole, TaskStatus
from ..models import (
    AutomationRule,
    AttachmentVersion,
    ArchiveCategory,
    ArchiveRecord,
    Contact,
    Device,
    DuplicateGroup,
    DocumentComparison,
    FileBlob,
    HandoverExport,
    KnowledgeEntry,
    MaterialItem,
    PeriodReport,
    SavedView,
    Task,
    TaskParticipant,
    TopicSpace,
    Transfer,
    User,
    WorkCalendarEntry,
    WorkJournalEntry,
    WorkspaceFile,
    WorkspaceLink,
    WorkspaceRoot,
    utcnow,
)
from ..problems import ProblemException
from ..recommendations import semantic_rerank_search_items
from ..schemas import (
    AutomationRuleCreate,
    AutomationRuleOut,
    AutomationRulePatch,
    DocumentComparisonCreate,
    DocumentComparisonOut,
    DuplicateGroupOut,
    SavedViewCreate,
    SavedViewOut,
    TaskBatchPatch,
    TopicSpaceCreate,
    TopicSpaceOut,
    TopicSpacePatch,
    WorkCalendarEntryCreate,
    WorkCalendarEntryOut,
    WorkCalendarEntryPatch,
    serialize_api_datetime,
)
from ..security import get_current_user, require_admin
from ..state_machine import transition
from ..storage import resolve_blob_path
from ..task_service import (
    apply_task_action,
    can_edit_task,
    can_manage_task,
    can_view_task,
    task_to_out,
    task_visibility_clause,
    visible_tasks,
)
from ..workspace import search_workspace_files
from ..workspace_access import workspace_root_permissions


router = APIRouter(tags=["productivity"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def _transition_matches(current: TaskStatus, action: str, target: TaskStatus) -> bool:
    try:
        return transition(current, action) == target
    except ProblemException:
        return False


@router.get("/workbench", response_model=dict)
def workbench(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    from ..task_service import dashboard

    board = dashboard(db, user)
    transfers = db.scalars(
        select(Transfer)
        .where(
            Transfer.requested_by == user.id,
            Transfer.status.in_(["awaiting_approval", "transferring", "failed"]),
        )
        .order_by(Transfer.updated_at.desc())
        .limit(20)
    ).all()
    devices = db.scalars(
        select(Device).where(Device.active.is_(True)).order_by(Device.last_seen_at.desc())
    ).all()
    recent_files = db.scalars(
        select(WorkspaceFile).order_by(WorkspaceFile.last_seen_at.desc()).limit(12)
    ).all()
    return {
        "updated_at": serialize_api_datetime(utcnow()),
        "dashboard": board.model_dump(mode="json"),
        "pending_transfers": [
            {
                "id": item.id,
                "name": item.original_name,
                "status": item.status.value,
                "direction": item.direction.value,
                "progress": round(item.completed_chunks / item.total_chunks * 100) if item.total_chunks else 0,
            }
            for item in transfers
        ],
        "devices": [
            {
                "id": item.id,
                "name": item.name,
                "status": item.status.value,
                "last_seen_at": serialize_api_datetime(item.last_seen_at)
                if item.last_seen_at
                else None,
            }
            for item in devices
        ],
        "recent_files": [
            {
                "id": item.id,
                "name": item.name,
                "relative_path": item.relative_path,
                "status": item.status.value,
                "availability": item.availability.value,
            }
            for item in recent_files
        ],
    }


@router.get("/global-search", response_model=dict)
def global_search(
    request: Request,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """统一检索事项、文件、联系人、日志、报告、知识和设备。

    返回值只包含用于识别与跳转的最小摘要，不返回文件绝对路径、正文全文或
    敏感事项详情。
    """

    keyword = q.strip()
    lowered = keyword.lower()
    items: list[dict[str, object]] = []

    def matches(*values: object) -> bool:
        return not lowered or any(lowered in str(value or "").lower() for value in values)

    task_statement = select(Task).where(
        Task.deleted_at.is_(None),
        task_visibility_clause(user),
    )
    if lowered:
        task_statement = task_statement.where(
            or_(
                func.lower(Task.title).contains(lowered),
                func.lower(Task.source).contains(lowered),
                func.lower(Task.category).contains(lowered),
                func.lower(Task.work_area).contains(lowered),
                func.lower(cast(Task.tags, String)).contains(lowered),
            )
        )
    task_statement = task_statement.order_by(
        Task.internal_due_at.is_(None),
        Task.internal_due_at,
        Task.updated_at.desc(),
    ).limit(limit)
    for task in db.scalars(task_statement).all():
        items.append(
            {
                "type": "task",
                "id": task.id,
                "title": task.title,
                "subtitle": f"{task.category or '未分类'} · {task.status.value}",
                "route": f"/tasks/{task.id}",
                "updated_at": serialize_api_datetime(task.updated_at),
            }
        )
        if len(items) >= limit:
            break

    if len(items) < limit and keyword:
        current = request_device(request, db)
        for file in search_workspace_files(db, keyword, limit=limit - len(items)):
            if file.is_directory:
                continue
            root = db.get(WorkspaceRoot, file.root_id)
            if not root or not workspace_root_permissions(
                db,
                root,
                user,
                current.id if current else None,
            )["browse"]:
                continue
            items.append(
                {
                    "type": "file",
                    "id": file.id,
                    "title": file.name,
                    "subtitle": f"{file.extension or '文件'} · {file.availability.value}",
                    "route": f"/workspace?file={file.id}",
                    "updated_at": serialize_api_datetime(
                        file.modified_at or file.last_seen_at
                    )
                    if file.modified_at or file.last_seen_at
                    else None,
                }
            )

    if len(items) < limit and keyword:
        archive_ids = archive_fts_search(db, keyword, limit=limit - len(items))
        if archive_ids:
            records = db.scalars(
                select(ArchiveRecord)
                .where(ArchiveRecord.id.in_(archive_ids))
                .order_by(ArchiveRecord.updated_at.desc())
            ).all()
        else:
            records = db.scalars(
                select(ArchiveRecord)
                .where(ArchiveRecord.search_text.contains(keyword))
                .order_by(ArchiveRecord.updated_at.desc())
                .limit(limit - len(items))
            ).all()
        for record in records:
            category = db.get(ArchiveCategory, record.category_id)
            if (
                not category
                or not category.active
                or not can_view_category(db, category, user)
                or record.status.value == "voided"
            ):
                continue
            items.append(
                {
                    "type": "archive",
                    "id": record.id,
                    "title": record.title,
                    "subtitle": f"{record.archive_year} · {category.name} · {record.document_no or '无文号'}",
                    "route": f"/archives?record={record.id}",
                    "updated_at": serialize_api_datetime(record.updated_at),
                }
            )
            if len(items) >= limit:
                break

    if len(items) < limit:
        for contact in db.scalars(select(Contact).order_by(Contact.name).limit(limit)).all():
            if not matches(contact.name, contact.organization, contact.note):
                continue
            items.append(
                {
                    "type": "contact",
                    "id": contact.id,
                    "title": contact.name,
                    "subtitle": contact.organization or "联系人",
                    "route": f"/knowledge?contact={contact.id}",
                    "updated_at": None,
                }
            )
            if len(items) >= limit:
                break

    if len(items) < limit:
        entries = db.scalars(
            select(WorkJournalEntry)
            .order_by(WorkJournalEntry.occurred_at.desc())
            .limit(limit * 2)
        ).all()
        for entry in entries:
            if entry.task_id:
                task = db.get(Task, entry.task_id)
                if not task or not can_view_task(db, task, user):
                    continue
            if not matches(entry.title, entry.content):
                continue
            items.append(
                {
                    "type": "journal",
                    "id": entry.id,
                    "title": entry.title,
                    "subtitle": "系统日志" if entry.immutable else "工作日志",
                    "route": f"/journal?entry={entry.id}",
                    "updated_at": serialize_api_datetime(entry.updated_at),
                }
            )
            if len(items) >= limit:
                break

    if len(items) < limit:
        for report in db.scalars(
            select(PeriodReport).order_by(PeriodReport.updated_at.desc()).limit(limit)
        ).all():
            if not matches(report.title, report.summary, report.period_key):
                continue
            items.append(
                {
                    "type": "report",
                    "id": report.id,
                    "title": report.title,
                    "subtitle": f"{report.period_key} · {report.status.value}",
                    "route": f"/reports?report={report.id}",
                    "updated_at": serialize_api_datetime(report.updated_at),
                }
            )
            if len(items) >= limit:
                break

    if len(items) < limit:
        for entry in db.scalars(
            select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc()).limit(limit)
        ).all():
            if not matches(entry.title, entry.category, entry.body):
                continue
            items.append(
                {
                    "type": "knowledge",
                    "id": entry.id,
                    "title": entry.title,
                    "subtitle": entry.category or "知识条目",
                    "route": f"/knowledge?entry={entry.id}",
                    "updated_at": serialize_api_datetime(entry.updated_at),
                }
            )
            if len(items) >= limit:
                break

    if user.role.value == "admin" and len(items) < limit:
        for device in db.scalars(
            select(Device).where(Device.active.is_(True)).order_by(Device.name)
        ).all():
            if not matches(device.name, device.platform, device.architecture, device.local_username):
                continue
            items.append(
                {
                    "type": "device",
                    "id": device.id,
                    "title": device.name,
                    "subtitle": f"{device.architecture} · {device.status.value}",
                    "route": "/fleet",
                    "updated_at": serialize_api_datetime(device.last_seen_at)
                    if device.last_seen_at
                    else None,
                }
            )
            if len(items) >= limit:
                break

    ranked_items = semantic_rerank_search_items(db, keyword, items[:limit])
    return {"query": keyword, "items": ranked_items, "total": len(ranked_items)}


@router.post("/tasks/batch", response_model=dict)
def batch_tasks(
    payload: TaskBatchPatch,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    tasks = [db.get(Task, task_id) for task_id in payload.task_ids]
    if any(task is None or not can_edit_task(db, task, user) for task in tasks):
        raise ProblemException(403, "BATCH_EDIT_DENIED", "批量修改包含无权事项", "请只选择自己可办理的事项。")
    if payload.owner_id and any(
        task is None or not can_manage_task(db, task, user) for task in tasks
    ):
        raise ProblemException(
            403,
            "BATCH_MANAGE_DENIED",
            "批量转交包含无权事项",
            "协办人可以批量补充办理信息，但不能转交主办责任。",
        )
    next_owner = db.get(User, payload.owner_id) if payload.owner_id else None
    if payload.owner_id and (next_owner is None or not next_owner.active):
        raise ProblemException(422, "OWNER_INVALID", "责任人无效", "请选择有效责任人。")
    changed: list[str] = []
    with db_runtime.write_lock:
        for task in tasks:
            if task is None:
                continue
            if payload.status and task.status != payload.status:
                action = next(
                    (
                        candidate
                        for candidate in (
                            "accept",
                            "start",
                            "wait_feedback",
                            "resume",
                            "submit_review",
                            "return",
                            "approve",
                            "complete",
                            "archive",
                            "reopen",
                        )
                        if _transition_matches(task.status, candidate, payload.status)
                    ),
                    None,
                )
                if action is None:
                    raise ProblemException(
                        409,
                        "BATCH_INVALID_TRANSITION",
                        "批量状态变更无法完成",
                        f"事项“{task.title}”不能从当前状态批量变更到目标状态。",
                    )
                apply_task_action(
                    db,
                    task,
                    action,
                    payload.note or "批量状态变更",
                    user,
                    client_ip(request),
                    task.version,
                    commit=False,
                )
            fields: dict[str, object] = {}
            if payload.owner_id:
                fields["owner_id"] = payload.owner_id
            if payload.internal_due_at is not None:
                fields["internal_due_at"] = payload.internal_due_at
            if payload.planned_start_at is not None:
                fields["planned_start_at"] = payload.planned_start_at
            if payload.planned_end_at is not None:
                fields["planned_end_at"] = payload.planned_end_at
            if payload.tags is not None:
                fields["tags"] = payload.tags
            if fields:
                for key, value in fields.items():
                    setattr(task, key, value)
                if "owner_id" in fields:
                    db.query(TaskParticipant).filter(
                        TaskParticipant.task_id == task.id,
                        TaskParticipant.role == ParticipantRole.OWNER,
                    ).delete(synchronize_session=False)
                    db.add(
                        TaskParticipant(
                            task_id=task.id,
                            user_id=str(fields["owner_id"]),
                            role=ParticipantRole.OWNER,
                        )
                    )
                task.updated_by = user.id
                task.version += 1
            if fields:
                write_audit(db, user, "task.batch_update", "task", task.id, {"fields": sorted(fields)}, client_ip(request))
            changed.append(task.id)
    db.commit()
    return {"updated": changed, "count": len(changed)}


@router.get("/saved-views", response_model=list[SavedViewOut])
def list_saved_views(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SavedView]:
    return list(
        db.scalars(
            select(SavedView)
            .where(SavedView.owner_id == user.id)
            .order_by(SavedView.pinned.desc(), SavedView.name)
        ).all()
    )


@router.post("/saved-views", response_model=SavedViewOut, status_code=201)
def create_saved_view(
    payload: SavedViewCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SavedView:
    view = SavedView(owner_id=user.id, **payload.model_dump())
    db.add(view)
    db.commit()
    db.refresh(view)
    return view


@router.delete("/saved-views/{view_id}", response_model=dict)
def delete_saved_view(
    view_id: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    view = db.get(SavedView, view_id)
    if not view or view.owner_id != user.id:
        raise ProblemException(404, "SAVED_VIEW_NOT_FOUND", "保存视图不存在", "未找到该保存视图。")
    if view.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "保存视图已变化", "请刷新后重试。")
    db.delete(view)
    db.commit()
    return {"deleted": True}


@router.get("/topics", response_model=list[TopicSpaceOut])
def list_topics(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[TopicSpace]:
    return list(
        db.scalars(
            select(TopicSpace)
            .where(TopicSpace.owner_id == user.id, TopicSpace.active.is_(True))
            .order_by(TopicSpace.updated_at.desc())
        ).all()
    )


@router.post("/topics", response_model=TopicSpaceOut, status_code=201)
def create_topic(
    payload: TopicSpaceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TopicSpace:
    if db.scalar(select(TopicSpace).where(TopicSpace.name == payload.name.strip())):
        raise ProblemException(409, "TOPIC_EXISTS", "专题已存在", "请使用其他专题名称。")
    topic = TopicSpace(owner_id=user.id, name=payload.name.strip(), description=payload.description)
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.patch("/topics/{topic_id}", response_model=TopicSpaceOut)
def patch_topic(
    topic_id: str,
    payload: TopicSpacePatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TopicSpace:
    topic = db.get(TopicSpace, topic_id)
    if not topic or topic.owner_id != user.id:
        raise ProblemException(404, "TOPIC_NOT_FOUND", "专题不存在", "未找到该专题。")
    if topic.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "专题已变化", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(topic, field, getattr(payload, field))
    topic.version += 1
    db.commit()
    db.refresh(topic)
    return topic


@router.get("/automation-rules", response_model=list[AutomationRuleOut])
def list_automation_rules(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AutomationRule]:
    return list(db.scalars(select(AutomationRule).where(AutomationRule.owner_id == user.id)).all())


@router.post("/automation-rules", response_model=AutomationRuleOut, status_code=201)
def create_automation_rule(
    payload: AutomationRuleCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AutomationRule:
    rule = AutomationRule(owner_id=user.id, **payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/automation-rules/{rule_id}", response_model=AutomationRuleOut)
def patch_automation_rule(
    rule_id: str,
    payload: AutomationRulePatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AutomationRule:
    rule = db.get(AutomationRule, rule_id)
    if not rule or rule.owner_id != user.id:
        raise ProblemException(404, "AUTOMATION_RULE_NOT_FOUND", "自动规则不存在", "未找到该自动规则。")
    if rule.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "自动规则已变化", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(rule, field, getattr(payload, field))
    rule.version += 1
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/automation-rules/{rule_id}", response_model=dict)
def delete_automation_rule(
    rule_id: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    rule = db.get(AutomationRule, rule_id)
    if not rule or rule.owner_id != user.id:
        raise ProblemException(404, "AUTOMATION_RULE_NOT_FOUND", "自动规则不存在", "未找到该自动规则。")
    if rule.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "自动规则已变化", "请刷新后重试。")
    db.delete(rule)
    db.commit()
    return {"deleted": True}


@router.get("/work-calendar", response_model=list[WorkCalendarEntryOut])
def list_calendar(
    year: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkCalendarEntry]:
    statement = select(WorkCalendarEntry)
    if year:
        statement = statement.where(WorkCalendarEntry.date_key.startswith(f"{year:04d}-"))
    return list(db.scalars(statement.order_by(WorkCalendarEntry.date_key)).all())


@router.post("/work-calendar", response_model=WorkCalendarEntryOut, status_code=201)
def create_calendar_entry(
    payload: WorkCalendarEntryCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> WorkCalendarEntry:
    existing = db.scalar(
        select(WorkCalendarEntry).where(
            WorkCalendarEntry.date_key == payload.date_key,
            WorkCalendarEntry.kind == payload.kind,
        )
    )
    if existing:
        raise ProblemException(
            409,
            "WORK_CALENDAR_ENTRY_EXISTS",
            "该日历日期已存在",
            "请编辑现有记录，不要重复添加。",
        )
    entry = WorkCalendarEntry(owner_id=user.id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.patch("/work-calendar/{entry_id}", response_model=WorkCalendarEntryOut)
def patch_calendar_entry(
    entry_id: str,
    payload: WorkCalendarEntryPatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> WorkCalendarEntry:
    entry = db.get(WorkCalendarEntry, entry_id)
    if not entry:
        raise ProblemException(404, "WORK_CALENDAR_ENTRY_NOT_FOUND", "日历记录不存在", "未找到该日期。")
    if entry.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "日历记录已变化", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(entry, field, getattr(payload, field))
    entry.version += 1
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/work-calendar/{entry_id}", response_model=dict)
def delete_calendar_entry(
    entry_id: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    entry = db.get(WorkCalendarEntry, entry_id)
    if not entry:
        raise ProblemException(404, "WORK_CALENDAR_ENTRY_NOT_FOUND", "日历记录不存在", "未找到该日期。")
    if entry.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "日历记录已变化", "请刷新后重试。")
    db.delete(entry)
    db.commit()
    return {"deleted": True}


@router.post("/handover", response_model=dict, status_code=201)
def create_handover(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    tasks = db.scalars(
        select(Task)
        .where(Task.deleted_at.is_(None), Task.status.not_in({TaskStatus.COMPLETED, TaskStatus.ARCHIVED}))
        .order_by(Task.internal_due_at.is_(None), Task.internal_due_at)
    ).all()
    tasks = [task for task in tasks if can_view_task(db, task, user)]
    task_ids = [task.id for task in tasks]
    contact_ids = {
        contact_id
        for task in tasks
        for contact_id in (task.contact_ids or [])
    }
    contacts = {
        item.id: item
        for item in db.scalars(select(Contact).where(Contact.id.in_(contact_ids or {""}))).all()
    }
    links = db.scalars(
        select(WorkspaceLink).where(
            WorkspaceLink.entity_type == "task",
            WorkspaceLink.entity_id.in_(task_ids or [""]),
        )
    ).all()
    workspace_files = {
        item.id: item
        for item in db.scalars(
            select(WorkspaceFile).where(
                WorkspaceFile.id.in_([link.file_id for link in links] or [""])
            )
        ).all()
    }
    links_by_task: dict[str, list[WorkspaceFile]] = {}
    for link in links:
        file = workspace_files.get(link.file_id)
        if file:
            links_by_task.setdefault(link.entity_id, []).append(file)

    material_items = db.scalars(
        select(MaterialItem).where(MaterialItem.task_id.in_(task_ids or [""]))
    ).all()
    materials_by_id = {item.id: item for item in material_items}
    final_versions = db.scalars(
        select(AttachmentVersion).where(
            AttachmentVersion.material_item_id.in_(list(materials_by_id) or [""]),
            AttachmentVersion.is_final.is_(True),
        )
    ).all()
    blobs = {
        item.sha256: item
        for item in db.scalars(
            select(FileBlob).where(
                FileBlob.sha256.in_([version.blob_sha256 for version in final_versions] or [""])
            )
        ).all()
    }
    finals_by_task: dict[str, list[dict[str, object]]] = {}
    archive_blobs: dict[str, tuple[Path, str]] = {}
    for version in final_versions:
        material = materials_by_id.get(version.material_item_id)
        blob = blobs.get(version.blob_sha256)
        if not material or not blob:
            continue
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", version.display_name or blob.original_name)
        archive_name = f"最终材料/{version.blob_sha256[:12]}-{safe_name}"
        source = resolve_blob_path(blob.relative_path)
        if source.is_file():
            archive_blobs.setdefault(version.blob_sha256, (source, archive_name))
        finals_by_task.setdefault(material.task_id, []).append(
            {
                "material": material.name,
                "category": material.category,
                "filename": safe_name,
                "sha256": version.blob_sha256,
                "archive_path": archive_name if source.is_file() else None,
            }
        )
    payload = {
        "generated_at": serialize_api_datetime(utcnow()),
        "operator": user.display_name,
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "owner_id": task.owner_id,
                "internal_due_at": serialize_api_datetime(task.internal_due_at)
                if task.internal_due_at
                else None,
                "formal_due_at": serialize_api_datetime(task.formal_due_at)
                if task.formal_due_at
                else None,
                "missing_materials": task_to_out(db, task, include_detail=False).missing_required_materials,
                "contacts": [
                    {
                        "name": contacts[contact_id].name,
                        "organization": contacts[contact_id].organization,
                        "phone": contacts[contact_id].phone,
                    }
                    for contact_id in (task.contact_ids or [])
                    if contact_id in contacts
                ],
                "linked_files": [
                    {
                        "name": file.name,
                        "extension": file.extension,
                        "availability": file.availability.value,
                        "sha256": file.sha256,
                    }
                    for file in links_by_task.get(task.id, [])
                ],
                "final_materials": finals_by_task.get(task.id, []),
            }
            for task in tasks
        ],
    }
    # 同一秒内重复生成交接包也必须得到独立文件名，避免唯一索引冲突。
    filename = f"PartyOps-交接清单-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.zip"
    path = get_settings().exports_dir / filename
    manifest_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    checksums = [f"{hashlib.sha256(manifest_bytes).hexdigest()}  manifest.json"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        for sha256, (source, archive_name) in archive_blobs.items():
            archive.write(source, archive_name)
            checksums.append(f"{sha256}  {archive_name}")
        archive.writestr("校验清单.sha256", "\n".join(checksums) + "\n")
    record = HandoverExport(
        name="工作交接清单",
        scope={
            "task_count": len(tasks),
            "contact_count": len(contacts),
            "linked_file_count": sum(len(items) for items in links_by_task.values()),
            "final_attachment_count": len(archive_blobs),
        },
        filename=filename,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
        created_by=user.id,
    )
    db.add(record)
    write_audit(db, user, "handover.create", "handover_export", record.id, {"task_count": len(tasks)}, client_ip(request))
    db.commit()
    return {"id": record.id, "filename": filename, "sha256": record.sha256, "size_bytes": record.size_bytes}


@router.post("/document-comparisons", response_model=DocumentComparisonOut, status_code=201)
def compare_documents(
    payload: DocumentComparisonCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> DocumentComparison:
    left = db.get(WorkspaceFile, payload.left_file_id)
    right = db.get(WorkspaceFile, payload.right_file_id)
    if not left or not right:
        raise ProblemException(404, "WORKSPACE_FILE_NOT_FOUND", "比较文件不存在", "请重新选择文件。")
    left_text = left.extracted_text or left.ocr_text
    right_text = right.extracted_text or right.ocr_text
    if payload.comparison_type == "text" and (not left_text or not right_text):
        raise ProblemException(422, "TEXT_COMPARE_UNAVAILABLE", "文件没有可比较正文", "请使用文本可提取的 DOCX、PDF 或文本文件。")
    if payload.comparison_type == "text":
        diff = list(
            difflib.unified_diff(
                left_text.splitlines(),
                right_text.splitlines(),
                fromfile=left.name,
                tofile=right.name,
                lineterm="",
            )
        )
        result = {"changed": left_text != right_text, "lines": diff[:20_000], "left_length": len(left_text), "right_length": len(right_text)}
    else:
        result = {
            "changed": left.sha256 != right.sha256 or left.size_bytes != right.size_bytes,
            "left": {"name": left.name, "size_bytes": left.size_bytes, "sha256": left.sha256},
            "right": {"name": right.name, "size_bytes": right.size_bytes, "sha256": right.sha256},
        }
    comparison = DocumentComparison(
        left_file_id=left.id,
        right_file_id=right.id,
        comparison_type=payload.comparison_type,
        result=result,
        created_by=user.id,
    )
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return comparison


@router.get("/document-comparisons", response_model=list[DocumentComparisonOut])
def list_document_comparisons(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[DocumentComparison]:
    statement = select(DocumentComparison)
    if user.role.value != "admin":
        statement = statement.where(DocumentComparison.created_by == user.id)
    return list(db.scalars(statement.order_by(DocumentComparison.created_at.desc()).limit(100)).all())


@router.post("/duplicates/scan", response_model=list[DuplicateGroupOut])
def scan_duplicates(
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[DuplicateGroup]:
    files = list(db.scalars(
        select(WorkspaceFile).where(
            WorkspaceFile.is_directory.is_(False),
        )
    ).all())
    groups: dict[str, list[str]] = {}
    for file in files:
        if file.sha256:
            groups.setdefault(file.sha256, []).append(file.id)
    db.query(DuplicateGroup).delete(synchronize_session=False)
    result: list[DuplicateGroup] = []
    for fingerprint, file_ids in groups.items():
        if len(file_ids) < 2:
            continue
        group = DuplicateGroup(
            algorithm="sha256",
            fingerprint=fingerprint,
            file_ids=file_ids,
            status="open",
        )
        db.add(group)
        result.append(group)
    # 文本近似重复只提出建议，限制候选数量避免扫描阻塞日常任务。
    text_files = [
        file
        for file in files
        if (file.extracted_text or file.ocr_text).strip()
    ][:1_000]
    shingles: dict[str, set[str]] = {}
    for file in text_files:
        normalized = re.sub(r"\s+", "", file.extracted_text or file.ocr_text).lower()
        shingles[file.id] = {
            normalized[index : index + 3]
            for index in range(max(0, len(normalized) - 2))
        }
    for index, left in enumerate(text_files):
        left_set = shingles[left.id]
        if len(left_set) < 8:
            continue
        for right in text_files[index + 1 :]:
            right_set = shingles[right.id]
            if len(right_set) < 8:
                continue
            score = len(left_set & right_set) / len(left_set | right_set)
            if score < 0.82:
                continue
            fingerprint = hashlib.sha256(
                f"{left.id}:{right.id}".encode("utf-8")
            ).hexdigest()[:32]
            group = DuplicateGroup(
                algorithm="text_jaccard",
                fingerprint=fingerprint,
                file_ids=[left.id, right.id],
                status="open",
            )
            db.add(group)
            result.append(group)
    db.commit()
    for group in result:
        db.refresh(group)
    return result


@router.get("/duplicates", response_model=list[DuplicateGroupOut])
def list_duplicates(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[DuplicateGroup]:
    return list(db.scalars(select(DuplicateGroup).order_by(DuplicateGroup.created_at.desc())).all())


@router.get("/handover/{export_id}/download")
def download_handover(
    export_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    record = db.get(HandoverExport, export_id)
    if not record or (record.created_by != user.id and user.role.value != "admin"):
        raise ProblemException(404, "HANDOVER_NOT_FOUND", "交接包不存在", "未找到交接包。")
    path = get_settings().exports_dir / record.filename
    if not path.exists():
        raise ProblemException(410, "HANDOVER_FILE_MISSING", "交接包文件缺失", "请重新生成交接包。")
    return FileResponse(path, media_type="application/zip", filename=record.filename)
