"""旧式只读备份配对令牌的集中认证与到期策略。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import ClientPairing, utcnow
from .problems import ProblemException
from .security import hash_token


def _as_utc(value: datetime) -> datetime:
    """SQLite 可能返回无时区时间；历史值统一按 UTC 解释。"""

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def pairing_expires_at(
    pairing: ClientPairing,
    settings: Settings | None = None,
) -> datetime:
    """计算旧式配对令牌的固定到期时间。"""

    active_settings = settings or get_settings()
    return _as_utc(pairing.created_at) + timedelta(days=active_settings.backup_pairing_ttl_days)


def authenticate_backup_pairing(
    db: Session,
    raw_token: str | None,
    settings: Settings | None = None,
) -> ClientPairing:
    """验证备份配对令牌，并在到期时立即撤销旧凭据。"""

    token = (raw_token or "").strip()
    pairing = db.scalar(
        select(ClientPairing).where(
            ClientPairing.token_hash == hash_token(token),
            ClientPairing.active.is_(True),
        )
    )
    if not pairing:
        raise ProblemException(401, "PAIRING_INVALID", "配对无效", "请重新配对终端。")

    if _as_utc(utcnow()) >= pairing_expires_at(pairing, settings):
        pairing.active = False
        db.commit()
        raise ProblemException(
            401,
            "PAIRING_EXPIRED",
            "配对已到期",
            "该旧式备份配对令牌已到期，请由主机管理员重新生成；新版协同设备不受影响。",
        )
    return pairing
