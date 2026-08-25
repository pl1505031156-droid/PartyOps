"""公文排版的常驻本机回环服务与短时设备票据。

浏览器只把文档发送到当前电脑的 ``127.0.0.1``。PartyOps 主机 API
仅签发不含文件信息的短时票据；本模块不把文件名、路径、正文或哈希写入
日志、数据库或协同链路。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from lxml import etree

from .official_format import (
    IDLE_TIMEOUT_SECONDS,
    VERSION,
    LocalDocument,
    OfficialFormatError,
    _append_stage_log,
    _extract_upload,
    _private_write,
    _safe_stem,
    diagnose_docx,
    format_docx,
    prepare_docx,
)

LOCAL_FORMAT_PORT = 18768
TICKET_TTL_SECONDS = 120


def _b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _b64decode(payload: str) -> bytes:
    return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))


def normalize_origin(value: str) -> str:
    """只接受无凭据、无路径的 HTTP(S) 页面来源。"""

    if len(value) > 512 or any(character in value for character in ("\r", "\n", "\x00")):
        raise ValueError("页面来源无效")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("页面来源无效")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def issue_local_format_ticket(
    secret: str,
    *,
    origin: str,
    user_id: str,
    device_id: str,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """签发两分钟内、来源及设备绑定的一次性启动票据。"""

    if len(secret) < 32:
        raise ValueError("本机排版票据密钥未就绪")
    normalized_origin = normalize_origin(origin)
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=TICKET_TTL_SECONDS)
    payload = {
        "device_id": device_id,
        "expires": int(expires_at.timestamp()),
        "nonce": secrets.token_urlsafe(18),
        "origin": normalized_origin,
        "purpose": "official-format",
        "user_id": user_id,
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    signature = _b64encode(
        hmac.new(secret.encode("ascii"), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}", expires_at


def verify_local_format_ticket(
    secret: str,
    ticket: str,
    *,
    origin: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """验证票据并返回最小化声明；错误不会回显敏感内容。"""

    try:
        encoded, signature = ticket.split(".", 1)
        expected = _b64encode(
            hmac.new(secret.encode("ascii"), encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise ValueError("签名不匹配")
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        if payload.get("purpose") != "official-format":
            raise ValueError("用途不匹配")
        if int(payload.get("expires", 0)) <= int((now or datetime.now(UTC)).timestamp()):
            raise ValueError("票据已过期")
        if not hmac.compare_digest(str(payload.get("origin", "")), normalize_origin(origin)):
            raise ValueError("来源不匹配")
        nonce = str(payload.get("nonce", ""))
        if not 20 <= len(nonce) <= 64:
            raise ValueError("随机标识无效")
        return {
            "device_id": str(payload.get("device_id", "")),
            "nonce": nonce,
            "origin": str(payload["origin"]),
            "user_id": str(payload.get("user_id", "")),
        }
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OfficialFormatError(
            "LOCAL_TICKET_INVALID",
            "本机操作票据无效",
            "页面会话可能已过期，请重新点击开始排版。",
        ) from exc


@dataclass
class LocalFormatSession:
    id: str
    token_hash: str
    origin: str
    workspace: Path
    last_activity: float = field(default_factory=time.monotonic)
    documents: dict[str, LocalDocument] = field(default_factory=dict)
    plain_token: str = field(default="", repr=False)


class OfficialFormatLocalService:
    """固定回环端口上的无窗口排版服务。"""

    def __init__(
        self,
        *,
        secret: str,
        config_dir: Path,
        port: int = LOCAL_FORMAT_PORT,
        idle_timeout: int = IDLE_TIMEOUT_SECONDS,
    ) -> None:
        if len(secret) < 32:
            raise OfficialFormatError(
                "LOCAL_FORMAT_SECRET_INVALID",
                "本机排版服务未就绪",
                "设备凭据不完整，请重新打开 PartyOps 或重新授权协同电脑。",
            )
        self.secret = secret
        self.config_dir = config_dir
        self.port = int(port)
        self.idle_timeout = max(30, int(idle_timeout))
        self.sessions: dict[str, LocalFormatSession] = {}
        self.used_nonces: dict[str, float] = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.cleanup_thread: threading.Thread | None = None

    def start(self) -> OfficialFormatLocalService:
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PartyOpsLocalFormatter/2"

            def _origin(self) -> str:
                try:
                    return normalize_origin(self.headers.get("Origin", ""))
                except ValueError:
                    return ""

            def _cors(self, origin: str) -> None:
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin, Access-Control-Request-Private-Network")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Authorization, Content-Type, X-PartyOps-Local-Token",
                )
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
                    self.send_header("Access-Control-Allow-Private-Network", "true")

            def _base_headers(self, origin: str, content_type: str, length: int) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
                self._cors(origin)

            def _send_json(self, status: int, payload: dict[str, Any], origin: str = "") -> None:
                body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                self.send_response(status)
                self._base_headers(origin, "application/json; charset=utf-8", len(body))
                self.end_headers()
                self.wfile.write(body)

            def _failure(self, error: OfficialFormatError, origin: str) -> None:
                self._send_json(
                    422,
                    {"code": error.code, "title": error.title, "detail": error.detail},
                    origin,
                )

            def _host_valid(self) -> bool:
                return self.headers.get("Host", "").lower() == f"127.0.0.1:{service.port}"

            def _session(self, session_id: str, origin: str) -> LocalFormatSession:
                token = self.headers.get("X-PartyOps-Local-Token", "")
                with service.lock:
                    current = service.sessions.get(session_id)
                    if (
                        current is None
                        or not origin
                        or not hmac.compare_digest(current.origin, origin)
                        or not hmac.compare_digest(
                            current.token_hash,
                            hashlib.sha256(token.encode("utf-8")).hexdigest(),
                        )
                    ):
                        raise OfficialFormatError(
                            "LOCAL_SESSION_INVALID",
                            "本机排版会话无效",
                            "本机服务已重启或会话已过期，请重新选择文件。",
                        )
                    current.last_activity = time.monotonic()
                    return current

            def do_OPTIONS(self) -> None:
                origin = self._origin()
                if not self._host_valid() or not origin:
                    self._send_json(403, {"code": "LOCAL_ORIGIN_DENIED"})
                    return
                self.send_response(204)
                self._base_headers(origin, "text/plain; charset=utf-8", 0)
                self.end_headers()

            def do_GET(self) -> None:
                origin = self._origin()
                path = urllib.parse.urlsplit(self.path).path
                if not self._host_valid():
                    self._send_json(403, {"code": "LOCAL_HOST_DENIED"}, origin)
                    return
                if path == "/health":
                    self._send_json(
                        200,
                        {"service": "official-format", "status": "ready", "version": VERSION},
                        origin,
                    )
                    return
                matched = re.fullmatch(
                    r"/v1/sessions/([0-9a-f]{32})/documents/([0-9a-f]{32})/download",
                    path,
                )
                if not matched:
                    self._send_json(404, {"code": "LOCAL_ROUTE_NOT_FOUND"}, origin)
                    return
                try:
                    session = self._session(matched.group(1), origin)
                    item = session.documents.get(matched.group(2))
                    if item is None or item.output is None or not item.output.is_file():
                        raise OfficialFormatError(
                            "FORMAT_RESULT_GONE",
                            "排版结果不可用",
                            "结果已清理或尚未生成，请重新执行排版。",
                        )
                    payload = item.output.read_bytes()
                    filename = urllib.parse.quote(f"{item.original_stem}-公文规范版.docx")
                    # 成品已完整读入受控内存后立即清理磁盘与会话，再发送响应；
                    # 即使浏览器在下载中途关闭，也不会留下临时副本。
                    _append_stage_log(service.config_dir, "download", session.last_activity, "OK")
                    service.remove_session(session.id)
                    self.send_response(200)
                    self._base_headers(
                        origin,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        len(payload),
                    )
                    self.send_header(
                        "Content-Disposition", f"attachment; filename*=UTF-8''{filename}"
                    )
                    self.end_headers()
                    self.wfile.write(payload)
                except OfficialFormatError as exc:
                    self._failure(exc, origin)

            def do_POST(self) -> None:
                origin = self._origin()
                path = urllib.parse.urlsplit(self.path).path
                if not self._host_valid() or not origin:
                    self._send_json(403, {"code": "LOCAL_ORIGIN_DENIED"}, origin)
                    return
                try:
                    if path == "/v1/sessions":
                        header = self.headers.get("Authorization", "")
                        if not header.startswith("Bearer "):
                            raise OfficialFormatError(
                                "LOCAL_TICKET_REQUIRED",
                                "缺少本机操作票据",
                                "请从 PartyOps 公文规范排版页面重新开始。",
                            )
                        claims = verify_local_format_ticket(
                            service.secret, header[7:].strip(), origin=origin
                        )
                        with service.lock:
                            if claims["nonce"] in service.used_nonces:
                                raise OfficialFormatError(
                                    "LOCAL_TICKET_USED",
                                    "本机操作票据已使用",
                                    "为防止重复请求，请重新点击开始排版。",
                                )
                            service.used_nonces[claims["nonce"]] = time.monotonic()
                        session = service.create_session(origin)
                        self._send_json(
                            201,
                            {
                                "expires_in_seconds": service.idle_timeout,
                                "session_id": session.id,
                                "session_token": service.session_token(session),
                            },
                            origin,
                        )
                        return

                    diagnose = re.fullmatch(r"/v1/sessions/([0-9a-f]{32})/diagnose", path)
                    if diagnose:
                        started = time.monotonic()
                        session = self._session(diagnose.group(1), origin)
                        filename, payload = _extract_upload(self)
                        document_id = uuid.uuid4().hex
                        source = session.workspace / (
                            document_id + Path(filename).suffix.lower()
                        )
                        _private_write(source, payload)
                        prepared, converted = prepare_docx(source, session.workspace)
                        report = diagnose_docx(prepared)
                        for old in tuple(session.documents):
                            service.remove_document(session, old)
                        session.documents[document_id] = LocalDocument(
                            prepared,
                            _safe_stem(filename),
                            converted,
                            report=report,
                        )
                        _append_stage_log(service.config_dir, "diagnose", started, "OK")
                        self._send_json(
                            200,
                            {
                                "converted": converted,
                                "document_id": document_id,
                                "report": report.as_dict(),
                            },
                            origin,
                        )
                        return

                    formatting = re.fullmatch(
                        r"/v1/sessions/([0-9a-f]{32})/documents/([0-9a-f]{32})/format",
                        path,
                    )
                    if formatting:
                        started = time.monotonic()
                        session = self._session(formatting.group(1), origin)
                        item = session.documents.get(formatting.group(2))
                        if item is None:
                            raise OfficialFormatError(
                                "FORMAT_DOCUMENT_GONE",
                                "待排版文档已清理",
                                "请重新选择文件并完成诊断。",
                            )
                        output = session.workspace / f"{formatting.group(2)}-formatted.docx"
                        report = format_docx(item.source, output)
                        item.output = output
                        item.report = report
                        _append_stage_log(service.config_dir, "format", started, "OK")
                        self._send_json(
                            200,
                            {"document_id": formatting.group(2), "report": report.as_dict()},
                            origin,
                        )
                        return
                    self._send_json(404, {"code": "LOCAL_ROUTE_NOT_FOUND"}, origin)
                except OfficialFormatError as exc:
                    _append_stage_log(service.config_dir, "process", time.monotonic(), exc.code)
                    self._failure(exc, origin)
                except (OSError, ValueError, etree.Error) as exc:
                    _append_stage_log(
                        service.config_dir,
                        "process",
                        time.monotonic(),
                        "FORMAT_PROCESS_FAILED",
                    )
                    self._failure(
                        OfficialFormatError(
                            "FORMAT_PROCESS_FAILED",
                            "本机排版未完成",
                            f"文档结构或本机办公套件返回异常：{type(exc).__name__}。原文件未改变。",
                        ),
                        origin,
                    )

            def do_DELETE(self) -> None:
                origin = self._origin()
                matched = re.fullmatch(
                    r"/v1/sessions/([0-9a-f]{32})", urllib.parse.urlsplit(self.path).path
                )
                if not self._host_valid() or not matched:
                    self._send_json(404, {"code": "LOCAL_ROUTE_NOT_FOUND"}, origin)
                    return
                try:
                    session = self._session(matched.group(1), origin)
                    service.remove_session(session.id)
                    self.send_response(204)
                    self._base_headers(origin, "text/plain; charset=utf-8", 0)
                    self.end_headers()
                except OfficialFormatError as exc:
                    self._failure(exc, origin)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                self.config_dir.chmod(0o700)
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
            # 测试和诊断允许传 0 由系统选择空闲端口；生产配置仍固定 18768。
            self.port = int(self.server.server_address[1])
        except OSError as exc:
            raise OfficialFormatError(
                "LOCAL_FORMAT_PORT_IN_USE",
                "本机排版服务端口不可用",
                f"回环端口 {self.port} 已被占用；请关闭旧版 PartyOps 后重新打开。",
            ) from exc
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="partyops-official-format",
            daemon=True,
        )
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="partyops-official-format-cleanup",
            daemon=True,
        )
        self.thread.start()
        self.cleanup_thread.start()
        _append_stage_log(self.config_dir, "service_start", time.monotonic(), "OK")
        return self

    def create_session(self, origin: str) -> LocalFormatSession:
        workspace = Path(tempfile.mkdtemp(prefix="partyops-official-format-"))
        if os.name != "nt":
            workspace.chmod(0o700)
        token = secrets.token_urlsafe(32)
        session = LocalFormatSession(
            id=uuid.uuid4().hex,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            origin=origin,
            workspace=workspace,
            plain_token=token,
        )
        with self.lock:
            self.sessions[session.id] = session
        return session

    @staticmethod
    def session_token(session: LocalFormatSession) -> str:
        token = session.plain_token
        if not token:
            raise RuntimeError("本机排版会话令牌已交付")
        session.plain_token = ""
        return token

    @staticmethod
    def remove_document(session: LocalFormatSession, document_id: str) -> None:
        item = session.documents.pop(document_id, None)
        if item is None:
            return
        for path in (item.source, item.output):
            if path is not None and path.is_file() and session.workspace in path.parents:
                path.unlink(missing_ok=True)

    def remove_session(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.pop(session_id, None)
        if session is None:
            return
        for document_id in tuple(session.documents):
            self.remove_document(session, document_id)
        shutil.rmtree(session.workspace, ignore_errors=True)

    def _cleanup_loop(self) -> None:
        while not self.stop_event.wait(5):
            now = time.monotonic()
            with self.lock:
                expired = [
                    session_id
                    for session_id, session in self.sessions.items()
                    if now - session.last_activity >= self.idle_timeout
                ]
                self.used_nonces = {
                    nonce: used_at
                    for nonce, used_at in self.used_nonces.items()
                    if now - used_at < max(self.idle_timeout, TICKET_TTL_SECONDS)
                }
            for session_id in expired:
                self.remove_session(session_id)

    def close(self) -> None:
        self.stop_event.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        for session_id in tuple(self.sessions):
            self.remove_session(session_id)
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.cleanup_thread is not None:
            self.cleanup_thread.join(timeout=2)
        _append_stage_log(self.config_dir, "service_stop", time.monotonic(), "OK")


__all__ = [
    "LOCAL_FORMAT_PORT",
    "OfficialFormatLocalService",
    "issue_local_format_ticket",
    "normalize_origin",
    "verify_local_format_ticket",
]
