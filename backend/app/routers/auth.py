"""首次配置、登录与当前用户。"""

from __future__ import annotations

import ipaddress
import secrets
import typing
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import get_settings
from ..database import db_runtime, get_session
from ..enums import UserRole
from ..login_throttle import login_throttle
from ..models import LoginSession, User, utcnow
from ..networking import discover_lan_addresses, service_url
from ..problems import ProblemException
from ..schemas import (
    BootstrapHostRequest,
    BootstrapStatus,
    HealthOut,
    LoginRequest,
    UserOut,
)
from ..security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    get_current_user,
    hash_password,
    hash_token,
    issue_session,
    verify_password,
)
from ..seed import seed_demo_data, seed_templates

router = APIRouter(tags=["bootstrap-auth"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def bootstrap_request_is_local(request: Request) -> bool:
    """首次管理员只能在主机本机创建，避免局域网内抢先接管。"""

    remote = client_ip(request).strip()
    settings = get_settings()
    if settings.environment == "test" and remote == "testclient":
        return True
    try:
        address = ipaddress.ip_address(remote)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_loopback:
            return True
    except ValueError:
        return False
    # 首位管理员由配置向导固定通过 127.0.0.1 创建。服务器自己的局域网
    # 地址不是“请求来源在本机”的证明，不能因地址碰撞、代理或错误转发放行。
    return False


def bootstrap_request_is_trusted(request: Request) -> bool:
    """只接受同源浏览器或持有受保护一次性配置令牌的本机向导。"""

    if not bootstrap_request_is_local(request):
        return False
    settings = get_settings()
    if settings.environment == "test":
        return True
    expected = settings.bootstrap_token.strip()
    supplied = request.headers.get("X-PartyOps-Bootstrap-Token", "").strip()
    if len(expected) >= 32 and secrets.compare_digest(supplied, expected):
        return True
    source = request.headers.get("Origin", "").strip()
    if not source:
        source = request.headers.get("Referer", "").strip()
    try:
        parsed = urlparse(source)
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        return False
    return (
        f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        == f"{request.url.scheme.lower()}://{request.url.netloc.lower()}"
    )


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(
        status="ok",
        app_version=settings.app_version,
        mode=settings.mode,
        host=settings.host,
        port=settings.port,
        service_url=service_url(
            settings.host,
            settings.port,
            tls_enabled=settings.tls_enabled,
        ),
        sqlite=db_runtime.validate_capabilities(),
    )


@router.get("/bootstrap/status", response_model=BootstrapStatus)
def bootstrap_status(db: Session = Depends(get_session)) -> BootstrapStatus:
    settings = get_settings()
    configured = bool(db.scalar(select(func.count()).select_from(User)))
    return BootstrapStatus(
        configured=configured,
        mode=settings.mode,
        app_name=settings.app_name,
        host=settings.host,
        port=settings.port,
        service_url=service_url(
            settings.host,
            settings.port,
            tls_enabled=settings.tls_enabled,
        ),
        lan_candidates=discover_lan_addresses(),
    )


@router.post("/bootstrap/host", response_model=UserOut, status_code=201)
def bootstrap_host(
    payload: BootstrapHostRequest,
    request: Request,
    db: Session = Depends(get_session),
) -> User:
    if not bootstrap_request_is_trusted(request):
        raise ProblemException(
            403,
            "BOOTSTRAP_TRUST_REQUIRED",
            "首次配置请求未经 PartyOps 向导确认",
            "请从主机桌面打开党建智办配置向导，或使用主机自身页面完成管理员创建。",
        )
    password_hash = hash_password(payload.password)
    # 正式部署只运行一个 Uvicorn 进程；应用级写锁把“检查并创建”收敛为
    # 一个临界区，避免并发请求同时创建多个首任管理员。
    with db_runtime.write_lock:
        if db.scalar(select(func.count()).select_from(User)):
            raise ProblemException(409, "ALREADY_CONFIGURED", "系统已完成配置", "请直接登录。")
        user = User(
            username=payload.username.lower(),
            display_name=payload.display_name.strip(),
            password_hash=password_hash,
            role=UserRole.ADMIN,
        )
        db.add(user)
        db.flush()
        write_audit(
            db,
            user,
            "bootstrap.host",
            "system",
            None,
            {"username": user.username},
            client_ip(request),
        )
        db.commit()
        db.refresh(user)
    settings = get_settings()
    if settings.seed_demo and settings.environment != "production":
        seed_demo_data(db, user)
    else:
        seed_templates(db, user)
    return user


@router.post("/auth/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_session),
) -> User:
    settings = get_settings()
    address = client_ip(request)
    retry_after = login_throttle.retry_after(payload.username, address, settings=settings)
    if retry_after:
        raise ProblemException(
            429,
            "LOGIN_THROTTLED",
            "登录尝试过于频繁",
            f"请等待约 {retry_after} 秒后再试。",
            extra={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )
    user = db.scalar(select(User).where(User.username == payload.username.lower()))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        retry_after = login_throttle.record_failure(
            payload.username,
            address,
            settings=settings,
        )
        if retry_after:
            raise ProblemException(
                429,
                "LOGIN_THROTTLED",
                "登录尝试过于频繁",
                f"请等待约 {retry_after} 秒后再试。",
                extra={"retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        raise ProblemException(401, "LOGIN_FAILED", "登录失败", "用户名或密码不正确。")
    login_throttle.record_success(payload.username)
    token, csrf_token, session = issue_session(db, user)
    write_audit(
        db,
        user,
        "auth.login",
        "session",
        session.id,
        {},
        client_ip(request),
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.tls_enabled,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.session_hours * 3600,
        httponly=False,
        samesite="strict",
        secure=settings.tls_enabled,
        path="/",
    )
    return user


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = db.scalar(
            select(LoginSession).where(LoginSession.token_hash == hash_token(token))
        )
        if session:
            session.revoked_at = utcnow()
            write_audit(
                db, user, "auth.logout", "session", session.id, {}, client_ip(request)
            )
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=typing.List[UserOut])
def active_users(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[User]:
    """协同分工所需的最小用户目录，不暴露密码或会话。"""

    return list(
        db.scalars(
            select(User).where(User.active.is_(True)).order_by(User.display_name)
        ).all()
    )
