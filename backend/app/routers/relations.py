"""业务对象双向关联与活动时间线接口。"""

from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import emit_event, write_audit
from ..database import get_session
from ..enums import UserRole
from ..models import ActivityEvent, ObjectLink, User
from ..object_graph import describe_object, visible_links
from ..problems import ProblemException
from ..schemas import ActivityEventOut, ObjectLinkCreate, ObjectLinkOut
from ..security import get_current_user
from ..work_journal import ACTION_LABELS, ROLE_LABELS
from .router_utils import client_ip, parse_if_match, parse_object_type

router = APIRouter(tags=["object-relations"])


@router.get(
    "/objects/{object_type}/{object_id}/links",
    response_model=typing.List[ObjectLinkOut],
)
def get_object_links(
    object_type: str,
    object_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, object]]:
    return visible_links(db, parse_object_type(object_type), object_id, user)


@router.post(
    "/objects/{object_type}/{object_id}/links",
    response_model=ObjectLinkOut,
    status_code=201,
)
def create_object_link(
    object_type: str,
    object_id: str,
    payload: ObjectLinkCreate,
    request: Request,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    source_type = parse_object_type(object_type)
    describe_object(db, source_type, object_id, user)
    describe_object(db, payload.target_type, payload.target_id, user)
    if source_type == payload.target_type and object_id == payload.target_id:
        raise ProblemException(
            422, "OBJECT_LINK_SELF", "不能关联对象自身", "请选择另一个业务对象。"
        )
    idempotency_key = idempotency_header or payload.idempotency_key
    if idempotency_key:
        event = db.scalar(
            select(ActivityEvent).where(
                ActivityEvent.idempotency_key == idempotency_key
            )
        )
        link_id = str((event.event_data or {}).get("link_id", "")) if event else ""
        existing = db.get(ObjectLink, link_id) if link_id else None
        if existing:
            return next(
                item
                for item in visible_links(db, source_type, object_id, user)
                if item["id"] == existing.id
            )
    link = db.scalar(
        select(ObjectLink).where(
            ObjectLink.source_type == source_type,
            ObjectLink.source_id == object_id,
            ObjectLink.target_type == payload.target_type,
            ObjectLink.target_id == payload.target_id,
            ObjectLink.link_type == payload.link_type,
        )
    )
    if link is None:
        link = ObjectLink(
            source_type=source_type,
            source_id=object_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            link_type=payload.link_type,
            note=payload.note,
            created_by=user.id,
        )
        db.add(link)
        db.flush()
    db.add(
        ActivityEvent(
            object_type=source_type,
            object_id=object_id,
            event_code="object.linked",
            actor_id=user.id,
            event_data={
                "link_id": link.id,
                "target_type": payload.target_type.value,
                "target_id": payload.target_id,
                "link_type": payload.link_type.value,
            },
            idempotency_key=idempotency_key,
        )
    )
    write_audit(
        db,
        user,
        "object.link_create",
        source_type.value,
        object_id,
        {"link_id": link.id, "target_type": payload.target_type.value},
        client_ip(request),
    )
    emit_event(db, "object.linked", object_id, {"link_id": link.id})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        link = db.scalar(
            select(ObjectLink).where(
                ObjectLink.source_type == source_type,
                ObjectLink.source_id == object_id,
                ObjectLink.target_type == payload.target_type,
                ObjectLink.target_id == payload.target_id,
                ObjectLink.link_type == payload.link_type,
            )
        )
        if not link:
            raise
    return next(
        item
        for item in visible_links(db, source_type, object_id, user)
        if item["id"] == link.id
    )


@router.delete(
    "/objects/{object_type}/{object_id}/links/{link_id}",
    response_model=dict,
)
def delete_object_link(
    object_type: str,
    object_id: str,
    link_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    source_type = parse_object_type(object_type)
    describe_object(db, source_type, object_id, user)
    link = db.get(ObjectLink, link_id)
    if not link or not (
        (link.source_type == source_type and link.source_id == object_id)
        or (link.target_type == source_type and link.target_id == object_id)
    ):
        raise ProblemException(
            404, "OBJECT_LINK_NOT_FOUND", "关联不存在", "未找到该关联。"
        )
    if link.version != parse_if_match(if_match):
        raise ProblemException(
            409, "VERSION_CONFLICT", "关联已变化", "请刷新后重试。"
        )
    if user.role != UserRole.ADMIN and link.created_by != user.id:
        raise ProblemException(
            403, "OBJECT_LINK_DENIED", "无权移除关联", "只有创建人或管理员可以移除。"
        )
    db.delete(link)
    write_audit(
        db,
        user,
        "object.link_delete",
        source_type.value,
        object_id,
        {"link_id": link_id},
        client_ip(request),
    )
    emit_event(db, "object.unlinked", object_id, {"link_id": link_id})
    db.commit()
    return {"deleted": True}


@router.get(
    "/objects/{object_type}/{object_id}/activity",
    response_model=typing.List[ActivityEventOut],
)
def get_object_activity(
    object_type: str,
    object_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, object]]:
    value = parse_object_type(object_type)
    describe_object(db, value, object_id, user)
    rows = db.scalars(
        select(ActivityEvent)
        .where(
            ActivityEvent.object_type == value,
            ActivityEvent.object_id == object_id,
        )
        .order_by(ActivityEvent.happened_at.desc())
        .limit(limit)
    ).all()
    actor_ids = {row.actor_id for row in rows if row.actor_id}
    actors = {
        actor.id: actor
        for actor in db.scalars(select(User).where(User.id.in_(actor_ids))).all()
    } if actor_ids else {}
    event_fallbacks = {
        "object.linked": "建立业务关联",
        "object.unlinked": "移除业务关联",
        "recurrence.exception": "设置周期例外",
    }
    return [
        {
            "id": row.id,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "event_code": row.event_code,
            "event_label": ACTION_LABELS.get(
                row.event_code, event_fallbacks.get(row.event_code, "更新业务记录")
            ),
            "actor_id": row.actor_id,
            "actor_name": actors[row.actor_id].display_name
            if row.actor_id in actors
            else "系统",
            "actor_role": ROLE_LABELS.get(
                actors[row.actor_id].role.value if row.actor_id in actors else "",
                "系统",
            ),
            "happened_at": row.happened_at,
            "recorded_at": row.recorded_at,
            "event_data": row.event_data,
            "correlation_id": row.correlation_id,
        }
        for row in rows
    ]
