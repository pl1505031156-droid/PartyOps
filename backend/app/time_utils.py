"""PartyOps 统一时间边界。

数据库内部继续保存 UTC 瞬时值；所有 API、日志和导出元数据在离开系统时
统一转换为显式携带 ``+08:00`` 的北京时间。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc
BEIJING_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def ensure_aware_utc(value: datetime) -> datetime:
    """把内部无时区值按既有 UTC 约定解释，并返回 UTC 瞬时值。"""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def beijing_now() -> datetime:
    """返回当前北京时间。"""

    return datetime.now(UTC).astimezone(BEIJING_TIMEZONE)


def to_beijing(value: datetime) -> datetime:
    """把内部瞬时值转换为北京时间。"""

    return ensure_aware_utc(value).astimezone(BEIJING_TIMEZONE)


def beijing_iso(value: Optional[datetime] = None) -> str:
    """返回显式携带 ``+08:00`` 的 ISO 8601 北京时间。"""

    return beijing_now().isoformat() if value is None else to_beijing(value).isoformat()
