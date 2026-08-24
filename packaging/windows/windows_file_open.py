"""partyops-file:// Windows 协议处理器，只通过回环服务打开一次性授权文件。"""

from __future__ import annotations

import ctypes
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.setup_wizard import load_host_environment


ERROR_MESSAGES = {
    "OPEN_GRANT_EXPIRED": "文件打开授权已过期，请回到原始文件中心重试。",
    "OPEN_GRANT_ALREADY_USED": "该打开授权已经使用，请重新点击打开。",
    "OPEN_GRANT_REVOKED": "文件权限已经变化，请重新选择文件。",
    "WORKSPACE_FILE_UNAVAILABLE": "原文件已移动、删除或不再授权。",
    "CERTIFICATE_FAILED": "PartyOps 内部证书校验失败，请重新打开配置向导修复证书。",
    "HOST_UNREACHABLE": "PartyOps 本机服务未启动，请重新双击桌面图标。",
    "HOST_TIMEOUT": "PartyOps 本机服务响应超时，请在运行诊断中检查服务状态。",
    "DEFAULT_APP_FAILED": "系统默认程序未能打开文件，请检查文件类型关联。",
    "HELPER_FAILED": "本机文件助手未能完成打开，请查看运行诊断。",
}


class OpenFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(ERROR_MESSAGES.get(code, ERROR_MESSAGES["HELPER_FAILED"]))


def _log_result(code: str) -> Path:
    root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "file-open-helper.log"
    try:
        if path.is_file() and path.stat().st_size > 512 * 1024:
            path.replace(path.with_suffix(".log.1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} code={code}\n")
    except OSError:
        pass
    return path


def _show_failure(failure: OpenFailure) -> None:
    log_path = _log_result(failure.code)
    message = f"[{failure.code}] {failure}\n\n诊断日志：{log_path}"
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "党建智办文件打开失败", 0x10)
    except (AttributeError, OSError):
        print(message, file=sys.stderr)


def _problem_code(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read(16 * 1024).decode("utf-8"))
        code = str(payload.get("code", ""))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        code = ""
    return code if code in ERROR_MESSAGES else "HELPER_FAILED"


def _completion(
    base_url: str,
    token: str,
    context: ssl.SSLContext | None,
    result_code: str,
) -> None:
    payload = json.dumps({"result_code": result_code, "detail": ""}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/v1/workspace/open-tokens/{urllib.parse.quote(token)}/complete",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(  # nosec B310 - base_url 固定为回环 HTTP(S)。
            request, timeout=5, context=context
        ):
            return
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return


def _load_runtime() -> tuple[str, ssl.SSLContext | None]:
    control_root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
    config = load_host_environment(control_root / "partyops.env")
    data_root = Path(config.get("PARTYOPS_DATA_DIR", str(control_root)))
    port = config.get("PARTYOPS_PORT", "18765")
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise OpenFailure("HELPER_FAILED")
    scheme = "https" if config.get("PARTYOPS_TLS_ENABLED", "").lower() == "true" else "http"
    context = None
    if scheme == "https":
        ca = data_root / "secrets" / "pki" / "ca.pem"
        if not ca.is_file():
            raise OpenFailure("CERTIFICATE_FAILED")
        try:
            context = ssl.create_default_context(cafile=str(ca))
        except (OSError, ssl.SSLError) as exc:
            raise OpenFailure("CERTIFICATE_FAILED") from exc
    return f"{scheme}://127.0.0.1:{port}", context


def _parse_token(argument: str) -> str:
    parsed = urllib.parse.urlparse(argument)
    token = (parsed.path or parsed.netloc).strip("/")
    if (
        parsed.scheme != "partyops-file"
        or not 32 <= len(token) <= 128
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for char in token
        )
    ):
        raise OpenFailure("HELPER_FAILED")
    return token


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise OpenFailure("HELPER_FAILED")
        token = _parse_token(sys.argv[1])
        base_url, context = _load_runtime()
        request = urllib.request.Request(
            f"{base_url}/api/v1/workspace/open-tokens/{urllib.parse.quote(token)}"
        )
        try:
            with urllib.request.urlopen(  # nosec B310 - base_url 固定为回环 HTTP(S)。
                request, timeout=10, context=context
            ) as response:
                target_text = response.read(32 * 1024).decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise OpenFailure(_problem_code(exc)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
                raise OpenFailure("CERTIFICATE_FAILED") from exc
            if isinstance(reason, TimeoutError):
                raise OpenFailure("HOST_TIMEOUT") from exc
            raise OpenFailure("HOST_UNREACHABLE") from exc
        try:
            target = Path(target_text).resolve(strict=True)
        except OSError as exc:
            _completion(base_url, token, context, "FILE_MISSING")
            raise OpenFailure("WORKSPACE_FILE_UNAVAILABLE") from exc
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except OSError as exc:
            _completion(base_url, token, context, "DEFAULT_APP_FAILED")
            raise OpenFailure("DEFAULT_APP_FAILED") from exc
        _completion(base_url, token, context, "OPENED")
        _log_result("OPENED")
        return 0
    except OpenFailure as failure:
        _show_failure(failure)
        return 3
    except Exception:  # noqa: BLE001 - GUI 顶层必须提供可见、脱敏的兜底错误。
        _show_failure(OpenFailure("HELPER_FAILED"))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
