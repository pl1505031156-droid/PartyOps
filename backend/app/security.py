"""密码、会话与权限依赖。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_session
from .enums import UserRole
from .models import LoginSession, User, utcnow
from .problems import ProblemException


password_hasher = PasswordHasher()
SESSION_COOKIE = "partyops_session"


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(db: Session, user: User) -> tuple[str, LoginSession]:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    login_session = LoginSession(
        token_hash=hash_token(token),
        user_id=user.id,
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
    )
    db.add(login_session)
    db.flush()
    return token, login_session


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_current_user(
    request: Request, db: Session = Depends(get_session)
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    if not token:
        raise ProblemException(401, "AUTH_REQUIRED", "需要登录", "请先登录系统。")
    record = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == hash_token(token))
    )
    if not record or record.revoked_at is not None:
        raise ProblemException(401, "SESSION_INVALID", "登录已失效", "请重新登录。")
    if _ensure_aware(record.expires_at) <= utcnow():
        raise ProblemException(401, "SESSION_EXPIRED", "登录已过期", "请重新登录。")
    user = db.get(User, record.user_id)
    if not user or not user.active:
        raise ProblemException(403, "USER_DISABLED", "账号不可用", "请联系管理员。")
    record.last_seen_at = utcnow()
    return user


def get_current_user_optional(
    request: Request, db: Session = Depends(get_session)
) -> User | None:
    """返回当前有效用户；无会话时不报错，供支持令牌认证的混合端点使用。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    record = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == hash_token(token))
    )
    if (
        not record
        or record.revoked_at is not None
        or _ensure_aware(record.expires_at) <= utcnow()
    ):
        return None
    user = db.get(User, record.user_id)
    return user if user and user.active else None


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise ProblemException(403, "ADMIN_REQUIRED", "无权操作", "此操作仅限管理员。")
    return user
