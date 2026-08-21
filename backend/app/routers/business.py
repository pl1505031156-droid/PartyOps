"""会议筹备、结构化在线文档与年度统计。"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, List
from urllib.parse import quote

from docx import Document
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..database import get_session
from ..enums import Priority, Sensitivity, TaskStatus, TaskType
from ..models import (
    BusinessDocument,
    BusinessDocumentRevision,
    BusinessMeeting,
    MeetingTopic,
    Task,
    TaskStep,
    User,
    WorkflowTemplate,
    utcnow,
)
from ..problems import ProblemException
from ..security import get_current_user, require_admin
from .router_utils import client_ip, parse_if_match


router = APIRouter(tags=["business-workflows"])

MEETING_TYPES = {
    "party_committee": "党委会",
    "branch_members": "支委会",
    "party_member_meeting": "党员大会",
    "party_group": "党小组会",
    "party_class": "党课",
    "study_group": "党委（党组）理论学习中心组学习",
    "theme_party_day": "主题党日",
    "organization_life": "组织生活会",
}

COMMITTEE_STEPS = [
    {"title": "会议议程起草", "offset_days": -20, "responsible_role": "承办人", "required_document": "agenda"},
    {"title": "人员通知", "offset_days": -10, "responsible_role": "会务", "required_document": "notice"},
    {"title": "会议资料准备", "offset_days": -7, "responsible_role": "承办人", "required_document": "materials"},
    {"title": "资料打印", "offset_days": -1, "responsible_role": "会务", "required_document": ""},
    {"title": "会议记录", "offset_days": 0, "responsible_role": "记录人", "required_document": "minutes"},
    {"title": "会后纪要", "offset_days": 3, "responsible_role": "记录人", "required_document": "summary"},
]


class WorkflowTemplateInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    business_type: str = Field(min_length=2, max_length=48)
    description: str = Field(default="", max_length=4000)
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    recurrence: dict[str, Any] = Field(default_factory=dict)


class MeetingInput(BaseModel):
    meeting_type: str
    organization: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    scheduled_at: datetime | None = None
    workflow_template_id: str | None = None
    owner_id: str | None = None
    assignees: dict[str, str] = Field(default_factory=dict)
    recurrence_key: str = Field(default="", max_length=120)


class MeetingPatch(BaseModel):
    organization: str | None = Field(default=None, min_length=1, max_length=160)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    scheduled_at: datetime | None = None
    status: str | None = Field(default=None, pattern=r"^(planned|in_progress|completed|cancelled|archived)$")


class TopicInput(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    review_result: str = Field(default="", max_length=20_000)
    amount: str = "0"
    reviewed: bool = False
    amount_confirmed: bool = False


class DocumentInput(BaseModel):
    meeting_id: str | None = None
    task_step_id: str | None = None
    document_type: str = Field(pattern=r"^(agenda|notice|minutes|summary|materials|other)$")
    title: str = Field(min_length=1, max_length=240)
    content: dict[str, Any] = Field(default_factory=dict)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: dict[str, Any] | None = None
    change_note: str = Field(default="", max_length=500)


def ensure_committee_template(db: Session, user: User) -> WorkflowTemplate:
    template = db.scalar(select(WorkflowTemplate).where(WorkflowTemplate.name == "党委会筹备（六步）"))
    if template:
        return template
    template = WorkflowTemplate(
        name="党委会筹备（六步）",
        business_type="party_committee",
        description="议程、通知、资料、打印、记录、纪要六个固定步骤，可按月复用。",
        steps=COMMITTEE_STEPS,
        recurrence={"kind": "monthly", "enabled": False},
        created_by=user.id,
    )
    db.add(template)
    db.flush()
    return template


def template_out(item: WorkflowTemplate) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "business_type": item.business_type,
        "description": item.description,
        "steps": item.steps,
        "recurrence": item.recurrence,
        "active": item.active,
        "version": item.version,
    }


def meeting_out(db: Session, item: BusinessMeeting) -> dict[str, Any]:
    steps = db.scalars(select(TaskStep).where(TaskStep.task_id == item.task_id).order_by(TaskStep.sort_order)).all() if item.task_id else []
    topics = db.scalars(select(MeetingTopic).where(MeetingTopic.meeting_id == item.id).order_by(MeetingTopic.created_at)).all()
    done = sum(1 for step in steps if step.done)
    return {
        "id": item.id,
        "meeting_type": item.meeting_type,
        "meeting_type_label": MEETING_TYPES.get(item.meeting_type, item.meeting_type),
        "organization": item.organization,
        "title": item.title,
        "scheduled_at": item.scheduled_at,
        "completed_at": item.completed_at,
        "status": item.status,
        "workflow_template_id": item.workflow_template_id,
        "task_id": item.task_id,
        "recurrence_key": item.recurrence_key,
        "version": item.version,
        "progress": {"done": done, "total": len(steps), "percent": round(done * 100 / len(steps)) if steps else 0},
        "steps": [
            {"id": step.id, "title": step.title, "assignee_id": step.assignee_id, "due_at": step.due_at, "done": step.done, "version": step.version}
            for step in steps
        ],
        "topics": [
            {"id": topic.id, "title": topic.title, "review_result": topic.review_result, "amount": f"{Decimal(topic.amount_cents) / 100:.2f}", "reviewed": topic.reviewed, "amount_confirmed": topic.amount_confirmed, "version": topic.version}
            for topic in topics
        ],
    }


def _create_meeting_record(
    db: Session,
    *,
    template: WorkflowTemplate,
    meeting_type: str,
    organization: str,
    title: str,
    scheduled_at: datetime | None,
    owner: User,
    created_by: User,
    assignees: dict[str, str] | None = None,
    recurrence_key: str = "",
) -> BusinessMeeting:
    """原子创建会议、任务和步骤，供手工入口与周期调度共享。"""

    if recurrence_key and db.scalar(
        select(BusinessMeeting.id).where(
            BusinessMeeting.recurrence_key == recurrence_key
        )
    ):
        raise ProblemException(
            409,
            "MEETING_RECURRENCE_EXISTS",
            "本周期会议已生成",
            "请打开已有会议，不要重复生成。",
        )
    task = Task(
        title=title,
        description=f"{MEETING_TYPES[meeting_type]}筹备流程",
        task_type=TaskType.PROJECT,
        status=TaskStatus.IN_PROGRESS,
        sensitivity=Sensitivity.NORMAL,
        priority=Priority.NORMAL,
        source="会议工作流",
        source_kind="workflow",
        category="党建会议",
        formal_due_at=scheduled_at,
        internal_due_at=scheduled_at,
        owner_id=owner.id,
        created_by=created_by.id,
        updated_by=created_by.id,
    )
    db.add(task)
    db.flush()
    assignments = assignees or {}
    assignment_ids = {str(value) for value in assignments.values() if value}
    if assignment_ids:
        active_ids = set(
            db.scalars(
                select(User.id).where(User.id.in_(assignment_ids), User.active.is_(True))
            ).all()
        )
        if active_ids != assignment_ids:
            raise ProblemException(
                422,
                "MEETING_ASSIGNEE_INVALID",
                "步骤负责人不可用",
                "请为每个角色选择仍在启用状态的用户。",
            )
    for order, definition in enumerate(template.steps):
        role = str(definition.get("responsible_role", ""))
        due_at = (
            scheduled_at + timedelta(days=int(definition.get("offset_days", 0)))
            if scheduled_at
            else None
        )
        db.add(
            TaskStep(
                task_id=task.id,
                title=str(definition.get("title", f"步骤{order + 1}"))[:240],
                assignee_id=assignments.get(role) or owner.id,
                due_at=due_at,
                sort_order=order,
            )
        )
    meeting = BusinessMeeting(
        meeting_type=meeting_type,
        organization=organization.strip(),
        title=title.strip(),
        scheduled_at=scheduled_at,
        workflow_template_id=template.id,
        task_id=task.id,
        recurrence_key=recurrence_key,
        created_by=created_by.id,
    )
    db.add(meeting)
    db.flush()
    return meeting


def generate_due_recurring_meetings(
    db: Session,
    admin: User,
    now: datetime,
) -> list[BusinessMeeting]:
    """幂等生成本月及预生成月份会议；无完整配置的模板保持停用。"""

    local_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    templates = db.scalars(
        select(WorkflowTemplate).where(WorkflowTemplate.active.is_(True))
    ).all()
    generated: list[BusinessMeeting] = []
    for template in templates:
        recurrence = template.recurrence if isinstance(template.recurrence, dict) else {}
        if recurrence.get("kind") != "monthly" or recurrence.get("enabled") is not True:
            continue
        organization = str(recurrence.get("organization", "")).strip()
        if not organization:
            continue
        day = max(1, min(28, int(recurrence.get("day", 1) or 1)))
        hour = max(0, min(23, int(recurrence.get("hour", 9) or 9)))
        minute = max(0, min(59, int(recurrence.get("minute", 0) or 0)))
        months_ahead = max(0, min(12, int(recurrence.get("months_ahead", 1) or 1)))
        owner = db.get(User, str(recurrence.get("owner_id", ""))) or admin
        if not owner.active:
            owner = admin
        for offset in range(months_ahead + 1):
            absolute_month = local_now.year * 12 + local_now.month - 1 + offset
            year, month_index = divmod(absolute_month, 12)
            month = month_index + 1
            recurrence_key = f"{template.id}:{year:04d}-{month:02d}"
            if db.scalar(
                select(BusinessMeeting.id).where(
                    BusinessMeeting.recurrence_key == recurrence_key
                )
            ):
                continue
            scheduled = datetime(year, month, day, hour, minute, tzinfo=local_now.tzinfo)
            pattern = str(recurrence.get("title_pattern", "{year}年{month}月{type}"))
            title = pattern.format(
                year=year,
                month=month,
                type=MEETING_TYPES.get(template.business_type, template.business_type),
                organization=organization,
            )[:240]
            generated.append(
                _create_meeting_record(
                    db,
                    template=template,
                    meeting_type=template.business_type,
                    organization=organization,
                    title=title,
                    scheduled_at=scheduled,
                    owner=owner,
                    created_by=admin,
                    recurrence_key=recurrence_key,
                )
            )
    return generated


@router.get("/workflow-templates", response_model=List[dict])
def list_workflow_templates(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_committee_template(db, user)
    db.commit()
    return [template_out(item) for item in db.scalars(select(WorkflowTemplate).where(WorkflowTemplate.active.is_(True)).order_by(WorkflowTemplate.name)).all()]


@router.post("/workflow-templates", response_model=dict, status_code=201)
def create_workflow_template(
    payload: WorkflowTemplateInput,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if not payload.steps:
        raise ProblemException(422, "WORKFLOW_STEPS_REQUIRED", "流程步骤不能为空", "请至少配置一个有完成条件的步骤。")
    item = WorkflowTemplate(**payload.model_dump(), created_by=admin.id)
    db.add(item)
    db.flush()
    write_audit(db, admin, "workflow_template.create", "workflow_template", item.id, {"step_count": len(item.steps)}, client_ip(request))
    db.commit()
    return template_out(item)


@router.delete("/workflow-templates/{template_id}", response_model=dict)
def archive_workflow_template(
    template_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    item = db.get(WorkflowTemplate, template_id)
    if not item:
        raise ProblemException(404, "WORKFLOW_TEMPLATE_NOT_FOUND", "流程模板不存在", "请刷新后重试。")
    item.active = False
    item.version += 1
    write_audit(db, admin, "workflow_template.archive", "workflow_template", item.id, {}, client_ip(request))
    db.commit()
    return {"archived": True, "id": item.id}


@router.get("/business-meetings", response_model=List[dict])
def list_meetings(
    year: int | None = Query(default=None, ge=2000, le=2200),
    organization: str = "",
    meeting_type: str = "",
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(BusinessMeeting)
    if organization:
        query = query.where(BusinessMeeting.organization == organization)
    if meeting_type:
        query = query.where(BusinessMeeting.meeting_type == meeting_type)
    if year:
        query = query.where(BusinessMeeting.scheduled_at >= datetime(year, 1, 1, tzinfo=timezone.utc), BusinessMeeting.scheduled_at < datetime(year + 1, 1, 1, tzinfo=timezone.utc))
    return [meeting_out(db, item) for item in db.scalars(query.order_by(BusinessMeeting.scheduled_at.desc())).all()]


@router.post("/business-meetings", response_model=dict, status_code=201)
def create_meeting(
    payload: MeetingInput,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if payload.meeting_type not in MEETING_TYPES:
        raise ProblemException(422, "MEETING_TYPE_INVALID", "会议类型无效", "请选择系统支持的党建会议类型。")
    owner = db.get(User, payload.owner_id) if payload.owner_id else user
    if not owner or not owner.active:
        raise ProblemException(422, "MEETING_OWNER_INVALID", "负责人不可用", "请选择启用用户。")
    template = db.get(WorkflowTemplate, payload.workflow_template_id) if payload.workflow_template_id else ensure_committee_template(db, user)
    if not template or not template.active:
        raise ProblemException(
            422,
            "WORKFLOW_TEMPLATE_INVALID",
            "流程模板不可用",
            "请选择仍在启用状态的流程模板。",
        )
    meeting = _create_meeting_record(
        db,
        template=template,
        meeting_type=payload.meeting_type,
        organization=payload.organization,
        title=payload.title,
        scheduled_at=payload.scheduled_at,
        owner=owner,
        created_by=user,
        assignees=payload.assignees,
        recurrence_key=payload.recurrence_key,
    )
    write_audit(db, user, "business_meeting.create", "business_meeting", meeting.id, {"meeting_type": meeting.meeting_type, "task_id": meeting.task_id}, client_ip(request))
    db.commit()
    return meeting_out(db, meeting)


@router.post("/business-meetings/recurring/generate", response_model=dict)
def generate_recurring_meetings_now(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    generated = generate_due_recurring_meetings(db, admin, utcnow())
    write_audit(
        db,
        admin,
        "business_meeting.recurring_generate",
        "workflow_template",
        None,
        {"count": len(generated)},
        client_ip(request),
    )
    db.commit()
    return {"generated": len(generated), "meeting_ids": [item.id for item in generated]}


@router.patch("/business-meetings/{meeting_id}", response_model=dict)
def patch_meeting(
    meeting_id: str,
    payload: MeetingPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    item = db.get(BusinessMeeting, meeting_id)
    if not item:
        raise ProblemException(404, "MEETING_NOT_FOUND", "会议不存在", "请刷新后重试。")
    if item.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "会议信息已更新", "请刷新后重试。")
    changes = payload.model_dump(exclude_unset=True)
    old_schedule = item.scheduled_at
    for key, value in changes.items():
        setattr(item, key, value)
    if "scheduled_at" in changes and item.task_id:
        task = db.get(Task, item.task_id)
        if task:
            task.formal_due_at = item.scheduled_at
            task.internal_due_at = item.scheduled_at
            task.version += 1
            steps = db.scalars(
                select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.sort_order)
            ).all()
            template = db.get(WorkflowTemplate, item.workflow_template_id) if item.workflow_template_id else None
            definitions = template.steps if template and isinstance(template.steps, list) else []
            for index, step in enumerate(steps):
                if index < len(definitions) and isinstance(definitions[index], dict):
                    step.due_at = (
                        item.scheduled_at + timedelta(days=int(definitions[index].get("offset_days", 0)))
                        if item.scheduled_at
                        else None
                    )
                elif item.scheduled_at and old_schedule and step.due_at:
                    # 历史自定义流程没有模板定义时，按绝对时差平移；SQLite 读取的
                    # 时间可能丢失 tzinfo，统一视作 UTC 后再相减。
                    new_aware = item.scheduled_at if item.scheduled_at.tzinfo else item.scheduled_at.replace(tzinfo=timezone.utc)
                    old_aware = old_schedule if old_schedule.tzinfo else old_schedule.replace(tzinfo=timezone.utc)
                    step.due_at += new_aware.astimezone(timezone.utc) - old_aware.astimezone(timezone.utc)
                elif item.scheduled_at is None:
                    step.due_at = None
                step.version += 1
    if item.status == "completed" and item.completed_at is None:
        item.completed_at = utcnow()
    item.version += 1
    write_audit(db, user, "business_meeting.update", "business_meeting", item.id, {"fields": sorted(changes)}, client_ip(request))
    db.commit()
    return meeting_out(db, item)


@router.post("/business-meetings/{meeting_id}/topics", response_model=dict, status_code=201)
def create_topic(
    meeting_id: str,
    payload: TopicInput,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if not db.get(BusinessMeeting, meeting_id):
        raise ProblemException(404, "MEETING_NOT_FOUND", "会议不存在", "请刷新后重试。")
    try:
        amount = Decimal(payload.amount)
        if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
            raise InvalidOperation
        amount_cents = int(amount * 100)
    except (InvalidOperation, ValueError):
        raise ProblemException(422, "MEETING_AMOUNT_INVALID", "议题金额无效", "请输入最多两位小数的非负金额。")
    if amount_cents < 0 or amount_cents > 9_000_000_000_000_000:
        raise ProblemException(422, "MEETING_AMOUNT_INVALID", "议题金额无效", "金额必须为非负数且不能超过系统统计上限。")
    item = MeetingTopic(meeting_id=meeting_id, title=payload.title.strip(), review_result=payload.review_result, amount_cents=amount_cents, reviewed=payload.reviewed, amount_confirmed=payload.amount_confirmed)
    db.add(item)
    db.flush()
    write_audit(db, user, "meeting_topic.create", "meeting_topic", item.id, {"meeting_id": meeting_id}, client_ip(request))
    db.commit()
    return {"id": item.id, "meeting_id": meeting_id, "version": item.version}


@router.get("/business-meetings/statistics/annual", response_model=dict)
def annual_meeting_statistics(
    year: int = Query(ge=2000, le=2200),
    organization: str = "",
    meeting_type: str = "",
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(BusinessMeeting).where(BusinessMeeting.status == "completed", BusinessMeeting.completed_at >= datetime(year, 1, 1, tzinfo=timezone.utc), BusinessMeeting.completed_at < datetime(year + 1, 1, 1, tzinfo=timezone.utc))
    if organization:
        query = query.where(BusinessMeeting.organization == organization)
    if meeting_type:
        query = query.where(BusinessMeeting.meeting_type == meeting_type)
    meetings = db.scalars(query).all()
    ids = [item.id for item in meetings]
    topics = db.scalars(select(MeetingTopic).where(MeetingTopic.meeting_id.in_(ids), MeetingTopic.reviewed.is_(True))).all() if ids else []
    confirmed_cents = sum(topic.amount_cents for topic in topics if topic.amount_confirmed)
    return {"year": year, "organization": organization, "meeting_type": meeting_type, "completed_meetings": len(meetings), "reviewed_topics": len(topics), "confirmed_amount": f"{Decimal(confirmed_cents) / 100:.2f}"}


@router.get("/business-documents", response_model=List[dict])
def list_documents(
    meeting_id: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    query = select(BusinessDocument).where(BusinessDocument.archived_at.is_(None))
    if meeting_id:
        query = query.where(BusinessDocument.meeting_id == meeting_id)
    return [{"id": item.id, "meeting_id": item.meeting_id, "task_step_id": item.task_step_id, "document_type": item.document_type, "title": item.title, "content": item.content, "version": item.version, "updated_at": item.updated_at} for item in db.scalars(query.order_by(BusinessDocument.updated_at.desc())).all()]


@router.post("/business-documents", response_model=dict, status_code=201)
def create_document(
    payload: DocumentInput,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    meeting = db.get(BusinessMeeting, payload.meeting_id) if payload.meeting_id else None
    step = db.get(TaskStep, payload.task_step_id) if payload.task_step_id else None
    if payload.meeting_id and not meeting:
        raise ProblemException(422, "DOCUMENT_MEETING_INVALID", "关联会议不存在", "请刷新会议列表后重试。")
    if payload.task_step_id and not step:
        raise ProblemException(422, "DOCUMENT_STEP_INVALID", "关联步骤不存在", "请刷新筹备步骤后重试。")
    if meeting and step and meeting.task_id != step.task_id:
        raise ProblemException(422, "DOCUMENT_LINK_MISMATCH", "会议与步骤不匹配", "请选择该会议筹备流程内的步骤。")
    item = BusinessDocument(**payload.model_dump(), created_by=user.id)
    db.add(item)
    db.flush()
    db.add(BusinessDocumentRevision(document_id=item.id, revision_no=1, content=item.content, change_note="创建文档", created_by=user.id))
    write_audit(db, user, "business_document.create", "business_document", item.id, {"document_type": item.document_type}, client_ip(request))
    db.commit()
    return {"id": item.id, "version": item.version, "updated_at": item.updated_at}


@router.patch("/business-documents/{document_id}", response_model=dict)
def update_document(
    document_id: str,
    payload: DocumentPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    item = db.get(BusinessDocument, document_id)
    if not item or item.archived_at:
        raise ProblemException(404, "BUSINESS_DOCUMENT_NOT_FOUND", "文档不存在", "请刷新后重试。")
    if item.version != parse_if_match(if_match):
        raise ProblemException(409, "DOCUMENT_VERSION_CONFLICT", "文档已被其他人修改", "请刷新并合并修改后重试。", extra={"current_version": item.version})
    if payload.title is not None:
        item.title = payload.title.strip()
    if payload.content is not None:
        item.content = payload.content
    item.version += 1
    db.add(BusinessDocumentRevision(document_id=item.id, revision_no=item.version, content=item.content, change_note=payload.change_note, created_by=user.id))
    write_audit(db, user, "business_document.update", "business_document", item.id, {"version": item.version}, client_ip(request))
    db.commit()
    return {"id": item.id, "title": item.title, "content": item.content, "version": item.version, "updated_at": item.updated_at}


@router.get("/business-documents/{document_id}/revisions", response_model=List[dict])
def document_revisions(
    document_id: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    item = db.get(BusinessDocument, document_id)
    if not item or item.archived_at:
        raise ProblemException(404, "BUSINESS_DOCUMENT_NOT_FOUND", "文档不存在", "请刷新后重试。")
    return [{"id": row.id, "revision_no": row.revision_no, "content": row.content, "change_note": row.change_note, "created_by": row.created_by, "created_at": row.created_at} for row in db.scalars(select(BusinessDocumentRevision).where(BusinessDocumentRevision.document_id == document_id).order_by(BusinessDocumentRevision.revision_no.desc())).all()]


def _append_structured_content(document: Document, content: dict[str, Any]) -> None:
    blocks = content.get("blocks", []) if isinstance(content, dict) else []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type", "paragraph")
        text = str(block.get("text", ""))
        if kind == "heading":
            document.add_heading(text, level=max(1, min(3, int(block.get("level", 1)))))
        elif kind == "list":
            document.add_paragraph(text, style="List Bullet")
        else:
            document.add_paragraph(text)


@router.get("/business-documents/{document_id}/export.docx")
def export_business_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    item = db.get(BusinessDocument, document_id)
    if not item or item.archived_at:
        raise ProblemException(404, "BUSINESS_DOCUMENT_NOT_FOUND", "文档不存在", "请刷新后重试。")
    doc = Document()
    doc.add_heading(item.title, 0)
    _append_structured_content(doc, item.content)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    filename = "".join(char if char not in '\\/:*?\"<>|' else "_" for char in item.title)[:80] or "PartyOps文档"
    write_audit(db, user, "business_document.export", "business_document", item.id, {"version": item.version})
    db.commit()
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}.docx"})
