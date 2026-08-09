"""审计与事件发件箱。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, EventOutbox, User


def write_audit(
    db: Session,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    detail: dict[str, Any] | None = None,
    ip_address: str = "",
) -> AuditLog:
    record = AuditLog(
        actor_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        detail=detail or {},
        ip_address=ip_address,
    )
    db.add(record)
    return record


def emit_event(
    db: Session,
    event_type: str,
    entity_id: str | None,
    payload: dict[str, Any] | None = None,
) -> EventOutbox:
    event = EventOutbox(
        event_type=event_type,
        entity_id=entity_id,
        payload=payload or {},
    )
    db.add(event)
    return event
