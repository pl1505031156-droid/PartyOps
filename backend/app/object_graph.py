"""跨领域对象的权限校验、标题解析和双向关联查询。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .archive_service import can_view_category
from .enums import ObjectType, UserRole, WorkspaceRootSource
from .models import (
    ArchiveCategory,
    ArchiveRecord,
    Contact,
    KnowledgeEntry,
    ObjectLink,
    PeriodReport,
    Task,
    TopicSpace,
    User,
    WorkJournalEntry,
    WorkspaceFile,
    WorkspaceRoot,
)
from .problems import ProblemException
from .task_service import can_view_task
from .workspace_access import grant_allows


@dataclass(frozen=True)
class ObjectDescriptor:
    title: str
    route: str


def _file_accessible(db: Session, item: WorkspaceFile, user: User) -> bool:
    if not item.in_scope:
        return False
    root = db.get(WorkspaceRoot, item.root_id)
    if not root or not root.enabled or root.approval_status != "approved":
        return False
    if user.role == UserRole.ADMIN or root.source == WorkspaceRootSource.HOST:
        return True
    return bool(
        root.device_id
        and (
            grant_allows(db, user, root.device_id, root.id, "download")
            or grant_allows(db, user, root.device_id, root.id, "share")
        )
    )


def describe_object(
    db: Session,
    object_type: ObjectType,
    object_id: str,
    user: User,
) -> ObjectDescriptor:
    if object_type == ObjectType.TASK:
        item = db.get(Task, object_id)
        if item and can_view_task(db, item, user):
            return ObjectDescriptor(item.title, f"/tasks/{item.id}")
    elif object_type == ObjectType.WORKSPACE_FILE:
        item = db.get(WorkspaceFile, object_id)
        if item and _file_accessible(db, item, user):
            return ObjectDescriptor(item.name, f"/workspace?file={item.id}")
    elif object_type == ObjectType.ARCHIVE_RECORD:
        item = db.get(ArchiveRecord, object_id)
        category = db.get(ArchiveCategory, item.category_id) if item else None
        if item and category and can_view_category(db, category, user):
            return ObjectDescriptor(item.title, f"/archives?record={item.id}")
    elif object_type == ObjectType.JOURNAL:
        item = db.get(WorkJournalEntry, object_id)
        if item and (
            user.role == UserRole.ADMIN
            or item.created_by == user.id
            or (
                item.task_id
                and (task := db.get(Task, item.task_id))
                and can_view_task(db, task, user)
            )
        ):
            return ObjectDescriptor(item.title, f"/journal?entry={item.id}")
    elif object_type == ObjectType.PERIOD_REPORT:
        item = db.get(PeriodReport, object_id)
        if item:
            return ObjectDescriptor(item.title, f"/reports?report={item.id}")
    elif object_type == ObjectType.KNOWLEDGE:
        item = db.get(KnowledgeEntry, object_id)
        if item:
            return ObjectDescriptor(item.title, f"/knowledge?entry={item.id}")
    elif object_type == ObjectType.CONTACT:
        item = db.get(Contact, object_id)
        if item:
            return ObjectDescriptor(item.name, f"/knowledge?contact={item.id}")
    elif object_type == ObjectType.TOPIC:
        item = db.get(TopicSpace, object_id)
        if item and (user.role == UserRole.ADMIN or item.owner_id == user.id):
            return ObjectDescriptor(item.name, f"/topics?topic={item.id}")
    raise ProblemException(
        404,
        "OBJECT_NOT_FOUND",
        "关联对象不存在",
        "未找到对象，或当前用户没有查看权限。",
    )


def visible_links(
    db: Session,
    object_type: ObjectType,
    object_id: str,
    user: User,
) -> list[dict[str, object]]:
    describe_object(db, object_type, object_id, user)
    links = db.scalars(
        select(ObjectLink)
        .where(
            or_(
                (ObjectLink.source_type == object_type)
                & (ObjectLink.source_id == object_id),
                (ObjectLink.target_type == object_type)
                & (ObjectLink.target_id == object_id),
            )
        )
        .order_by(ObjectLink.created_at.desc())
    ).all()
    result: list[dict[str, object]] = []
    for link in links:
        outgoing = link.source_type == object_type and link.source_id == object_id
        related_type = link.target_type if outgoing else link.source_type
        related_id = link.target_id if outgoing else link.source_id
        try:
            descriptor = describe_object(db, related_type, related_id, user)
        except ProblemException:
            continue
        result.append(
            {
                "id": link.id,
                "source_type": link.source_type,
                "source_id": link.source_id,
                "target_type": link.target_type,
                "target_id": link.target_id,
                "link_type": link.link_type,
                "note": link.note,
                "version": link.version,
                "created_by": link.created_by,
                "created_at": link.created_at,
                "direction": "outgoing" if outgoing else "incoming",
                "title": descriptor.title,
                "route": descriptor.route,
            }
        )
    return result
