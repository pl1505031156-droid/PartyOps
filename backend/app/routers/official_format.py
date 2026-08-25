"""公文规范排版本机服务票据接口。"""

# FastAPI 通过声明式 Depends 在请求期注入会话，属于框架约定。
# ruff: noqa: B008

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_session
from ..device_versions import ensure_device_context_secret, request_device
from ..models import User
from ..official_format_service import (
    issue_local_format_ticket,
    normalize_origin,
)
from ..problems import ProblemException
from ..security import get_current_user
from .workspace import is_host_local_request

UTC = timezone.utc

router = APIRouter(tags=["official-format"])


class LocalFormatTicketCreate(BaseModel):
    origin: str = Field(min_length=8, max_length=512)


def _request_origin(request: Request) -> str:
    raw = request.headers.get("Origin", "").strip()
    if not raw and get_settings().environment == "test":
        return ""
    try:
        return normalize_origin(raw)
    except ValueError as exc:
        raise ProblemException(
            403,
            "LOCAL_FORMAT_ORIGIN_DENIED",
            "页面来源不受信任",
            "请在 PartyOps 公文规范排版页面中发起操作。",
        ) from exc


@router.post("/official-format/local-ticket", response_model=dict)
def create_local_format_ticket(
    payload: LocalFormatTicketCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """只签发不含文件信息的短时票据，文档不会经过本接口。"""

    try:
        origin = normalize_origin(payload.origin)
    except ValueError as exc:
        raise ProblemException(
            422,
            "LOCAL_FORMAT_ORIGIN_INVALID",
            "页面来源无效",
            "请刷新 PartyOps 页面后重试。",
        ) from exc
    header_origin = _request_origin(request)
    if header_origin and header_origin != origin:
        raise ProblemException(
            403,
            "LOCAL_FORMAT_ORIGIN_MISMATCH",
            "页面来源不一致",
            "安全校验未通过，请刷新页面后重试。",
        )

    device = request_device(request, db)
    if device is not None:
        if device.credential_state != "active" or not device.agent_token_hash:
            raise ProblemException(
                409,
                "LOCAL_FORMAT_DEVICE_REAUTHORIZE_REQUIRED",
                "协同电脑需要重新授权",
                "请由主机管理员恢复本机凭据后再使用公文排版。",
            )
        secret = device.agent_token_hash
        device_id = device.id
    elif is_host_local_request(request):
        secret = ensure_device_context_secret(db)
        device_id = "host-local"
    else:
        raise ProblemException(
            409,
            "LOCAL_FORMAT_HELPER_REQUIRED",
            "当前电脑未连接本机助手",
            "请使用已安装并已授权的 PartyOps 主机或协同电脑打开此页面。",
        )

    ticket, expires_at = issue_local_format_ticket(
        secret,
        origin=origin,
        user_id=user.id,
        device_id=device_id,
    )
    db.commit()
    return {
        "expires_at": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "local_base_url": f"http://127.0.0.1:{get_settings().official_format_port}",
        "ticket": ticket,
    }


__all__ = ["router"]
