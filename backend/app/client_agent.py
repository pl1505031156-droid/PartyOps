"""协同终端伴随进程：连接检测与灾备副本拉取。"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import http.client
import json
import logging
import mimetypes
import os
import platform
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pathlib import PurePosixPath

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .enrollment_codes import normalize_enrollment_code as _normalize_enrollment_code
from .platform_info import detect_platform_info

from .schemas import serialize_api_datetime


AGENT_VERSION = "1.4.3-rc.5"
_ACTIVE_SSL_CONTEXT = None
HEARTBEAT_INTERVAL_SECONDS = 15
MAX_UPDATE_PACKAGE_BYTES = 4 * 1024**3
MAX_TRANSFER_BYTES = 20 * 1024**3
MAX_TRANSFER_CHUNK_BYTES = 16 * 1024**2
MAX_BACKUP_DOWNLOAD_BYTES = 100 * 1024**3
MAX_BACKUP_EXPANDED_BYTES = 500 * 1024**3
MAX_BACKUP_MEMBERS = 200_000
MAX_BACKUP_MANIFEST_BYTES = 1024**2
BACKUP_FREE_SPACE_RESERVE_BYTES = 512 * 1024**2
COMMAND_POLL_INTERVAL_SECONDS = 5
logger = logging.getLogger("partyops.client_agent")


def configure_agent_logging(config_path: Path) -> Path:
    """在协同机配置目录写入轮转日志，不记录令牌或文件正文。"""

    log_dir = config_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "partyops-agent.log"
    target = str(log_path.resolve()).lower()
    if not any(
        str(getattr(handler, "baseFilename", "")).lower() == target
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return log_path


def _record_agent_failure(
    config_path: Path,
    config: dict[str, object],
    operation: str,
    exc: BaseException,
) -> None:
    """持久化可诊断状态；鉴权失效时明确要求重新入网。"""

    status = getattr(exc, "code", None)
    logger.warning("operation_failed operation=%s type=%s status=%s", operation, type(exc).__name__, status or "")
    config["last_agent_error"] = operation
    config["last_agent_error_at"] = datetime.now(timezone.utc).isoformat()
    if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403}:
        config["authentication_state"] = "reauth_required"
    try:
        _save_config(config_path, config)
    except OSError:
        logger.exception("agent_state_persist_failed operation=%s", operation)


class AgentCommandError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _validated_transfer_id(value: object) -> str:
    """把主机下发的传输编号限制为单一路径段，禁止逃逸终端暂存目录。"""

    transfer_id = str(value)
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not transfer_id or len(transfer_id) > 64 or any(char not in allowed for char in transfer_id):
        raise AgentCommandError("TRANSFER_ID_INVALID", "主机下发的传输编号无效")
    return transfer_id


def _validated_transfer_geometry(
    status: dict[str, object],
    fallback: dict[str, object] | None = None,
) -> tuple[int, int, int]:
    """约束主机下发的分块几何，避免异常状态触发超大读、长循环或越界写。"""

    fallback = fallback or {}
    try:
        chunk_size = int(status.get("chunk_size", fallback.get("chunk_size", 0)))
        total_chunks = int(status.get("total_chunks", fallback.get("total_chunks", 0)))
        size_bytes = int(status.get("size_bytes", fallback.get("size_bytes", 0)))
    except (TypeError, ValueError) as exc:
        raise AgentCommandError("TRANSFER_METADATA_INVALID", "主机返回的传输参数无效") from exc
    expected_chunks = (
        (size_bytes + chunk_size - 1) // chunk_size
        if chunk_size > 0 and size_bytes
        else 0
    )
    if (
        chunk_size <= 0
        or chunk_size > MAX_TRANSFER_CHUNK_BYTES
        or size_bytes < 0
        or size_bytes > MAX_TRANSFER_BYTES
        or total_chunks < 0
        or total_chunks != expected_chunks
    ):
        raise AgentCommandError(
            "TRANSFER_METADATA_INVALID",
            "主机返回的分块大小或总长度不一致",
        )
    return chunk_size, total_chunks, size_bytes


def _urlopen(request, timeout: int):
    target = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AgentCommandError("REMOTE_URL_INVALID", "协同请求只允许使用有效的 HTTP/HTTPS 主机地址")
    if _ACTIVE_SSL_CONTEXT is not None:
        return urllib.request.urlopen(  # nosec B310 - 协议和主机已在上方白名单校验。
            request,
            timeout=timeout,
            context=_ACTIVE_SSL_CONTEXT,
        )
    return urllib.request.urlopen(  # nosec B310 - 协议和主机已在上方白名单校验。
        request,
        timeout=timeout,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local_backup(path: Path) -> dict[str, object]:
    """终端独立校验备份清单，损坏副本不会覆盖上次可用文件。"""

    if not zipfile.is_zipfile(path):
        raise ValueError("下载内容不是有效备份包")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_BACKUP_MEMBERS:
            raise ValueError("备份文件数量超过终端安全限制")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("备份包含重复文件")
        expanded = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            mode = (info.external_attr >> 16) & 0o170000
            if (
                not name
                or "\x00" in name
                or relative.is_absolute()
                or ".." in relative.parts
                or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
            ):
                raise ValueError("备份包含非法路径或特殊文件")
            expanded += max(0, info.file_size)
            if expanded > MAX_BACKUP_EXPANDED_BYTES:
                raise ValueError("备份展开体积超过终端安全限制")
            if (
                info.file_size > 100 * 1024**2
                and info.file_size > max(info.compress_size, 1) * 1000
            ):
                raise ValueError("备份成员压缩比异常")
        manifest_info = next(
            (info for info in infos if info.filename == "manifest.json"),
            None,
        )
        if manifest_info is None or manifest_info.file_size > MAX_BACKUP_MANIFEST_BYTES:
            raise ValueError("备份清单缺失或体积异常")
        manifest = json.loads(archive.read(manifest_info))
        if not isinstance(manifest, dict) or manifest.get("format") != "partyops-backup":
            raise ValueError("备份格式不匹配")
        files = manifest.get("files", [])
        if not isinstance(files, list) or len(files) > MAX_BACKUP_MEMBERS:
            raise ValueError("备份清单文件列表无效")
        verified_names: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("备份清单文件记录无效")
            try:
                item_path = str(item["path"])
                expected_size = int(item["size"])
                expected_hash = str(item["sha256"]).lower()
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("备份清单文件校验字段无效") from exc
            relative = PurePosixPath(item_path)
            if (
                not item_path
                or "\\" in item_path
                or relative.is_absolute()
                or ".." in relative.parts
                or item_path in verified_names
            ):
                raise ValueError("备份清单包含非法路径")
            verified_names.add(item_path)
            if (
                expected_size < 0
                or expected_size > MAX_BACKUP_EXPANDED_BYTES
                or len(expected_hash) != 64
                or any(character not in "0123456789abcdef" for character in expected_hash)
            ):
                raise ValueError(f"备份文件校验字段无效：{item_path}")
            digest = hashlib.sha256()
            size = 0
            with archive.open(item_path) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError(f"备份文件超过清单大小：{item_path}")
            if size != expected_size:
                raise ValueError(f"备份文件大小不匹配：{item_path}")
            if digest.hexdigest() != expected_hash:
                raise ValueError(f"备份文件哈希不匹配：{item_path}")
        if "database/partyops.db" not in verified_names:
            raise ValueError("备份缺少 PartyOps 数据库")
        allowed_members = verified_names | {"manifest.json", "config/config.json"}
        unexpected = [
            info.filename
            for info in infos
            if not info.is_dir() and info.filename not in allowed_members
        ]
        if unexpected:
            raise ValueError("备份包含未在清单登记的文件")
    return manifest


def _response_filename(disposition: str) -> str:
    for part in disposition.split(";"):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        if key.lower() == "filename*":
            encoded = value.strip('"')
            if "''" in encoded:
                encoded = encoded.split("''", 1)[1]
            return Path(urllib.parse.unquote(encoded)).name
        if key.lower() == "filename":
            return Path(value.strip('"; ')).name
    return "PartyOps-latest.partyops-backup"


def validate_config(config: dict[str, object]) -> tuple[str, str, Path]:
    host_url = str(config.get("host_url", "")).rstrip("/")
    parsed = urllib.parse.urlparse(host_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("host_url 必须是无账号信息的 HTTP/HTTPS 局域网地址")
    token = str(config.get("device_token") or config.get("pairing_token", "")).strip()
    if not token:
        raise ValueError("缺少终端配对令牌")
    destination = Path(str(config.get("backup_dir", ""))).expanduser().resolve()
    return host_url, token, destination


def configure_ssl_context(config: dict[str, object]) -> None:
    """为 HTTPS 主机和独立 mTLS Agent 通道加载随设备保存的证书。"""

    global _ACTIVE_SSL_CONTEXT
    ca_value = str(config.get("ca_file", "")).strip()
    cert_value = str(config.get("certificate_file", "")).strip()
    key_value = str(config.get("key_file", "")).strip()
    ca_file = Path(ca_value).expanduser() if ca_value else Path()
    cert_file = Path(cert_value).expanduser() if cert_value else Path()
    key_file = Path(key_value).expanduser() if key_value else Path()
    if not ca_value or not ca_file.is_file():
        _ACTIVE_SSL_CONTEXT = None
        return
    context = ssl.create_default_context(cafile=str(ca_file))
    if cert_value and key_value and cert_file.is_file() and key_file.is_file():
        context.load_cert_chain(str(cert_file), str(key_file))
    _ACTIVE_SSL_CONTEXT = context


def device_metadata() -> dict[str, object]:
    info = detect_platform_info()
    try:
        disk_free = shutil.disk_usage(Path.home()).free
    except OSError:
        disk_free = 0
    return {
        **info,
        "kernel": platform.release()[:120],
        "app_version": AGENT_VERSION,
        "agent_version": AGENT_VERSION,
        "local_username": getpass.getuser()[:120],
        "ip_address": "",
        "disk_free_bytes": disk_free,
        "root_count": 0,
        "indexed_file_count": 0,
    }


def _json_request(
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
    method: str = "GET",
    timeout: int = 15,
) -> object:
    headers: dict[str, str] = {}
    data = None
    if token:
        headers["X-PartyOps-Device-Token"] = token
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    with _urlopen(request, timeout) as response:
        return json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))


def create_browser_launch_url(
    host_url: str,
    agent_url: str,
    token: str,
) -> str:
    """由设备认证通道获取短期签名上下文，再让浏览器进入主机页面。"""

    try:
        response = _json_request(
            f"{agent_url.rstrip('/')}/api/v1/devices/browser-token",
            token=token,
            payload={},
            method="POST",
            timeout=15,
        )
        if isinstance(response, dict) and response.get("token"):
            encoded = urllib.parse.quote(str(response["token"]), safe="")
            return f"{host_url.rstrip('/')}/device-launch?token={encoded}"
    except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
        pass
    # 1.1.2 → 1.1.3 首次桥接期间旧主机尚无令牌端点；主机仍可依据
    # 唯一局域网 IP 识别设备并显示更新页。
    return host_url.rstrip("/")


def normalize_enrollment_code(value: str) -> str:
    """兼容国产浏览器剪贴板，并在请求前识别被截断或污染的入网码。"""

    try:
        return _normalize_enrollment_code(value)
    except ValueError as exc:
        raise ValueError("入网码不完整，请在主机页面点击“复制完整入网码”") from exc


def enrollment_http_error(error: urllib.error.HTTPError) -> ValueError:
    """把主机 problem+json 转成配置向导可直接处理的中文原因。"""

    code = ""
    detail = ""
    try:
        payload = json.loads(error.read(256 * 1024).decode("utf-8"))
        if isinstance(payload, dict):
            code = str(payload.get("code", ""))
            detail = str(payload.get("detail", "") or payload.get("title", ""))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        pass
    if code in {"ENROLLMENT_INVALID", "ENROLLMENT_CODE_FORMAT_INVALID"}:
        return ValueError(
            "入网码无效或已过期，请在主机重新生成，并点击“复制完整入网码”后粘贴"
        )
    if code in {"ENROLLMENT_ALREADY_COMPLETED", "ENROLLMENT_RECOVERY_UNAVAILABLE"}:
        return ValueError(
            detail
            or "该入网码已经在主机创建了设备；请先删除主机上的未完成设备，再重新生成入网码"
        )
    if code == "DEVICE_NAME_EXISTS":
        return ValueError(detail or "设备名称已存在，请换一个名称")
    return ValueError(
        f"主机拒绝入网（HTTP {error.code}）：{detail or '请在主机设备中心重新生成入网码'}"
    )


def enroll_device(
    host_url: str,
    code: str,
    name: str,
    *,
    pending_path: Path | None = None,
) -> dict[str, object]:
    normalized_host = host_url.rstrip("/")
    normalized_name = name.strip()
    pending: dict[str, object] = {}
    if pending_path and pending_path.is_file():
        try:
            loaded = json.loads(pending_path.read_text(encoding="utf-8"))
            if (
                isinstance(loaded, dict)
                and loaded.get("host_url") == normalized_host
                and loaded.get("device_name") == normalized_name
            ):
                pending = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pending = {}
    normalized_code = normalize_enrollment_code(code)
    pending_identity = {
        "host_url": normalized_host,
        "device_name": normalized_name,
        "code_hash": hashlib.sha256(normalized_code.encode("utf-8")).hexdigest(),
    }
    if pending and pending.get("code_hash") != pending_identity["code_hash"]:
        # 新入网码代表管理员正在当前主机数据库重新登记设备。旧主机或旧数据
        # 目录签发的缓存令牌绝不能盖过新入网码，否则终端会反复复用失效身份，
        # 看似能打开网页，当前主机设备中心却永远没有这台电脑。
        pending = {}
    # 主机已签发设备凭据、但向导在保存 CA 或首次心跳时中断的电脑，可以用
    # 同一个入网码从本机 0600 临时记录续接。只有主机地址、设备名和入网码
    # 哈希全部一致时才允许复用，输入新码时必须重新向主机登记。
    cached_result = pending.get("result")
    if isinstance(cached_result, dict) and cached_result.get("device_token"):
        return dict(cached_result)
    private_key_pem = str(pending.get("private_key_pem", ""))
    try:
        private_key = (
            serialization.load_pem_private_key(
                private_key_pem.encode("utf-8"),
                password=None,
            )
            if private_key_pem
            else rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
    except (TypeError, ValueError):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    if pending_path:
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = pending_path.with_suffix(pending_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    **pending_identity,
                    "private_key_pem": private_key_pem,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(pending_path)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, normalized_name)]
            )
        )
        .sign(private_key, hashes.SHA256())
    )
    payload = {
        **device_metadata(),
        "code": normalized_code,
        "name": normalized_name,
        "csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode(),
    }
    global _ACTIVE_SSL_CONTEXT
    previous_context = _ACTIVE_SSL_CONTEXT
    parsed = urllib.parse.urlparse(host_url)
    if parsed.scheme == "https" and previous_context is None:
        expected_fingerprint = normalized_code.rsplit(".", 1)[1].lower()
        bootstrap_request = urllib.request.Request(
            f"{host_url.rstrip('/')}/api/v1/bootstrap/ca.pem",
            method="GET",
        )
        # 此次下载的证书不直接受信；只有哈希与管理员页面给出的入网码
        # 完全一致后，才用它建立真正的入网 TLS 连接。
        with urllib.request.urlopen(  # nosec B310 - URL 来源已校验；证书由下方 SHA-256 固定校验。
            bootstrap_request,
            timeout=15,
            context=ssl._create_unverified_context(),  # nosec B323 - 仅用于取得待固定 CA，信任建立在入网码指纹上。
        ) as response:
            ca_pem = response.read(256 * 1024)
        ca_certificate = x509.load_pem_x509_certificate(ca_pem)
        actual_fingerprint = ca_certificate.fingerprint(hashes.SHA256()).hex()
        if not secrets.compare_digest(actual_fingerprint, expected_fingerprint):
            raise ValueError("主机 CA 指纹与入网码不一致，已拒绝连接")
        _ACTIVE_SSL_CONTEXT = ssl.create_default_context(
            cadata=ca_pem.decode("utf-8")
        )
    try:
        for attempt in range(2):
            try:
                result = _json_request(
                    f"{normalized_host}/api/v1/devices/enroll",
                    payload=payload,
                    method="POST",
                )
                break
            except urllib.error.HTTPError as exc:
                raise enrollment_http_error(exc) from exc
            except (OSError, TimeoutError, urllib.error.URLError) as exc:
                if attempt == 0:
                    time.sleep(0.4)
                    continue
                raise ValueError(
                    "主机可能已收到入网请求但终端未收到确认；请保持当前入网码并再次提交，系统会自动恢复"
                ) from exc
    finally:
        _ACTIVE_SSL_CONTEXT = previous_context
    if not isinstance(result, dict) or not result.get("device_token"):
        raise ValueError("主机未返回有效设备凭据")
    result["_private_key_pem"] = private_key_pem
    if pending_path:
        temporary = pending_path.with_suffix(pending_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    **pending_identity,
                    "private_key_pem": private_key_pem,
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(pending_path)
    return result


def register_shared_root(
    host_url: str,
    token: str,
    name: str,
    remote_key: str,
    action_token: str = "",
) -> dict[str, object]:
    result = _json_request(
        f"{host_url.rstrip('/')}/api/v1/devices/workspace/roots",
        token=token,
        payload={
            "name": name,
            "remote_key": remote_key,
            "action_token": action_token,
        },
        method="POST",
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise ValueError("主机未返回共享目录编号")
    return result


def list_registered_shared_roots(host_url: str, token: str) -> list[dict[str, object]]:
    result = _json_request(
        f"{host_url.rstrip('/')}/api/v1/devices/workspace/roots",
        token=token,
    )
    if not isinstance(result, list):
        raise ValueError("主机未返回共享目录列表")
    return [item for item in result if isinstance(item, dict)]


def add_shared_root(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
    local_path: Path,
    name: str = "",
    action_token: str = "",
) -> dict[str, object]:
    """原子写入本机目录配置，并立即向主机登记待审批共享根。"""

    requested = local_path.expanduser()
    absolute_requested = requested.absolute()
    path_chain = [absolute_requested, *absolute_requested.parents]
    if any(
        part.is_symlink()
        or bool(getattr(part, "is_junction", lambda: False)())
        for part in path_chain
        if part.exists()
    ):
        raise ValueError("共享目录必须是本机真实文件夹，不能是符号链接")
    resolved = requested.resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("共享目录必须是本机真实文件夹，不能是符号链接")
    roots = _safe_shared_roots(config)
    for item in roots:
        existing = Path(str(item["local_path"]))
        if resolved == existing or resolved in existing.parents or existing in resolved.parents:
            raise ValueError("共享目录与已有目录重复或相互嵌套，请只保留最合适的一层")
    root: dict[str, object] = {
        "name": name.strip() or resolved.name,
        "local_path": str(resolved),
        "remote_key": secrets.token_urlsafe(12).replace("-", "_"),
    }
    result = register_shared_root(
        host_url,
        token,
        str(root["name"]),
        str(root["remote_key"]),
        action_token,
    )
    root.update(
        {
            "root_id": result.get("id"),
            "approval_status": result.get("approval_status", "pending"),
            "approval_note": result.get("approval_note", ""),
            "enabled": result.get("enabled", False),
        }
    )
    roots.append(root)
    config["shared_roots"] = roots
    _save_config(config_path, config)
    return root


def rename_shared_root(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
    root_id: str,
    name: str,
) -> dict[str, object]:
    normalized = name.strip()
    if not normalized:
        raise ValueError("共享目录名称不能为空")
    result = _json_request(
        f"{host_url.rstrip('/')}/api/v1/devices/workspace/roots/{urllib.parse.quote(root_id)}",
        token=token,
        payload={"name": normalized},
        method="PATCH",
    )
    roots = _safe_shared_roots(config)
    root = next((item for item in roots if str(item.get("root_id", "")) == root_id), None)
    if root is None:
        raise ValueError("本机配置中未找到该共享目录")
    root["name"] = normalized
    config["shared_roots"] = roots
    _save_config(config_path, config)
    return result if isinstance(result, dict) else {"id": root_id, "name": normalized}


def remove_shared_root(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
    root_id: str,
) -> None:
    _json_request(
        f"{host_url.rstrip('/')}/api/v1/devices/workspace/roots/{urllib.parse.quote(root_id)}",
        token=token,
        method="DELETE",
    )
    roots = [
        item for item in _safe_shared_roots(config)
        if str(item.get("root_id", "")) != root_id
    ]
    config["shared_roots"] = roots
    _save_config(config_path, config)


def refresh_shared_root_statuses(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
) -> list[dict[str, object]]:
    remote = {
        str(item.get("id", "")): item
        for item in list_registered_shared_roots(host_url, token)
    }
    roots = _safe_shared_roots(config)
    for root in roots:
        status = remote.get(str(root.get("root_id", "")))
        if not status:
            continue
        for key in (
            "name",
            "approval_status",
            "approval_note",
            "enabled",
            "share_scope",
            "semantic_content_enabled",
        ):
            if key in status:
                root[key] = status[key]
    config["shared_roots"] = roots
    _save_config(config_path, config)
    return roots


def _save_config(path: Path, config: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(path)


def _safe_shared_roots(config: dict[str, object]) -> list[dict[str, object]]:
    roots = config.get("shared_roots", [])
    if not isinstance(roots, list):
        return []
    valid: list[dict[str, object]] = []
    for item in roots:
        if not isinstance(item, dict):
            continue
        local_path = Path(str(item.get("local_path", ""))).expanduser()
        remote_key = str(item.get("remote_key", ""))
        try:
            resolved = local_path.resolve(strict=True)
        except OSError:
            continue
        if (
            not resolved.is_dir()
            or resolved.is_symlink()
            or not remote_key
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in remote_key)
        ):
            continue
        valid.append({**item, "local_path": str(resolved), "remote_key": remote_key})
    return valid


def sync_shared_roots(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
) -> list[dict[str, object]]:
    roots = _safe_shared_roots(config)
    changed = False
    for root in roots:
        if root.get("root_id") and root.get("approval_status") == "approved":
            continue
        result = register_shared_root(
            host_url,
            token,
            str(root.get("name") or Path(str(root["local_path"])).name),
            str(root["remote_key"]),
        )
        for key, remote_field in (
            ("root_id", "id"),
            ("approval_status", "approval_status"),
            ("approval_note", "approval_note"),
            ("enabled", "enabled"),
        ):
            if root.get(key) != result.get(remote_field):
                root[key] = result.get(remote_field)
                changed = True
    if changed or config.get("shared_roots") != roots:
        config["shared_roots"] = roots
        _save_config(config_path, config)
    return roots


def _resolve_shared_path(
    config: dict[str, object],
    remote_file_key: str,
    *,
    allow_directory: bool = False,
) -> Path:
    device_id, separator, remainder = remote_file_key.partition(":")
    remote_key, separator2, relative = remainder.partition(":")
    if (
        not separator
        or not separator2
        or device_id != str(config.get("device_id", ""))
    ):
        raise AgentCommandError("ROOT_NOT_APPROVED", "远程文件标识不属于本设备")
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative:
        raise AgentCommandError("PATH_TRAVERSAL_DENIED", "远程文件相对路径无效")
    root = next(
        (
            item
            for item in _safe_shared_roots(config)
            if item.get("remote_key") == remote_key
            and item.get("approval_status") == "approved"
        ),
        None,
    )
    if not root:
        raise AgentCommandError("ROOT_NOT_APPROVED", "共享目录尚未在主机获批")
    root_path = Path(str(root["local_path"]))
    candidate = root_path.joinpath(*relative_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AgentCommandError("SOURCE_MISSING", "源文件已被移动或删除") from exc
    if root_path != resolved and root_path not in resolved.parents:
        raise AgentCommandError("PATH_TRAVERSAL_DENIED", "文件超出共享目录")
    current = root_path
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise AgentCommandError("SYMLINK_DENIED", "共享目录中的符号链接已被拒绝")
    if not resolved.is_file() and not (allow_directory and resolved.is_dir()):
        raise AgentCommandError("SOURCE_MISSING", "共享项目不存在或类型不受支持")
    return resolved


def _resolve_shared_file(
    config: dict[str, object],
    remote_file_key: str,
) -> Path:
    return _resolve_shared_path(config, remote_file_key, allow_directory=False)


def _open_shared_file(
    config: dict[str, object],
    remote_file_key: str,
):
    """在 Linux 上逐级 O_NOFOLLOW 打开，避免检查与打开之间被替换为链接。"""

    resolved = _resolve_shared_file(config, remote_file_key)
    if os.name == "nt" or not hasattr(os, "O_NOFOLLOW"):
        return resolved.open("rb")
    relative: PurePosixPath | None = None
    root_path: Path | None = None
    _device_id, _separator, remainder = remote_file_key.partition(":")
    remote_key, _separator2, relative_text = remainder.partition(":")
    for item in _safe_shared_roots(config):
        if item.get("remote_key") == remote_key:
            root_path = Path(str(item["local_path"]))
            relative = PurePosixPath(relative_text)
            break
    if root_path is None or relative is None:
        raise AgentCommandError("ROOT_NOT_APPROVED", "共享目录尚未获批")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    directory_fd = os.open(root_path, directory_flags)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(relative.parts[-1], file_flags, dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_fd)
            raise AgentCommandError("SOURCE_MISSING", "源文件不是普通文件")
        return os.fdopen(file_fd, "rb")
    finally:
        os.close(directory_fd)


def _scan_state_path(config_path: Path) -> Path:
    return config_path.with_name("client-index-state.json")


def _load_scan_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _remote_index_item(
    root_path: Path,
    path: Path,
    old_signature: str,
    extract_content: bool = False,
) -> tuple[dict[str, object], str]:
    relative = path.relative_to(root_path).as_posix()
    info = path.stat(follow_symlinks=False)
    signature = f"{info.st_size}:{info.st_mtime_ns}:{info.st_ino}"
    is_directory = stat.S_ISDIR(info.st_mode)
    suffix = "" if is_directory else path.suffix.lower()[:32]
    extracted_text = ""
    if (
        extract_content
        and not is_directory
        and suffix in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".log"}
        and info.st_size <= 2 * 1024 * 1024
    ):
        raw = path.read_bytes()[:200_000]
        try:
            extracted_text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            extracted_text = raw.decode("gb18030", errors="replace")
    return (
        {
            "relative_path": relative,
            "name": path.name,
            "is_directory": is_directory,
            "parent_relative_path": path.parent.relative_to(root_path).as_posix()
            if path.parent != root_path
            else None,
            "extension": suffix,
            "size_bytes": 0 if is_directory else info.st_size,
            "modified_at": serialize_api_datetime(
                datetime.fromtimestamp(info.st_mtime, timezone.utc)
            ),
            "mime_type": "inode/directory"
            if is_directory
            else mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "sha256": None,
            "content_changed": signature != old_signature,
            "extracted_text": extracted_text,
            "ocr_text": "",
        },
        signature,
    )


def scan_and_upload_roots(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
) -> tuple[int, int]:
    """低优先级增量扫描；正文仅在该共享根显式开启后上传。"""

    state_path = _scan_state_path(config_path)
    state = _load_scan_state(state_path)
    indexed = 0
    errors = 0
    for root in _safe_shared_roots(config):
        if root.get("approval_status") != "approved" or not root.get("root_id"):
            continue
        root_path = Path(str(root["local_path"]))
        root_state = state.get(str(root["remote_key"]), {})
        next_state: dict[str, object] = {}
        batch: list[dict[str, object]] = []
        for current, dirnames, filenames in os.walk(root_path, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not (current_path / name).is_symlink()
            ]
            for name in [*dirnames, *sorted(filenames)]:
                path = current_path / name
                if path.is_symlink():
                    continue
                try:
                    relative = path.relative_to(root_path).as_posix()
                    item, signature = _remote_index_item(
                        root_path,
                        path,
                        str(root_state.get(relative, "")),
                        bool(root.get("semantic_content_enabled", False)),
                    )
                    next_state[relative] = signature
                    batch.append(item)
                    if len(batch) >= 200:
                        _json_request(
                            f"{host_url.rstrip('/')}/api/v1/devices/workspace/index-delta",
                            token=token,
                            payload={"root_id": str(root["root_id"]), "files": batch},
                            method="POST",
                            timeout=120,
                        )
                        indexed += len(batch)
                        batch = []
                except (OSError, RuntimeError, ValueError):
                    errors += 1
            time.sleep(0.002)
        if batch:
            _json_request(
                f"{host_url.rstrip('/')}/api/v1/devices/workspace/index-delta",
                token=token,
                payload={"root_id": str(root["root_id"]), "files": batch},
                method="POST",
                timeout=120,
            )
            indexed += len(batch)
        removed = sorted(set(root_state) - set(next_state))
        for offset in range(0, len(removed), 5000):
            _json_request(
                f"{host_url.rstrip('/')}/api/v1/devices/workspace/index-delta",
                token=token,
                payload={
                    "root_id": str(root["root_id"]),
                    "files": [],
                    "removed_paths": removed[offset : offset + 5000],
                },
                method="POST",
                timeout=120,
            )
        state[str(root["remote_key"])] = next_state
    _save_config(state_path, state)
    return indexed, errors


def send_device_heartbeat(
    host_url: str,
    token: str,
    config: dict[str, object],
    *,
    strict_identity: bool = False,
) -> bool:
    """设备上线心跳；首次配置可要求明确区分身份失效与网络不通。"""

    if not config.get("device_token"):
        return False
    payload = {
        **device_metadata(),
        "root_count": len(_safe_shared_roots(config)),
        "indexed_file_count": int(config.get("indexed_file_count", 0) or 0),
    }
    request = urllib.request.Request(
        f"{host_url.rstrip('/')}/api/v1/devices/heartbeat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-PartyOps-Device-Token": token,
        },
        method="POST",
    )
    try:
        with _urlopen(request, 10) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if strict_identity and exc.code in {401, 403}:
            raise ValueError(
                "当前保存的设备凭据不属于主机正在使用的数据库。"
                "请在协同机备份旧配置后重新入网；不要继续重复使用旧凭据。"
            ) from exc
        return False
    except (urllib.error.URLError, TimeoutError):
        return False


def poll_device_commands(host_url: str, token: str) -> list[dict[str, object]]:
    request = urllib.request.Request(
        f"{host_url.rstrip('/')}/api/v1/devices/commands",
        headers={"X-PartyOps-Device-Token": token},
    )
    try:
        with _urlopen(request, 10) as response:
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
            return payload if isinstance(payload, list) else []
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return []


def ack_device_command(host_url: str, token: str, command_id: str, result: dict[str, object]) -> bool:
    request = urllib.request.Request(
        f"{host_url.rstrip('/')}/api/v1/devices/commands/{urllib.parse.quote(command_id)}/ack",
        data=json.dumps(result).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-PartyOps-Device-Token": token,
        },
        method="POST",
    )
    try:
        with _urlopen(request, 10) as response:
            return response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def rotate_device_certificate(
    host_url: str,
    token: str,
    config: dict[str, object],
    config_path: Path,
) -> dict[str, object]:
    """生成新密钥并通过旧证书完成一次性证书轮换。"""

    device_id = str(config.get("device_id", "")).strip()
    if not device_id:
        raise AgentCommandError("DEVICE_ID_MISSING", "设备配置缺少编号")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
        .sign(private_key, hashes.SHA256())
    )
    result = _json_request(
        f"{host_url.rstrip('/')}/api/v1/devices/certificate/rotate",
        token=token,
        payload={"csr_pem": csr.public_bytes(serialization.Encoding.PEM).decode()},
        method="POST",
        timeout=30,
    )
    if not isinstance(result, dict) or not result.get("certificate_pem"):
        raise AgentCommandError("CERTIFICATE_ROTATION_FAILED", "主机未返回新设备证书")
    pki_dir = config_path.parent / "pki"
    _save_config(config_path, config)
    pki_dir.mkdir(parents=True, exist_ok=True)
    key_path = pki_dir / "device.key"
    cert_path = pki_dir / "device.pem"
    ca_path = pki_dir / "ca.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_text(str(result["certificate_pem"]), encoding="utf-8")
    ca_path.write_text(str(result["ca_certificate_pem"]), encoding="utf-8")
    if os.name != "nt":
        key_path.chmod(0o600)
        cert_path.chmod(0o600)
        ca_path.chmod(0o644)
    config.update(
        {
            "agent_url": str(result.get("agent_url", config.get("agent_url", ""))),
            "key_file": str(key_path.resolve()),
            "certificate_file": str(cert_path.resolve()),
            "ca_file": str(ca_path.resolve()),
        }
    )
    _save_config(config_path, config)
    configure_ssl_context(config)
    return {"ok": True, "message": "设备证书已轮换"}


def get_transfer_status(host_url: str, token: str, transfer_id: str) -> dict[str, object]:
    transfer_id = _validated_transfer_id(transfer_id)
    result = _json_request(
        f"{host_url.rstrip('/')}/api/v1/devices/transfers/{urllib.parse.quote(transfer_id)}/status",
        token=token,
        timeout=15,
    )
    if not isinstance(result, dict):
        raise AgentCommandError("TRANSFER_INVALID", "主机返回了无效传输状态")
    return result


def upload_transfer(
    host_url: str,
    token: str,
    payload: dict[str, object],
    config: dict[str, object],
) -> dict[str, object]:
    transfer_id = _validated_transfer_id(payload.get("transfer_id", ""))
    remote_file_key = str(payload.get("remote_file_key", ""))
    source_path = _resolve_shared_file(config, remote_file_key)
    before = source_path.stat(follow_symlinks=False)
    expected_size = int(payload.get("size_bytes", 0) or 0)
    if expected_size and before.st_size != expected_size:
        raise AgentCommandError("SOURCE_CHANGED", "源文件大小已变化")
    expected_modified = str(payload.get("modified_at", ""))
    if expected_modified:
        indexed_time = datetime.fromisoformat(expected_modified.replace("Z", "+00:00"))
        if abs(before.st_mtime - indexed_time.timestamp()) > 1:
            raise AgentCommandError("SOURCE_CHANGED", "源文件修改时间已变化")
    status = get_transfer_status(host_url, token, transfer_id)
    completed = {
        int(value)
        for value in status.get("completed_chunks", [])
        if isinstance(value, int) or str(value).isdigit()
    }
    chunk_size, total_chunks, _size_bytes = _validated_transfer_geometry(
        status,
        {**payload, "size_bytes": payload.get("size_bytes", before.st_size)},
    )
    with _open_shared_file(config, remote_file_key) as source:
        for chunk_no in range(total_chunks):
            if chunk_no in completed:
                continue
            source.seek(chunk_no * chunk_size)
            chunk = source.read(chunk_size)
            digest = hashlib.sha256(chunk).hexdigest()
            request = urllib.request.Request(
                (
                    f"{host_url.rstrip('/')}/api/v1/devices/transfers/"
                    f"{urllib.parse.quote(transfer_id)}/chunks/{chunk_no}"
                ),
                data=chunk,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-PartyOps-Device-Token": token,
                    "X-Chunk-SHA256": digest,
                },
                method="PUT",
            )
            try:
                with _urlopen(request, 180) as response:
                    result = json.loads(response.read(256 * 1024).decode("utf-8"))
            except (urllib.error.URLError, TimeoutError) as exc:
                raise AgentCommandError(
                    "NETWORK_INTERRUPTED",
                    "网络中断，稍后将断点续传",
                    retryable=True,
                ) from exc
            if result.get("status") == "failed":
                raise AgentCommandError(
                    str(result.get("error_code", "HASH_MISMATCH")),
                    "主机校验文件失败",
                )
    after = source_path.stat(follow_symlinks=False)
    if (
        after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or after.st_ino != before.st_ino
    ):
        raise AgentCommandError("SOURCE_CHANGED", "源文件在传输期间发生变化")
    _json_request(
        (
            f"{host_url.rstrip('/')}/api/v1/devices/transfers/"
            f"{urllib.parse.quote(transfer_id)}/finalize"
        ),
        token=token,
        payload={},
        method="POST",
        timeout=60,
    )
    return {"ok": True, "message": "文件已安全上传到主机中转区"}


def _upload_local_path(
    host_url: str,
    token: str,
    transfer_id: str,
    source_path: Path,
) -> None:
    """上传 Agent 自己生成的受管 ZIP，并复用主机断点与分块哈希。"""

    transfer_id = _validated_transfer_id(transfer_id)
    status = get_transfer_status(host_url, token, transfer_id)
    completed = {
        int(value)
        for value in status.get("completed_chunks", [])
        if isinstance(value, int) or str(value).isdigit()
    }
    chunk_size, total_chunks, _size_bytes = _validated_transfer_geometry(
        status,
        {"size_bytes": source_path.stat().st_size},
    )
    with source_path.open("rb") as source:
        for chunk_no in range(total_chunks):
            if chunk_no in completed:
                continue
            source.seek(chunk_no * chunk_size)
            chunk = source.read(chunk_size)
            digest = hashlib.sha256(chunk).hexdigest()
            request = urllib.request.Request(
                (
                    f"{host_url.rstrip('/')}/api/v1/devices/transfers/"
                    f"{urllib.parse.quote(transfer_id)}/chunks/{chunk_no}"
                ),
                data=chunk,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-PartyOps-Device-Token": token,
                    "X-Chunk-SHA256": digest,
                },
                method="PUT",
            )
            try:
                with _urlopen(request, 180) as response:
                    result = json.loads(response.read(256 * 1024).decode("utf-8"))
            except (urllib.error.URLError, TimeoutError) as exc:
                raise AgentCommandError(
                    "NETWORK_INTERRUPTED",
                    "网络中断，稍后将断点续传",
                    retryable=True,
                ) from exc
            if result.get("status") == "failed":
                raise AgentCommandError("HASH_MISMATCH", "主机校验压缩包失败")
    _json_request(
        (
            f"{host_url.rstrip('/')}/api/v1/devices/transfers/"
            f"{urllib.parse.quote(transfer_id)}/finalize"
        ),
        token=token,
        payload={},
        method="POST",
        timeout=60,
    )


def upload_bundle_transfer(
    host_url: str,
    token: str,
    payload: dict[str, object],
    config: dict[str, object],
) -> dict[str, object]:
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list) or not raw_items:
        raise AgentCommandError("BUNDLE_ITEMS_INVALID", "主机未提供有效的压缩项目")
    transfer_id = _validated_transfer_id(payload.get("transfer_id", ""))
    max_bytes = int(payload.get("max_bytes", 20 * 1024**3) or 20 * 1024**3)
    receive_dir = Path(
        str(config.get("receive_dir", Path.home() / "PartyOps-接收文件"))
    ).expanduser().resolve()
    staging = receive_dir / ".partyops-transfers"
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle_path = staging / f"{transfer_id}.bundle"
    bundle_path.unlink(missing_ok=True)
    written: set[str] = set()
    uncompressed_size = 0

    def checked_archive_name(value: str) -> str:
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise AgentCommandError("PATH_TRAVERSAL_DENIED", "压缩项目相对路径无效")
        return relative.as_posix()

    try:
        with zipfile.ZipFile(
            bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive:
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise AgentCommandError("BUNDLE_ITEMS_INVALID", "压缩项目格式无效")
                remote_key = str(raw.get("remote_file_key", ""))
                base_name = checked_archive_name(str(raw.get("relative_path", "")))
                source = _resolve_shared_path(config, remote_key, allow_directory=True)
                candidates = [source]
                if source.is_dir():
                    candidates.extend(sorted(source.rglob("*"), key=lambda item: item.as_posix()))
                for candidate in candidates:
                    if candidate.is_symlink():
                        raise AgentCommandError("SYMLINK_DENIED", "压缩范围含符号链接")
                    relative_tail = candidate.relative_to(source).as_posix() if candidate != source else ""
                    archive_name = base_name if not relative_tail else f"{base_name}/{relative_tail}"
                    archive_name = checked_archive_name(archive_name)
                    if archive_name in written:
                        continue
                    written.add(archive_name)
                    if candidate.is_dir():
                        archive.writestr(archive_name.rstrip("/") + "/", b"")
                        continue
                    if not candidate.is_file():
                        raise AgentCommandError("SOURCE_MISSING", "压缩范围含不受支持的文件类型")
                    uncompressed_size += candidate.stat(follow_symlinks=False).st_size
                    if uncompressed_size > max_bytes:
                        raise AgentCommandError("TRANSFER_FILE_TOO_LARGE", "所选内容超过20GB限制")
                    archive.write(candidate, archive_name)
        if bundle_path.stat().st_size > max_bytes:
            raise AgentCommandError("TRANSFER_FILE_TOO_LARGE", "压缩包超过20GB限制")
        digest = sha256_file(bundle_path)
        _json_request(
            (
                f"{host_url.rstrip('/')}/api/v1/devices/transfers/"
                f"{urllib.parse.quote(transfer_id)}/prepare"
            ),
            token=token,
            payload={"size_bytes": bundle_path.stat().st_size, "sha256": digest},
            method="POST",
            timeout=60,
        )
        _upload_local_path(host_url, token, transfer_id, bundle_path)
    finally:
        bundle_path.unlink(missing_ok=True)
    return {"ok": True, "message": "所选文件已生成 ZIP 并安全上传"}


def _non_overwriting_target(directory: Path, name: str) -> Path:
    safe = Path(name).name
    if not safe or safe in {".", ".."} or any(ord(char) < 32 for char in safe):
        raise AgentCommandError("FILENAME_INVALID", "接收文件名无效")
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 10_000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise AgentCommandError("FILENAME_CONFLICT", "接收目录中同名文件过多")


def download_transfer(
    host_url: str,
    token: str,
    payload: dict[str, object],
    config: dict[str, object],
) -> dict[str, object]:
    transfer_id = _validated_transfer_id(payload.get("transfer_id", ""))
    status = get_transfer_status(host_url, token, transfer_id)
    chunk_size, total_chunks, expected_size = _validated_transfer_geometry(
        status,
        payload,
    )
    expected_hash = str(status.get("sha256", payload.get("sha256", ""))).lower()
    receive_dir = Path(
        str(config.get("receive_dir", Path.home() / "PartyOps-接收文件"))
    ).expanduser().resolve()
    receive_dir.mkdir(parents=True, exist_ok=True)
    staging = receive_dir / ".partyops-transfers"
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    part = staging / f"{transfer_id}.part"
    if part.is_symlink():
        raise AgentCommandError("SYMLINK_DENIED", "接收暂存文件异常，已拒绝覆盖")
    with part.open("r+b" if part.exists() else "w+b") as target:
        for chunk_no in range(total_chunks):
            expected_offset = chunk_no * chunk_size
            if part.stat().st_size >= min(expected_size, expected_offset + chunk_size):
                continue
            request = urllib.request.Request(
                (
                    f"{host_url.rstrip('/')}/api/v1/devices/transfers/"
                    f"{urllib.parse.quote(transfer_id)}/chunks/{chunk_no}"
                ),
                headers={"X-PartyOps-Device-Token": token},
            )
            try:
                with _urlopen(request, 180) as response:
                    chunk = response.read(chunk_size + 1)
                    expected_chunk_hash = response.headers.get("X-Chunk-SHA256", "")
            except urllib.error.HTTPError as exc:
                if exc.code in {404, 409, 425}:
                    raise AgentCommandError(
                        "CHUNK_NOT_READY",
                        "主机中转文件尚未准备完成",
                        retryable=True,
                    ) from exc
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                raise AgentCommandError(
                    "NETWORK_INTERRUPTED",
                    "网络中断，稍后将断点续传",
                    retryable=True,
                ) from exc
            if len(chunk) > chunk_size or (
                expected_chunk_hash
                and hashlib.sha256(chunk).hexdigest() != expected_chunk_hash
            ):
                raise AgentCommandError("HASH_MISMATCH", "接收分块校验失败")
            target.seek(expected_offset)
            target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    if part.stat().st_size != expected_size:
        raise AgentCommandError("HASH_MISMATCH", "接收文件大小校验失败")
    actual_hash = sha256_file(part)
    if expected_hash and actual_hash != expected_hash:
        # 哈希失败不能留下“已下载长度足够”的坏断点，否则重试会跳过
        # 所有分块并永久重复失败。
        part.unlink(missing_ok=True)
        raise AgentCommandError("HASH_MISMATCH", "接收文件整体校验失败")
    final = _non_overwriting_target(receive_dir, str(payload.get("name", status.get("name", "接收文件"))))
    os.replace(part, final)
    return {"ok": True, "message": f"文件已保存到 {final.name}"}


def process_device_command(
    host_url: str,
    token: str,
    command: dict[str, object],
    config: dict[str, object],
    config_path: Path | None = None,
) -> bool:
    command_id = str(command.get("id", ""))
    command_type = str(command.get("type", ""))
    payload = command.get("payload", {})
    if not command_id or not isinstance(payload, dict):
        return False
    try:
        if command_type == "upload_file":
            result = upload_transfer(host_url, token, payload, config)
        elif command_type == "upload_bundle":
            result = upload_bundle_transfer(host_url, token, payload, config)
        elif command_type == "download_file":
            result = download_transfer(host_url, token, payload, config)
        elif command_type == "apply_update":
            result = apply_update_command(host_url, token, payload, config)
        elif command_type == "rotate_certificate" and config_path is not None:
            result = rotate_device_certificate(host_url, token, config, config_path)
        else:
            result = {
                "ok": False,
                "error_code": "COMMAND_UNSUPPORTED",
                "message": "当前 Agent 不支持该命令。",
            }
    except AgentCommandError as exc:
        if exc.retryable:
            return False
        result = {"ok": False, "error_code": exc.code, "message": str(exc)}
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        result = {
            "ok": False,
            "error_code": "AGENT_EXECUTION_FAILED",
            "message": f"设备端执行失败：{type(exc).__name__}",
        }
    acknowledged = ack_device_command(host_url, token, command_id, result)
    if (
        command_type == "apply_update"
        and bool(result.get("ok"))
        and config_path is not None
    ):
        # 更新程序替换的是磁盘上的 Agent。当前进程仍映射着旧版本，如果不
        # 主动重启，它会持续上报旧版本并让更新门禁一直阻止用户进入系统。
        _restart_agent_after_update(config_path)
    return acknowledged


def _restart_agent_after_update(config_path: Path) -> None:
    """用刚安装的新二进制原位替换 Agent，不依赖用户注销或再次双击。"""

    if getattr(sys, "frozen", False):
        arguments = [
            sys.executable,
            "--config",
            str(config_path),
            "--no-open-browser",
        ]
    else:
        arguments = [
            sys.executable,
            "-m",
            "app.client_agent",
            "--config",
            str(config_path),
            "--no-open-browser",
        ]
    os.execv(sys.executable, arguments)


def _run_windows_elevated_update(helper: Path, package: Path, timeout_seconds: int = 900) -> bool:
    """使用 Windows 原生 UAC 启动固定更新器，避免 PowerShell 参数拼接。"""

    try:
        import ctypes
        from ctypes import wintypes

        class ShellExecuteInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.ULONG),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", wintypes.LPVOID),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = str(helper)
        info.lpParameters = subprocess.list2cmdline(["--install-package", str(package)])
        info.lpDirectory = str(helper.parent)
        info.nShow = 0
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):  # type: ignore[attr-defined]
            raise ctypes.WinError()
        if not info.hProcess:
            return False
        try:
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(  # type: ignore[attr-defined]
                info.hProcess,
                max(1, timeout_seconds) * 1000,
            )
            if wait_result == 0x00000102:  # WAIT_TIMEOUT
                raise AgentCommandError(
                    "UPDATE_STATE_UNKNOWN",
                    "管理员更新器仍在运行，请勿重复点击；PartyOps 会在下一次心跳确认最终版本。",
                    retryable=False,
                )
            if wait_result == 0xFFFFFFFF:  # WAIT_FAILED
                raise ctypes.WinError(ctypes.get_last_error())
            if wait_result != 0:  # 非预期等待结果，保持保守失败。
                raise AgentCommandError(
                    "UPDATE_WAIT_FAILED",
                    "无法确认管理员更新器状态，请勿重复点击；请查看本机更新日志。",
                    retryable=False,
                )
            exit_code = wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                info.hProcess,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == 0
        finally:
            ctypes.windll.kernel32.CloseHandle(info.hProcess)  # type: ignore[attr-defined]
    except OSError as exc:
        if getattr(exc, "winerror", None) == 786 or getattr(exc, "errno", None) == 786:
            raise AgentCommandError(
                "ADMIN_POLICY_BLOCKED",
                (
                    "Windows 组织策略阻止了 PartyOps 更新器。请让单位电脑管理员允许"
                    "安装目录中的 PartyOpsUpdater.exe 后重试；系统不会绕过安全策略。"
                ),
            ) from exc
        logger.exception("Windows 更新管理员授权或执行未完成")
        return False
    except (AttributeError, ValueError):
        logger.exception("Windows 更新管理员授权或执行未完成")
        return False


def apply_update_command(
    host_url: str,
    token: str,
    payload: dict[str, object],
    config: dict[str, object],
) -> dict[str, object]:
    """下载更新包后交给固定路径 pkexec helper，不执行包内任意脚本。"""

    official_online = payload.get("official_online") is True
    expected_hash = ""
    expected_size = 0
    response_factory = None
    resume_offset_helper = None
    partial_open_helper = None
    if official_online:
        try:
            # 下载地址不接受主机命令传入。协同机使用自己冻结运行时中的
            # 公钥重新验签官网目录，并按本机系统/架构独立选包。
            from .routers.updates import (
                _open_partial_download,
                _open_trusted_update_url,
                _validated_resume_offset,
                fetch_online_update_catalog,
            )

            catalog = fetch_online_update_catalog()
            expected_version = str(payload.get("version") or "")
            if not bool(catalog["available"]) or str(catalog["version"]) != expected_version:
                raise ValueError("官方目录目标版本与主机升级命令不一致")
            expected_hash = str(catalog["package_sha256"])
            expected_size = int(catalog["package_size"])
            filename = (
                f"official-{expected_version}-{expected_hash[:12]}.partyops-update"
            )
            package_url = str(catalog["package_url"])

            def response_factory(offset: int = 0):
                if offset:
                    return _open_trusted_update_url(
                        package_url,
                        extra_headers={"Range": f"bytes={offset}-"},
                    )
                return _open_trusted_update_url(package_url)

            resume_offset_helper = _validated_resume_offset
            partial_open_helper = _open_partial_download
        except Exception as exc:
            raise AgentCommandError(
                "UPDATE_CATALOG_UNAVAILABLE",
                "官方更新目录暂时不可用或验证未通过，请稍后重试",
                retryable=True,
            ) from exc
    else:
        filename = Path(str(payload.get("package", ""))).name
        if not filename.endswith(".partyops-update"):
            return {
                "ok": False,
                "error_code": "UPDATE_PACKAGE_INVALID",
                "message": "更新包名称无效",
            }
        request = urllib.request.Request(
            (
                f"{host_url.rstrip('/')}/api/v1/devices/update-package/"
                f"{urllib.parse.quote(filename)}"
            ),
            headers={"X-PartyOps-Device-Token": token},
        )
        response_factory = lambda _offset=0: _urlopen(request, 300)
    updates_dir = Path(
        str(config.get("updates_dir", Path.home() / "PartyOps-更新"))
    ).expanduser().resolve()
    updates_dir.mkdir(parents=True, exist_ok=True)
    target = updates_dir / filename
    temporary: Path | None = None
    try:
        assert response_factory is not None
        if (
            official_online
            and target.is_file()
            and target.stat().st_size == expected_size
            and sha256_file(target) == expected_hash
        ):
            # 已完整校验的同一官方包可直接交给更新器，避免设备重启或命令
            # 重试后重复下载数百 MB。
            pass
        else:
            if target.exists():
                target.unlink()
            if official_online:
                assert resume_offset_helper is not None
                assert partial_open_helper is not None
                temporary = updates_dir / f".{filename}.part"
                resume_at = resume_offset_helper(temporary, expected_size)
                digest = hashlib.sha256()
                if resume_at:
                    with temporary.open("rb") as existing:
                        while chunk := existing.read(1024 * 1024):
                            digest.update(chunk)
            else:
                resume_at = 0
                digest = hashlib.sha256()
                temporary = None
            with response_factory(resume_at) as response:
                status = int(getattr(response, "status", 200))
                if official_online and resume_at and status == 206:
                    expected_range = (
                        f"bytes {resume_at}-{expected_size - 1}/{expected_size}"
                    )
                    if str(response.headers.get("Content-Range", "")).strip() != expected_range:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_RANGE_INVALID",
                            "官方更新包断点范围与签名目录不一致",
                        )
                    write_offset = resume_at
                elif status == 200:
                    write_offset = 0
                    resume_at = 0
                    digest = hashlib.sha256()
                else:
                    raise AgentCommandError(
                        "UPDATE_PACKAGE_RANGE_INVALID",
                        "更新服务器未返回可安全使用的完整内容或断点范围",
                    )
                if official_online:
                    handle_context = partial_open_helper(
                        temporary,
                        offset=write_offset,
                    )
                else:
                    handle_context = tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=updates_dir,
                        prefix=f".{filename}.",
                        suffix=".part",
                        delete=False,
                    )
                with handle_context as handle:
                    if not official_online:
                        temporary = Path(handle.name)
                    headers = getattr(response, "headers", {})
                    if official_online and str(headers.get("Content-Encoding", "")).strip():
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_ENCODING_INVALID",
                            "官方更新包响应使用了不允许的传输编码",
                        )
                    try:
                        content_length = int(headers.get("Content-Length", "0") or 0)
                    except (TypeError, ValueError) as exc:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_LENGTH_INVALID",
                            "更新服务器返回的更新包长度无效",
                        ) from exc
                    if content_length < 0:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_LENGTH_INVALID",
                            "更新服务器返回的更新包长度无效",
                        )
                    if content_length > MAX_UPDATE_PACKAGE_BYTES:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_TOO_LARGE",
                            "更新包超过本机允许的安全上限",
                        )
                    if official_online and content_length != expected_size - write_offset:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_LENGTH_MISMATCH",
                            "官方更新包大小与签名目录不一致",
                        )
                    if (
                        content_length
                        and shutil.disk_usage(updates_dir).free
                        < content_length + BACKUP_FREE_SPACE_RESERVE_BYTES
                    ):
                        raise AgentCommandError(
                            "UPDATE_DISK_FULL",
                            "本机空间不足，暂未下载更新包",
                        )
                    received = write_offset
                    try:
                        while chunk := response.read(1024 * 1024):
                            received += len(chunk)
                            if received > MAX_UPDATE_PACKAGE_BYTES:
                                raise AgentCommandError(
                                    "UPDATE_PACKAGE_TOO_LARGE",
                                    "更新包超过本机允许的安全上限",
                                )
                            handle.write(chunk)
                            digest.update(chunk)
                    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                        handle.flush()
                        os.fsync(handle.fileno())
                        raise
                    if content_length and received - write_offset != content_length:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_LENGTH_MISMATCH",
                            "更新包实际长度与更新服务器声明不一致",
                        )
                    handle.flush()
                    os.fsync(handle.fileno())
                    if official_online and digest.hexdigest() != expected_hash:
                        raise AgentCommandError(
                            "UPDATE_PACKAGE_HASH_MISMATCH",
                            "官方更新包 SHA-256 与签名目录不一致",
                        )
            assert temporary is not None
            os.replace(temporary, target)
            temporary = None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        # 官方包使用固定片段名并在重试时重新计算已有内容哈希；网络中断
        # 保留该片段。手工/主机包无外层固定哈希，仍删除临时文件。
        if temporary is not None and not official_online:
            temporary.unlink(missing_ok=True)
        raise AgentCommandError(
            "NETWORK_INTERRUPTED",
            "更新包下载中断；重新连接后会自动继续",
            retryable=True,
        ) from exc
    except AgentCommandError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    helper = (
        Path(sys.executable).resolve().with_name("PartyOpsUpdater.exe")
        if os.name == "nt"
        else Path("/opt/partyops/partyops-updater")
    )
    if not helper.exists():
        target.unlink(missing_ok=True)
        return {
            "ok": False,
            "error_code": "UPDATE_HELPER_MISSING",
            "message": "本机缺少受限更新服务，请先安装 1.1.1 桥接包。",
        }
    try:
        if os.name == "nt":
            installed = _run_windows_elevated_update(helper, target)
        else:
            result = subprocess.run(
                ["pkexec", str(helper), "--install-package", str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=900,
            )
            installed = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("本机受限更新器未能完成")
        installed = False
    if not installed:
        return {
            "ok": False,
            "error_code": "UPDATE_INSTALL_FAILED",
            "message": "系统更新未完成，请查看本机更新记录。",
        }
    return {"ok": True, "message": "设备升级完成"}


def _agent_headers(token: str, device_auth: bool = False) -> dict[str, str]:
    """为新版设备证书令牌和旧版灾备配对令牌选择互不混用的请求头。"""

    header = "X-PartyOps-Device-Token" if device_auth else "X-PartyOps-Pairing"
    return {header: token}


def pull_backup(
    host_url: str,
    token: str,
    destination: Path,
    device_auth: bool = False,
) -> Path | None:
    destination.mkdir(parents=True, exist_ok=True)
    latest_checksum = destination / ".latest.sha256"
    previous_hash = (
        latest_checksum.read_text(encoding="utf-8").strip()
        if latest_checksum.exists()
        else ""
    )
    headers = _agent_headers(token, device_auth)
    if previous_hash:
        headers["If-None-Match"] = f'"{previous_hash}"'
    request = urllib.request.Request(
        f"{host_url.rstrip('/')}/api/v1/backups/latest",
        headers=headers,
    )
    try:
        with _urlopen(request, 30) as response:
            disposition = response.headers.get("Content-Disposition", "")
            filename = _response_filename(disposition)
            target = destination / Path(filename).name
            raw_length = response.headers.get("Content-Length", "")
            available_budget = max(
                0,
                shutil.disk_usage(destination).free - BACKUP_FREE_SPACE_RESERVE_BYTES,
            )
            if available_budget <= 0:
                raise ValueError("灾备目录可用空间不足")
            if raw_length:
                try:
                    declared_length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("主机返回的灾备长度无效") from exc
                if declared_length < 0 or declared_length > MAX_BACKUP_DOWNLOAD_BYTES:
                    raise ValueError("灾备副本超过终端下载上限")
                if declared_length > available_budget:
                    raise ValueError("灾备目录可用空间不足")
            temporary: Path | None = None
            try:
                digest = hashlib.sha256()
                written = 0
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination,
                    prefix=f".{target.name}.",
                    suffix=".part",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    while chunk := response.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_BACKUP_DOWNLOAD_BYTES or written > available_budget:
                            raise ValueError("灾备副本超过终端下载上限")
                        handle.write(chunk)
                        digest.update(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if raw_length and written != declared_length:
                    raise ValueError("灾备副本实际长度与主机声明不一致")
                expected = response.headers.get("X-PartyOps-SHA256", "")
                actual = digest.hexdigest()
                if expected and expected.lower() != actual:
                    raise ValueError("下载文件与主机校验值不一致")
                assert temporary is not None
                verify_local_backup(temporary)
                os.replace(temporary, target)
                temporary = None
                latest_checksum.write_text(actual, encoding="utf-8")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            checksum_path = target.with_suffix(target.suffix + ".sha256")
            checksum_path.write_text(
                f"{sha256_file(target)}  {target.name}\n", encoding="utf-8"
            )
            return target
    except urllib.error.HTTPError as exc:
        # 新入网协同机在主机尚未创建首份灾备时会得到 404；这是正常的
        # “暂无可拉取副本”，不应污染 Agent 错误日志或让 --once 失败。
        if exc.code in {304, 404}:
            return None
        raise
    except (urllib.error.URLError, TimeoutError):
        return None


def host_reachable(host_url: str) -> bool:
    """区分“备份未变化”与“主机不可达”，避免终端误报离线。"""

    request = urllib.request.Request(f"{host_url.rstrip('/')}/api/v1/health")
    try:
        with _urlopen(request, 5) as response:
            return response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def fetch_notification_summary(
    host_url: str,
    token: str,
    device_auth: bool = False,
) -> dict[str, object] | None:
    """读取脱敏提醒摘要；终端永远拿不到任务标题、正文或文件路径。"""

    request = urllib.request.Request(
        f"{host_url.rstrip('/')}/api/v1/notifications/paired-summary",
        headers=_agent_headers(token, device_auth),
    )
    try:
        with _urlopen(request, 5) as response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
            return {
                "unread_count": max(0, int(payload.get("unread_count", 0))),
                "revision": str(payload.get("revision", "")),
            }
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def show_desktop_notification(unread_count: int) -> bool:
    """调用 UOS/FreeDesktop 标准通知程序；不可用时静默保留站内提醒。"""

    executable = shutil.which("notify-send")
    if not executable or unread_count <= 0:
        return False
    try:
        subprocess.run(  # noqa: S603 - executable 由 PATH 固定解析，参数不进入 shell。
            [
                executable,
                "--app-name=党建智办",
                "--icon=partyops",
                "党建智办",
                f"您有 {unread_count} 条未读工作提醒，请双击桌面图标查看。",
            ],
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def poll_desktop_notifications(
    host_url: str,
    token: str,
    destination: Path,
    device_auth: bool = False,
) -> bool:
    summary = fetch_notification_summary(host_url, token, device_auth)
    if not summary:
        return False
    revision = str(summary["revision"])
    count = int(summary["unread_count"])
    revision_path = destination / ".notification-revision"
    previous = (
        revision_path.read_text(encoding="utf-8").strip()
        if revision_path.exists()
        else ""
    )
    if not revision or revision == previous or count <= 0:
        return False
    if not show_desktop_notification(count):
        return False
    destination.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(revision, encoding="utf-8")
    return True


def _heartbeat_loop(
    stop_event: threading.Event,
    host_url: str,
    token: str,
    config: dict[str, object],
) -> None:
    """独立维持设备在线状态，避免扫描、传输或更新阻塞心跳。"""

    while not stop_event.wait(HEARTBEAT_INTERVAL_SECONDS):
        send_device_heartbeat(host_url, token, config)


def run(
    config_path: Path,
    once: bool = False,
    open_browser: bool | None = None,
) -> int:
    if not config_path.exists():
        print(f"配置不存在：{config_path}", file=sys.stderr)
        return 2
    configure_agent_logging(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    configure_ssl_context(config)
    try:
        host_url, token, destination = validate_config(config)
    except ValueError as exc:
        print(f"配置无效：{exc}", file=sys.stderr)
        return 2
    should_open_browser = (
        bool(config.get("open_browser", True))
        if open_browser is None
        else open_browser
    )
    agent_host_url = str(config.get("agent_url") or host_url).rstrip("/")
    if should_open_browser:
        webbrowser.open(create_browser_launch_url(host_url, agent_host_url, token))
    backup_interval = int(config.get("interval_seconds", 600))
    notification_interval = max(
        15,
        min(300, int(config.get("notification_interval_seconds", 30))),
    )
    next_backup_at = 0.0
    next_command_at = 0.0
    next_notification_at = 0.0
    next_scan_at = 0.0
    device_auth = bool(config.get("device_token"))
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if device_auth:
        # 第一次心跳同步发送，确保刚入网的设备立即出现在主机设备中心。
        send_device_heartbeat(agent_host_url, token, config)
        if not once:
            heartbeat_thread = threading.Thread(
                target=_heartbeat_loop,
                args=(heartbeat_stop, agent_host_url, token, config),
                name="partyops-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()
    try:
        while True:
            now = time.monotonic()
            # 命令轮询必须使用独立时钟，不能与心跳共用截止时间；否则心跳
            # 每次续期后，目录同步、传输与系统更新命令将永远没有机会执行。
            if device_auth and not once and now >= next_command_at:
                try:
                    sync_shared_roots(agent_host_url, token, config, config_path)
                except (
                    OSError,
                    ValueError,
                    urllib.error.HTTPError,
                    urllib.error.URLError,
                ) as exc:
                    _record_agent_failure(config_path, config, "shared_root_sync", exc)
                for command in poll_device_commands(agent_host_url, token):
                    process_device_command(
                        agent_host_url,
                        token,
                        command,
                        config,
                        config_path,
                    )
                next_command_at = time.monotonic() + COMMAND_POLL_INTERVAL_SECONDS

            result = None
            if once or now >= next_backup_at:
                try:
                    result = pull_backup(host_url, token, destination, device_auth)
                except (
                    OSError,
                    TimeoutError,
                    ValueError,
                    urllib.error.HTTPError,
                    urllib.error.URLError,
                    zipfile.BadZipFile,
                ) as exc:
                    print(f"灾备拉取失败：{exc}", file=sys.stderr)
                    if once:
                        return 1
                next_backup_at = time.monotonic() + backup_interval
                if result:
                    print(f"已拉取灾备副本：{result}")
                    if once:
                        return 0
                elif once:
                    if host_reachable(host_url):
                        print("主机在线，灾备副本已是最新。")
                        return 0
                    print("主机暂不可达，未拉取备份。", file=sys.stderr)
                    return 1

            if (
                device_auth
                and now >= next_scan_at
                and _safe_shared_roots(config)
            ):
                try:
                    indexed, _errors = scan_and_upload_roots(
                        agent_host_url,
                        token,
                        config,
                        config_path,
                    )
                    config["indexed_file_count"] = indexed
                    _save_config(config_path, config)
                    if _errors:
                        logger.warning("scan_partial indexed=%s errors=%s", indexed, _errors)
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    urllib.error.HTTPError,
                    urllib.error.URLError,
                ) as exc:
                    _record_agent_failure(config_path, config, "workspace_scan", exc)
                next_scan_at = time.monotonic() + max(
                    300,
                    int(config.get("scan_interval_seconds", 600) or 600),
                )

            if now >= next_notification_at:
                poll_desktop_notifications(
                    host_url,
                    token,
                    destination,
                    device_auth,
                )
                next_notification_at = time.monotonic() + notification_interval
            if once:
                return 0
            time.sleep(1)
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="党建智办协同终端伴随进程")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="后台自启动时不打开浏览器",
    )
    args = parser.parse_args()
    raise SystemExit(
        run(args.config, args.once, open_browser=False if args.no_open_browser else None)
    )


if __name__ == "__main__":
    main()
