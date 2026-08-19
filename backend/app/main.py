"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import errno
import os
import json
import logging
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import ENCODERS_BY_TYPE
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_settings
from .database import db_runtime, get_session
from .device_versions import (
    DEVICE_CONTEXT_COOKIE,
    device_version_state,
    ensure_current_release,
    ensure_device_context_secret,
    issue_device_context_token,
    request_device,
    verify_device_context_token,
)
from .problems import install_problem_handlers
from .routers import admin, ai, archives, auth, bootstrap, events, fleet, integration, operations, party_development, productivity, support, tasks, updates, workspace
from .scheduler import scheduler_loop
from .seed import seed_templates
from .models import User, utcnow
from .enums import UserRole
from .networking import discover_lan_addresses, validate_bind_host, validate_transport_security
from .schemas import serialize_api_datetime
from sqlalchemy import select
from .upgrades import (
    create_pre_upgrade_backup,
    record_upgrade,
    restore_database_from_upgrade_backup,
    upgrade_required,
)
from .startup_diagnostics import classify_database_startup_error


settings = get_settings()

_lan_address_cache: tuple[float, tuple[str, ...]] = (0.0, ())
_lan_address_cache_lock = threading.Lock()


def cached_lan_addresses(ttl_seconds: float = 30.0) -> tuple[str, ...]:
    """缓存昂贵的网卡枚举；安全 Host 校验最多延迟一个短 TTL 感知变化。"""

    global _lan_address_cache
    now = time.monotonic()
    cached_at, values = _lan_address_cache
    if now - cached_at < ttl_seconds:
        return values
    with _lan_address_cache_lock:
        cached_at, values = _lan_address_cache
        if now - cached_at >= ttl_seconds:
            values = tuple(discover_lan_addresses())
            _lan_address_cache = (now, values)
    return values


class DataDirectoryInstanceLock:
    """阻止两个 PartyOps 运行时同时操作同一业务数据根。"""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / ".partyops-instance.lock"
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            if getattr(exc, "errno", None) in {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
            } or os.name == "nt":
                raise RuntimeError(
                    "[INSTANCE_ALREADY_RUNNING] 当前数据目录已有 PartyOps 在运行；"
                    "请先退出旧模式后重试。"
                ) from exc
            raise
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self.handle = handle

    def release(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __del__(self) -> None:
        try:
            self.release()
        except OSError:
            pass

# FastAPI 对显式 Pydantic 响应会使用 schemas.BaseModel 的统一序列化器，
# 但少量聚合接口返回普通字典。为这类响应登记同一 datetime 编码规则，
# 保证历史 SQLite 无时区 UTC 也始终输出 RFC 3339 的 ``Z`` 后缀。
ENCODERS_BY_TYPE[datetime] = serialize_api_datetime


class JsonLogFormatter(logging.Formatter):
    """把日志编码为单行 JSON，避免输入中的引号或换行破坏诊断文件。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created).astimezone().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def normalize_trace_id(value: str | None) -> str:
    """只接受规范 UUID 追踪号，其他客户端输入一律换成本机生成值。"""

    if value:
        try:
            parsed = uuid.UUID(value)
            if str(parsed) == value.lower():
                return str(parsed)
        except (ValueError, AttributeError):
            pass
    return str(uuid.uuid4())


def configure_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / "partyops.log"
    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=190,
        utc=True,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    target = str(log_path.resolve()).lower()
    if not any(
        str(getattr(current, "baseFilename", "")).lower() == target
        for current in root.handlers
    ):
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def _initialize_runtime() -> dict[str, object]:
    """执行可失败的同步启动阶段；调用方负责释放实例锁。"""

    configure_logging()
    validate_bind_host(
        settings.network_bind_host,
        settings.environment == "production",
        advertised_host=settings.network_advertise_host,
    )
    validate_transport_security(
        host=settings.network_advertise_host,
        production=settings.environment == "production",
        tls_enabled=settings.tls_enabled,
    )
    needs_upgrade, from_revision = upgrade_required()
    upgrade_backup = create_pre_upgrade_backup() if needs_upgrade else None
    try:
        db_runtime.create_schema()
    except Exception as exc:
        code, public_detail = classify_database_startup_error(exc)
        if upgrade_backup:
            restore_database_from_upgrade_backup(upgrade_backup)
            try:
                record_upgrade(
                    from_revision,
                    upgrade_backup,
                    status="rolled_back",
                    message=f"{code}: {public_detail}",
                )
            except Exception:
                logging.getLogger("partyops").exception("upgrade_rollback_record_failed")
        logging.getLogger("partyops").exception(
            "schema_upgrade_failed code=%s from_revision=%s", code, from_revision
        )
        if os.name == "nt":
            try:
                from .windows_host_status import write_service_status

                write_service_status(
                    settings.data_dir,
                    stage="schema_failed",
                    code=code,
                    detail=public_detail,
                )
            except OSError:
                logging.getLogger("partyops").exception("startup_status_write_failed")
        raise RuntimeError(f"[{code}] {public_detail}") from exc
    if upgrade_backup:
        record_upgrade(from_revision, upgrade_backup, status="completed")
    capabilities = db_runtime.validate_capabilities()
    with db_runtime.session_factory() as db:
        admin = db.scalar(
            select(User).where(User.role == UserRole.ADMIN, User.active.is_(True))
        )
        if admin:
            seed_templates(db, admin)
        ensure_device_context_secret(db)
        ensure_current_release(db)
        db.commit()
    logging.getLogger("partyops").info("database_ready %s", capabilities)
    return capabilities


@asynccontextmanager
async def lifespan(_app: FastAPI):
    instance_lock = DataDirectoryInstanceLock(settings.data_dir)
    instance_lock.acquire()
    scheduler: asyncio.Task | None = None
    stop_event: asyncio.Event | None = None
    try:
        _initialize_runtime()
        stop_event = asyncio.Event()
        scheduler = asyncio.create_task(scheduler_loop(stop_event))
    except BaseException:
        db_runtime.dispose()
        instance_lock.release()
        raise
    try:
        yield
    finally:
        assert stop_event is not None and scheduler is not None
        stop_event.set()
        try:
            await scheduler
        finally:
            db_runtime.dispose()
            instance_lock.release()


app = FastAPI(
    title="党建智办 API",
    version=__version__,
    docs_url="/api/docs" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_problem_handlers(app)


def _apply_security_headers(response, request: Request):
    """为正常响应和中间件提前拒绝响应统一增加浏览器安全边界。"""

    response.headers["X-Trace-Id"] = request.state.trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if "Referrer-Policy" not in response.headers:
        response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:"
    )
    if settings.tls_enabled:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = (
            "no-store" if request.url.path.startswith("/api/") else "no-cache"
        )
    return response


def _origin_allowed(request: Request) -> bool:
    """Cookie 鉴权写请求必须来自本服务或显式允许的前端来源。"""

    origin = request.headers.get("Origin", "").strip()
    if not origin:
        origin = request.headers.get("Referer", "").strip()
        if not origin:
            # 测试夹具和显式非生产调试客户端可以省略来源；生产浏览器请求
            # 必须携带 Origin/Referer，CLI 则应使用 Bearer 或专用设备令牌。
            return settings.environment != "production"
        allow_path = True
    else:
        allow_path = False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or (not allow_path and parsed.path not in {"", "/"})
        or parsed.query
        or parsed.fragment
    ):
        return False
    normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    request_origin = f"{request.url.scheme.lower()}://{request.url.netloc.lower()}"
    configured = {value.rstrip("/").lower() for value in settings.origins()}
    return normalized == request_origin or normalized in configured


def _host_allowed(request: Request) -> bool:
    """生产环境拒绝 DNS 重绑定和伪造 Host，只接受本机真实服务地址。"""

    if settings.environment != "production":
        return True
    hostname = (request.url.hostname or "").strip().rstrip(".").lower()
    allowed = {"127.0.0.1", "::1", "localhost"}
    for value in (
        settings.host,
        settings.network_bind_host,
        settings.network_advertise_host,
        *cached_lan_addresses(),
    ):
        candidate = str(value).strip().rstrip(".").lower()
        if candidate and candidate not in {"0.0.0.0", "::"}:  # nosec B104 - 这里只比较受信主机名，未创建监听套接字。
            allowed.add(candidate)
    return hostname in allowed


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    request.state.trace_id = normalize_trace_id(request.headers.get("X-Trace-Id"))
    if not _host_allowed(request):
        return _apply_security_headers(
            JSONResponse(
                status_code=421,
                content={
                    "type": "https://partyops.local/problems/HOST_DENIED",
                    "title": "请求主机地址被拒绝",
                    "detail": "请使用 PartyOps 配置向导显示的本机或局域网地址访问。",
                    "code": "HOST_DENIED",
                    "trace_id": request.state.trace_id,
                },
                media_type="application/problem+json",
            ),
            request,
        )
    authorization = request.headers.get("Authorization", "").strip().lower()
    non_browser_write = (
        authorization.startswith("bearer ")
        or bool(request.headers.get("X-PartyOps-Device-Token"))
        or (
            request.url.path == "/api/v1/bootstrap/host"
            and bool(request.headers.get("X-PartyOps-Bootstrap-Token"))
        )
    )
    if (
        request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and not non_browser_write
        and not _origin_allowed(request)
    ):
        return _apply_security_headers(
            JSONResponse(
                status_code=403,
                content={
                    "type": "https://partyops.local/problems/ORIGIN_DENIED",
                    "title": "请求来源被拒绝",
                    "detail": "浏览器写请求必须来自当前 PartyOps 服务；本机工具需使用专用安全令牌。",
                    "code": "ORIGIN_DENIED",
                    "trace_id": request.state.trace_id,
                },
                media_type="application/problem+json",
            ),
            request,
        )
    # 设备令牌只允许走独立的双向 TLS Agent 端口；浏览器主端口即使在同一
    # 局域网内也不能用复制的令牌冒充终端。
    if (
        settings.environment == "production"
        and request.headers.get("X-PartyOps-Device-Token")
        and (not settings.tls_enabled or request.url.port != settings.agent_port)
    ):
        return _apply_security_headers(
            JSONResponse(
                status_code=403,
                content={
                    "type": "https://partyops.local/problems/AGENT_MTLS_REQUIRED",
                    "title": "需要设备双向 TLS 通道",
                    "detail": "终端接口必须通过 Agent 双向 TLS 安全端口访问。",
                    "code": "AGENT_MTLS_REQUIRED",
                    "trace_id": request.state.trace_id,
                },
                media_type="application/problem+json",
            ),
            request,
        )
    if request.url.path.startswith("/api/v1/") and not request.url.path.startswith(
        (
            "/api/v1/health",
            "/api/v1/bootstrap/",
            "/api/v1/auth/",
            "/api/v1/device/",
            "/api/v1/devices/",
        )
    ):
        # 协同电脑版本低于主机时，服务端同时阻止业务 API。前端路由门禁
        # 负责给出更新说明；此处避免手工输入 URL 绕过版本一致性要求。
        with db_runtime.session_factory() as gate_db:
            # 仅版本门禁允许旧客户端按唯一 IP 识别；业务授权始终要求签名
            # 设备上下文，避免同一 NAT/IP 下的浏览器继承设备权限。
            device = request_device(request, gate_db, allow_ip_fallback=True)
            if device:
                state = device_version_state(device)
                if state != "current":
                    status_code = 403 if state in {"revoked", "quarantined"} else 426
                    return _apply_security_headers(
                        JSONResponse(
                            status_code=status_code,
                            content={
                                "type": "https://partyops.local/problems/DEVICE_UPDATE_REQUIRED",
                                "title": "协同电脑需要更新",
                                "detail": "当前协同电脑版本与主机不一致，请完成更新后再进入业务系统。",
                                "code": "DEVICE_UPDATE_REQUIRED",
                                "trace_id": request.state.trace_id,
                                "device_id": device.id,
                                "current_version": device.app_version or "未上报",
                                "target_version": settings.app_version,
                                "state": state,
                            },
                            media_type="application/problem+json",
                        ),
                        request,
                    )
    response = await call_next(request)
    return _apply_security_headers(response, request)


api_prefix = "/api/v1"
app.include_router(auth.router, prefix=api_prefix)
app.include_router(tasks.router, prefix=api_prefix)
app.include_router(support.router, prefix=api_prefix)
app.include_router(admin.router, prefix=api_prefix)
app.include_router(events.router, prefix=api_prefix)
app.include_router(operations.router, prefix=api_prefix)
app.include_router(workspace.router, prefix=api_prefix)
app.include_router(archives.router, prefix=api_prefix)
app.include_router(ai.router, prefix=api_prefix)
app.include_router(fleet.router, prefix=api_prefix)
app.include_router(productivity.router, prefix=api_prefix)
app.include_router(party_development.router, prefix=api_prefix)
app.include_router(updates.router, prefix=api_prefix)
app.include_router(bootstrap.router, prefix=api_prefix)
app.include_router(integration.router, prefix=api_prefix)


@app.get("/device-launch", include_in_schema=False)
def device_launch(
    token: str = Query(min_length=40, max_length=2_048),
    db=Depends(get_session),
) -> RedirectResponse:
    device = verify_device_context_token(db, token, purpose="launch")
    if not device:
        response = RedirectResponse(url="/?device_context_error=1", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    context_token, expires_at = issue_device_context_token(db, device)
    db.commit()
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        DEVICE_CONTEXT_COOKIE,
        context_token,
        max_age=max(1, int((expires_at - utcnow()).total_seconds())),
        httponly=True,
        secure=settings.tls_enabled,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def frontend_directory() -> Path:
    if settings.frontend_dist:
        return settings.frontend_dist.resolve()
    if getattr(sys, "frozen", False):
        bundle_root = Path(
            getattr(sys, "_MEIPASS", Path(sys.executable).parent)
        )
        return (bundle_root / "frontend").resolve()
    return (Path(__file__).resolve().parents[2] / "frontend" / "dist" / "client").resolve()


frontend_dist = frontend_directory()
if frontend_dist.exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        candidate = (frontend_dist / full_path).resolve()
        if (
            full_path
            and frontend_dist in candidate.parents
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")


def run() -> None:
    import uvicorn

    uvicorn_options = {
        "host": settings.network_bind_host,
        "port": settings.port,
        "loop": "asyncio",
        "http": "h11",
        "ws": "none",
        "workers": 1,
        "reload": False,
        # 业务请求已经进入按日轮转的 partyops.log；关闭 Uvicorn 每请求
        # access log，避免个人模式的 launcher.log 随访问量无上限增长。
        "access_log": False,
        # SSE、下载或旧浏览器连接不能无限阻塞软件升级。收到 SIGTERM 后
        # 最多等待 15 秒完成请求，随后由 Uvicorn 取消残留连接并执行 lifespan
        # 清理；安装器仍会在覆盖程序前复核进程身份和退出状态。
        "timeout_graceful_shutdown": 15,
    }
    if settings.tls_enabled:
        from .pki import ensure_tls_material

        ensure_tls_material(settings)
        if not settings.tls_cert_file or not settings.tls_key_file:
            raise RuntimeError("已启用 HTTPS，但未配置证书和私钥。")
        uvicorn_options.update(
            {
                "ssl_certfile": str(settings.tls_cert_file),
                "ssl_keyfile": str(settings.tls_key_file),
                "ssl_ca_certs": str(settings.tls_client_ca_file)
                if settings.tls_client_ca_file
                else None,
                "ssl_cert_reqs": 2 if settings.tls_require_client_cert else 0,
            }
        )
        # Agent 使用独立端口并强制客户端证书；浏览器主端口不要求客户端证书。
        if settings.agent_port != settings.port:
            agent_options = {
                **uvicorn_options,
                "port": settings.agent_port,
                "ssl_cert_reqs": 2,
                # 主端口负责数据库、调度器和后台任务初始化；Agent 端口只复用
                # 已初始化的 ASGI 应用，避免同一进程重复启动两个调度器。
                "lifespan": "off",
            }
            agent_server = uvicorn.Server(uvicorn.Config("app.main:app", **agent_options))
            agent_thread = threading.Thread(
                target=agent_server.run,
                name="partyops-agent-tls",
                daemon=True,
            )
            agent_thread.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if agent_server.started:
                    break
                if not agent_thread.is_alive():
                    raise RuntimeError(
                        f"设备安全端口启动失败：{settings.agent_port}。"
                        "请检查主机启动日志、端口占用和内部 CA 文件权限。"
                    )
                time.sleep(0.1)
            else:
                agent_server.should_exit = True
                agent_thread.join(timeout=2)
                raise RuntimeError(
                    f"设备安全端口启动超时：{settings.agent_port}。"
                    "为避免主机处于仅业务端口在线的半启动状态，服务已停止。"
                )
    uvicorn.run("app.main:app", **uvicorn_options)


if __name__ == "__main__":
    run()
