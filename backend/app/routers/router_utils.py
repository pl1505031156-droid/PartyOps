"""路由层共享的轻量校验工具。

这里只放无业务状态的纯函数，领域查询和写入继续留在各自服务模块，避免
路由之间相互导入形成循环依赖。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request

from ..enums import ObjectType
from ..problems import ProblemException


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise ProblemException(
            428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。"
        )
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(
            400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。"
        ) from exc


def parse_object_type(value: str) -> ObjectType:
    try:
        return ObjectType(value)
    except ValueError as exc:
        raise ProblemException(
            422, "OBJECT_TYPE_INVALID", "对象类型无效", "请选择系统支持的业务对象。"
        ) from exc


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
