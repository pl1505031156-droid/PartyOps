"""签名离线更新包的校验、分发和可回滚运行记录。"""

from __future__ import annotations

import typing

import base64
import hashlib
import hmac
import json
import logging
import os
import shutil
import stat
import sys
import unicodedata
import zipfile
import secrets
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import __version__
from ..audit import emit_event, write_audit
from ..backups import SCHEMA_VERSION
from ..config import get_settings
from ..database import db_runtime, get_session
from ..device_versions import ensure_current_release
from ..models import (
    Device,
    DeviceCommand,
    ReleaseHistory,
    UpdatePackage,
    UpdateRun,
    User,
    utcnow,
)
from ..platform_info import (
    detect_platform_info,
    normalize_architecture,
    update_platform_key,
)
from ..problems import ProblemException
from ..schemas import (
    ReleaseHistoryOut,
    UpdateApplyRequest,
    UpdatePackageOut,
    UpdateRunOut,
)
from ..security import require_admin
from ..enums import UpdateStatus
from ..versioning import parse_release_version
from ..upgrades import create_pre_upgrade_backup
from ..update_executor import (
    _trusted_public_key,
    launch_linux_personal_update,
    launch_macos_update,
    launch_windows_personal_update,
)

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - 生产依赖固定包含 cryptography
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


router = APIRouter(tags=["updates"])
logger = logging.getLogger("partyops.updates")
SUPPORTED_FORMAT_VERSIONS = {2, 3, 4}
MIN_UPDATE_FREE_BYTES = 512 * 1024 * 1024
MAX_UPDATE_MEMBERS = 16
MAX_UPDATE_MANIFEST_BYTES = 1024 * 1024
MAX_UPDATE_ARTIFACT_BYTES = 4 * 1024**3
MAX_UPDATE_EXPANDED_BYTES = 16 * 1024**3
MAX_UPDATE_CATALOG_BYTES = 256 * 1024
_online_download_lock = threading.Lock()
_online_download_ids: set[str] = set()
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}

V3_PLATFORM_ARTIFACTS = {
    "windows": {"amd64": "_windows_amd64.exe"},
    "windows7": {
        "amd64": "_windows7_amd64.exe",
        "x86": "_windows7_x86.exe",
    },
    "linux-deb": {
        "amd64": "_linux_amd64.deb",
        "arm64": "_linux_arm64.deb",
    },
    "linux-rpm": {
        "amd64": ".x86_64.rpm",
        "arm64": ".aarch64.rpm",
    },
}


class _DuplicateJSONFieldError(ValueError):
    """签名 JSON 出现重复字段，避免不同解析器产生歧义。"""
V4_PLATFORM_ARTIFACTS = {
    **V3_PLATFORM_ARTIFACTS,
    "macos": {
        "amd64": "_macos_x86_64.pkg",
        "arm64": "_macos_arm64.pkg",
    },
}


def _trusted_download_hosts() -> set[str]:
    return {
        host.strip().lower().rstrip(".")
        for host in get_settings().update_download_hosts.split(",")
        if host.strip()
    }


def _validate_update_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ProblemException(
            502, "UPDATE_URL_INVALID", "官方更新地址无效", "更新地址格式不正确。"
        ) from exc
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or hostname not in _trusted_download_hosts()
    ):
        raise ProblemException(
            502,
            "UPDATE_URL_NOT_TRUSTED",
            "官方更新地址不在受信范围",
            "系统已拒绝访问非 HTTPS、带凭据、非标准端口或未登记主机的更新地址。",
        )
    return urllib.parse.urlunparse(parsed._replace(fragment=""))


class _RestrictedUpdateRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            _validate_update_url(newurl),
        )


def _reject_duplicate_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONFieldError(f"JSON 包含重复字段：{key}")
        result[key] = value
    return result


def _open_trusted_update_url(
    url: str,
    *,
    extra_headers: dict[str, str] | None = None,
):
    opener = urllib.request.build_opener(_RestrictedUpdateRedirect())
    headers = {
        "Accept": "application/json, application/octet-stream",
        "Accept-Encoding": "identity",
    }
    if extra_headers:
        # 调用方只能增加断点续传所需的 Range；禁止覆盖身份、Host 或编码
        # 头，避免受信下载函数被意外扩展成通用网络代理。
        unexpected = set(extra_headers) - {"Range"}
        if unexpected:
            raise ValueError(f"更新下载包含不允许的请求头：{sorted(unexpected)}")
        headers.update(extra_headers)
    request = urllib.request.Request(
        _validate_update_url(url),
        headers=headers,
    )
    return opener.open(request, timeout=30)  # nosec B310 - URL 已限定 HTTPS、端口与精确主机白名单。


def _validated_resume_offset(path: Path, expected_size: int) -> int:
    """只复用普通、未超长的下载片段；异常文件立即从零重下。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    except OSError:
        path.unlink(missing_ok=True)
        return 0
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_size <= 0
        or metadata.st_size >= expected_size
    ):
        path.unlink(missing_ok=True)
        return 0
    return metadata.st_size


def _open_partial_download(path: Path, *, offset: int):
    """以不可跟随链接的文件描述符打开更新片段并复核续传偏移。"""

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if offset:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | no_follow)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != offset:
            os.close(descriptor)
            raise OSError("更新下载片段在续传前被替换")
    else:
        path.unlink(missing_ok=True)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
            0o600,
        )
    return os.fdopen(descriptor, "ab" if offset else "wb")


def _catalog_network_problem(exc: BaseException) -> ProblemException:
    """把官方更新故障分类为可执行的脱敏诊断，不暴露代理或本机路径。"""

    if isinstance(exc, urllib.error.HTTPError):
        return ProblemException(
            502,
            "UPDATE_CATALOG_HTTP_ERROR",
            f"官方更新服务返回 HTTP {exc.code}",
            "当前版本不会受到影响；请稍后重试，并把该状态码提供给技术支持。",
        )
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    reason_text = str(reason).lower()
    if isinstance(reason, socket.gaierror):
        return ProblemException(
            502,
            "UPDATE_CATALOG_DNS_FAILED",
            "无法解析官方更新域名",
            "请检查 DNS、网关或单位网络策略；当前版本不会受到影响。",
        )
    if isinstance(reason, (ssl.SSLError, ssl.CertificateError)) or any(
        marker in reason_text for marker in ("certificate verify", "ssl", "tls")
    ):
        return ProblemException(
            502,
            "UPDATE_CATALOG_TLS_FAILED",
            "官方更新 TLS 校验失败",
            "请检查系统时间、根证书和单位 HTTPS 检查策略；系统不会绕过证书校验。",
        )
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in reason_text:
        return ProblemException(
            504,
            "UPDATE_CATALOG_TIMEOUT",
            "读取官方更新信息超时",
            "请检查网络稳定性后重试；当前版本不会受到影响。",
        )
    if "proxy" in reason_text or "tunnel" in reason_text:
        return ProblemException(
            502,
            "UPDATE_CATALOG_PROXY_FAILED",
            "单位代理未能连接官方更新服务",
            "请核对系统代理和单位网络放行策略；诊断不会显示代理凭据。",
        )
    return ProblemException(
        502,
        "UPDATE_CATALOG_NETWORK_FAILED",
        "官方更新网络连接失败",
        "请检查网络后重试；当前版本不会受到影响。",
    )


def fetch_online_update_catalog() -> dict[str, object]:
    """读取并验证官网更新目录；目录签名与包内签名是两道独立门禁。"""

    try:
        with _open_trusted_update_url(get_settings().update_catalog_url) as response:
            if (
                response.status != 200
                or str(response.headers.get("Content-Encoding", "")).strip()
            ):
                raise ValueError("更新目录响应状态或编码不符合要求")
            raw = response.read(MAX_UPDATE_CATALOG_BYTES + 1)
        if len(raw) > MAX_UPDATE_CATALOG_BYTES:
            raise ValueError("更新目录超过 256KB")
        catalog = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONFieldError) as exc:
        raise ProblemException(
            502,
            "UPDATE_CATALOG_FORMAT_INVALID",
            "官方更新清单不是有效 JSON",
            "官网可能错误返回了网页内容；系统已拒绝使用，当前版本不会受到影响。",
        ) from exc
    except ValueError as exc:
        raise ProblemException(
            502,
            "UPDATE_CATALOG_RESPONSE_INVALID",
            "官方更新响应格式异常",
            "响应状态、压缩方式或字段编码不符合更新协议，系统已拒绝使用。",
        ) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise _catalog_network_problem(exc) from exc
    catalog_format_version = (
        catalog.get("format_version") if isinstance(catalog, dict) else None
    )
    if (
        not isinstance(catalog, dict)
        or catalog.get("format") != "partyops-update-channel"
        # bool 是 int 的子类；签名目录仍必须使用真正的 JSON 整数，避免
        # 不同实现把 true/1 解析成不同契约。
        or type(catalog_format_version) is not int
        or catalog_format_version not in {1, 2, 3}
        or not _manifest_signature_valid(catalog)
    ):
        raise ProblemException(
            502,
            "UPDATE_CATALOG_SIGNATURE_INVALID",
            "官方更新目录签名无效",
            "系统已拒绝使用该更新信息，当前版本不会受到影响。",
        )
    release = catalog.get("release")
    if not isinstance(release, dict):
        raise ProblemException(
            502, "UPDATE_CATALOG_INVALID", "官方更新目录内容不完整", "缺少版本信息。"
        )
    try:
        version = str(release["version"])
        parsed_version = parse_release_version(version)
        published_at = str(release["published_at"])
        published_time = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published_time.tzinfo is None or len(published_at) > 80:
            raise ValueError("上传时间必须包含时区")
    except (KeyError, TypeError, ValueError, ProblemException) as exc:
        raise ProblemException(
            502,
            "UPDATE_CATALOG_INVALID",
            "官方更新目录内容不完整",
            "版本或上传时间无效。",
        ) from exc
    notes = release.get("release_notes")
    if (
        not isinstance(notes, list)
        or not notes
        or any(
            not isinstance(note, str) or not note.strip() or len(note) > 500
            for note in notes
        )
    ):
        raise ProblemException(
            502,
            "UPDATE_CATALOG_INVALID",
            "官方更新目录内容不完整",
            "更新内容无效。",
        )
    result: dict[str, object] = {
        "available": False,
        "target_available": True,
        "availability_message": "",
        "current_version": __version__,
        "version": version,
        "title": str(release.get("title") or "PartyOps 功能与稳定性更新")[:200],
        "release_notes": notes[:50],
        "published_at": published_at,
    }
    package_record = release
    if catalog_format_version in {2, 3}:
        platform_name = update_platform_key(detect_platform_info())
        architecture = normalize_architecture()
        packages = release.get("platform_packages")
        platform_packages = (
            packages.get(platform_name) if isinstance(packages, dict) else None
        )
        selected = (
            platform_packages.get(architecture)
            if isinstance(platform_packages, dict)
            else None
        )
        if not isinstance(selected, dict):
            result.update(
                {
                    "target_available": False,
                    "availability_message": (
                        f"{version} 的 {platform_name}/{architecture} 制品尚未通过发布门禁；"
                        "系统不会下载其他平台的安装包，当前版本可继续安全使用。"
                    ),
                }
            )
            return result
        package_record = selected
    try:
        size = int(package_record["package_size"])
        sha256 = str(package_record["package_sha256"]).lower()
        package_url = _validate_update_url(package_record["package_url"])
    except (KeyError, TypeError, ValueError, ProblemException) as exc:
        if isinstance(exc, ProblemException) and exc.code in {
            "UPDATE_URL_INVALID",
            "UPDATE_URL_NOT_TRUSTED",
        }:
            raise
        raise ProblemException(
            502,
            "UPDATE_CATALOG_INVALID",
            "官方更新目录内容不完整",
            "大小、哈希或下载地址无效。",
        ) from exc
    if (
        size <= 0
        or size > MAX_UPDATE_ARTIFACT_BYTES
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ProblemException(
            502,
            "UPDATE_CATALOG_INVALID",
            "官方更新目录内容不完整",
            "制品元数据无效。",
        )
    result.update(
        {
            "available": parsed_version > parse_release_version(__version__),
            "package_url": package_url,
            "package_size": size,
            "package_sha256": sha256,
        }
    )
    return result


def _online_download_state(
    catalog: dict[str, object],
    *,
    received: int = 0,
    state: str = "waiting",
    message: str = "等待下载",
) -> dict[str, object]:
    """生成可持久化的公开下载进度，不保存签名或其他敏感值。"""

    size = int(catalog["package_size"])
    return {
        "source": "official-online-catalog",
        "download_state": state,
        "download_message": message,
        "download_received": max(0, min(received, size)),
        "download_total": size,
        "package_url": str(catalog["package_url"]),
        "package_sha256": str(catalog["package_sha256"]),
        "release_title": str(catalog["title"]),
        "release_notes": list(catalog["release_notes"]),
        "published_at": str(catalog["published_at"]),
    }


def _set_online_download_progress(
    package_id: str,
    catalog: dict[str, object],
    *,
    received: int,
    state: str,
    message: str,
    status: UpdateStatus | None = None,
) -> None:
    with db_runtime.session_factory() as db:
        package = db.get(UpdatePackage, package_id)
        if not package:
            return
        package.manifest = _online_download_state(
            catalog,
            received=received,
            state=state,
            message=message,
        )
        if status is not None:
            package.status = status
        db.commit()


def _download_online_update(package_id: str, catalog: dict[str, object]) -> None:
    """后台下载官方统一更新包，完成外层哈希与包内签名双重校验。"""

    settings = get_settings()
    expected_size = int(catalog["package_size"])
    expected_hash = str(catalog["package_sha256"])
    incoming = settings.updates_dir / f".{package_id}.incoming"
    final_name = f"partyops_{catalog['version']}_{expected_hash[:12]}.partyops-update"
    final_path = settings.updates_dir / final_name
    received = 0
    last_reported = 0
    digest = hashlib.sha256()
    discard_partial = True
    try:
        _ensure_free_space(
            settings.updates_dir,
            max(MIN_UPDATE_FREE_BYTES, expected_size * 3),
        )
        resume_at = _validated_resume_offset(incoming, expected_size)
        if resume_at:
            with incoming.open("rb") as existing:
                while chunk := existing.read(1024 * 1024):
                    digest.update(chunk)
            received = resume_at
            last_reported = resume_at
            discard_partial = False
        _set_online_download_progress(
            package_id,
            catalog,
            received=received,
            state="downloading",
            message=(
                "正在从上次中断位置继续安全下载"
                if resume_at
                else "正在从官网安全下载更新包"
            ),
        )
        request_headers = {"Range": f"bytes={resume_at}-"} if resume_at else None
        response_context = (
            _open_trusted_update_url(
                str(catalog["package_url"]),
                extra_headers=request_headers,
            )
            if request_headers
            else _open_trusted_update_url(str(catalog["package_url"]))
        )
        with response_context as response:
            status = int(response.status)
            if status not in ({200, 206} if resume_at else {200}):
                raise ValueError(f"下载响应状态为 {response.status}")
            if str(response.headers.get("Content-Encoding", "")).strip():
                raise ValueError("更新包响应不得压缩传输")
            if resume_at and status == 206:
                expected_range = (
                    f"bytes {resume_at}-{expected_size - 1}/{expected_size}"
                )
                if (
                    str(response.headers.get("Content-Range", "")).strip()
                    != expected_range
                ):
                    raise ValueError("断点续传范围与签名目录不一致")
                write_offset = resume_at
            else:
                # 下载站点不支持 Range 时，200 的完整响应仍可安全从零写入，
                # 不把旧片段与新响应拼接。
                write_offset = 0
                received = 0
                last_reported = 0
                digest = hashlib.sha256()
            try:
                content_length = int(response.headers.get("Content-Length", ""))
            except (TypeError, ValueError) as exc:
                raise ValueError("更新包缺少有效 Content-Length") from exc
            if content_length != expected_size - write_offset:
                raise ValueError("更新包响应大小与签名目录不一致")
            discard_partial = False
            with _open_partial_download(incoming, offset=write_offset) as handle:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > expected_size:
                        discard_partial = True
                        raise ValueError("更新包实际大小超过签名目录")
                    handle.write(chunk)
                    digest.update(chunk)
                    if received - last_reported >= 8 * 1024 * 1024:
                        handle.flush()
                        _set_online_download_progress(
                            package_id,
                            catalog,
                            received=received,
                            state="downloading",
                            message="正在下载并实时校验更新包",
                        )
                        last_reported = received
                handle.flush()
                os.fsync(handle.fileno())
        if received != expected_size:
            discard_partial = False
            raise ValueError("更新包下载尚未完整")
        if not hmac.compare_digest(digest.hexdigest(), expected_hash):
            discard_partial = True
            raise ValueError("更新包大小或 SHA-256 与签名目录不一致")
        # 已完整下载但包内契约无效时不得保留为可续传片段。
        discard_partial = True
        manifest = _extract_manifest(incoming)
        if str(manifest.get("version", "")) != str(catalog["version"]):
            raise ValueError("更新包内部版本与签名目录不一致")
        # 同名文件只可能来自相同版本与外层哈希。若已存在仍逐字节核对，
        # 防止异常中断留下的旧文件被误当成此次下载。
        if final_path.exists():
            if final_path.stat().st_size != expected_size or not hmac.compare_digest(
                _sha256_path(final_path), expected_hash
            ):
                raise ValueError("本地已有同名更新包但内容不一致")
            incoming.unlink(missing_ok=True)
        else:
            incoming.replace(final_path)
        with db_runtime.session_factory() as db:
            package = db.get(UpdatePackage, package_id)
            if not package:
                final_path.unlink(missing_ok=True)
                return
            package.filename = final_name
            package.version = str(manifest["version"])
            package.min_version = str(manifest.get("min_version", ""))
            package.schema_revision = str(manifest.get("schema_revision", ""))
            package.manifest = {
                **{
                    key: value
                    for key, value in manifest.items()
                    if key not in {"signature", "public_key"}
                },
                "online_download": _online_download_state(
                    catalog,
                    received=received,
                    state="ready",
                    message="下载、签名与哈希校验均已通过",
                ),
            }
            package.sha256 = expected_hash
            package.signature_valid = True
            package.status = UpdateStatus.VALIDATED
            db.commit()
    except Exception as exc:
        logger.exception("官方更新包后台下载或校验失败，任务=%s", package_id)
        if discard_partial:
            incoming.unlink(missing_ok=True)
        partial_saved = incoming.is_file()
        _set_online_download_progress(
            package_id,
            catalog,
            received=received,
            state="failed",
            message=(
                "安全下载暂时中断，已保留校验过的下载进度；重试会从中断位置继续。"
                if partial_saved
                else "安全下载未完成。请检查网络后重试；当前版本和业务数据不会受到影响。"
            ),
            status=UpdateStatus.FAILED,
        )


def _start_online_download(package_id: str, catalog: dict[str, object]) -> bool:
    """同一更新记录进程内只运行一个下载线程。"""

    with _online_download_lock:
        if package_id in _online_download_ids:
            return False
        _online_download_ids.add(package_id)

    def worker() -> None:
        try:
            _download_online_update(package_id, catalog)
        finally:
            with _online_download_lock:
                _online_download_ids.discard(package_id)

    threading.Thread(
        target=worker,
        name=f"partyops-online-update-{package_id[:8]}",
        daemon=True,
    ).start()
    return True


def _sha256_path(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_member(name: str) -> str:
    """返回跨平台碰撞键，并拒绝 Windows 特殊路径语义。"""

    path = PurePosixPath(name)
    segments = name.split("/")
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or len(name) > 512
        or any(
            not segment
            or segment in {".", ".."}
            or segment.endswith((" ", "."))
            or ":" in segment
            or any(ord(character) < 32 for character in segment)
            or segment.rstrip(" .").split(".", 1)[0].casefold()
            in _WINDOWS_RESERVED_NAMES
            for segment in segments
        )
    ):
        raise ProblemException(
            422,
            "UPDATE_PACKAGE_INVALID",
            "更新包路径无效",
            "更新包包含不安全的文件路径。",
        )
    # NFC + casefold 同时覆盖 Windows 大小写碰撞与常见 Unicode 等价名，
    # 避免同一签名清单在不同文件系统上解析成不同文件。
    return unicodedata.normalize("NFC", "/".join(segments)).casefold()


def _validate_zip_entry_type(info: zipfile.ZipInfo) -> None:
    """更新包只允许普通、未加密且使用受控算法的文件。"""

    mode = (info.external_attr >> 16) & 0o170000
    if (
        info.is_dir()
        or mode not in {0, stat.S_IFREG}
        or info.flag_bits & 0x1
        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    ):
        raise ProblemException(
            422,
            "UPDATE_PACKAGE_INVALID",
            "更新包包含不支持的成员",
            "更新包不得包含链接、设备节点、加密成员或非标准压缩格式。",
        )
    if (
        info.file_size > 100 * 1024**2
        and info.file_size > max(info.compress_size, 1) * 1000
    ):
        raise ProblemException(
            422,
            "UPDATE_COMPRESSION_INVALID",
            "更新包压缩比异常",
            "更新包成员的声明容量与压缩容量不合理，请重新获取官方更新包。",
        )


def _version_tuple(value: object):
    """保留旧内部函数名，实际返回可正确区分候选版的标准版本对象。"""

    return parse_release_version(value)


def _ensure_free_space(path, required_bytes: int) -> None:
    free = shutil.disk_usage(path).free
    if free < required_bytes:
        required_gb = required_bytes / 1024**3
        free_gb = free / 1024**3
        raise ProblemException(
            507,
            "UPDATE_DISK_FULL",
            "升级空间不足",
            f"至少需要 {required_gb:.1f} GB 可用空间，当前约 {free_gb:.1f} GB。",
        )


def _validate_manifest_contract(manifest: dict) -> None:
    if manifest.get("format") != "partyops-update":
        raise ProblemException(
            422,
            "UPDATE_FORMAT_INVALID",
            "更新包类型无效",
            "请选择由党建智办发布工具生成的更新包。",
        )
    raw_format_version = manifest.get("format_version", 0)
    if type(raw_format_version) is not int:
        raise ProblemException(
            422,
            "UPDATE_FORMAT_VERSION_UNSUPPORTED",
            "更新包格式版本无效",
            "format_version 必须是真正的 JSON 整数。",
        )
    format_version = raw_format_version
    if format_version not in SUPPORTED_FORMAT_VERSIONS:
        raise ProblemException(
            422,
            "UPDATE_FORMAT_VERSION_UNSUPPORTED",
            "更新包格式版本不受支持",
            "当前支持第 2、3、4 版更新包格式。",
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ProblemException(
            422,
            "UPDATE_MANIFEST_INVALID",
            "更新制品清单无效",
            "制品清单必须按文件名列出。",
        )
    if format_version == 4:
        _validate_v4_platform_artifacts(manifest, artifacts)
    elif format_version == 3:
        _validate_v3_platform_artifacts(manifest, artifacts)
    else:
        _validate_v2_platform_artifacts(manifest, artifacts)
    target = _version_tuple(manifest.get("version"))
    current = _version_tuple(__version__)
    minimum = _version_tuple(manifest.get("min_version"))
    if target <= current:
        raise ProblemException(
            409,
            "UPDATE_VERSION_NOT_NEWER",
            "更新包不是更高版本",
            f"当前版本为 {__version__}，请选择更高版本更新包。",
        )
    if current < minimum:
        raise ProblemException(
            409,
            "UPDATE_BRIDGE_REQUIRED",
            "需要先安装桥接版本",
            f"该更新包要求当前版本不低于 {manifest.get('min_version')}。",
        )
    schema_revision = str(manifest.get("schema_revision", ""))
    if not schema_revision.isdigit() or len(schema_revision) != 4:
        raise ProblemException(
            422,
            "UPDATE_SCHEMA_INVALID",
            "数据库模式版本无效",
            "更新包缺少有效的数据库模式版本。",
        )
    if schema_revision < SCHEMA_VERSION:
        raise ProblemException(
            409,
            "UPDATE_SCHEMA_DOWNGRADE",
            "更新包数据库模式过旧",
            "系统禁止数据库模式降级。",
        )
    release_notes = manifest.get("release_notes")
    if (
        not isinstance(release_notes, list)
        or not release_notes
        or len(release_notes) > 50
        or any(
            not isinstance(note, str) or not note.strip() or len(note) > 500
            for note in release_notes
        )
    ):
        raise ProblemException(
            422,
            "UPDATE_RELEASE_NOTES_INVALID",
            "更新内容不完整",
            "更新包必须包含 1—50 条简明中文更新内容。",
        )


def _validate_v2_platform_artifacts(manifest: dict, artifacts: dict) -> None:
    """兼容 1.4.x 已发布的双架构 DEB + Windows x64 清单。"""

    architecture_artifacts = manifest.get("architecture_artifacts")
    if not isinstance(architecture_artifacts, dict) or set(architecture_artifacts) != {
        "amd64",
        "arm64",
    }:
        raise ProblemException(
            422,
            "UPDATE_ARCHITECTURES_INCOMPLETE",
            "更新包缺少双架构安装程序",
            "更新包必须同时包含 amd64 和 ARM64 安装程序。",
        )
    for architecture, filename in architecture_artifacts.items():
        if (
            not isinstance(filename, str)
            or not filename.endswith(f"_{architecture}.deb")
            or filename not in artifacts
        ):
            raise ProblemException(
                422,
                "UPDATE_ARCHITECTURE_ARTIFACT_INVALID",
                "架构安装程序不匹配",
                f"{architecture} 安装程序与清单不一致。",
            )
    platform_artifacts = manifest.get("platform_artifacts")
    if platform_artifacts is not None:
        if not isinstance(platform_artifacts, dict):
            raise ProblemException(
                422,
                "UPDATE_PLATFORM_ARTIFACTS_INVALID",
                "平台制品映射无效",
                "平台制品映射必须按系统和架构列出。",
            )
        uos_artifacts = platform_artifacts.get("uos")
        windows_artifacts = platform_artifacts.get("windows")
        if (
            not isinstance(uos_artifacts, dict)
            or uos_artifacts != architecture_artifacts
        ):
            raise ProblemException(
                422,
                "UPDATE_UOS_ARTIFACTS_INVALID",
                "UOS 制品映射无效",
                "UOS 平台映射必须与旧双架构映射一致。",
            )
        if not isinstance(windows_artifacts, dict) or set(windows_artifacts) != {
            "amd64"
        }:
            raise ProblemException(
                422,
                "UPDATE_WINDOWS_ARTIFACT_MISSING",
                "更新包缺少 Windows 安装器",
                "统一更新包必须包含 Windows x64 安装器。",
            )
        windows_name = windows_artifacts["amd64"]
        if (
            not isinstance(windows_name, str)
            or not windows_name.endswith("_windows_amd64.exe")
            or windows_name not in artifacts
        ):
            raise ProblemException(
                422,
                "UPDATE_WINDOWS_ARTIFACT_INVALID",
                "Windows 安装器与清单不一致",
                "请重新生成统一更新包。",
            )


def _validate_v3_platform_artifacts(manifest: dict, artifacts: dict) -> None:
    platform_artifacts = manifest.get("platform_artifacts")
    if not isinstance(platform_artifacts, dict) or set(platform_artifacts) != set(
        V3_PLATFORM_ARTIFACTS
    ):
        raise ProblemException(
            422,
            "UPDATE_PLATFORM_ARTIFACTS_INVALID",
            "平台制品映射不完整",
            "第 3 版清单必须包含 Windows、Windows 7、DEB Linux 和 RPM Linux 四个平台映射。",
        )
    for platform_name, expected_architectures in V3_PLATFORM_ARTIFACTS.items():
        platform_map = platform_artifacts.get(platform_name)
        if not isinstance(platform_map, dict) or set(platform_map) != set(
            expected_architectures
        ):
            raise ProblemException(
                422,
                "UPDATE_PLATFORM_ARCHITECTURES_INVALID",
                "平台架构映射不完整",
                f"{platform_name} 的架构列表与发布契约不一致。",
            )
        for architecture, suffix in expected_architectures.items():
            filename = platform_map.get(architecture)
            if (
                not isinstance(filename, str)
                or not filename.endswith(suffix)
                or filename not in artifacts
            ):
                raise ProblemException(
                    422,
                    "UPDATE_PLATFORM_ARTIFACT_INVALID",
                    "平台安装制品与清单不一致",
                    f"{platform_name}/{architecture} 的文件名、后缀或制品记录无效。",
                )


def _validate_v4_platform_artifacts(manifest: dict, artifacts: dict) -> None:
    """校验只携带当前系统安装器的轻量在线更新包。"""

    platform_name = str(manifest.get("target_platform") or "")
    architecture = str(manifest.get("target_architecture") or "")
    expected_architectures = V4_PLATFORM_ARTIFACTS.get(platform_name)
    suffix = (
        expected_architectures.get(architecture) if expected_architectures else None
    )
    mappings = manifest.get("platform_artifacts")
    platform_map = mappings.get(platform_name) if isinstance(mappings, dict) else None
    filename = (
        platform_map.get(architecture) if isinstance(platform_map, dict) else None
    )
    if (
        manifest.get("package_role") != "platform-update"
        or not suffix
        or not isinstance(mappings, dict)
        or set(mappings) != {platform_name}
        or not isinstance(platform_map, dict)
        or set(platform_map) != {architecture}
        or not isinstance(filename, str)
        or not filename.endswith(suffix)
        or set(artifacts) != {filename}
    ):
        raise ProblemException(
            422,
            "UPDATE_PLATFORM_TARGET_INVALID",
            "在线更新包与当前平台不匹配",
            "单平台签名更新包必须且只能包含清单声明的一个系统与架构安装器。",
        )


def _manifest_signature_valid(manifest: dict) -> bool:
    signature = manifest.get("signature")
    public_key = _trusted_public_key()
    if not signature or not public_key or Ed25519PublicKey is None:
        return False
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    try:
        key_bytes = base64.b64decode(public_key)
        signature_bytes = base64.b64decode(signature)
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature_bytes, canonical)
        return True
    except Exception:
        return False


def _extract_manifest(path, *, require_signature: bool = True):
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_UPDATE_MEMBERS:
                raise ProblemException(
                    422,
                    "UPDATE_MEMBER_LIMIT",
                    "更新包文件数量异常",
                    "请重新获取官方更新包。",
                )
            names = [info.filename for info in infos]
            collision_keys = [_safe_zip_member(info.filename) for info in infos]
            if len(names) != len(set(names)) or len(collision_keys) != len(
                set(collision_keys)
            ):
                raise ProblemException(
                    422,
                    "UPDATE_DUPLICATE_MEMBER",
                    "更新包包含重复文件",
                    "请重新获取官方更新包。",
                )
            info_by_name = {info.filename: info for info in infos}
            for info in infos:
                _validate_zip_entry_type(info)
            manifest_info = info_by_name.get("manifest.json")
            if manifest_info is None:
                raise KeyError("manifest.json")
            if manifest_info.file_size > MAX_UPDATE_MANIFEST_BYTES:
                raise ProblemException(
                    422,
                    "UPDATE_MANIFEST_TOO_LARGE",
                    "更新清单体积异常",
                    "请重新获取官方更新包。",
                )
            raw = archive.read(manifest_info)
    except KeyError as exc:
        raise ProblemException(
            422,
            "UPDATE_MANIFEST_MISSING",
            "更新包缺少清单",
            "请使用 .partyops-update 更新包。",
        ) from exc
    except zipfile.BadZipFile as exc:
        raise ProblemException(
            422, "UPDATE_PACKAGE_INVALID", "更新包损坏", "请重新复制更新包后重试。"
        ) from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProblemException(
            422, "UPDATE_MANIFEST_INVALID", "更新清单无效", "请重新生成更新包。"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or not manifest.get("version")
        or not manifest.get("artifacts")
    ):
        raise ProblemException(
            422,
            "UPDATE_MANIFEST_INVALID",
            "更新清单字段不完整",
            "清单必须包含版本和架构制品。",
        )
    _validate_manifest_contract(manifest)
    # 签名覆盖完整清单（包括每个制品的 SHA-256 与大小）。先验证清单身份，
    # 再流式读取最多数 GiB 的制品，避免未受信输入抢占计算资源。
    if require_signature and not _manifest_signature_valid(manifest):
        raise ProblemException(
            422,
            "UPDATE_SIGNATURE_INVALID",
            "更新包签名无效",
            "系统只接受由外部受信公钥验证通过的更新包。",
        )
    artifacts = manifest["artifacts"]
    allowed_files = {"manifest.json", "RELEASE-NOTES.txt", *artifacts}
    unexpected = set(info_by_name) - allowed_files
    if unexpected:
        raise ProblemException(
            422,
            "UPDATE_UNREGISTERED_MEMBER",
            "更新包包含未登记文件",
            f"未登记文件：{sorted(unexpected)[0]}",
        )
    expanded_size = 0
    for filename, expected in artifacts.items():
        _safe_zip_member(str(filename))
        info = info_by_name.get(filename)
        if info is None or not isinstance(expected, dict):
            raise ProblemException(
                422,
                "UPDATE_ARTIFACT_MISSING",
                "更新制品缺失",
                f"更新包缺少 {filename}。",
            )
        try:
            expected_size = int(expected.get("size", -1))
        except (TypeError, ValueError) as exc:
            raise ProblemException(
                422,
                "UPDATE_ARTIFACT_SIZE_INVALID",
                "更新制品大小无效",
                f"{filename} 的大小字段无效。",
            ) from exc
        if (
            expected_size < 0
            or expected_size > MAX_UPDATE_ARTIFACT_BYTES
            or info.file_size != expected_size
        ):
            raise ProblemException(
                422,
                "UPDATE_ARTIFACT_SIZE_INVALID",
                "更新制品大小无效",
                f"{filename} 的大小超限或与清单不一致。",
            )
        expanded_size += expected_size
        if expanded_size > MAX_UPDATE_EXPANDED_BYTES:
            raise ProblemException(
                422,
                "UPDATE_EXPANDED_LIMIT",
                "更新包展开体积过大",
                "请重新获取官方更新包。",
            )
        try:
            with zipfile.ZipFile(path) as archive:
                digest = hashlib.sha256()
                size = 0
                with archive.open(info) as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        if size > expected_size:
                            raise ProblemException(
                                422,
                                "UPDATE_ARTIFACT_SIZE_INVALID",
                                "更新制品大小无效",
                                f"{filename} 解压后超过清单大小。",
                            )
        except zipfile.BadZipFile as exc:
            raise ProblemException(
                422,
                "UPDATE_PACKAGE_INVALID",
                "更新包载荷损坏",
                f"{filename} 的 ZIP CRC 校验失败。",
            ) from exc
        if (
            expected_size != size
            or str(expected.get("sha256", "")).lower() != digest.hexdigest()
        ):
            raise ProblemException(
                422,
                "UPDATE_ARTIFACT_HASH_MISMATCH",
                "更新制品校验失败",
                f"{filename} 的大小或哈希不匹配。",
            )
    return manifest


@router.get("/admin/updates/online")
def check_online_update(
    _admin: User = Depends(require_admin),
) -> dict[str, object]:
    """检查官方签名更新目录，不自动安装或改变本机状态。"""

    return fetch_online_update_catalog()


@router.post(
    "/admin/updates/online/prepare",
    response_model=UpdatePackageOut,
    status_code=202,
)
def prepare_online_update(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UpdatePackage:
    """下载并验证官方统一更新包；用户确认后才会进入实际安装。"""

    catalog = fetch_online_update_catalog()
    if catalog.get("target_available") is False:
        raise ProblemException(
            409,
            "UPDATE_TARGET_UNAVAILABLE",
            "当前系统的更新包暂未通过发布门禁",
            str(
                catalog.get("availability_message")
                or "请继续使用当前版本，稍后再检查更新。"
            ),
        )
    if not bool(catalog["available"]):
        raise ProblemException(
            409,
            "UPDATE_ALREADY_CURRENT",
            "当前已经是最新版本",
            f"已安装版本为 {__version__}，无需重复下载。",
        )
    expected_hash = str(catalog["package_sha256"])
    expected_version = str(catalog["version"])
    package = db.scalar(
        select(UpdatePackage)
        .where(
            UpdatePackage.version == expected_version,
            UpdatePackage.sha256 == expected_hash,
        )
        .order_by(UpdatePackage.created_at.desc())
    )
    settings = get_settings()
    if package and package.status in {
        UpdateStatus.VALIDATED,
        UpdateStatus.APPLYING,
        UpdateStatus.COMPLETED,
    }:
        path = settings.updates_dir / package.filename
        if path.is_file() and path.stat().st_size == int(catalog["package_size"]):
            if hmac.compare_digest(_sha256_path(path), expected_hash):
                return package
        if package.status in {UpdateStatus.APPLYING, UpdateStatus.COMPLETED}:
            raise ProblemException(
                409,
                "UPDATE_LOCAL_PACKAGE_MISSING",
                "已登记的更新包文件缺失",
                "请保留当前版本并联系管理员核对更新目录，不会继续安装。",
            )
    if package is None:
        package_id = secrets.token_hex(16)
        package = UpdatePackage(
            id=package_id,
            filename=f"online-{package_id}.partyops-update",
            version=expected_version,
            min_version="",
            schema_revision="",
            manifest=_online_download_state(catalog),
            sha256=expected_hash,
            signature_valid=False,
            status=UpdateStatus.UPLOADED,
            created_by=admin.id,
        )
        db.add(package)
    else:
        package.manifest = _online_download_state(catalog)
        package.signature_valid = False
        package.status = UpdateStatus.UPLOADED
    write_audit(
        db,
        admin,
        "update.online_download",
        "update_package",
        package.id,
        {"version": expected_version, "sha256": expected_hash},
        request.client.host if request.client else "",
    )
    db.commit()
    db.refresh(package)
    _start_online_download(package.id, catalog)
    return package


@router.get("/admin/updates", response_model=typing.List[UpdatePackageOut])
def list_update_packages(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[UpdatePackage]:
    return list(
        db.scalars(
            select(UpdatePackage).order_by(UpdatePackage.created_at.desc())
        ).all()
    )


@router.get("/admin/update-history", response_model=typing.List[ReleaseHistoryOut])
def list_update_history(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[ReleaseHistory]:
    ensure_current_release(db)
    db.commit()
    return list(
        db.scalars(
            select(ReleaseHistory).order_by(ReleaseHistory.installed_at.desc())
        ).all()
    )


@router.post("/admin/updates/upload", response_model=UpdatePackageOut, status_code=201)
async def upload_update_package(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UpdatePackage:
    settings = get_settings()
    _ensure_free_space(settings.updates_dir, MIN_UPDATE_FREE_BYTES)
    safe_name = PurePosixPath(file.filename or "partyops.partyops-update").name
    if not safe_name.endswith(".partyops-update"):
        raise ProblemException(
            422,
            "UPDATE_EXTENSION_INVALID",
            "更新包格式不正确",
            "请选择 .partyops-update 文件。",
        )
    path = settings.updates_dir / f"upload-{secrets.token_hex(8)}.partyops-update"
    total_size = 0
    with path.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > 4 * 1024**3:
                path.unlink(missing_ok=True)
                raise ProblemException(
                    413,
                    "UPDATE_PACKAGE_TOO_LARGE",
                    "更新包超过4GB限制",
                    "请重新生成更新包。",
                )
            handle.write(chunk)
    try:
        manifest = _extract_manifest(path)
        _ensure_free_space(
            settings.updates_dir,
            max(MIN_UPDATE_FREE_BYTES, total_size * 3),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    signature_valid = True
    digest = _sha256_path(path)
    final_name = f"partyops_{manifest['version']}_{digest[:12]}.partyops-update"
    final_path = settings.updates_dir / final_name
    path.replace(final_path)
    package = UpdatePackage(
        filename=final_name,
        version=str(manifest["version"]),
        min_version=str(manifest.get("min_version", "")),
        schema_revision=str(manifest.get("schema_revision", "")),
        manifest={
            key: value
            for key, value in manifest.items()
            if key not in {"signature", "public_key"}
        },
        sha256=digest,
        signature_valid=signature_valid,
        status=UpdateStatus.VALIDATED,
        created_by=admin.id,
    )
    db.add(package)
    write_audit(
        db,
        admin,
        "update.upload",
        "update_package",
        package.id,
        {"version": package.version, "signature_valid": signature_valid},
        request.client.host if request.client else "",
    )
    db.commit()
    db.refresh(package)
    return package


@router.post(
    "/admin/updates/{package_id}/apply",
    response_model=typing.List[UpdateRunOut],
    status_code=202,
)
def apply_update(
    package_id: str,
    payload: UpdateApplyRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[UpdateRun]:
    package = db.get(UpdatePackage, package_id)
    if not package or package.status not in {
        UpdateStatus.VALIDATED,
        UpdateStatus.COMPLETED,
    }:
        raise ProblemException(
            409, "UPDATE_NOT_READY", "更新包尚未通过校验", "请先上传并校验更新包。"
        )
    if not package.signature_valid:
        raise ProblemException(
            403,
            "UPDATE_SIGNATURE_INVALID",
            "更新包签名无效",
            "系统禁止使用未通过受信公钥验证的更新包。",
        )
    package_path = get_settings().updates_dir / package.filename
    if not package_path.is_file() or _sha256_path(package_path) != package.sha256:
        raise ProblemException(
            410,
            "UPDATE_FILE_MISSING",
            "更新包文件缺失或校验失败",
            "请重新导入更新包。",
        )
    _ensure_free_space(
        get_settings().data_dir,
        max(MIN_UPDATE_FREE_BYTES, package_path.stat().st_size * 3),
    )
    settings = get_settings()
    personal_update = settings.mode == "personal" and payload.include_host
    windows_personal_update = personal_update and os.name == "nt"
    linux_personal_update = personal_update and sys.platform.startswith("linux")
    macos_local_update = payload.include_host and sys.platform == "darwin"
    active_host_run = db.scalar(
        select(UpdateRun).where(
            UpdateRun.target_device_id.is_(None),
            UpdateRun.status == UpdateStatus.APPLYING,
        )
    )
    if payload.include_host and active_host_run:
        raise ProblemException(
            409,
            "UPDATE_ALREADY_RUNNING",
            "主机已有升级任务正在运行",
            "请等待当前升级完成后再试。",
        )
    if personal_update or macos_local_update:
        # 先通过活动任务门禁，再创建可校验备份。macOS 的个人
        # 与主机模式都由同一个本机 PKG 事务替换 `.app`，因此两者
        # 都必须在启动 helper 之前完成数据库快照。
        create_pre_upgrade_backup()
    devices = []
    target_ids = payload.target_device_ids
    if payload.include_host:
        # 主机是唯一更新权威。主机升级时始终登记全部启用终端，
        # 离线终端保留等待记录，下一次上线后由用户确认更新。
        devices = (
            []
            if personal_update
            else list(
                db.scalars(
                    select(Device)
                    .where(Device.active.is_(True))
                    .order_by(Device.created_at)
                ).all()
            )
        )
        target_ids = [device.id for device in devices]
    elif target_ids:
        devices = list(
            db.scalars(
                select(Device).where(Device.id.in_(target_ids), Device.active.is_(True))
            ).all()
        )
        if len(devices) != len(set(target_ids)):
            raise ProblemException(
                404, "DEVICE_NOT_FOUND", "目标设备不存在", "请刷新设备列表后重试。"
            )
    runs: list[UpdateRun] = []
    for device in devices:
        run = db.scalar(
            select(UpdateRun).where(
                UpdateRun.package_id == package.id,
                UpdateRun.target_device_id == device.id,
                UpdateRun.status.in_(
                    [
                        UpdateStatus.UPLOADED,
                        UpdateStatus.APPLYING,
                        UpdateStatus.COMPLETED,
                    ]
                ),
            )
        )
        if run:
            runs.append(run)
            continue
        waiting_for_host = payload.include_host
        run = UpdateRun(
            package_id=package.id,
            target_device_id=device.id,
            status=UpdateStatus.UPLOADED,
            message=(
                "等待主机升级和健康检查完成"
                if waiting_for_host
                else "等待设备上线后升级"
            ),
            created_by=admin.id,
        )
        db.add(run)
        db.flush()
        if not waiting_for_host:
            online_state = (
                package.manifest.get("online_download", package.manifest)
                if isinstance(package.manifest, dict)
                else {}
            )
            db.add(
                DeviceCommand(
                    device_id=device.id,
                    command_type="apply_update",
                    idempotency_key=f"update:{package.id}:{device.id}",
                    payload={
                        "package": package.filename,
                        "version": package.version,
                        "run_id": run.id,
                        "official_online": online_state.get("source")
                        == "official-online-catalog",
                    },
                )
            )
        runs.append(run)
    if payload.include_host:
        run = UpdateRun(
            package_id=package.id,
            target_device_id=None,
            status=UpdateStatus.APPLYING,
            progress=5,
            message=(
                "已完成升级前备份，等待 Windows 管理员确认"
                if windows_personal_update
                else (
                    "已完成升级前备份，等待 macOS 系统授权安装并自动回滚"
                    if macos_local_update
                    else
                    "已完成升级前备份，等待系统更新服务校验、安装并自动回滚"
                    if personal_update
                    else "已进入主机升级队列；终端将在主机健康检查通过后升级。"
                )
            ),
            created_by=admin.id,
        )
        db.add(run)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise ProblemException(
                409,
                "UPDATE_ALREADY_RUNNING",
                "主机已有升级任务正在运行",
                "请等待当前升级完成后再试。",
            ) from exc
        package.status = UpdateStatus.APPLYING
        runs.insert(0, run)
    if not runs:
        raise ProblemException(
            422,
            "UPDATE_TARGET_REQUIRED",
            "请选择升级目标",
            "至少选择主机或一台协同设备。",
        )
    write_audit(
        db,
        admin,
        "update.apply",
        "update_package",
        package.id,
        {"targets": target_ids, "host": payload.include_host},
        request.client.host if request.client else "",
    )
    emit_event(
        db,
        "update.queued",
        package.id,
        {"target_count": len(devices), "include_host": payload.include_host},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ProblemException(
            409,
            "UPDATE_ALREADY_RUNNING",
            "主机已有升级任务正在运行",
            "请等待当前升级完成后再试。",
        ) from exc
    if windows_personal_update:
        personal_run = next(run for run in runs if run.target_device_id is None)
        threading.Thread(
            target=launch_windows_personal_update,
            args=(personal_run.id,),
            name="partyops-personal-update-uac",
            daemon=True,
        ).start()
    elif linux_personal_update:
        personal_run = next(run for run in runs if run.target_device_id is None)
        threading.Thread(
            target=launch_linux_personal_update,
            args=(personal_run.id,),
            name="partyops-personal-update-polkit",
            daemon=True,
        ).start()
    elif macos_local_update:
        local_run = next(run for run in runs if run.target_device_id is None)
        threading.Thread(
            target=launch_macos_update,
            args=(local_run.id,),
            name="partyops-macos-update-helper",
            daemon=True,
        ).start()
    return runs


@router.get("/admin/update-runs", response_model=typing.List[UpdateRunOut])
def list_update_runs(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[UpdateRun]:
    return list(
        db.scalars(
            select(UpdateRun).order_by(UpdateRun.created_at.desc()).limit(200)
        ).all()
    )


@router.get("/devices/update-package/{filename}")
def download_device_update(
    filename: str,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> FileResponse:
    from .fleet import authenticated_device

    device = authenticated_device(token, db)
    package = db.scalar(
        select(UpdatePackage).where(
            UpdatePackage.filename == filename,
            UpdatePackage.status.in_(
                [
                    UpdateStatus.VALIDATED,
                    UpdateStatus.APPLYING,
                    UpdateStatus.COMPLETED,
                ]
            ),
        )
    )
    if not package:
        raise ProblemException(
            404, "UPDATE_NOT_FOUND", "更新包不存在", "更新任务可能已撤销。"
        )
    path = get_settings().updates_dir / package.filename
    if not path.exists():
        raise ProblemException(
            410, "UPDATE_FILE_MISSING", "更新包文件缺失", "请由管理员重新上传更新包。"
        )
    return FileResponse(
        path, media_type="application/octet-stream", filename=package.filename
    )
