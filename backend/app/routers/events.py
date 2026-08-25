"""SSE 实时协作事件。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from ..database import db_runtime
from ..models import EventOutbox, User
from ..security import get_current_user

router = APIRouter(tags=["events"])
_active_streams = 0


def active_stream_count() -> int:
    return _active_streams


@router.get("/events/stream")
async def event_stream(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _user: User = Depends(get_current_user),
) -> EventSourceResponse:
    try:
        cursor = int(last_event_id or 0)
    except ValueError:
        cursor = 0

    async def generate():
        global _active_streams
        nonlocal cursor
        idle_ticks = 0
        _active_streams += 1
        try:
            while True:
                with db_runtime.session_factory() as db:
                    events = db.scalars(
                        select(EventOutbox)
                        .where(EventOutbox.id > cursor)
                        .order_by(EventOutbox.id)
                        .limit(100)
                    ).all()
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = event.id
                        yield {
                            "id": str(event.id),
                            "event": event.event_type,
                            # SSE 只承担“有变化，请重载”的失效通知。业务实体、
                            # 提及对象和传输信息必须重新经过各 API 的行级权限校验。
                            "data": "{}",
                        }
                else:
                    idle_ticks += 1
                    if idle_ticks >= 15:
                        idle_ticks = 0
                        yield {"event": "heartbeat", "data": "{}"}
                await asyncio.sleep(1)
        finally:
            _active_streams = max(0, _active_streams - 1)

    return EventSourceResponse(generate(), ping=15)
