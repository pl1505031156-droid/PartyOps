"""任务闭环、可见性、并发冲突与序列化。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update as sql_update
from sqlalchemy.orm import Session

from .audit import emit_event, write_audit
from .archiving import create_archive_snapshot
from .database import db_runtime
from .enums import ParticipantRole, Sensitivity, TaskStatus, TaskType, UserRole
from .models import (
    AttachmentVersion,
    ConflictDraft,
    Contact,
    FileBlob,
    MaterialItem,
    Notification,
    ReminderPreference,
    Task,
    TaskComment,
    TaskParticipant,
    TaskStatusEvent,
    TaskStep,
    User,
    utcnow,
)
from .problems import ProblemException
from .schemas import (
    CommentOut,
    DashboardBucket,
    DashboardOut,
    MaterialOut,
    MaterialVersionOut,
    ParticipantOut,
    StatusEventOut,
    StepOut,
    SubtaskSummaryOut,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    serialize_api_datetime,
)
from .state_machine import transition
from .work_journal import record_system_entry


def _participant_ids(db: Session, task_id: str) -> set[str]:
    return set(
        db.scalars(
            select(TaskParticipant.user_id).where(TaskParticipant.task_id == task_id)
        ).all()
    )


def can_view_task(db: Session, task: Task, user: User) -> bool:
    if task.deleted_at is not None:
        return False
    if task.sensitivity == Sensitivity.NORMAL:
        return True
    return user.id in {
        task.owner_id,
        task.reviewer_id,
        task.created_by,
    } or user.id in _participant_ids(db, task.id)


def task_visibility_clause(user: User):
    """生成与 ``can_view_task`` 等价的 SQL 条件，供有界列表/检索下推。"""

    participant = select(TaskParticipant.id).where(
        TaskParticipant.task_id == Task.id,
        TaskParticipant.user_id == user.id,
    ).exists()
    return or_(
        Task.sensitivity == Sensitivity.NORMAL,
        Task.owner_id == user.id,
        Task.reviewer_id == user.id,
        Task.created_by == user.id,
        participant,
    )


def can_edit_task(db: Session, task: Task, user: User) -> bool:
    if not can_view_task(db, task, user):
        return False
    if task.sensitivity == Sensitivity.NORMAL and user.role == UserRole.ADMIN:
        return True
    allowed = _participant_ids(db, task.id) | {task.owner_id, task.created_by}
    return user.id in allowed


def can_manage_task(db: Session, task: Task, user: User) -> bool:
    """判断是否可管理责任关系、流程状态和删除等治理操作。"""

    if not can_view_task(db, task, user):
        return False
    return user.role == UserRole.ADMIN or user.id in {task.owner_id, task.created_by}


def get_task_or_404(db: Session, task_id: str, user: User) -> Task:
    task = db.get(Task, task_id)
    if not task or not can_view_task(db, task, user):
        raise ProblemException(404, "TASK_NOT_FOUND", "事项不存在", "未找到该事项。")
    return task


def _material_versions(db: Session, material_id: str) -> list[MaterialVersionOut]:
    rows = db.execute(
        select(AttachmentVersion, FileBlob)
        .join(FileBlob, FileBlob.sha256 == AttachmentVersion.blob_sha256)
        .where(AttachmentVersion.material_item_id == material_id)
        .order_by(AttachmentVersion.version_no.desc())
    ).all()
    return [
        MaterialVersionOut(
            id=version.id,
            version_no=version.version_no,
            stage=version.stage,
            is_final=version.is_final,
            original_name=version.display_name or blob.original_name,
            note=version.note,
            size_bytes=blob.size_bytes,
            mime_type=blob.mime_type,
            uploaded_by=version.uploaded_by,
            created_at=version.created_at,
        )
        for version, blob in rows
    ]


def task_to_out(db: Session, task: Task, include_detail: bool = True) -> TaskOut:
    participants = [
        ParticipantOut.model_validate(item)
        for item in db.scalars(
            select(TaskParticipant)
            .where(TaskParticipant.task_id == task.id)
            .order_by(TaskParticipant.created_at)
        ).all()
    ]
    steps: list[StepOut] = []
    materials: list[MaterialOut] = []
    comments: list[CommentOut] = []
    events: list[StatusEventOut] = []
    subtasks: list[SubtaskSummaryOut] = []
    if include_detail:
        steps = [
            StepOut.model_validate(item)
            for item in db.scalars(
                select(TaskStep)
                .where(TaskStep.task_id == task.id)
                .order_by(TaskStep.sort_order, TaskStep.id)
            ).all()
        ]
        for item in db.scalars(
            select(MaterialItem)
            .where(MaterialItem.task_id == task.id)
            .order_by(MaterialItem.created_at)
        ).all():
            versions = _material_versions(db, item.id)
            complete = item.not_applicable or any(version.is_final for version in versions)
            materials.append(
                MaterialOut(
                    id=item.id,
                    category=item.category,
                    name=item.name,
                    required=item.required,
                    not_applicable=item.not_applicable,
                    not_applicable_reason=item.not_applicable_reason,
                    version=item.version,
                    versions=versions,
                    complete=complete,
                )
            )
        comments = [
            CommentOut.model_validate(item)
            for item in db.scalars(
                select(TaskComment)
                .where(TaskComment.task_id == task.id)
                .order_by(TaskComment.created_at)
            ).all()
        ]
        events = [
            StatusEventOut.model_validate(item)
            for item in db.scalars(
                select(TaskStatusEvent)
                .where(TaskStatusEvent.task_id == task.id)
                .order_by(TaskStatusEvent.created_at.desc())
            ).all()
        ]
        children = db.scalars(
            select(Task)
            .where(Task.parent_task_id == task.id, Task.deleted_at.is_(None))
            .order_by(
                Task.internal_due_at.is_(None),
                Task.internal_due_at,
                Task.created_at,
            )
        ).all()
        subtasks = [
            SubtaskSummaryOut.model_validate(child).model_copy(
                update={
                    "missing_required_materials": len(
                        required_materials_missing(db, child.id)
                    )
                }
            )
            for child in children
        ]
    else:
        required = db.scalars(
            select(MaterialItem).where(
                MaterialItem.task_id == task.id, MaterialItem.required.is_(True)
            )
        ).all()
        missing = 0
        for item in required:
            if item.not_applicable:
                continue
            final_exists = db.scalar(
                select(func.count())
                .select_from(AttachmentVersion)
                .where(
                    AttachmentVersion.material_item_id == item.id,
                    AttachmentVersion.is_final.is_(True),
                )
            )
            if not final_exists:
                missing += 1
        return TaskOut.model_validate(task).model_copy(
            update={
                "participants": participants,
                "missing_required_materials": missing,
            }
        )
    missing = sum(1 for item in materials if item.required and not item.complete)
    return TaskOut.model_validate(task).model_copy(
        update={
            "participants": participants,
            "steps": steps,
            "materials": materials,
            "comments": comments,
            "events": events,
            "subtasks": subtasks,
            "missing_required_materials": missing,
        }
    )


def create_task(db: Session, payload: TaskCreate, actor: User, ip: str = "") -> Task:
    owner = db.get(User, payload.owner_id)
    if not owner or not owner.active:
        raise ProblemException(422, "OWNER_INVALID", "责任人无效", "请选择有效责任人。")
    reviewer = db.get(User, payload.reviewer_id) if payload.reviewer_id else None
    if payload.reviewer_id and not reviewer:
        raise ProblemException(422, "REVIEWER_INVALID", "审核人无效", "请选择有效审核人。")
    parent = db.get(Task, payload.parent_task_id) if payload.parent_task_id else None
    if payload.parent_task_id and (
        not parent or parent.deleted_at is not None or parent.task_type != TaskType.PROJECT
    ):
        raise ProblemException(
            422,
            "PARENT_TASK_INVALID",
            "上级项目无效",
            "子任务只能创建在有效的项目任务下。",
        )
    if parent and not can_edit_task(db, parent, actor):
        raise ProblemException(403, "PARENT_TASK_DENIED", "无权添加子任务", "你不是该项目参与人。")
    if payload.contact_ids:
        valid_contacts = set(
            db.scalars(select(Contact.id).where(Contact.id.in_(payload.contact_ids))).all()
        )
        if valid_contacts != set(payload.contact_ids):
            raise ProblemException(422, "CONTACT_INVALID", "联系人无效", "请选择有效联系人。")
    if payload.sensitivity == Sensitivity.RESTRICTED and payload.description:
        raise ProblemException(
            422,
            "RESTRICTED_BODY_DISABLED",
            "敏感正文默认不保存",
            "请先创建最小任务，再由授权人员显式开启敏感内容保存。",
        )
    if payload.owner_id != actor.id:
        status = TaskStatus.PENDING_RECEIPT
    elif payload.start_in_breakdown and payload.task_type != TaskType.QUICK:
        status = TaskStatus.PENDING_BREAKDOWN
    else:
        status = TaskStatus.IN_PROGRESS
    with db_runtime.write_lock:
        task = Task(
            title=payload.title.strip(),
            description=payload.description,
            task_type=payload.task_type,
            status=status,
            sensitivity=payload.sensitivity,
            priority=payload.priority,
            source=payload.source.strip(),
            source_kind=payload.source_kind,
            category=payload.category.strip(),
            tags=payload.tags,
            formal_due_at=payload.formal_due_at,
            internal_due_at=payload.internal_due_at,
            planned_start_at=payload.planned_start_at,
            planned_end_at=payload.planned_end_at
            or payload.internal_due_at
            or payload.formal_due_at,
            work_area=payload.work_area.strip(),
            annual_focus=payload.annual_focus.strip(),
            reporting_scope=payload.reporting_scope.strip(),
            owner_id=payload.owner_id,
            reviewer_id=payload.reviewer_id,
            parent_task_id=payload.parent_task_id,
            template_id=payload.template_id,
            recurrence_rule_id=payload.recurrence_rule_id,
            experience_notes=payload.experience_notes.strip(),
            contact_ids=payload.contact_ids,
            created_by=actor.id,
            updated_by=actor.id,
        )
        db.add(task)
        db.flush()
        roles = {(payload.owner_id, ParticipantRole.OWNER)}
        roles.update(
            (user_id, ParticipantRole.COLLABORATOR)
            for user_id in payload.collaborator_ids
            if user_id != payload.owner_id
        )
        if payload.reviewer_id:
            roles.add((payload.reviewer_id, ParticipantRole.REVIEWER))
        for user_id, role in roles:
            if not db.get(User, user_id):
                raise ProblemException(
                    422, "PARTICIPANT_INVALID", "参与人无效", "任务参与人不存在。"
                )
            db.add(TaskParticipant(task_id=task.id, user_id=user_id, role=role))
        for index, step in enumerate(payload.steps):
            db.add(
                TaskStep(
                    task_id=task.id,
                    title=step.title,
                    assignee_id=step.assignee_id,
                    due_at=step.due_at,
                    sort_order=index,
                )
            )
        for material in payload.materials:
            db.add(
                MaterialItem(
                    task_id=task.id,
                    category=material.category,
                    name=material.name,
                    required=material.required,
                )
            )
        db.add(
            TaskStatusEvent(
                task_id=task.id,
                actor_id=actor.id,
                from_status=None,
                to_status=status,
                note="创建事项",
            )
        )
        write_audit(db, actor, "task.create", "task", task.id, {"title": task.title}, ip)
        record_system_entry(
            db,
            actor,
            f"新建事项：{task.title}",
            "事项已创建并进入办理流程。",
            task_id=task.id,
            event_code="task.created",
            event_data={"to_status": status.value},
        )
        emit_event(db, "task.created", task.id, {"status": status.value})
        if parent:
            parent.version += 1
            parent.updated_by = actor.id
            emit_event(db, "task.updated", parent.id, {"version": parent.version})
        db.commit()
        db.refresh(task)
        return task


def update_task(
    db: Session,
    task: Task,
    payload: TaskUpdate,
    expected_version: int,
    actor: User,
    ip: str = "",
) -> Task:
    if not can_edit_task(db, task, actor):
        raise ProblemException(403, "TASK_EDIT_DENIED", "无权修改", "你不是该事项参与人。")
    managed_fields = {
        "owner_id",
        "reviewer_id",
        "sensitivity",
        "allow_sensitive_content",
    }
    if payload.model_fields_set & managed_fields and not can_manage_task(db, task, actor):
        raise ProblemException(
            403,
            "TASK_MANAGE_DENIED",
            "无权调整事项治理信息",
            "协办人可以补充办理内容，但不能移交主办人、指定审核人或调整敏感级别。",
        )
    submitted = payload.model_dump(include=payload.model_fields_set, mode="json")
    previous_owner_id = task.owner_id
    def raise_version_conflict() -> None:
        db.rollback()
        db.refresh(task)
        current = {
            key: getattr(task, key)
            for key in submitted
            if hasattr(task, key)
        }
        changed = [
            key
            for key, value in submitted.items()
            if str(current.get(key)) != str(value)
        ]
        draft = ConflictDraft(
            task_id=task.id,
            user_id=actor.id,
            submitted_version=expected_version,
            payload=submitted,
        )
        db.add(draft)
        db.commit()
        raise ProblemException(
            409,
            "VERSION_CONFLICT",
            "事项已被他人更新",
            "你的修改已另存为冲突草稿，请比较后决定如何处理。",
            extra={
                "current_version": task.version,
                "submitted_version": expected_version,
                "changed_fields": changed,
                "draft_id": draft.id,
                "current": {
                    key: serialize_api_datetime(value)
                    if isinstance(value, datetime)
                    else value
                    for key, value in current.items()
                },
                "submitted": submitted,
            },
        )

    if expected_version != task.version:
        raise_version_conflict()
    next_sensitivity = payload.sensitivity or task.sensitivity
    next_allow = (
        payload.allow_sensitive_content
        if "allow_sensitive_content" in payload.model_fields_set
        else task.allow_sensitive_content
    )
    next_description = (
        payload.description
        if "description" in payload.model_fields_set
        else task.description
    )
    if (
        next_sensitivity == Sensitivity.RESTRICTED
        and next_description
        and not next_allow
    ):
        raise ProblemException(
            422,
            "RESTRICTED_BODY_DISABLED",
            "敏感正文尚未授权",
            "需显式开启本机保存敏感内容后才能写入正文。",
        )
    if payload.owner_id:
        owner = db.get(User, payload.owner_id)
        if not owner or not owner.active:
            raise ProblemException(422, "OWNER_INVALID", "责任人无效", "请选择有效责任人。")
    if "reviewer_id" in payload.model_fields_set and payload.reviewer_id:
        reviewer = db.get(User, payload.reviewer_id)
        if not reviewer or not reviewer.active:
            raise ProblemException(422, "REVIEWER_INVALID", "审核人无效", "请选择有效审核人。")
    if payload.contact_ids is not None:
        valid_contacts = set(
            db.scalars(select(Contact.id).where(Contact.id.in_(payload.contact_ids))).all()
        )
        if valid_contacts != set(payload.contact_ids):
            raise ProblemException(422, "CONTACT_INVALID", "联系人无效", "请选择有效联系人。")
    with db_runtime.write_lock:
        update_values = {
            field: getattr(payload, field)
            for field in payload.model_fields_set
        }
        update_values.update(
            version=expected_version + 1,
            updated_by=actor.id,
            updated_at=utcnow(),
        )
        result = db.execute(
            sql_update(Task)
            .where(Task.id == task.id, Task.version == expected_version)
            .values(**update_values)
        )
        if int(result.rowcount or 0) != 1:
            raise_version_conflict()
        db.refresh(task)
        if "owner_id" in payload.model_fields_set:
            db.query(TaskParticipant).filter(
                TaskParticipant.task_id == task.id,
                TaskParticipant.role == ParticipantRole.OWNER,
            ).delete(synchronize_session=False)
            db.add(
                TaskParticipant(
                    task_id=task.id,
                    user_id=task.owner_id,
                    role=ParticipantRole.OWNER,
                )
            )
        if "reviewer_id" in payload.model_fields_set:
            db.query(TaskParticipant).filter(
                TaskParticipant.task_id == task.id,
                TaskParticipant.role == ParticipantRole.REVIEWER,
            ).delete(synchronize_session=False)
            if task.reviewer_id:
                db.add(
                    TaskParticipant(
                        task_id=task.id,
                        user_id=task.reviewer_id,
                        role=ParticipantRole.REVIEWER,
                    )
                )
        write_audit(
            db,
            actor,
            "task.update",
            "task",
            task.id,
            {"fields": sorted(payload.model_fields_set), "version": task.version},
            ip,
        )
        event_code = (
            "task.transferred"
            if "owner_id" in payload.model_fields_set
            and task.owner_id != previous_owner_id
            else "task.updated"
        )
        previous_owner = db.get(User, previous_owner_id)
        current_owner = db.get(User, task.owner_id)
        field_labels = {
            "title": "事项名称",
            "description": "事项正文",
            "owner_id": "主办人",
            "reviewer_id": "审核人",
            "formal_due_at": "正式截止时间",
            "internal_due_at": "内部截止时间",
            "planned_start_at": "计划开始时间",
            "planned_end_at": "计划完成时间",
            "category": "工作领域",
            "tags": "标签",
            "annual_focus": "年度重点",
            "reporting_scope": "汇报口径",
        }
        changed_labels = [
            field_labels.get(field, "事项信息")
            for field in sorted(payload.model_fields_set)
        ]
        record_system_entry(
            db,
            actor,
            (
                f"转交事项：{task.title}"
                if event_code == "task.transferred"
                else f"更新事项：{task.title}"
            ),
            (
                f"主办人由{previous_owner.display_name if previous_owner else '原主办人'}"
                f"调整为{current_owner.display_name if current_owner else '新主办人'}。"
                if event_code == "task.transferred"
                else "修改内容：" + "、".join(dict.fromkeys(changed_labels))
            ),
            task_id=task.id,
            event_code=event_code,
            event_data={
                "fields": sorted(payload.model_fields_set),
                "previous_owner": (
                    previous_owner.display_name if previous_owner else ""
                ),
                "current_owner": current_owner.display_name if current_owner else "",
            },
        )
        emit_event(db, "task.updated", task.id, {"version": task.version})
        db.commit()
        db.refresh(task)
        return task


def required_materials_missing(db: Session, task_id: str) -> list[MaterialItem]:
    items = db.scalars(
        select(MaterialItem).where(
            MaterialItem.task_id == task_id,
            MaterialItem.required.is_(True),
            MaterialItem.not_applicable.is_(False),
        )
    ).all()
    missing: list[MaterialItem] = []
    for item in items:
        final_exists = db.scalar(
            select(func.count())
            .select_from(AttachmentVersion)
            .where(
                AttachmentVersion.material_item_id == item.id,
                AttachmentVersion.is_final.is_(True),
            )
        )
        if not final_exists:
            missing.append(item)
    return missing


def apply_task_action(
    db: Session,
    task: Task,
    action: str,
    note: str,
    actor: User,
    ip: str = "",
    expected_version: int | None = None,
    *,
    commit: bool = True,
) -> Task:
    if not can_edit_task(db, task, actor):
        raise ProblemException(403, "TASK_ACTION_DENIED", "无权操作", "你不是该事项参与人。")
    if expected_version is not None and task.version != expected_version:
        raise ProblemException(
            409,
            "VERSION_CONFLICT",
            "事项已被他人更新",
            "请刷新最新状态后重试。",
            extra={
                "current_version": task.version,
                "submitted_version": expected_version,
            },
        )
    if action in {"approve", "return"}:
        if actor.id != task.reviewer_id and actor.role != UserRole.ADMIN:
            raise ProblemException(403, "REVIEWER_REQUIRED", "无权审核", "此操作由审核人执行。")
    elif not can_manage_task(db, task, actor):
        raise ProblemException(
            403,
            "TASK_WORKFLOW_DENIED",
            "无权变更事项状态",
            "协办人可以补充步骤和材料，流程流转由主办人或管理员执行。",
        )
    if action == "complete" and task.reviewer_id:
        raise ProblemException(
            409, "REVIEW_REQUIRED", "需要审核", "该事项已设置审核人，请先提交审核。"
        )
    if action in {"complete", "approve", "archive"}:
        unfinished = db.scalars(
            select(Task).where(
                Task.parent_task_id == task.id,
                Task.deleted_at.is_(None),
                Task.status.not_in({TaskStatus.COMPLETED, TaskStatus.ARCHIVED}),
            )
        ).all()
        if unfinished:
            raise ProblemException(
                409,
                "SUBTASKS_INCOMPLETE",
                "仍有子任务未完成",
                "请先完成或归档全部子任务。",
                extra={
                    "subtasks": [
                        {"id": child.id, "title": child.title} for child in unfinished
                    ]
                },
            )
    if action == "archive":
        missing = required_materials_missing(db, task.id)
        if missing:
            raise ProblemException(
                409,
                "MATERIALS_INCOMPLETE",
                "必备材料尚未齐全",
                "请补齐必备材料或标记为不适用后再归档。",
                extra={"missing": [{"id": item.id, "name": item.name} for item in missing]},
            )
    if action in {"reopen", "return"} and not note.strip():
        raise ProblemException(
            422,
            "ACTION_REASON_REQUIRED",
            "需要说明原因",
            "重新打开或退回修改必须填写原因。",
        )
    target = transition(task.status, action)
    observed_version = task.version
    with db_runtime.write_lock:
        previous = task.status
        timestamp = utcnow()
        update_values: dict[str, object] = {
            "status": target,
            "version": observed_version + 1,
            "updated_by": actor.id,
            "updated_at": timestamp,
        }
        if target == TaskStatus.COMPLETED:
            update_values["completed_at"] = timestamp
        if target == TaskStatus.ARCHIVED:
            update_values["archived_at"] = timestamp
        if action == "reopen":
            update_values["completed_at"] = None
            update_values["archived_at"] = None
        result = db.execute(
            sql_update(Task)
            .where(
                Task.id == task.id,
                Task.version == observed_version,
                Task.status == previous,
            )
            .values(**update_values)
        )
        if int(result.rowcount or 0) != 1:
            db.rollback()
            db.refresh(task)
            raise ProblemException(
                409,
                "VERSION_CONFLICT",
                "事项已被他人更新",
                "请刷新最新状态后重试。",
                extra={
                    "current_version": task.version,
                    "submitted_version": expected_version or observed_version,
                },
            )
        db.refresh(task)
        if target == TaskStatus.ARCHIVED:
            create_archive_snapshot(db, task, actor)
        db.add(
            TaskStatusEvent(
                task_id=task.id,
                actor_id=actor.id,
                from_status=previous,
                to_status=target,
                note=note.strip(),
            )
        )
        write_audit(
            db,
            actor,
            f"task.{action}",
            "task",
            task.id,
            {"from": previous.value, "to": target.value, "note": note.strip()},
            ip,
        )
        record_system_entry(
            db,
            actor,
            f"状态变更：{task.title}",
            note.strip(),
            task_id=task.id,
            event_code="task.status_changed",
            event_data={
                "action": action,
                "from_status": previous.value,
                "to_status": target.value,
                "note": note.strip(),
            },
        )
        if target == TaskStatus.COMPLETED:
            # 完成动作与周期归集处于同一事务：主机或任一协同电脑完成事项后，
            # 当前周、月、季度和年度草稿会立即出现该事项。
            db.flush()
            from .reports import ensure_period_reports

            reports, created_reports, added_items = ensure_period_reports(
                db,
                actor,
                task.completed_at,
            )
            if created_reports or added_items:
                emit_event(
                    db,
                    "period_report.updated",
                    reports[0].id,
                    {
                        "created_reports": created_reports,
                        "added_items": added_items,
                        "task_id": task.id,
                    },
                )
        emit_event(db, "task.status_changed", task.id, {"status": target.value})
        if commit:
            db.commit()
            db.refresh(task)
        else:
            db.flush()
        return task


def visible_tasks(db: Session, user: User) -> list[Task]:
    tasks = db.scalars(
        select(Task)
        .where(Task.deleted_at.is_(None), task_visibility_clause(user))
        .order_by(Task.internal_due_at.is_(None), Task.internal_due_at, Task.updated_at.desc())
    ).all()
    return list(tasks)


def dashboard(db: Session, user: User) -> DashboardOut:
    now = utcnow()
    preference = db.get(ReminderPreference, user.id)
    advance_days = preference.advance_days if preference and preference.enabled else 3
    upcoming = now + timedelta(days=advance_days)
    tasks = visible_tasks(db, user)
    active = [task for task in tasks if task.status != TaskStatus.ARCHIVED]

    def due(task: Task) -> datetime | None:
        value = task.internal_due_at or task.formal_due_at
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    definitions = [
        (
            "today",
            "今天必须办理",
            lambda task: due(task) is not None
            and due(task).date() == now.date()
            and task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED},
        ),
        (
            "three_days",
            f"{advance_days}日内到期",
            lambda task: due(task) is not None
            and now < due(task) <= upcoming
            and task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED},
        ),
        (
            "overdue",
            "已逾期",
            lambda task: due(task) is not None
            and due(task) < now
            and task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED},
        ),
        (
            "my_action",
            "等待我处理",
            lambda task: task.owner_id == user.id
            and task.status
            in {
                TaskStatus.PENDING_RECEIPT,
                TaskStatus.PENDING_BREAKDOWN,
                TaskStatus.IN_PROGRESS,
                TaskStatus.RETURNED,
            },
        ),
        (
            "other_action",
            "等待对方处理",
            lambda task: task.owner_id != user.id
            and task.status
            not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED},
        ),
        (
            "review",
            "待审核",
            lambda task: task.status == TaskStatus.PENDING_REVIEW,
        ),
        (
            "feedback",
            "待反馈",
            lambda task: task.status == TaskStatus.WAITING_FEEDBACK,
        ),
        (
            "materials",
            "材料不完整",
            lambda task: task.status == TaskStatus.COMPLETED
            and bool(required_materials_missing(db, task.id)),
        ),
    ]
    buckets: list[DashboardBucket] = []
    for key, label, predicate in definitions:
        items = [task for task in active if predicate(task)]
        if preference and preference.enabled:
            if key == "overdue" and not preference.remind_overdue:
                items = []
            elif key == "review" and not preference.remind_review:
                items = []
            elif key == "feedback" and not preference.remind_feedback:
                items = []
            elif key == "materials" and not preference.remind_materials:
                items = []
        elif preference and not preference.enabled and key in {
            "today",
            "three_days",
            "overdue",
            "review",
            "feedback",
            "materials",
        }:
            items = []
        buckets.append(
            DashboardBucket(
                key=key,
                label=label,
                count=len(items),
                items=[task_to_out(db, task, include_detail=False) for task in items[:8]],
            )
        )
    local_timezone = timezone(timedelta(hours=8))
    local_now = now.astimezone(local_timezone)
    this_week_start_local = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    this_week_end_local = this_week_start_local + timedelta(days=7)
    next_week_end_local = this_week_end_local + timedelta(days=7)
    this_week_start = this_week_start_local.astimezone(timezone.utc)
    this_week_end = this_week_end_local.astimezone(timezone.utc)
    next_week_end = next_week_end_local.astimezone(timezone.utc)

    def aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    this_week_completed = [
        task
        for task in tasks
        if (completed := aware(task.completed_at)) is not None
        and this_week_start <= completed < this_week_end
    ]
    next_week_planned = [
        task
        for task in active
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED}
        and (
            (
                (planned := aware(task.planned_start_at or task.planned_end_at))
                is not None
                and this_week_end <= planned < next_week_end
            )
            or (
                (planned_due := aware(task.internal_due_at or task.formal_due_at))
                is not None
                and this_week_end <= planned_due < next_week_end
            )
        )
    ]
    carry_over = [
        task
        for task in active
        if task.status not in {TaskStatus.COMPLETED, TaskStatus.ARCHIVED}
        and (planned_end := aware(task.planned_end_at)) is not None
        and planned_end < this_week_end
    ]
    unread = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        )
        or 0
    )
    return DashboardOut(
        buckets=buckets,
        updated_at=now,
        this_week_completed=[
            task_to_out(db, task, include_detail=False)
            for task in this_week_completed[:12]
        ],
        next_week_planned=[
            task_to_out(db, task, include_detail=False)
            for task in next_week_planned[:12]
        ],
        carry_over=[
            task_to_out(db, task, include_detail=False) for task in carry_over[:12]
        ],
        unread_notifications=unread,
    )


def list_tasks(
    db: Session,
    user: User,
    status: TaskStatus | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
    scope: str | None = None,
) -> list[Task]:
    tasks = visible_tasks(db, user)
    if scope == "owned":
        tasks = [task for task in tasks if task.owner_id == user.id]
    elif scope == "collaborating":
        collaborating_ids = set(
            db.scalars(
                select(TaskParticipant.task_id).where(
                    TaskParticipant.user_id == user.id,
                    TaskParticipant.role == ParticipantRole.COLLABORATOR,
                )
            ).all()
        )
        tasks = [task for task in tasks if task.id in collaborating_ids]
    elif scope == "reviewing":
        tasks = [
            task
            for task in tasks
            if task.reviewer_id == user.id and task.status == TaskStatus.PENDING_REVIEW
        ]
    elif scope == "step_assigned":
        assigned_ids = set(
            db.scalars(
                select(TaskStep.task_id).where(
                    TaskStep.assignee_id == user.id,
                    TaskStep.done.is_(False),
                )
            ).all()
        )
        tasks = [task for task in tasks if task.id in assigned_ids]
    if status:
        tasks = [task for task in tasks if task.status == status]
    if owner_id:
        tasks = [task for task in tasks if task.owner_id == owner_id]
    if keyword:
        lowered = keyword.lower()
        tasks = [
            task
            for task in tasks
            if lowered in task.title.lower()
            or lowered in task.source.lower()
            or lowered in task.description.lower()
        ]
    return tasks
