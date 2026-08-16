"""Windows 主机服务与首次配置向导共享的启动状态协议。"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .startup_diagnostics import (
    DATABASE_CORRUPT,
    DATABASE_IO_FAILED,
    DATABASE_LOCKED,
    DATABASE_SCHEMA_FAILED,
    DATABASE_STARTUP_FAILED,
    DATA_DIR_FULL,
)


SERVICE_MISSING = "SERVICE_MISSING"
SERVICE_STOPPED = "SERVICE_STOPPED"
CHILD_EXITED = "CHILD_EXITED"
PORT_IN_USE = "PORT_IN_USE"
DATA_DIR_DENIED = "DATA_DIR_DENIED"
TLS_INIT_FAILED = "TLS_INIT_FAILED"
HEALTH_TIMEOUT = "HEALTH_TIMEOUT"

TERMINAL_CODES = {
    CHILD_EXITED,
    PORT_IN_USE,
    DATA_DIR_DENIED,
    TLS_INIT_FAILED,
    DATABASE_LOCKED,
    DATABASE_CORRUPT,
    DATABASE_SCHEMA_FAILED,
    DATABASE_IO_FAILED,
    DATA_DIR_FULL,
    DATABASE_STARTUP_FAILED,
}


def service_log_path(data_dir: Path) -> Path:
    """返回用户可直接提交的主机服务日志位置。"""

    return data_dir / "logs" / "partyops-host-service.log"


def service_status_path(data_dir: Path) -> Path:
    """返回服务与向导之间的原子状态文件位置。"""

    return data_dir / "logs" / "partyops-host-status.json"


def write_service_status(
    data_dir: Path,
    *,
    stage: str,
    code: str = "",
    detail: str = "",
    pid: int | None = None,
    exit_code: int | None = None,
) -> Path:
    """原子写入不含密钥的服务状态，供向导快速失败与复制诊断。"""

    path = service_status_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "format_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "code": code,
        "detail": detail[-2000:],
    }
    if pid is not None:
        payload["pid"] = pid
    if exit_code is not None:
        payload["exit_code"] = exit_code
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_service_status(data_dir: Path) -> dict[str, Any] | None:
    """读取状态文件；半写、旧格式或损坏内容视为暂无状态。"""

    try:
        payload = json.loads(service_status_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        return None
    return payload


def tail_service_log(data_dir: Path, *, max_bytes: int = 8192) -> str:
    """读取日志尾部并容忍冻结进程正在追加内容。"""

    try:
        path = service_log_path(data_dir)
        size = path.stat().st_size
        with path.open("rb") as stream:
            offset = max(0, size - max_bytes)
            stream.seek(offset)
            text = stream.read(max_bytes).decode("utf-8", errors="replace")
        # 从文件中部读取时丢弃第一条残行，避免 UTF-8 多字节字符被切开后
        # 在向导中显示成乱码；完整原始日志不受影响。
        if offset and "\n" in text:
            text = text.split("\n", 1)[1]
        return text
    except OSError:
        return ""


def probe_loopback_health(
    port: int,
    *,
    tls: bool,
    timeout: float = 3.0,
    ca_file: Path | None = None,
    expected_version: str | None = None,
) -> tuple[bool, str]:
    """探测本机 PartyOps 健康接口，供 Windows 监督服务持续上报阶段。

    这里只访问固定的 127.0.0.1 地址；TLS 证书由 PartyOps 内部 CA 签发，
    服务进程尚未把 CA 安装到系统信任区时也必须能够完成本机就绪探测。
    """

    if not 1024 <= port <= 65534:
        return False, "主机服务端口超出允许范围"
    scheme = "https" if tls else "http"
    if tls and ca_file is not None:
        if not ca_file.is_file():
            return False, "PartyOps 内部 CA 尚未生成"
        try:
            # 即使目标固定为回环地址，也校验 PartyOps 自己的 CA，避免其他本机
            # 进程抢占端口后伪造健康响应。
            context = ssl.create_default_context(cafile=str(ca_file.resolve()))
        except (OSError, ssl.SSLError, ValueError) as exc:
            return False, f"PartyOps 内部 CA 无法读取：{exc}"
    elif tls:
        return False, "TLS 健康检查缺少 PartyOps 内部 CA"
    else:
        context = None
    request = urllib.request.Request(f"{scheme}://127.0.0.1:{port}/api/v1/health")
    try:
        with urllib.request.urlopen(  # nosec B310 - URL 固定为本机回环地址与健康路径。
            request,
            timeout=max(0.5, min(timeout, 5.0)),
            context=context,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if health_payload_ready(payload, expected_version=expected_version):
            return True, ""
        return False, "健康检查返回内容无效"
    except (
        ConnectionResetError,
        ssl.SSLError,
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        return False, str(exc)[-2000:]


def health_payload_ready(
    payload: object,
    *,
    expected_version: str | None = None,
    expected_mode: str = "host",
) -> bool:
    """只有完整且模式匹配的 PartyOps 健康契约才能标记进程就绪。"""

    if not isinstance(payload, dict) or expected_mode not in {"host", "personal"}:
        return False
    sqlite_info = payload.get("sqlite")
    version = str(payload.get("app_version") or "").strip()
    return bool(
        payload.get("status") == "ok"
        and payload.get("mode") == expected_mode
        and isinstance(sqlite_info, dict)
        and sqlite_info.get("safe_version") is True
        and sqlite_info.get("fts5") is True
        and version
        and (expected_version is None or version == expected_version)
    )
