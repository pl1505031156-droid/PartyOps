"""收件解析、模板、周期、知识库、搜索与导出。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..database import db_runtime, get_session
from ..enums import TaskStatus, UserRole
from ..exporting import export_inspection_package, export_tasks_docx, export_tasks_xlsx
from ..intake import parse_text, parse_upload
from ..models import (
    Contact,
    AttachmentVersion,
    ArchiveSnapshot,
    MaterialItem,
    ReminderPreference,
    KnowledgeEntry,
    RecurrenceRule,
    Task,
    TaskTemplate,
    TemplateMaterial,
    TemplateStep,
    FileBlob,
    User,
)
from ..problems import ProblemException
from ..recurrence import instantiate_template, run_due_rules
from ..schemas import (
    ArchiveSnapshotOut,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    IntakeCandidate,
    KnowledgeCreate,
    KnowledgeOut,
    KnowledgeUpdate,
    MaterialInput,
    RecurrenceCreate,
    RecurrenceOut,
    RecurrenceUpdate,
    ReminderPreferenceOut,
    ReminderPreferencePatch,
    TaskListOut,
    TaskOut,
    TemplateCreate,
    TemplateInstantiate,
    TemplateOut,
    TemplateUpdate,
)
from ..security import get_current_user
from ..task_service import can_view_task, get_task_or_404, task_to_out


router = APIRouter(tags=["support"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改操作必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def template_to_out(db: Session, template: TaskTemplate) -> TemplateOut:
    steps = db.scalars(
        select(TemplateStep)
        .where(TemplateStep.template_id == template.id)
        .order_by(TemplateStep.sort_order)
    ).all()
    materials = db.scalars(
        select(TemplateMaterial).where(TemplateMaterial.template_id == template.id)
    ).all()
    return TemplateOut.model_validate(template).model_copy(
        update={
            "steps": [item.title for item in steps],
            "materials": [
                MaterialInput(
                    category=item.category,
                    name=item.name,
                    required=item.required,
                )
                for item in materials
            ],
        }
    )


@router.post("/intake/parse", response_model=IntakeCandidate)
async def intake_parse(
    pasted_text: Annotated[str, Form()] = "",
    file: UploadFile | None = File(default=None),
    _user: User = Depends(get_current_user),
) -> IntakeCandidate:
    if file:
        return await parse_upload(file, pasted_text)
    return parse_text(pasted_text, "wechat")


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[TemplateOut]:
    statement = select(TaskTemplate).order_by(TaskTemplate.name)
    if not include_inactive or user.role != UserRole.ADMIN:
        statement = statement.where(TaskTemplate.active.is_(True))
    templates = db.scalars(statement).all()
    return [template_to_out(db, item) for item in templates]


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(
    payload: TemplateCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TemplateOut:
    if user.role != UserRole.ADMIN:
        raise ProblemException(403, "ADMIN_REQUIRED", "无权操作", "模板管理仅限管理员。")
    existing = db.scalar(select(TaskTemplate).where(TaskTemplate.name == payload.name))
    if existing:
        raise ProblemException(409, "TEMPLATE_EXISTS", "模板名称重复", "请使用其他名称。")
    template = TaskTemplate(
        name=payload.name,
        category=payload.category,
        task_type=payload.task_type,
        description=payload.description,
        created_by=user.id,
    )
    db.add(template)
    db.flush()
    for index, title in enumerate(payload.steps):
        db.add(TemplateStep(template_id=template.id, title=title, sort_order=index))
    for material in payload.materials:
        db.add(
            TemplateMaterial(
                template_id=template.id,
                category=material.category,
                name=material.name,
                required=material.required,
            )
        )
    db.flush()
    write_audit(
        db,
        user,
        "template.create",
        "template",
        template.id,
        {"name": template.name},
        client_ip(request),
    )
    db.commit()
    db.refresh(template)
    return template_to_out(db, template)


@router.patch("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str,
    payload: TemplateUpdate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TemplateOut:
    if user.role != UserRole.ADMIN:
        raise ProblemException(403, "ADMIN_REQUIRED", "无权操作", "模板管理仅限管理员。")
    template = db.get(TaskTemplate, template_id)
    if not template:
        raise ProblemException(404, "TEMPLATE_NOT_FOUND", "模板不存在", "未找到该模板。")
    expected = parse_if_match(if_match)
    if template.version != expected:
        raise ProblemException(409, "VERSION_CONFLICT", "模板已更新", "请刷新后重试。")
    duplicate = db.scalar(
        select(TaskTemplate).where(
            TaskTemplate.name == payload.name,
            TaskTemplate.id != template.id,
        )
    )
    if duplicate:
        raise ProblemException(409, "TEMPLATE_EXISTS", "模板名称重复", "请使用其他名称。")
    with db_runtime.write_lock:
        template.name = payload.name.strip()
        template.category = payload.category.strip()
        template.task_type = payload.task_type
        template.description = payload.description.strip()
        template.active = payload.active
        template.version += 1
        db.execute(delete(TemplateStep).where(TemplateStep.template_id == template.id))
        db.execute(delete(TemplateMaterial).where(TemplateMaterial.template_id == template.id))
        for index, title in enumerate(payload.steps):
            db.add(TemplateStep(template_id=template.id, title=title, sort_order=index))
        for material in payload.materials:
            db.add(TemplateMaterial(template_id=template.id, **material.model_dump()))
        write_audit(
            db,
            user,
            "template.update",
            "template",
            template.id,
            {"name": template.name, "version": template.version},
            client_ip(request),
        )
        db.commit()
    return template_to_out(db, template)


@router.post("/templates/{template_id}/instantiate", response_model=TaskOut, status_code=201)
def instantiate(
    template_id: str,
    payload: TemplateInstantiate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    template = db.get(TaskTemplate, template_id)
    if not template or not template.active:
        raise ProblemException(404, "TEMPLATE_NOT_FOUND", "模板不存在", "未找到该模板。")
    task = instantiate_template(
        db,
        template,
        payload.owner_id,
        user,
        title=payload.title,
        formal_due_at=payload.formal_due_at,
    )
    return task_to_out(db, task)


@router.get("/recurrences", response_model=list[RecurrenceOut])
def list_recurrences(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[RecurrenceRule]:
    return list(db.scalars(select(RecurrenceRule).order_by(RecurrenceRule.next_run_at)).all())


@router.post("/recurrences", response_model=RecurrenceOut, status_code=201)
def create_recurrence(
    payload: RecurrenceCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> RecurrenceRule:
    if user.role != UserRole.ADMIN:
        raise ProblemException(403, "ADMIN_REQUIRED", "无权操作", "周期事项管理仅限管理员。")
    if not db.get(TaskTemplate, payload.template_id):
        raise ProblemException(422, "TEMPLATE_NOT_FOUND", "模板不存在", "请选择有效模板。")
    if payload.contact_ids:
        valid_contacts = set(
            db.scalars(select(Contact.id).where(Contact.id.in_(payload.contact_ids))).all()
        )
        if valid_contacts != set(payload.contact_ids):
            raise ProblemException(422, "CONTACT_INVALID", "联系人无效", "请选择有效联系人。")
    rule = RecurrenceRule(**payload.model_dump())
    db.add(rule)
    db.flush()
    write_audit(
        db,
        user,
        "recurrence.create",
        "recurrence",
        rule.id,
        {"name": rule.name, "kind": rule.kind.value},
        client_ip(request),
    )
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/recurrences/{rule_id}", response_model=RecurrenceOut)
def update_recurrence(
    rule_id: str,
    payload: RecurrenceUpdate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> RecurrenceRule:
    if user.role != UserRole.ADMIN:
        raise ProblemException(403, "ADMIN_REQUIRED", "无权操作", "周期事项管理仅限管理员。")
    rule = db.get(RecurrenceRule, rule_id)
    if not rule:
        raise ProblemException(404, "RECURRENCE_NOT_FOUND", "周期规则不存在", "未找到该规则。")
    if rule.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "周期规则已更新", "请刷新后重试。")
    if payload.owner_id and not db.get(User, payload.owner_id):
        raise ProblemException(422, "OWNER_INVALID", "责任人无效", "请选择有效责任人。")
    if payload.contact_ids is not None:
        valid_contacts = set(
            db.scalars(select(Contact.id).where(Contact.id.in_(payload.contact_ids))).all()
        )
        if valid_contacts != set(payload.contact_ids):
            raise ProblemException(422, "CONTACT_INVALID", "联系人无效", "请选择有效联系人。")
    for field in payload.model_fields_set:
        setattr(rule, field, getattr(payload, field))
    rule.version += 1
    write_audit(
        db,
        user,
        "recurrence.update",
        "recurrence",
        rule.id,
        {"fields": sorted(payload.model_fields_set), "version": rule.version},
        client_ip(request),
    )
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/recurrences/run-due", response_model=list[str])
def run_recurrences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[str]:
    if user.role != UserRole.ADMIN:
        raise ProblemException(403, "ADMIN_REQUIRED", "无权操作", "周期事项管理仅限管理员。")
    return run_due_rules(db, user)


@router.get("/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(
    keyword: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[KnowledgeEntry]:
    statement = select(KnowledgeEntry).order_by(KnowledgeEntry.updated_at.desc())
    if keyword:
        statement = statement.where(
            or_(
                KnowledgeEntry.title.contains(keyword),
                KnowledgeEntry.body.contains(keyword),
                KnowledgeEntry.category.contains(keyword),
            )
        )
    return list(db.scalars(statement).all())


@router.post("/knowledge", response_model=KnowledgeOut, status_code=201)
def create_knowledge(
    payload: KnowledgeCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> KnowledgeEntry:
    entry = KnowledgeEntry(**payload.model_dump(), updated_by=user.id)
    db.add(entry)
    db.flush()
    write_audit(
        db,
        user,
        "knowledge.create",
        "knowledge",
        entry.id,
        {"title": entry.title},
        client_ip(request),
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.patch("/knowledge/{entry_id}", response_model=KnowledgeOut)
def update_knowledge(
    entry_id: str,
    payload: KnowledgeUpdate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> KnowledgeEntry:
    entry = db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise ProblemException(404, "KNOWLEDGE_NOT_FOUND", "知识条目不存在", "未找到该条目。")
    if entry.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "知识条目已更新", "请刷新后重试。")
    for field, value in payload.model_dump().items():
        setattr(entry, field, value.strip() if isinstance(value, str) else value)
    entry.updated_by = user.id
    entry.version += 1
    write_audit(db, user, "knowledge.update", "knowledge", entry.id, {"title": entry.title}, client_ip(request))
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/knowledge/{entry_id}", response_model=dict)
def delete_knowledge(
    entry_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    entry = db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise ProblemException(404, "KNOWLEDGE_NOT_FOUND", "知识条目不存在", "未找到该条目。")
    if entry.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "知识条目已更新", "请刷新后重试。")
    db.delete(entry)
    write_audit(db, user, "knowledge.delete", "knowledge", entry.id, {"title": entry.title}, client_ip(request))
    db.commit()
    return {"deleted": True, "id": entry_id}


@router.get("/contacts", response_model=list[ContactOut])
def list_contacts(
    keyword: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[Contact]:
    statement = select(Contact).order_by(Contact.name)
    if keyword:
        statement = statement.where(
            or_(
                Contact.name.contains(keyword),
                Contact.organization.contains(keyword),
                Contact.note.contains(keyword),
            )
        )
    return list(db.scalars(statement).all())


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(
    payload: ContactCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Contact:
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.flush()
    write_audit(
        db,
        user,
        "contact.create",
        "contact",
        contact.id,
        {"name": contact.name},
        client_ip(request),
    )
    db.commit()
    db.refresh(contact)
    return contact


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Contact:
    contact = db.get(Contact, contact_id)
    if not contact:
        raise ProblemException(404, "CONTACT_NOT_FOUND", "联系人不存在", "未找到该联系人。")
    if contact.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "联系人已更新", "请刷新后重试。")
    for field, value in payload.model_dump().items():
        setattr(contact, field, value.strip())
    contact.version += 1
    write_audit(db, user, "contact.update", "contact", contact.id, {"name": contact.name}, client_ip(request))
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/contacts/{contact_id}", response_model=dict)
def delete_contact(
    contact_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    contact = db.get(Contact, contact_id)
    if not contact:
        raise ProblemException(404, "CONTACT_NOT_FOUND", "联系人不存在", "未找到该联系人。")
    if contact.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "联系人已更新", "请刷新后重试。")
    db.delete(contact)
    write_audit(db, user, "contact.delete", "contact", contact.id, {"name": contact.name}, client_ip(request))
    db.commit()
    return {"deleted": True, "id": contact_id}


@router.get("/reminders/preferences", response_model=ReminderPreferenceOut)
def reminder_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ReminderPreference:
    preference = db.get(ReminderPreference, user.id)
    if not preference:
        preference = ReminderPreference(user_id=user.id)
        db.add(preference)
        db.commit()
        db.refresh(preference)
    return preference


@router.patch("/reminders/preferences", response_model=ReminderPreferenceOut)
def update_reminder_preferences(
    payload: ReminderPreferencePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ReminderPreference:
    preference = db.get(ReminderPreference, user.id)
    if not preference:
        preference = ReminderPreference(user_id=user.id)
        db.add(preference)
        db.flush()
    if preference.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "提醒设置已更新", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(preference, field, getattr(payload, field))
    preference.version += 1
    write_audit(
        db,
        user,
        "reminder.update",
        "reminder_preference",
        user.id,
        {"fields": sorted(payload.model_fields_set)},
        client_ip(request),
    )
    db.commit()
    db.refresh(preference)
    return preference


@router.get("/search", response_model=TaskListOut)
def search(
    q: str = Query(default="", max_length=200),
    year: int | None = Query(default=None, ge=2000, le=2200),
    category: str | None = Query(default=None, max_length=80),
    owner_id: str | None = None,
    status: TaskStatus | None = None,
    file_name: str | None = Query(default=None, max_length=200),
    smart: str | None = Query(
        default=None,
        pattern=r"^(this_week_completed|unarchived|finals|annual_focus)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskListOut:
    ids: list[str] = []
    if q.strip():
        try:
            ids = [
            row[0]
            for row in db.execute(
                text(
                        "SELECT task_id FROM task_search_fts_v2 "
                        "WHERE task_search_fts_v2 MATCH :query LIMIT 500"
                ),
                {"query": f'"{q.replace(chr(34), "")}"'},
            ).all()
            ]
        except Exception:
            ids = []
        related_ids = db.scalars(
            select(MaterialItem.task_id)
            .join(
                AttachmentVersion,
                AttachmentVersion.material_item_id == MaterialItem.id,
                isouter=True,
            )
            .where(
                or_(
                    MaterialItem.name.contains(q),
                    MaterialItem.category.contains(q),
                    AttachmentVersion.display_name.contains(q),
                )
            )
        ).all()
        ids = list(dict.fromkeys([*ids, *related_ids]))
    statement = select(Task).where(Task.deleted_at.is_(None))
    if q.strip() and ids:
        statement = statement.where(Task.id.in_(ids))
    elif q.strip():
        statement = statement.where(
            or_(
                Task.title.contains(q),
                Task.source.contains(q),
                Task.description.contains(q),
                Task.category.contains(q),
            )
        )
    if year:
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        statement = statement.where(Task.created_at >= start, Task.created_at < end)
    if category:
        statement = statement.where(Task.category == category)
    if owner_id:
        statement = statement.where(Task.owner_id == owner_id)
    if status:
        statement = statement.where(Task.status == status)
    if file_name:
        file_task_ids = db.scalars(
            select(MaterialItem.task_id)
            .join(AttachmentVersion, AttachmentVersion.material_item_id == MaterialItem.id)
            .where(AttachmentVersion.display_name.contains(file_name))
        ).all()
        statement = statement.where(Task.id.in_(file_task_ids or [""]))
    if smart == "this_week_completed":
        local_now = datetime.now(timezone(timedelta(hours=8)))
        local_start = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = local_start.astimezone(timezone.utc)
        end = (local_start + timedelta(days=7)).astimezone(timezone.utc)
        statement = statement.where(Task.completed_at >= start, Task.completed_at < end)
    elif smart == "unarchived":
        statement = statement.where(Task.status == TaskStatus.COMPLETED)
    elif smart == "finals":
        final_task_ids = db.scalars(
            select(MaterialItem.task_id)
            .join(AttachmentVersion, AttachmentVersion.material_item_id == MaterialItem.id)
            .where(AttachmentVersion.is_final.is_(True))
        ).all()
        statement = statement.where(Task.id.in_(final_task_ids or [""]))
    elif smart == "annual_focus":
        statement = statement.where(Task.annual_focus != "")
    tasks = db.scalars(
        statement.order_by(
            Task.internal_due_at.is_(None),
            Task.internal_due_at,
            Task.updated_at.desc(),
        )
    ).all()
    visible = [task for task in tasks if can_view_task(db, task, user)]
    start = (page - 1) * page_size
    return TaskListOut(
        items=[task_to_out(db, task, include_detail=False) for task in visible[start : start + page_size]],
        total=len(visible),
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{task_id}/archive-snapshots", response_model=list[ArchiveSnapshotOut])
def archive_snapshots(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ArchiveSnapshot]:
    get_task_or_404(db, task_id, user)
    return list(
        db.scalars(
            select(ArchiveSnapshot)
            .where(ArchiveSnapshot.task_id == task_id)
            .order_by(ArchiveSnapshot.created_at.desc())
        ).all()
    )


@router.get("/tasks/{task_id}/archive-package")
def task_archive_package(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    get_task_or_404(db, task_id, user)
    path = export_inspection_package(db, user, [task_id])
    write_audit(db, user, "export.task_archive", "task", task_id, {}, client_ip(request))
    db.commit()
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/exports/tasks.xlsx")
def export_xlsx(
    request: Request,
    kind: str = "任务台账",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    path = export_tasks_xlsx(db, user, kind)
    write_audit(db, user, "export.xlsx", "export", None, {"kind": kind}, client_ip(request))
    db.commit()
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@router.get("/exports/tasks.docx")
def export_docx(
    request: Request,
    kind: str = "周工作清单",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    path = export_tasks_docx(db, user, kind)
    write_audit(db, user, "export.docx", "export", None, {"kind": kind}, client_ip(request))
    db.commit()
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


@router.get("/inspection/package")
def inspection_package(
    request: Request,
    task_ids: list[str] | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    path = export_inspection_package(db, user, task_ids)
    write_audit(db, user, "export.inspection", "export", None, {"task_ids": task_ids or []}, client_ip(request))
    db.commit()
    return FileResponse(path, media_type="application/zip", filename=path.name)
