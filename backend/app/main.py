"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
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
    request_device,
    verify_device_context_token,
)
from .problems import install_problem_handlers
from .routers import admin, ai, archives, auth, bootstrap, events, fleet, integration, operations, productivity, support, tasks, updates, workspace
from .scheduler import scheduler_loop
from .seed import seed_templates
from .models import User
from .enums import UserRole
from .networking import validate_bind_host, validate_transport_security
from .schemas import serialize_api_datetime
from sqlalchemy import select
from .upgrades import (
    create_pre_upgrade_backup,
    record_upgrade,
    restore_database_from_upgrade_backup,
    upgrade_required,
)


settings = get_settings()

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    validate_bind_host(settings.host, settings.environment == "production")
    validate_transport_security(
        host=settings.host,
        production=settings.environment == "production",
        tls_enabled=settings.tls_enabled,
    )
    needs_upgrade, from_revision = upgrade_required()
    upgrade_backup = create_pre_upgrade_backup() if needs_upgrade else None
    try:
        db_runtime.create_schema()
    except Exception as exc:
        if upgrade_backup:
            restore_database_from_upgrade_backup(upgrade_backup)
        logging.getLogger("partyops").exception(
            "schema_upgrade_failed from_revision=%s", from_revision
        )
        raise RuntimeError("数据库升级失败，已恢复升级前数据。") from exc
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
    stop_event = asyncio.Event()
    scheduler = asyncio.create_task(scheduler_loop(stop_event))
    yield
    stop_event.set()
    await scheduler
    db_runtime.dispose()


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


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    request.state.trace_id = normalize_trace_id(request.headers.get("X-Trace-Id"))
    # 设备令牌只允许走独立的双向 TLS Agent 端口；浏览器主端口即使在同一
    # 局域网内也不能用复制的令牌冒充终端。
    if (
        settings.tls_enabled
        and request.headers.get("X-PartyOps-Device-Token")
        and request.url.port != settings.agent_port
    ):
        return JSONResponse(
            status_code=403,
            content={
                "type": "https://partyops.local/problems/AGENT_MTLS_REQUIRED",
                "title": "需要设备双向 TLS 通道",
                "detail": "终端接口必须通过 Agent 安全端口访问。",
                "code": "AGENT_MTLS_REQUIRED",
                "trace_id": request.state.trace_id,
            },
            media_type="application/problem+json",
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
                    return JSONResponse(
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
                    )
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = (
        "no-store" if request.url.path.startswith("/api/") else "no-cache"
    )
    return response


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
app.include_router(updates.router, prefix=api_prefix)
app.include_router(bootstrap.router, prefix=api_prefix)
app.include_router(integration.router, prefix=api_prefix)


@app.get("/device-launch", include_in_schema=False)
def device_launch(
    token: str = Query(min_length=40, max_length=2_048),
    db=Depends(get_session),
) -> RedirectResponse:
    device = verify_device_context_token(db, token)
    if not device:
        return RedirectResponse(url="/?device_context_error=1", status_code=303)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        DEVICE_CONTEXT_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=settings.tls_enabled,
        samesite="strict",
        path="/",
    )
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
        "host": settings.host,
        "port": settings.port,
        "loop": "asyncio",
        "http": "h11",
        "ws": "none",
        "workers": 1,
        "reload": False,
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
