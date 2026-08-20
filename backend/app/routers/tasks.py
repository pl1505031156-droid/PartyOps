"""任务、评论、步骤和材料 API。"""

from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update as sql_update
from sqlalchemy.orm import Session

from ..audit import emit_event, write_audit
from ..database import db_runtime, get_session
from ..enums import MaterialStage, ParticipantRole, TaskStatus
from ..material_categories import DEFAULT_MATERIAL_CATEGORIES
from ..notifications import add_notification
from ..models import (
    AttachmentVersion,
    ConflictDraft,
    FileBlob,
    MaterialItem,
    Task,
    TaskComment,
    TaskParticipant,
    TaskStep,
    User,
)
from ..problems import ProblemException
from ..schemas import (
    AttachmentRollbackRequest,
    CommentCreate,
    CommentOut,
    DashboardOut,
    MaterialCategoryOut,
    MaterialCreate,
    MaterialOut,
    MaterialPatch,
    ParticipantAdd,
    StepCreate,
    StepOut,
    StepPatch,
    TaskAction,
    TaskCreate,
    TaskListOut,
    TaskOut,
    TaskUpdate,
    serialize_api_datetime,
)
from ..security import get_current_user
from ..storage import resolve_blob_path, rollback_attachment_version, save_attachment
from ..work_journal import record_system_entry
from ..task_service import (
    apply_task_action,
    can_edit_task,
    can_manage_task,
    can_view_task,
    create_task,
    dashboard,
    get_task_or_404,
    list_tasks,
    task_to_out,
    update_task,
)


router = APIRouter(tags=["tasks"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "更新事项必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(
    user: User = Depends(get_current_user), db: Session = Depends(get_session)
) -> DashboardOut:
    return dashboard(db, user)


@router.get("/tasks", response_model=TaskListOut)
def get_tasks(
    status: TaskStatus | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
    scope: str | None = Query(
        default=None,
        pattern=r"^(owned|collaborating|reviewing|step_assigned)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskListOut:
    items = list_tasks(db, user, status=status, owner_id=owner_id, keyword=keyword, scope=scope)
    start = (page - 1) * page_size
    return TaskListOut(
        items=[task_to_out(db, task, include_detail=False) for task in items[start : start + page_size]],
        total=len(items),
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/my-work-summary", response_model=dict)
def my_work_summary(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, int]:
    return {
        scope: len(list_tasks(db, user, scope=scope))
        for scope in ("owned", "collaborating", "reviewing", "step_assigned")
    }


@router.get("/material-categories", response_model=typing.List[MaterialCategoryOut])
def list_material_categories(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[MaterialCategoryOut]:
    """返回常用预设，并把已实际使用的自定义类别保留为后续选项。"""

    defaults = [
        MaterialCategoryOut(value=value, label=label)
        for value, label in DEFAULT_MATERIAL_CATEGORIES
    ]
    known = {item.value for item in defaults}
    custom_values = db.scalars(
        select(MaterialItem.category)
        .where(MaterialItem.category.not_in(known))
        .distinct()
        .order_by(MaterialItem.category)
    ).all()
    defaults.extend(
        MaterialCategoryOut(value=value, label=value, custom=True)
        for value in custom_values
        if value and value.strip()
    )
    return defaults


@router.post("/tasks", response_model=TaskOut, status_code=201)
def post_task(
    payload: TaskCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = create_task(db, payload, user, client_ip(request))
    return task_to_out(db, task)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    return task_to_out(db, get_task_or_404(db, task_id, user))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    task = update_task(
        db, task, payload, parse_if_match(if_match), user, client_ip(request)
    )
    return task_to_out(db, task)


@router.get("/conflicts", response_model=typing.List[dict])
def list_conflicts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    drafts = db.scalars(
        select(ConflictDraft)
        .where(ConflictDraft.user_id == user.id)
        .order_by(ConflictDraft.created_at.desc())
        .limit(100)
    ).all()
    result: list[dict] = []
    for draft in drafts:
        task = db.get(Task, draft.task_id)
        if task and task.deleted_at is None:
            result.append(
                {
                    "id": draft.id,
                    "task_id": draft.task_id,
                    "task_title": task.title,
                    "submitted_version": draft.submitted_version,
                    "current_version": task.version,
                    "payload": draft.payload,
                    "created_at": serialize_api_datetime(draft.created_at),
                }
            )
    return result


@router.post("/conflicts/{draft_id}/apply", response_model=TaskOut)
def apply_conflict_draft(
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    draft = db.get(ConflictDraft, draft_id)
    if not draft or draft.user_id != user.id:
        raise ProblemException(404, "CONFLICT_DRAFT_NOT_FOUND", "冲突草稿不存在", "未找到该草稿。")
    task = get_task_or_404(db, draft.task_id, user)
    task = update_task(
        db,
        task,
        TaskUpdate.model_validate(draft.payload),
        parse_if_match(if_match),
        user,
        client_ip(request),
    )
    db.delete(draft)
    write_audit(db, user, "conflict.apply", "task", task.id, {"draft_id": draft_id}, client_ip(request))
    db.commit()
    return task_to_out(db, task)


@router.post("/tasks/{task_id}/actions", response_model=TaskOut)
def task_action(
    task_id: str,
    payload: TaskAction,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    task = apply_task_action(
        db,
        task,
        payload.action,
        payload.note,
        user,
        client_ip(request),
        parse_if_match(if_match) if if_match is not None else None,
    )
    return task_to_out(db, task)


@router.delete("/tasks/{task_id}", response_model=dict)
def delete_task(
    task_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    expected = parse_if_match(if_match)
    task = get_task_or_404(db, task_id, user)
    with db_runtime.write_lock:
        from ..models import utcnow

        # 在写锁内强制回读权限与版本，并由 SQL 的版本条件完成最终 CAS。
        # 即使另一进程不共享本进程锁，也不能用旧 If-Match 删除刚更新的事项。
        db.refresh(task)
        if not can_view_task(db, task, user):
            raise ProblemException(404, "TASK_NOT_FOUND", "事项不存在", "未找到该事项。")
        if not can_manage_task(db, task, user):
            raise ProblemException(
                403,
                "TASK_DELETE_DENIED",
                "无权删除",
                "只有主办人或管理员可以删除事项。",
            )
        if task.version != expected:
            raise ProblemException(409, "VERSION_CONFLICT", "事项已更新", "请刷新后重试。")
        deleted_at = utcnow()
        result = db.execute(
            sql_update(Task)
            .where(
                Task.id == task_id,
                Task.version == expected,
                Task.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at, version=Task.version + 1)
        )
        if result.rowcount != 1:
            db.rollback()
            raise ProblemException(409, "VERSION_CONFLICT", "事项已更新", "请刷新后重试。")
        task.deleted_at = deleted_at
        task.version = expected + 1
        write_audit(db, user, "task.delete", "task", task.id, {}, client_ip(request))
        emit_event(db, "task.deleted", task.id, {})
        db.commit()
    return {"deleted": True, "task_id": task.id}


@router.post("/tasks/{task_id}/participants", response_model=TaskOut)
def add_participant(
    task_id: str,
    payload: ParticipantAdd,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    if not can_manage_task(db, task, user):
        raise ProblemException(403, "PARTICIPANT_EDIT_DENIED", "无权调整参与人", "你不是该事项主办人。")
    if if_match is not None and task.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "事项已更新", "请刷新参与人后重试。")
    participant_user = db.get(User, payload.user_id)
    if not participant_user:
        raise ProblemException(422, "USER_NOT_FOUND", "用户不存在", "请选择有效用户。")
    existing = db.scalar(
        select(TaskParticipant).where(
            TaskParticipant.task_id == task.id,
            TaskParticipant.user_id == payload.user_id,
            TaskParticipant.role == payload.role,
        )
    )
    if not existing:
        db.add(TaskParticipant(task_id=task.id, user_id=payload.user_id, role=payload.role))
        task.version += 1
        record_system_entry(
            db,
            user,
            f"添加参与人：{participant_user.display_name}",
            f"{participant_user.display_name}已作为"
            f"{'协办人' if payload.role == ParticipantRole.COLLABORATOR else '审核人'}加入事项。",
            task_id=task.id,
            event_code="task.participant_added",
            event_data={
                "participant_name": participant_user.display_name,
                "participant_role": payload.role.value,
            },
        )
        write_audit(
            db,
            user,
            "task.participant_add",
            "task",
            task.id,
            payload.model_dump(mode="json"),
            client_ip(request),
        )
        emit_event(db, "task.updated", task.id, {"version": task.version})
        db.commit()
    return task_to_out(db, task)


@router.delete("/tasks/{task_id}/participants/{participant_id}", response_model=TaskOut)
def remove_participant(
    task_id: str,
    participant_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    if not can_manage_task(db, task, user):
        raise ProblemException(403, "PARTICIPANT_EDIT_DENIED", "无权调整参与人", "只有主办人或管理员可以调整参与人。")
    if if_match is not None and task.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "事项已更新", "请刷新参与人后重试。")
    participant = db.get(TaskParticipant, participant_id)
    if not participant or participant.task_id != task.id:
        raise ProblemException(404, "PARTICIPANT_NOT_FOUND", "参与人不存在", "未找到该参与关系。")
    if participant.role == ParticipantRole.OWNER:
        raise ProblemException(409, "OWNER_REMOVE_DENIED", "不能移除主办人", "请先移交事项。")
    participant_user = db.get(User, participant.user_id)
    db.delete(participant)
    task.version += 1
    record_system_entry(
        db,
        user,
        f"移除参与人：{participant_user.display_name if participant_user else '原参与人'}",
        "参与关系已移除。",
        task_id=task.id,
        event_code="task.participant_removed",
        event_data={
            "participant_name": participant_user.display_name if participant_user else "",
            "participant_role": participant.role.value,
        },
    )
    write_audit(
        db,
        user,
        "task.participant_remove",
        "task",
        task.id,
        {"participant_id": participant_id},
        client_ip(request),
    )
    emit_event(db, "task.updated", task.id, {"version": task.version})
    db.commit()
    return task_to_out(db, task)


@router.post("/tasks/{task_id}/steps", response_model=StepOut, status_code=201)
def add_step(
    task_id: str,
    payload: StepCreate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskStep:
    task = get_task_or_404(db, task_id, user)
    if not can_edit_task(db, task, user):
        raise ProblemException(403, "STEP_EDIT_DENIED", "无权添加步骤", "你不是该事项参与人。")
    if if_match is not None and task.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "事项已更新", "请刷新办理清单后重试。")
    order = db.scalar(
        select(func.max(TaskStep.sort_order)).where(TaskStep.task_id == task.id)
    )
    step = TaskStep(
        task_id=task.id,
        title=payload.title,
        assignee_id=payload.assignee_id,
        due_at=payload.due_at,
        sort_order=(order or 0) + 1,
    )
    db.add(step)
    db.flush()
    task.version += 1
    record_system_entry(
        db,
        user,
        f"添加办理步骤：{step.title}",
        "已加入事项办理清单。",
        task_id=task.id,
        event_code="task.step_added",
        event_data={"step_id": step.id, "step_title": step.title},
    )
    write_audit(
        db,
        user,
        "task.step_add",
        "step",
        step.id,
        {"task_id": task.id, "title": step.title},
        client_ip(request),
    )
    emit_event(db, "task.updated", task.id, {"version": task.version})
    db.commit()
    db.refresh(step)
    return step


@router.patch("/tasks/{task_id}/steps/{step_id}", response_model=StepOut)
def patch_step(
    task_id: str,
    step_id: str,
    payload: StepPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskStep:
    task = get_task_or_404(db, task_id, user)
    step = db.get(TaskStep, step_id)
    if not step or step.task_id != task.id:
        raise ProblemException(404, "STEP_NOT_FOUND", "步骤不存在", "未找到该步骤。")
    if not can_edit_task(db, task, user):
        raise ProblemException(403, "STEP_EDIT_DENIED", "无权修改步骤", "你不是该事项参与人。")
    expected = parse_if_match(if_match) if if_match is not None else payload.version
    if expected is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "更新步骤必须携带版本号。")
    if step.version != expected:
        raise ProblemException(409, "VERSION_CONFLICT", "步骤已更新", "请刷新后重试。")
    was_done = step.done
    for field in payload.model_fields_set - {"version"}:
        setattr(step, field, getattr(payload, field))
    step.version += 1
    task.version += 1
    completed_now = not was_done and step.done
    record_system_entry(
        db,
        user,
        (
            f"完成办理步骤：{step.title}"
            if completed_now
            else f"修改办理步骤：{step.title}"
        ),
        "该步骤已完成。" if completed_now else "办理步骤信息已更新。",
        task_id=task.id,
        event_code="task.step_completed" if completed_now else "task.step_updated",
        event_data={
            "step_id": step.id,
            "step_title": step.title,
            "done": step.done,
        },
    )
    write_audit(
        db,
        user,
        "task.step_update",
        "step",
        step.id,
        {"task_id": task.id, "done": step.done},
        client_ip(request),
    )
    emit_event(db, "task.updated", task.id, {"version": task.version})
    db.commit()
    db.refresh(step)
    return step


@router.delete("/tasks/{task_id}/steps/{step_id}", response_model=TaskOut)
def delete_step(
    task_id: str,
    step_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    step = db.get(TaskStep, step_id)
    if not step or step.task_id != task.id:
        raise ProblemException(404, "STEP_NOT_FOUND", "步骤不存在", "未找到该步骤。")
    if not can_edit_task(db, task, user):
        raise ProblemException(403, "STEP_EDIT_DENIED", "无权删除步骤", "你不是该事项参与人。")
    if if_match is not None and step.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "步骤已更新", "请刷新后重试。")
    step_title = step.title
    db.delete(step)
    task.version += 1
    record_system_entry(
        db,
        user,
        f"移除办理步骤：{step_title}",
        "该步骤已从办理清单移除。",
        task_id=task.id,
        event_code="task.step_removed",
        event_data={"step_id": step.id, "step_title": step_title},
    )
    write_audit(db, user, "task.step_delete", "step", step.id, {"task_id": task.id}, client_ip(request))
    emit_event(db, "task.updated", task.id, {"version": task.version})
    db.commit()
    return task_to_out(db, task)


@router.post("/tasks/{task_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(
    task_id: str,
    payload: CommentCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskComment:
    task = get_task_or_404(db, task_id, user)
    if not can_edit_task(db, task, user):
        raise ProblemException(403, "COMMENT_DENIED", "无权评论", "你不是该事项参与人。")
    parent: TaskComment | None = None
    if payload.parent_id:
        parent = db.get(TaskComment, payload.parent_id)
        if not parent or parent.task_id != task.id:
            raise ProblemException(422, "COMMENT_PARENT_INVALID", "回复对象无效", "请选择本事项中的评论。")
    mentioned_ids = list(dict.fromkeys(payload.mentioned_user_ids))
    mentioned_users: dict[str, User] = {}
    for user_id in mentioned_ids:
        mentioned = db.get(User, user_id)
        if not mentioned or not mentioned.active or not can_view_task(db, task, mentioned):
            raise ProblemException(422, "COMMENT_MENTION_INVALID", "提及人员无效", "只能提及有权查看本事项的人员。")
        mentioned_users[user_id] = mentioned
    comment = TaskComment(
        task_id=task.id,
        author_id=user.id,
        parent_id=payload.parent_id,
        body=payload.body.strip(),
        mentioned_user_ids=mentioned_ids,
    )
    db.add(comment)
    db.flush()
    recipients = set(
        db.scalars(
            select(TaskParticipant.user_id).where(TaskParticipant.task_id == task.id)
        ).all()
    )
    recipients.update({task.owner_id, task.reviewer_id, parent.author_id if parent else None})
    recipients.update(mentioned_ids)
    recipients.discard(None)
    recipients.discard(user.id)
    excerpt = comment.body[:160]
    for recipient_id in recipients:
        is_mention = recipient_id in mentioned_users
        add_notification(
            db,
            user_id=str(recipient_id),
            notification_type="mention" if is_mention else "comment",
            title=f"{user.display_name}{'提及了你' if is_mention else '更新了协同反馈'}：{task.title}",
            body=excerpt,
            entity_type="task",
            entity_id=task.id,
            dedupe_key=f"comment:{comment.id}:{recipient_id}",
        )
    write_audit(db, user, "comment.create", "comment", comment.id, {"task_id": task.id}, client_ip(request))
    emit_event(db, "comment.created", task.id, {"comment_id": comment.id, "mentioned_user_ids": mentioned_ids})
    db.commit()
    db.refresh(comment)
    return comment


@router.post("/tasks/{task_id}/materials", response_model=MaterialOut, status_code=201)
def add_material(
    task_id: str,
    payload: MaterialCreate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> MaterialOut:
    task = get_task_or_404(db, task_id, user)
    if not can_edit_task(db, task, user):
        raise ProblemException(403, "MATERIAL_EDIT_DENIED", "无权添加材料", "你不是该事项参与人。")
    with db_runtime.write_lock:
        # 获取写锁后重新读取版本，避免后台扫描或另一台协同电脑的写入与本次
        # 新增交错，最终表现为只有追踪编号的数据库异常。
        db.refresh(task)
        if if_match is not None and task.version != parse_if_match(if_match):
            raise ProblemException(409, "VERSION_CONFLICT", "事项已更新", "请刷新材料清单后重试。")
        item = MaterialItem(task_id=task.id, **payload.model_dump())
        db.add(item)
        db.flush()
        task.version += 1
        record_system_entry(
            db,
            user,
            f"新增材料项：{item.name}",
            f"材料类别：{item.category}；{'必备材料' if item.required else '可选材料'}。",
            task_id=task.id,
            event_code="task.material_added",
            event_data={
                "material_id": item.id,
                "material_name": item.name,
                "material_category": item.category,
                "required": item.required,
            },
        )
        write_audit(
            db,
            user,
            "task.material_add",
            "material",
            item.id,
            {"task_id": task.id, "name": item.name, "category": item.category},
            client_ip(request),
        )
        emit_event(db, "task.updated", task.id, {"version": task.version})
        db.commit()
        db.refresh(item)
        # 显式构造响应，避免旧库 ORM 实例在第一次序列化时尝试读取不存在的
        # 动态关系，从而把已成功的新增操作误报成内部错误。
        return MaterialOut(
            id=item.id,
            category=item.category,
            name=item.name,
            required=item.required,
            not_applicable=item.not_applicable,
            not_applicable_reason=item.not_applicable_reason,
            version=item.version,
            versions=[],
            complete=False,
        )


@router.patch("/tasks/{task_id}/materials/{material_id}", response_model=MaterialOut)
def patch_material(
    task_id: str,
    material_id: str,
    payload: MaterialPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> MaterialOut:
    task = get_task_or_404(db, task_id, user)
    item = db.get(MaterialItem, material_id)
    if not item or item.task_id != task.id:
        raise ProblemException(404, "MATERIAL_NOT_FOUND", "材料项不存在", "未找到材料项。")
    if not can_edit_task(db, task, user):
        raise ProblemException(403, "MATERIAL_EDIT_DENIED", "无权修改材料", "你不是该事项参与人。")
    expected = parse_if_match(if_match) if if_match is not None else payload.version
    if expected is not None and item.version != expected:
        raise ProblemException(409, "VERSION_CONFLICT", "材料项已更新", "请刷新材料清单后重试。")
    if payload.not_applicable and not payload.reason.strip():
        raise ProblemException(422, "NA_REASON_REQUIRED", "需要填写原因", "标记不适用时必须说明原因。")
    item.not_applicable = payload.not_applicable
    item.not_applicable_reason = payload.reason.strip()
    item.version += 1
    task.version += 1
    write_audit(
        db,
        user,
        "task.material_update",
        "material",
        item.id,
        {
            "task_id": task.id,
            "not_applicable": item.not_applicable,
            "reason": item.not_applicable_reason,
        },
        client_ip(request),
    )
    emit_event(db, "task.updated", task.id, {"version": task.version})
    db.commit()
    return MaterialOut.model_validate(item).model_copy(
        update={"complete": item.not_applicable}
    )


@router.post(
    "/tasks/{task_id}/materials/{material_id}/versions",
    response_model=TaskOut,
    status_code=201,
)
async def upload_material_version(
    task_id: str,
    material_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    file: UploadFile = File(...),
    stage: MaterialStage = Form(MaterialStage.DRAFT),
    is_final: bool = Form(False),
    note: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    material = db.get(MaterialItem, material_id)
    if not material or material.task_id != task.id:
        raise ProblemException(404, "MATERIAL_NOT_FOUND", "材料项不存在", "未找到材料项。")
    await save_attachment(
        db,
        task,
        material,
        file,
        stage,
        is_final,
        user,
        note,
        client_ip(request),
        parse_if_match(if_match) if if_match is not None else None,
    )
    return task_to_out(db, task)


@router.post(
    "/tasks/{task_id}/materials/{material_id}/versions/{version_id}/rollback",
    response_model=TaskOut,
)
def rollback_material_version(
    task_id: str,
    material_id: str,
    version_id: str,
    payload: AttachmentRollbackRequest,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> TaskOut:
    task = get_task_or_404(db, task_id, user)
    material = db.get(MaterialItem, material_id)
    target = db.get(AttachmentVersion, version_id)
    if not material or material.task_id != task.id or not target or target.material_item_id != material.id:
        raise ProblemException(404, "ATTACHMENT_NOT_FOUND", "材料版本不存在", "请刷新材料目录后重试。")
    rollback_attachment_version(
        db,
        task,
        material,
        target,
        user,
        payload.reason,
        ip=client_ip(request),
        expected_task_version=parse_if_match(if_match),
    )
    return task_to_out(db, task)


@router.get("/attachments/{version_id}/download")
def download_attachment(
    version_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    row = db.execute(
        select(AttachmentVersion, FileBlob, MaterialItem)
        .join(FileBlob, FileBlob.sha256 == AttachmentVersion.blob_sha256)
        .join(MaterialItem, MaterialItem.id == AttachmentVersion.material_item_id)
        .where(AttachmentVersion.id == version_id)
    ).one_or_none()
    if not row:
        raise ProblemException(404, "ATTACHMENT_NOT_FOUND", "附件不存在", "未找到附件。")
    version, blob, material = row
    get_task_or_404(db, material.task_id, user)
    path = resolve_blob_path(blob.relative_path)
    if not path.exists():
        raise ProblemException(410, "ATTACHMENT_MISSING", "附件文件缺失", "请从备份恢复。")
    write_audit(
        db,
        user,
        "attachment.download",
        "attachment",
        version.id,
        {"task_id": material.task_id, "name": blob.original_name},
        client_ip(request),
    )
    db.commit()
    return FileResponse(path, media_type=blob.mime_type, filename=blob.original_name)
