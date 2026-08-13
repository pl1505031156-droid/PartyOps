"""Windows 主机服务与首次配置向导共享的启动状态协议。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            stream.seek(max(0, size - max_bytes))
            return stream.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""
