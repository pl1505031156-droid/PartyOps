"""多设备安全协同、共享目录审批与可审计文件传输。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import typing
import zipfile
from datetime import timedelta
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..archive_service import (
    can_contribute_category,
    category_for_record,
    index_archive_attachment,
)
from ..audit import emit_event, write_audit
from ..compat import to_thread
from ..config import get_settings
from ..content_security import may_render_inline
from ..database import db_runtime, get_session
from ..device_versions import (
    build_device_gate,
    device_version_state,
    issue_device_context_token,
    latest_device_run,
    reconcile_device_update,
    request_device,
    start_device_update,
)
from ..enrollment_codes import normalize_enrollment_code
from ..enums import MaterialStage, TaskStatus
from ..local_secrets import decrypt_local_json, encrypt_local_json
from ..models import (
    ArchiveAttachment,
    ArchiveRecord,
    AttachmentVersion,
    ClientDeviceCredential,
    Device,
    DeviceCommand,
    DeviceEnrollment,
    DeviceGrant,
    LocalShareAction,
    MaterialItem,
    Notification,
    SemanticIndexCheckpoint,
    Task,
    Transfer,
    TransferChunk,
    UpdateRun,
    User,
    WorkspaceFile,
    WorkspaceRoot,
    utcnow,
)
from ..networking import (
    discover_lan_addresses,
    enrollment_service_url,
    service_url,
    validate_advertise_host,
)
from ..pki import issue_device_certificate
from ..problems import ProblemException
from ..schemas import (
    DeviceBrowserTokenOut,
    DeviceCertificateOut,
    DeviceCertificateRotateRequest,
    DeviceEnrollmentCreate,
    DeviceEnrollmentOut,
    DeviceEnrollmentStatusOut,
    DeviceEnrollOut,
    DeviceEnrollRequest,
    DeviceGrantCreate,
    DeviceGrantOut,
    DeviceHeartbeat,
    DeviceOut,
    DevicePatch,
    DeviceRemoteRootCreate,
    DeviceRemoteRootPatch,
    DeviceUpdateGate,
    DeviceVersionStatus,
    RemoteIndexDelta,
    RemoteRootPatch,
    RemoteRootRequest,
    TransferAction,
    TransferAttachCreate,
    TransferCreate,
    TransferOut,
    WorkspaceDownloadCreate,
    WorkspaceDownloadOut,
    serialize_api_datetime,
)
from ..security import get_current_user, hash_token, require_admin
from ..task_service import can_edit_task
from ..workspace import resolve_workspace_path, store_managed_path
from ..workspace_access import (
    DEVICE_GRANT_CAPABILITIES,
    grant_allows,
    workspace_root_permissions,
)
from .router_utils import aware_utc

router = APIRouter(tags=["fleet"])
logger = logging.getLogger("partyops.transfers")

CHUNK_SIZE = 8 * 1024 * 1024
MAX_FILENAME = 255
BLOCKED_EXTENSIONS = {
    ".sh",
    ".desktop",
    ".so",
    ".deb",
    ".appimage",
    ".exe",
    ".bat",
    ".cmd",
}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def notify_root_approval_needed(db: Session, root: WorkspaceRoot, device: Device) -> None:
    admins = db.scalars(
        select(User).where(User.active.is_(True), User.role == "admin")
    ).all()
    for admin in admins:
        dedupe = f"workspace-root:pending:{root.id}:{admin.id}"
        if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe)):
            continue
        db.add(
            Notification(
                user_id=admin.id,
                notification_type="root_approval",
                title=f"共享目录待审批：{root.name}",
                body=f"协同电脑“{device.name}”申请共享本机目录，请确认范围并授权。",
                entity_type="workspace_root",
                entity_id=root.id,
                dedupe_key=dedupe,
            )
        )


def enrollment_request_fingerprint(
    normalized_code: str,
    payload: DeviceEnrollRequest,
) -> str:
    canonical = json.dumps(
        {
            "code_hash": hash_token(normalized_code),
            "name": payload.name.strip(),
            "csr_pem": payload.csr_pem or "",
            "architecture": payload.architecture,
            "platform_family": payload.platform_family,
            "distribution": payload.distribution,
            "distribution_version": payload.distribution_version,
            "package_format": payload.package_format,
            "runtime_profile": payload.runtime_profile,
            "capabilities": sorted(set(payload.capabilities or [])),
            "local_username": payload.local_username,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def enrollment_device(db: Session, enrollment_id: str) -> Device | None:
    """设备最多 20 台，逐个核对元数据比依赖 SQLite JSON 方言更稳妥。"""

    for device in db.scalars(select(Device).where(Device.active.is_(True))).all():
        if str((device.device_metadata or {}).get("enrollment_id", "")) == enrollment_id:
            return device
    return None


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改设备或授权必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def safe_name(value: str) -> str:
    name = Path(value).name.strip()
    if not name or name in {".", ".."} or len(name) > MAX_FILENAME:
        raise ProblemException(422, "FILENAME_INVALID", "文件名无效", "请使用正常的文件名。")
    if any(ord(char) < 32 for char in name):
        raise ProblemException(422, "FILENAME_INVALID", "文件名包含控制字符", "请重新选择文件。")
    return name


def safe_archive_component(value: str) -> str:
    """把展示名称转换为单个 ZIP 路径片段，避免绝对路径和穿越条目。"""

    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", value).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "共享文件"
    return cleaned[:MAX_FILENAME]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_transfer_chunk(part: Path, offset: int, body: bytes) -> None:
    """在工作线程中写入分块，避免同步磁盘 I/O 占用事件循环。"""

    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("r+b" if part.exists() else "w+b") as handle:
        handle.seek(offset)
        handle.write(body)
        handle.flush()


def cleanup_transfer_part(transfer_id: str) -> None:
    """删除单个传输的受管临时文件；清理失败只记录诊断，不覆盖业务结果。"""

    part = get_settings().transfers_dir / f"{transfer_id}.part"
    try:
        part.unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "transfer_part_cleanup_failed transfer_id=%s",
            transfer_id,
            exc_info=True,
        )


def enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def normalized_platform(value: str) -> str:
    raw = value.strip().lower()
    if raw.startswith("linux") or raw in {"uos", "deepin", "kylin"}:
        return "uos"
    if raw in {"win32", "windows", "windows10", "windows11"}:
        return "windows"
    if raw in {"darwin", "mac", "macos", "osx"}:
        return "macos"
    return raw[:40]


def device_is_deleted(device: Device) -> bool:
    return bool((device.device_metadata or {}).get("deleted_at"))


def managed_device_count(db: Session) -> int:
    return sum(
        1
        for device in db.scalars(select(Device)).all()
        if not device_is_deleted(device)
    )


def get_max_devices(db: Session) -> int:
    from ..models import SystemSetting

    setting = db.get(SystemSetting, "fleet.max_devices")
    try:
        value = int(setting.value) if setting else get_settings().max_devices
    except (TypeError, ValueError):
        value = get_settings().max_devices
    return max(1, min(20, value))


def device_token(device: Device, token: str) -> bool:
    return bool(device.active and device.agent_token_hash and hash_token(token) == device.agent_token_hash)


def authenticated_device(
    token: str | None,
    db: Session,
) -> Device:
    if not token:
        raise ProblemException(401, "DEVICE_TOKEN_REQUIRED", "缺少设备令牌", "请使用已批准的 PartyOps Agent。")
    token_digest = hash_token(token)
    credential = db.scalar(
        select(ClientDeviceCredential).where(
            ClientDeviceCredential.token_hash == token_digest,
            ClientDeviceCredential.state == "active",
            ClientDeviceCredential.revoked_at.is_(None),
        )
    )
    device = db.get(Device, credential.device_id) if isinstance(credential, ClientDeviceCredential) else db.scalar(
        select(Device).where(Device.agent_token_hash == token_digest)
    )
    if not device or not device_token(device, token):
        raise ProblemException(401, "DEVICE_TOKEN_INVALID", "设备令牌无效", "请重新入网或联系管理员。")
    if device.status in {"revoked", "quarantined"}:
        raise ProblemException(403, "DEVICE_BLOCKED", "设备已被阻断", "请联系主机管理员恢复设备。")
    if isinstance(credential, ClientDeviceCredential):
        credential.last_used_at = utcnow()
    return device


def issue_v2_device_credential(db: Session, device: Device) -> str:
    """吊销旧凭据并签发只返回一次的 v2 令牌。"""

    now = utcnow()
    token = secrets.token_urlsafe(32)
    replacement = ClientDeviceCredential(
        device_id=device.id,
        token_hash=hash_token(token),
        protocol_version=2,
        state="active",
    )
    db.add(replacement)
    db.flush()
    for existing in db.scalars(
        select(ClientDeviceCredential).where(
            ClientDeviceCredential.device_id == device.id,
            ClientDeviceCredential.id != replacement.id,
            ClientDeviceCredential.state == "active",
        )
    ).all():
        existing.state = "replaced"
        existing.revoked_at = now
        existing.replaced_by_id = replacement.id
    device.agent_token_hash = replacement.token_hash
    device.protocol_version = 2
    device.credential_state = "active"
    device.credential_rotated_at = now
    device.version += 1
    return token


def transfer_permission_still_valid(
    db: Session,
    transfer: Transfer,
    capability: str,
    device_id: str | None,
    root_id: str | None,
) -> bool:
    """每个分块重新验证用户、设备开关和目录授权，撤销后立即生效。"""

    if not device_id:
        return False
    device = db.get(Device, device_id)
    user = db.get(User, transfer.requested_by)
    if not device or not user or not device.active:
        return False
    if device.status in {"revoked", "quarantined"}:
        return False
    if capability == "download" and not device.allow_host_access and user.role.value != "admin":
        return False
    if (
        capability == "upload"
        and transfer.delivery_mode == "current_device"
        and transfer.destination_device_id == device_id
    ):
        return True
    if capability == "upload" and root_id is None and device.allow_device_transfer:
        return True
    if capability in {"upload", "share"} and not device.allow_device_transfer and user.role.value != "admin":
        return False
    return grant_allows(db, user, device_id, root_id, capability)


def queue_transfer_commands(db: Session, transfer: Transfer) -> None:
    """按传输阶段建立幂等 Agent 命令；设备间传输必须先上传到主机再下载。"""

    def enqueue(device_id: str, command_type: str, payload: dict) -> None:
        key = f"{transfer.id}:{command_type}"
        command = db.scalar(
            select(DeviceCommand).where(DeviceCommand.idempotency_key == key)
        )
        if command is None:
            db.add(
                DeviceCommand(
                    device_id=device_id,
                    command_type=command_type,
                    idempotency_key=key,
                    payload=payload,
                )
            )
        elif command.status == "failed":
            command.status = "queued"
            command.result = {}
            command.delivered_at = None
            command.completed_at = None

    source = db.get(WorkspaceFile, transfer.source_file_id) if transfer.source_file_id else None
    if (
        enum_value(transfer.direction) in {"device_to_host", "device_to_device"}
        and transfer.source_device_id
        and (
            (transfer.bundle_mode != "single" and not transfer.sha256)
            or (
                transfer.bundle_mode == "single"
                and transfer.total_chunks == 0
                and not transfer.transit_path
            )
            or transfer.completed_chunks < transfer.total_chunks
        )
    ):
        bundle_items = []
        if transfer.bundle_mode != "single":
            for item_id in transfer.item_ids:
                item = db.get(WorkspaceFile, item_id)
                if item:
                    bundle_items.append(
                        {
                            "remote_file_key": item.remote_file_key,
                            "relative_path": item.relative_path,
                            "name": item.name,
                            "is_directory": item.is_directory,
                        }
                    )
        payload = {
            "transfer_id": transfer.id,
            "remote_file_key": source.remote_file_key if source else "",
            "name": transfer.original_name,
            "size_bytes": transfer.size_bytes,
            "sha256": transfer.sha256,
            "chunk_size": transfer.chunk_size,
            "total_chunks": transfer.total_chunks,
            "modified_at": serialize_api_datetime(source.modified_at)
            if source and source.modified_at
            else "",
            "bundle_mode": transfer.bundle_mode,
            "items": bundle_items,
            "max_bytes": get_settings().transfer_max_file_gb * 1024**3,
        }
        enqueue(
            transfer.source_device_id,
            "upload_bundle" if transfer.bundle_mode != "single" else "upload_file",
            payload,
        )
        return
    if (
        enum_value(transfer.direction) in {"host_to_device", "device_to_device"}
        and transfer.destination_device_id
    ):
        payload = {
            "transfer_id": transfer.id,
            "name": transfer.original_name,
            "size_bytes": transfer.size_bytes,
            "sha256": transfer.sha256,
            "chunk_size": transfer.chunk_size,
            "total_chunks": transfer.total_chunks,
        }
        enqueue(transfer.destination_device_id, "download_file", payload)


def transfer_source_root(db: Session, transfer: Transfer) -> WorkspaceRoot | None:
    item = db.get(WorkspaceFile, transfer.source_file_id) if transfer.source_file_id else None
    return db.get(WorkspaceRoot, item.root_id) if item else None


def transfer_sources_still_allowed(db: Session, transfer: Transfer) -> bool:
    """批量包在每个分块与最终确认时复核全部源文件的目录权限。

    协同机来源需要同时绑定原设备和共享授权；主机来源没有设备标识，仍要
    逐项检查根目录是否启用以及当前发起人的实时下载权限。两类来源不能
    共用“必须存在 source_device_id”的旧假设，否则主机生成的 ZIP 会在
    已返回下载地址后被最终读取接口错误拒绝。
    """

    user = db.get(User, transfer.requested_by)
    if not user:
        return False
    item_ids = transfer.item_ids or ([transfer.source_file_id] if transfer.source_file_id else [])
    if not item_ids:
        return False
    for item_id in item_ids:
        item = db.get(WorkspaceFile, item_id)
        root = db.get(WorkspaceRoot, item.root_id) if item else None
        if not item or not root or not root.enabled:
            return False
        source = root.source.value
        if source == "device":
            if (
                not transfer.source_device_id
                or root.device_id != transfer.source_device_id
                or not workspace_root_permissions(db, root, user)["download"]
            ):
                return False
        elif source == "host":
            if transfer.source_device_id or not workspace_root_permissions(
                db, root, user
            )["download"]:
                return False
        else:
            return False
    return True


def ensure_transfer_storage_available(incoming_bytes: int = 0) -> None:
    settings = get_settings()
    quota = settings.transfer_quota_gb * 1024**3
    used = sum(
        path.stat().st_size
        for path in settings.transfers_dir.glob("*.part")
        if path.is_file()
    )
    free = shutil.disk_usage(settings.transfers_dir).free
    if used + incoming_bytes > quota:
        raise ProblemException(
            507,
            "TRANSFER_QUOTA_EXCEEDED",
            "文件中转区空间不足",
            "请完成或清理已有传输后重试。",
        )
    if incoming_bytes > free:
        raise ProblemException(
            507,
            "DISK_FULL",
            "主机磁盘空间不足",
            "请释放主机磁盘空间后继续传输。",
        )


@router.get("/admin/devices", response_model=typing.List[DeviceOut])
def list_devices(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[Device]:
    return [
        device
        for device in db.scalars(select(Device).order_by(Device.created_at.desc())).all()
        if not device_is_deleted(device)
    ]


@router.get(
    "/admin/devices/version-status",
    response_model=typing.List[DeviceVersionStatus],
)
def list_device_version_status(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[DeviceVersionStatus]:
    target = get_settings().app_version
    values: list[DeviceVersionStatus] = []
    for device in db.scalars(select(Device).order_by(Device.created_at.desc())).all():
        if device_is_deleted(device):
            continue
        run, _package = latest_device_run(db, device.id)
        values.append(
            DeviceVersionStatus(
                device_id=device.id,
                device_name=device.name,
                current_version=device.app_version or "未上报",
                target_version=target,
                version_state=device_version_state(device),
                update_status=getattr(run.status, "value", run.status) if run else "",
                update_message=run.message if run else "",
                last_seen_at=device.last_seen_at,
            )
        )
    return values


@router.get("/admin/devices/config", response_model=dict)
def fleet_config(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, int]:
    return {
        "max_devices": get_max_devices(db),
        "current_devices": managed_device_count(db),
    }


@router.patch("/admin/devices/config", response_model=dict)
def update_fleet_config(
    max_devices: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, int]:
    if not 1 <= max_devices <= 20:
        raise ProblemException(422, "MAX_DEVICES_INVALID", "设备数量无效", "设备数量必须在 1—20 台之间。")
    from ..models import SystemSetting

    setting = db.get(SystemSetting, "fleet.max_devices")
    if not setting:
        setting = SystemSetting(key="fleet.max_devices", value=max_devices)
        db.add(setting)
    else:
        setting.value = max_devices
    write_audit(db, admin, "device.max_devices_update", "system_setting", "fleet.max_devices", {"max_devices": max_devices}, client_ip(request))
    db.commit()
    return {"max_devices": max_devices, "current_devices": managed_device_count(db)}


@router.post("/admin/devices/enrollments", response_model=DeviceEnrollmentOut, status_code=201)
def create_enrollment(
    payload: DeviceEnrollmentCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DeviceEnrollmentOut:
    settings = get_settings()
    available_hosts = discover_lan_addresses()
    # 旧测试契约没有 advertised_host；隔离测试环境使用固定首项以保持历史用例
    # 可重复。开发和生产环境必须由管理员确认，不能把虚拟网卡猜成办公网卡。
    requested_host = payload.advertised_host
    if not requested_host and settings.environment == "test" and available_hosts:
        requested_host = available_hosts[0]
    try:
        advertised_url = enrollment_service_url(
            requested_host=requested_host,
            configured_host=settings.network_advertise_host,
            configured_port=settings.port,
            request_base_url=str(request.base_url),
            lan_candidates=available_hosts,
            tls_enabled=settings.tls_enabled,
        )
    except ValueError as exc:
        raise ProblemException(
            422,
            "ENROLLMENT_HOST_INVALID",
            "主机地址不可用于协同",
            "请选择列表中的真实局域网地址；127.0.0.1 只能由主机自己访问。",
            fields={"advertised_host": str(exc)},
            extra={"available_hosts": available_hosts},
        ) from exc
    except LookupError as exc:
        raise ProblemException(
            422,
            "ENROLLMENT_HOST_REQUIRED",
            "请选择主机局域网地址",
            "检测到多个或没有可确认的网卡地址，系统不会猜测，以免协同电脑配置失败。",
            fields={"advertised_host": str(exc)},
            extra={"available_hosts": available_hosts},
        ) from exc
    active_count = db.scalar(select(func.count()).select_from(Device).where(Device.active.is_(True))) or 0
    if active_count >= get_max_devices(db):
        raise ProblemException(409, "DEVICE_LIMIT_REACHED", "已达到设备上限", "请先提高上限或撤销不用的设备。")
    from ..pki import ensure_tls_material

    ca_fingerprint = str(ensure_tls_material(settings)["fingerprint"])
    # 将 CA 指纹作为入网码的一部分交给终端。终端会先固定该指纹，再发送
    # 一次性秘密，避免首次 HTTPS 引导阶段遭遇中间人窃取入网码。
    code = normalize_enrollment_code(
        f"{secrets.token_urlsafe(18)}.{ca_fingerprint}"
    )
    enrollment = DeviceEnrollment(
        code_hash=hash_token(code),
        expires_at=utcnow() + timedelta(minutes=10),
        created_by=admin.id,
    )
    db.add(enrollment)
    write_audit(
        db,
        admin,
        "device.enrollment_create",
        "device_enrollment",
        enrollment.id,
        {"name": payload.name, "advertised_url": advertised_url},
        client_ip(request),
    )
    db.commit()
    return DeviceEnrollmentOut(
        id=enrollment.id,
        name=payload.name,
        code=code,
        expires_at=enrollment.expires_at,
        host_url=advertised_url,
        ca_fingerprint=ca_fingerprint,
    )


@router.get(
    "/admin/devices/enrollments/{enrollment_id}/status",
    response_model=DeviceEnrollmentStatusOut,
)
def enrollment_status(
    enrollment_id: str,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DeviceEnrollmentStatusOut:
    """供首次配置界面确认协同 Agent 是否真正完成入网。"""

    enrollment = db.get(DeviceEnrollment, enrollment_id)
    if enrollment is None:
        raise ProblemException(404, "ENROLLMENT_NOT_FOUND", "入网任务不存在", "请重新生成入网码。")
    device = enrollment_device(db, enrollment.id)
    if device is not None:
        status = "enrolled"
    elif aware_utc(enrollment.expires_at) <= utcnow():
        status = "expired"
    else:
        status = "pending"
    return DeviceEnrollmentStatusOut(
        id=enrollment.id,
        status=status,
        expires_at=enrollment.expires_at,
        used_at=enrollment.used_at,
        device_id=device.id if device else None,
        device_name=device.name if device else "",
        device_status=device.status if device else "",
        last_seen_at=device.last_seen_at if device else None,
    )


@router.post("/devices/enroll", response_model=DeviceEnrollOut, status_code=201)
def enroll_device(
    payload: DeviceEnrollRequest,
    request: Request,
    enrollment_code_header: str | None = Header(
        default=None,
        alias="X-PartyOps-Enrollment-Code",
    ),
    db: Session = Depends(get_session),
) -> DeviceEnrollOut:
    try:
        normalized_code = normalize_enrollment_code(payload.code)
    except ValueError as exc:
        raise ProblemException(
            400,
            "ENROLLMENT_CODE_FORMAT_INVALID",
            "入网码格式不完整",
            "请在主机设备中心点击“复制完整入网码”后直接粘贴。",
        ) from exc
    # 生产环境的无会话 Agent 请求必须同时证明持有一次性入网码。中间件
    # 只按路径和“是否携带”放行，真正的机密比对在这里完成；测试环境保留
    # 旧的直接路由契约，避免把测试客户端误当作生产浏览器。
    if get_settings().environment == "production":
        if not enrollment_code_header:
            raise ProblemException(
                403,
                "ENROLLMENT_TOKEN_REQUIRED",
                "缺少入网安全令牌",
                "请使用当前版本配置向导重新提交入网码。",
            )
        try:
            normalized_header = normalize_enrollment_code(enrollment_code_header)
        except ValueError as exc:
            raise ProblemException(
                403,
                "ENROLLMENT_TOKEN_INVALID",
                "入网安全令牌无效",
                "请求头中的入网码格式不正确。",
            ) from exc
        if not secrets.compare_digest(normalized_header, normalized_code):
            raise ProblemException(
                403,
                "ENROLLMENT_TOKEN_MISMATCH",
                "入网安全令牌不匹配",
                "请从主机重新复制完整入网码后重试。",
            )
    with db_runtime.write_lock:
        enrollment = db.scalar(
            select(DeviceEnrollment).where(
                DeviceEnrollment.code_hash == hash_token(normalized_code),
                DeviceEnrollment.expires_at > utcnow(),
            )
        )
        if not enrollment:
            raise ProblemException(
                400,
                "ENROLLMENT_INVALID",
                "入网码无效或已过期",
                "请让主机管理员重新生成入网码。",
            )
        fingerprint = enrollment_request_fingerprint(normalized_code, payload)
        if enrollment.used_at is not None:
            existing_device = enrollment_device(db, enrollment.id)
            metadata = dict(existing_device.device_metadata or {}) if existing_device else {}
            if (
                existing_device
                and metadata.get("enrollment_request_fingerprint") == fingerprint
                and metadata.get("enrollment_recovery")
            ):
                try:
                    recovered = decrypt_local_json(
                        str(metadata["enrollment_recovery"])
                    )
                    return DeviceEnrollOut(**recovered)
                except (TypeError, ValueError):
                    pass
            raise ProblemException(
                409,
                "ENROLLMENT_ALREADY_COMPLETED",
                "该入网码已经创建了设备",
                (
                    f"主机已创建“{existing_device.name}”，但终端未完成配置。"
                    "请先在主机删除这条未完成设备，再生成新入网码。"
                    if existing_device
                    else "该入网请求已被消费，请由管理员生成新的入网码。"
                ),
            )
        if db.scalar(
            select(Device).where(
                Device.name == payload.name.strip(),
                Device.active.is_(True),
            )
        ):
            raise ProblemException(
                409,
                "DEVICE_NAME_EXISTS",
                "设备名称已存在",
                "请删除主机上的未完成设备，或换一个设备名称。",
            )
        token = secrets.token_urlsafe(32)
        device = Device(
            name=payload.name.strip(),
            # 只有 Agent 首次心跳成功后才能显示在线，避免把只完成证书
            # 签发、尚未保存终端配置的设备误报为在线。
            status="offline",
            architecture=payload.architecture,
            platform=normalized_platform(payload.platform),
            platform_family=payload.platform_family or "",
            distribution=payload.distribution or "",
            distribution_version=payload.distribution_version or "",
            package_format=payload.package_format or "",
            runtime_profile=payload.runtime_profile or "",
            capabilities=sorted(set(payload.capabilities or [])),
            kernel=payload.kernel,
            app_version=payload.app_version,
            agent_version=payload.agent_version,
            protocol_version=max(2, payload.protocol_version),
            credential_state="active",
            credential_rotated_at=utcnow(),
            local_username=payload.local_username,
            ip_address=payload.ip_address or client_ip(request),
            agent_token_hash=hash_token(token),
            last_seen_at=None,
            disk_free_bytes=payload.disk_free_bytes,
            device_metadata={
                "root_count": payload.root_count,
                "indexed_file_count": payload.indexed_file_count,
                "enrollment_id": enrollment.id,
                "enrollment_request_fingerprint": fingerprint,
            },
            created_by=enrollment.created_by,
        )
        db.add(device)
        db.flush()
        db.add(
            ClientDeviceCredential(
                device_id=device.id,
                token_hash=device.agent_token_hash,
                protocol_version=device.protocol_version,
            )
        )
        certificate_bundle = issue_device_certificate(
            get_settings(),
            device.id,
            payload.csr_pem,
        )
        device.certificate_fingerprint = str(
            certificate_bundle.get("certificate_fingerprint", "")
        )
        device.certificate_expires_at = utcnow() + timedelta(days=365)
        settings = get_settings()
        if settings.environment == "test":
            response_host_url = str(request.base_url).rstrip("/")
            response_agent_url = f"{request.base_url.scheme}://{request.base_url.hostname}:{settings.agent_port}"
        else:
            try:
                validate_advertise_host(settings.network_advertise_host)
            except RuntimeError as exc:
                raise ProblemException(
                    409,
                    "ENROLLMENT_ADVERTISE_HOST_INVALID",
                    "主机协同地址已失效",
                    "主机当前公布地址不能由其他电脑访问，请由管理员修复网络配置后重新入网。",
                ) from exc
            response_host_url = service_url(
                settings.network_advertise_host,
                settings.port,
                tls_enabled=settings.tls_enabled,
            )
            response_agent_url = service_url(
                settings.network_advertise_host,
                settings.agent_port,
                tls_enabled=settings.tls_enabled,
            )
        response = DeviceEnrollOut(
            device_id=device.id,
            device_token=token,
            host_url=response_host_url,
            expires_at=utcnow() + timedelta(days=365),
            **certificate_bundle,
            agent_url=response_agent_url,
        )
        device.device_metadata = {
            **dict(device.device_metadata or {}),
            "enrollment_recovery": encrypt_local_json(
                response.model_dump(mode="json")
            ),
        }
        enrollment.used_at = utcnow()
        write_audit(
            db,
            db.get(User, enrollment.created_by),
            "device.enroll",
            "device",
            device.id,
            {"name": device.name, "architecture": device.architecture},
            client_ip(request),
        )
        db.commit()
        return response


@router.patch("/admin/devices/{device_id}", response_model=DeviceOut)
def patch_device(
    device_id: str,
    payload: DevicePatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> Device:
    device = db.get(Device, device_id)
    if not device or device_is_deleted(device):
        raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "未找到该协同设备。")
    if device.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "设备配置已变化", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(device, field, getattr(payload, field))
    device.version += 1
    if payload.active is False:
        device.status = "revoked"
    elif payload.active is True and device.status == "revoked":
        device.status = "offline"
    write_audit(db, admin, "device.update", "device", device.id, {"fields": sorted(payload.model_fields_set)}, client_ip(request))
    db.commit()
    db.refresh(device)
    return device


@router.delete("/admin/devices/{device_id}", response_model=dict)
def delete_managed_device(
    device_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """从纳管列表删除设备；历史传输与审计保留，不物理擦除证据。"""

    with db_runtime.write_lock:
        device = db.get(Device, device_id)
        if not device or device_is_deleted(device):
            raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "未找到该协同设备。")
        if device.version != parse_version(if_match):
            raise ProblemException(409, "VERSION_CONFLICT", "设备配置已变化", "请刷新后重试。")
        unfinished = db.scalar(
            select(func.count())
            .select_from(Transfer)
            .where(
                or_(
                    Transfer.source_device_id == device.id,
                    Transfer.destination_device_id == device.id,
                ),
                Transfer.status.in_(
                    ["queued", "awaiting_approval", "transferring", "paused"]
                ),
            )
        ) or 0
        if unfinished:
            raise ProblemException(
                409,
                "DEVICE_HAS_ACTIVE_TRANSFERS",
                "设备仍有未完成传输",
                "请先在传输队列取消或完成相关任务，再删除设备。",
                extra={"unfinished_transfers": unfinished},
            )
        deleted_at = utcnow()
        original_name = device.name
        metadata = dict(device.device_metadata or {})
        metadata.update(
            {
                "deleted_at": deleted_at.isoformat(),
                "deleted_by": admin.id,
                "original_name": original_name,
            }
        )
        device.device_metadata = metadata
        device.name = f"{original_name[:78]}（已删除-{device.id[:8]}）"
        device.active = False
        device.status = "revoked"
        device.allow_host_access = False
        device.allow_device_transfer = False
        device.agent_token_hash = ""
        device.certificate_fingerprint = ""
        device.version += 1
        for grant in db.scalars(
            select(DeviceGrant).where(DeviceGrant.device_id == device.id)
        ).all():
            grant.active = False
            grant.version += 1
        for command in db.scalars(
            select(DeviceCommand).where(
                DeviceCommand.device_id == device.id,
                DeviceCommand.status.in_(["queued", "delivered"]),
            )
        ).all():
            command.status = "failed"
            command.result = {"code": "DEVICE_DELETED", "message": "设备已由管理员删除"}
            command.completed_at = deleted_at
        for root in db.scalars(
            select(WorkspaceRoot).where(WorkspaceRoot.device_id == device.id)
        ).all():
            root.enabled = False
            root.approval_status = "revoked"
            root.scan_status = "disabled"
            root.version += 1
        write_audit(
            db,
            admin,
            "device.delete",
            "device",
            device.id,
            {"name": original_name, "unfinished_transfers": 0},
            client_ip(request),
        )
        emit_event(db, "device.deleted", device.id, {"name": original_name})
        db.commit()
    return {
        "deleted": True,
        "device_id": device_id,
        "name": original_name,
        "history_preserved": True,
    }


@router.post("/admin/devices/{device_id}/rotate-token", response_model=dict)
def rotate_device_token(
    device_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    device = db.get(Device, device_id)
    if not device or device_is_deleted(device):
        raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "未找到该协同设备。")
    token = issue_v2_device_credential(db, device)
    write_audit(db, admin, "device.token_rotate", "device", device.id, {}, client_ip(request))
    db.commit()
    return {"device_id": device.id, "device_token": token, "protocol_version": 2}


@router.post("/client-agents/credential/upgrade", response_model=dict)
def upgrade_client_agent_credential(
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    """有效旧令牌原地换发 v2；无效令牌必须由主机管理员重新授权。"""

    device = authenticated_device(token, db)
    previous_protocol = device.protocol_version
    new_token = issue_v2_device_credential(db, device)
    write_audit(
        db,
        db.get(User, device.created_by),
        "device.credential_upgrade",
        "device",
        device.id,
        {"from_protocol": max(1, previous_protocol), "to_protocol": 2},
        client_ip(request),
    )
    db.commit()
    return {
        "device_id": device.id,
        "device_token": new_token,
        "protocol_version": 2,
        "credential_state": "active",
    }


@router.post("/client-agents/credential/reauthorize", response_model=dict)
def reauthorize_client_agent(
    request: Request,
    device_id: str = Query(min_length=1),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    """管理员一次确认恢复旧绑定；不删除设备、共享目录或本地业务数据。"""

    device = db.get(Device, device_id)
    if not device or device_is_deleted(device):
        raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "未找到需要重新授权的协同设备。")
    if not device.active:
        raise ProblemException(409, "DEVICE_DISABLED", "设备已停用", "请先恢复设备，再重新授权。")
    token = issue_v2_device_credential(db, device)
    device.status = "offline"
    write_audit(
        db,
        admin,
        "device.reauthorize",
        "device",
        device.id,
        {"binding_preserved": True, "protocol_version": 2},
        client_ip(request),
    )
    db.commit()
    return {
        "device_id": device.id,
        "device_token": token,
        "protocol_version": 2,
        "binding_preserved": True,
    }


@router.post("/admin/devices/{device_id}/rotate-certificate", response_model=dict)
def queue_certificate_rotation(
    device_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    device = db.get(Device, device_id)
    if not device or device_is_deleted(device) or not device.active:
        raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "请先恢复设备后再轮换证书。")
    command = DeviceCommand(
        device_id=device.id,
        command_type="rotate_certificate",
        idempotency_key=f"rotate-certificate:{device.id}:{device.version}",
        payload={},
    )
    db.add(command)
    write_audit(db, admin, "device.certificate_rotate_queue", "device", device.id, {}, client_ip(request))
    db.commit()
    return {"device_id": device.id, "command_id": command.id, "message": "证书轮换命令已排队"}


@router.get("/admin/device-grants", response_model=typing.List[DeviceGrantOut])
def list_device_grants(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[DeviceGrant]:
    return list(db.scalars(select(DeviceGrant).order_by(DeviceGrant.created_at.desc())).all())


@router.post("/admin/device-grants", response_model=DeviceGrantOut, status_code=201)
def create_device_grant(
    payload: DeviceGrantCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DeviceGrant:
    device = db.get(Device, payload.device_id)
    if not device or not device.active:
        raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "请先完成设备入网。")
    unknown = sorted(set(payload.capabilities) - DEVICE_GRANT_CAPABILITIES)
    if unknown or not payload.capabilities:
        raise ProblemException(
            422,
            "DEVICE_GRANT_CAPABILITY_INVALID",
            "设备授权能力无效",
            "请选择下载、分享或接收能力。",
            extra={"capabilities": unknown},
        )
    if payload.user_id and not db.get(User, payload.user_id):
        raise ProblemException(404, "USER_NOT_FOUND", "人员不存在", "未找到授权人员。")
    root = db.get(WorkspaceRoot, payload.root_id) if payload.root_id else None
    if payload.root_id and not root:
        raise ProblemException(404, "WORKSPACE_ROOT_NOT_FOUND", "共享目录不存在", "请先批准共享目录。")
    if root and (
        not root.enabled
        or root.approval_status != "approved"
        or (root.source.value == "device" and root.device_id != device.id)
    ):
        raise ProblemException(
            422,
            "DEVICE_GRANT_ROOT_INVALID",
            "共享目录与设备不匹配",
            "只能授权该设备已获批准的共享目录。",
        )
    grant = DeviceGrant(created_by=admin.id, **payload.model_dump())
    db.add(grant)
    write_audit(db, admin, "device.grant_create", "device_grant", grant.id, {"device_id": payload.device_id, "root_id": payload.root_id, "capabilities": payload.capabilities}, client_ip(request))
    db.commit()
    db.refresh(grant)
    return grant


@router.patch("/admin/device-grants/{grant_id}", response_model=DeviceGrantOut)
def patch_device_grant(
    grant_id: str,
    active: bool,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DeviceGrant:
    grant = db.get(DeviceGrant, grant_id)
    if not grant:
        raise ProblemException(404, "DEVICE_GRANT_NOT_FOUND", "设备授权不存在", "未找到授权记录。")
    if grant.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "设备授权已变化", "请刷新后重试。")
    grant.active = active
    grant.version += 1
    write_audit(db, admin, "device.grant_update", "device_grant", grant.id, {"active": active}, client_ip(request))
    db.commit()
    db.refresh(grant)
    return grant


@router.post("/admin/workspace/remote-roots", response_model=dict, status_code=201)
def request_remote_root(
    payload: RemoteRootRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    device = db.get(Device, payload.device_id)
    if not device:
        raise ProblemException(404, "DEVICE_NOT_FOUND", "设备不存在", "未找到协同设备。")
    remote_key = PurePosixPath(payload.remote_key).as_posix().lstrip("/")
    if not remote_key or ".." in PurePosixPath(remote_key).parts:
        raise ProblemException(422, "REMOTE_PATH_INVALID", "远程目录无效", "共享目录必须是设备端选择的相对标识。")
    absolute_marker = f"device:{device.id}:{remote_key}"
    existing = db.scalar(select(WorkspaceRoot).where(WorkspaceRoot.absolute_path == absolute_marker))
    if existing:
        return {
            "root": {
                "id": existing.id,
                "name": existing.name,
                "source": existing.source.value,
                "device_id": existing.device_id,
                "remote_key": existing.remote_key,
                "approval_status": existing.approval_status,
                "enabled": existing.enabled,
                "version": existing.version,
            },
            "created": False,
        }
    root = WorkspaceRoot(
        name=payload.name.strip(),
        absolute_path=absolute_marker,
        source="device",
        device_id=device.id,
        remote_key=remote_key,
        approval_status="pending",
        enabled=False,
        read_only=True,
        created_by=admin.id,
    )
    db.add(root)
    write_audit(db, admin, "workspace.remote_root_request", "workspace_root", root.id, {"device_id": device.id, "name": root.name}, client_ip(request))
    db.commit()
    db.refresh(root)
    return {
        "root": {
            "id": root.id,
            "name": root.name,
            "source": root.source.value,
            "device_id": root.device_id,
            "remote_key": root.remote_key,
            "approval_status": root.approval_status,
            "enabled": root.enabled,
            "published_by_user_id": root.published_by_user_id,
            "share_scope": root.share_scope,
            "semantic_content_enabled": root.semantic_content_enabled,
            "published_at": serialize_api_datetime(root.published_at) if root.published_at else None,
            "version": root.version,
        },
        "created": True,
    }


@router.patch("/admin/workspace/remote-roots/{root_id}", response_model=dict)
def patch_remote_root(
    root_id: str,
    payload: RemoteRootPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    root = db.get(WorkspaceRoot, root_id)
    if not root or root.source.value != "device":
        raise ProblemException(404, "REMOTE_ROOT_NOT_FOUND", "远程共享目录不存在", "未找到远程共享目录。")
    if root.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "共享目录已变化", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(root, field, getattr(payload, field))
    if payload.approval_status == "approved":
        root.enabled = True
    if payload.approval_status == "rejected":
        root.enabled = False
    root.version += 1
    write_audit(db, admin, "workspace.remote_root_update", "workspace_root", root.id, {"approval_status": root.approval_status}, client_ip(request))
    db.commit()
    return {"id": root.id, "approval_status": root.approval_status, "approval_note": root.approval_note, "enabled": root.enabled, "version": root.version}


@router.get("/admin/workspace/remote-roots", response_model=typing.List[dict])
def list_admin_remote_roots(
    approval_status: str | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[dict]:
    statement = select(WorkspaceRoot).where(WorkspaceRoot.source == "device")
    if approval_status:
        if approval_status not in {"pending", "approved", "rejected"}:
            raise ProblemException(422, "REMOTE_ROOT_STATUS_INVALID", "审批状态无效", "请选择有效审批状态。")
        statement = statement.where(WorkspaceRoot.approval_status == approval_status)
    roots = db.scalars(statement.order_by(WorkspaceRoot.created_at.desc())).all()
    return [
        {
            "id": root.id,
            "name": root.name,
            "device_id": root.device_id,
            "remote_key": root.remote_key,
            "approval_status": root.approval_status,
            "approval_note": root.approval_note,
            "enabled": root.enabled,
            "file_count": root.file_count,
            "last_scan_at": serialize_api_datetime(root.last_scan_at) if root.last_scan_at else None,
            "version": root.version,
        }
        for root in roots
    ]


@router.get("/devices/workspace/roots", response_model=typing.List[dict])
def list_device_roots(
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> list[dict]:
    device = authenticated_device(token, db)
    roots = db.scalars(
        select(WorkspaceRoot)
        .where(WorkspaceRoot.device_id == device.id, WorkspaceRoot.source == "device")
        .order_by(WorkspaceRoot.created_at)
    ).all()
    return [
        {
            "id": root.id,
            "name": root.name,
            "remote_key": root.remote_key,
            "approval_status": root.approval_status,
            "approval_note": root.approval_note,
            "enabled": root.enabled,
            "published_by_user_id": root.published_by_user_id,
            "share_scope": root.share_scope,
            "semantic_content_enabled": root.semantic_content_enabled,
            "published_at": serialize_api_datetime(root.published_at) if root.published_at else None,
            "version": root.version,
        }
        for root in roots
    ]


@router.post("/devices/workspace/roots", response_model=dict, status_code=201)
def create_device_root(
    payload: DeviceRemoteRootCreate,
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    """终端声明本机所选目录；绝对路径只保存在本机 Agent 配置中。"""

    device = authenticated_device(token, db)
    share_action = None
    if payload.action_token:
        share_action = db.scalar(
            select(LocalShareAction).where(
                LocalShareAction.token_hash == hash_token(payload.action_token),
                LocalShareAction.device_id == device.id,
                LocalShareAction.consumed_at.is_(None),
                LocalShareAction.expires_at > utcnow(),
            )
        )
        if not share_action:
            raise ProblemException(
                401,
                "LOCAL_SHARE_ACTION_INVALID",
                "共享操作已失效",
                "请回到文件中心重新点击“共享本机文件夹”。",
            )
        if not device.allow_user_shares:
            raise ProblemException(
                403,
                "LOCAL_SHARE_DISABLED",
                "本机目录发布已停用",
                "请联系管理员开启该协同电脑的目录发布能力。",
            )
    marker = f"device:{device.id}:{payload.remote_key}"
    root = db.scalar(
        select(WorkspaceRoot).where(
            WorkspaceRoot.device_id == device.id,
            WorkspaceRoot.remote_key == payload.remote_key,
        )
    )
    created = root is None
    if root is None:
        direct_publish = share_action is not None
        root = WorkspaceRoot(
            name=payload.name.strip(),
            absolute_path=marker,
            source="device",
            device_id=device.id,
            remote_key=payload.remote_key,
            approval_status="approved" if direct_publish else "pending",
            approval_note="用户从本机显式选择并发布" if direct_publish else "",
            published_by_user_id=share_action.user_id if share_action else None,
            share_scope="team" if direct_publish else "selected",
            published_at=utcnow() if direct_publish else None,
            enabled=direct_publish,
            read_only=True,
            created_by=share_action.user_id if share_action else device.created_by,
        )
        db.add(root)
        db.flush()
        if direct_publish:
            share_action.consumed_at = utcnow()
        else:
            notify_root_approval_needed(db, root, device)
        write_audit(
            db,
            db.get(User, root.created_by),
            "workspace.remote_root_publish" if direct_publish else "workspace.remote_root_request",
            "workspace_root",
            root.id,
            {
                "device_id": device.id,
                "name": root.name,
                "share_scope": root.share_scope,
                "direct_publish": direct_publish,
            },
            client_ip(request),
        )
    elif share_action:
        # 同一远端标识重新发布时恢复目录，但令牌仍只能消费一次。
        if root.published_by_user_id not in {None, share_action.user_id}:
            raise ProblemException(
                409,
                "REMOTE_ROOT_OWNED_BY_OTHER_USER",
                "该本机目录已由其他用户发布",
                "请由原发布人管理，或联系管理员停用后重新发布。",
            )
        root.name = payload.name.strip()
        root.published_by_user_id = share_action.user_id
        root.share_scope = root.share_scope or "team"
        root.approval_status = "approved"
        root.approval_note = "用户从本机重新发布"
        root.published_at = utcnow()
        root.enabled = True
        root.version += 1
        share_action.consumed_at = utcnow()
        write_audit(
            db,
            db.get(User, share_action.user_id),
            "workspace.remote_root_republish",
            "workspace_root",
            root.id,
            {"device_id": device.id, "name": root.name},
            client_ip(request),
        )
    db.commit()
    return {
        "id": root.id,
        "name": root.name,
        "remote_key": root.remote_key,
        "approval_status": root.approval_status,
        "enabled": root.enabled,
        "share_scope": root.share_scope,
        "semantic_content_enabled": root.semantic_content_enabled,
        "version": root.version,
        "created": created,
    }


@router.patch("/devices/workspace/roots/{root_id}", response_model=dict)
def rename_device_root(
    root_id: str,
    payload: DeviceRemoteRootPatch,
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    root = db.get(WorkspaceRoot, root_id)
    if not root or root.device_id != device.id or root.source.value != "device":
        raise ProblemException(404, "REMOTE_ROOT_NOT_FOUND", "共享目录不存在", "未找到本机共享目录。")
    root.name = payload.name.strip()
    root.version += 1
    write_audit(db, db.get(User, device.created_by), "workspace.remote_root_rename", "workspace_root", root.id, {"name": root.name}, client_ip(request))
    db.commit()
    return {"id": root.id, "name": root.name, "version": root.version}


@router.delete("/devices/workspace/roots/{root_id}", response_model=dict)
def disable_device_root(
    root_id: str,
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    root = db.get(WorkspaceRoot, root_id)
    if not root or root.device_id != device.id or root.source.value != "device":
        raise ProblemException(404, "REMOTE_ROOT_NOT_FOUND", "共享目录不存在", "未找到本机共享目录。")
    root.enabled = False
    root.approval_status = "rejected"
    root.approval_note = "协同电脑已停止共享"
    root.version += 1
    for item in db.scalars(select(WorkspaceFile).where(WorkspaceFile.root_id == root.id)).all():
        item.in_scope = False
        item.status = "missing"
        for checkpoint in db.scalars(
            select(SemanticIndexCheckpoint).where(
                SemanticIndexCheckpoint.object_type == "workspace_file_content",
                SemanticIndexCheckpoint.object_id == item.id,
            )
        ).all():
            db.delete(checkpoint)
    write_audit(db, db.get(User, device.created_by), "workspace.remote_root_disable", "workspace_root", root.id, {"device_id": device.id}, client_ip(request))
    db.commit()
    return {"id": root.id, "disabled": True, "version": root.version}


@router.post("/devices/heartbeat", response_model=DeviceOut)
def heartbeat(
    payload: DeviceHeartbeat,
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> Device:
    device = authenticated_device(token, db)
    device.status = "online"
    device.last_seen_at = utcnow()
    device.protocol_version = max(device.protocol_version, payload.protocol_version)
    device.credential_state = "active"
    for field in (
        "architecture",
        "kernel",
        "app_version",
        "agent_version",
        "local_username",
    ):
        setattr(device, field, getattr(payload, field))
    for field in (
        "platform_family",
        "distribution",
        "distribution_version",
        "package_format",
        "runtime_profile",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(device, field, value)
    device.platform = normalized_platform(payload.platform)
    if payload.capabilities is not None:
        device.capabilities = sorted(set(payload.capabilities))
    device.ip_address = payload.ip_address or client_ip(request)
    device.disk_free_bytes = payload.disk_free_bytes
    previous_metadata = dict(device.device_metadata or {})
    device.device_metadata = {
        **previous_metadata,
        "root_count": payload.root_count,
        "indexed_file_count": payload.indexed_file_count,
        "enrollment_id": previous_metadata.get("enrollment_id", ""),
    }
    reconcile_device_update(db, device)
    db.commit()
    db.refresh(device)
    return device


@router.post("/devices/browser-token", response_model=DeviceBrowserTokenOut)
def create_device_browser_token(
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> DeviceBrowserTokenOut:
    device = authenticated_device(token, db)
    # 启动票据会短暂出现在自定义协议和浏览器 URL 中，只允许 60 秒内换取
    # 真正的 HttpOnly 设备上下文；不能直接作为业务接口 Cookie 使用。
    browser_token, expires_at = issue_device_context_token(
        db,
        device,
        lifetime=timedelta(seconds=60),
        purpose="launch",
    )
    db.commit()
    return DeviceBrowserTokenOut(token=browser_token, expires_at=expires_at)


@router.get("/device/update-gate", response_model=DeviceUpdateGate)
def device_update_gate(
    request: Request,
    db: Session = Depends(get_session),
) -> DeviceUpdateGate:
    value = build_device_gate(db, request_device(request, db, allow_ip_fallback=True))
    db.commit()
    return DeviceUpdateGate(**value)


@router.post("/device/update-start", response_model=DeviceUpdateGate)
def start_current_device_update(
    request: Request,
    db: Session = Depends(get_session),
) -> DeviceUpdateGate:
    device = request_device(request, db)
    if not device:
        raise ProblemException(
            400,
            "DEVICE_CONTEXT_REQUIRED",
            "无法识别当前协同电脑",
            "请从本机“党建智办”桌面图标重新进入系统。",
        )
    start_device_update(db, device)
    db.commit()
    value = build_device_gate(db, device)
    db.commit()
    return DeviceUpdateGate(**value)


@router.post("/devices/certificate/rotate", response_model=DeviceCertificateOut)
def rotate_certificate(
    payload: DeviceCertificateRotateRequest,
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> DeviceCertificateOut:
    device = authenticated_device(token, db)
    bundle = issue_device_certificate(get_settings(), device.id, payload.csr_pem)
    device.certificate_fingerprint = str(bundle.get("certificate_fingerprint", ""))
    device.certificate_expires_at = utcnow() + timedelta(days=365)
    device.version += 1
    db.commit()
    settings = get_settings()
    if settings.environment == "test":
        response_agent_url = f"{request.base_url.scheme}://{request.base_url.hostname}:{settings.agent_port}"
    else:
        try:
            validate_advertise_host(settings.network_advertise_host)
        except RuntimeError as exc:
            raise ProblemException(409, "AGENT_ADVERTISE_HOST_INVALID", "主机协同地址已失效", "请由主机管理员修复网络配置。") from exc
        response_agent_url = service_url(
            settings.network_advertise_host,
            settings.agent_port,
            tls_enabled=settings.tls_enabled,
        )
    return DeviceCertificateOut(
        certificate_pem=str(bundle.get("certificate_pem", "")),
        ca_certificate_pem=str(bundle.get("ca_certificate_pem", "")),
        certificate_fingerprint=device.certificate_fingerprint,
        agent_url=response_agent_url,
        expires_at=device.certificate_expires_at,
    )


@router.post("/devices/workspace/index-delta", response_model=dict)
def upload_index_delta(
    payload: RemoteIndexDelta,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    root = db.get(WorkspaceRoot, payload.root_id)
    if not root or root.source.value != "device" or root.device_id != device.id or root.approval_status != "approved":
        raise ProblemException(403, "ROOT_NOT_APPROVED", "共享目录尚未批准", "请由主机管理员批准该目录。")
    now = utcnow()
    changed = 0
    for item in payload.files:
        relative = PurePosixPath(item.relative_path).as_posix().lstrip("/")
        if not relative or ".." in PurePosixPath(relative).parts:
            raise ProblemException(422, "REMOTE_PATH_INVALID", "索引路径无效", "远程索引只能使用相对路径。")
        parent_id = None
        if item.parent_relative_path:
            parent_relative = PurePosixPath(item.parent_relative_path).as_posix().lstrip("/")
            if ".." in PurePosixPath(parent_relative).parts:
                raise ProblemException(422, "REMOTE_PATH_INVALID", "父目录索引无效", "远程索引只能使用相对路径。")
            parent_id = db.scalar(
                select(WorkspaceFile.id).where(
                    WorkspaceFile.root_id == root.id,
                    WorkspaceFile.relative_path == parent_relative,
                    WorkspaceFile.is_directory.is_(True),
                )
            )
        current = db.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.root_id == root.id,
                WorkspaceFile.relative_path == relative,
            )
        )
        created = current is None
        if not current:
            current = WorkspaceFile(
                root_id=root.id,
                relative_path=relative,
                name=safe_name(item.name),
                is_directory=item.is_directory,
                parent_id=parent_id,
                device_id=device.id,
                remote_file_key=f"{device.id}:{root.remote_key}:{relative}",
            )
            db.add(current)
        current.name = safe_name(item.name)
        current.is_directory = item.is_directory
        current.parent_id = parent_id
        current.extension = item.extension.lower()
        current.size_bytes = item.size_bytes
        current.modified_at = item.modified_at
        current.mime_type = item.mime_type
        if item.sha256:
            current.sha256 = item.sha256.lower()
        elif created or item.content_changed:
            current.sha256 = None
        # 正文只在发布人或管理员逐目录开启后接收；关闭时旧 Agent 即使继续
        # 发送也会被主机丢弃，并在下次语义批处理中清理正文向量。
        current.extracted_text = item.extracted_text if root.semantic_content_enabled else ""
        current.ocr_text = ""
        current.content_status = "indexed" if current.extracted_text else "metadata_only"
        current.content_error_code = ""
        current.status = "indexed"
        current.availability = "online"
        current.indexed_at = now
        current.last_seen_at = now
        current.version = (current.version or 0) + 1
        changed += 1
    for removed in payload.removed_paths:
        relative = PurePosixPath(removed).as_posix().lstrip("/")
        if not relative or ".." in PurePosixPath(relative).parts:
            raise ProblemException(422, "REMOTE_PATH_INVALID", "索引路径无效", "远程索引只能使用相对路径。")
        current = db.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.root_id == root.id,
                WorkspaceFile.relative_path == relative,
            )
        )
        if current and current.status != "missing":
            current.status = "missing"
            current.availability = "missing"
            current.version += 1
            changed += 1
    root.last_scan_at = now
    root.scan_status = "indexed"
    # Session 关闭了 autoflush；统计前必须先把本批新增/状态变化写入当前事务，
    # 否则共享根会长期显示 0 个文件。计数只包含仍在共享范围内的活动项目。
    db.flush()
    root.file_count = db.scalar(
        select(func.count()).select_from(WorkspaceFile).where(
            WorkspaceFile.root_id == root.id,
            WorkspaceFile.is_directory.is_(False),
            WorkspaceFile.in_scope.is_(True),
            WorkspaceFile.status != "missing",
        )
    ) or 0
    root.directory_count = db.scalar(
        select(func.count()).select_from(WorkspaceFile).where(
            WorkspaceFile.root_id == root.id,
            WorkspaceFile.is_directory.is_(True),
            WorkspaceFile.in_scope.is_(True),
            WorkspaceFile.status != "missing",
        )
    ) or 0
    db.commit()
    return {
        "root_id": root.id,
        "changed": changed,
        "indexed_at": serialize_api_datetime(now),
    }


@router.get("/devices/commands", response_model=typing.List[dict])
def list_commands(
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> list[dict]:
    device = authenticated_device(token, db)
    lease_expired = utcnow() - timedelta(seconds=45)
    commands = db.scalars(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device.id,
            (
                (DeviceCommand.status == "queued")
                | (
                    (DeviceCommand.status == "delivered")
                    & (DeviceCommand.delivered_at < lease_expired)
                )
            ),
        )
        .order_by(DeviceCommand.created_at)
        .limit(10)
    ).all()
    active_global = db.scalar(
        select(func.count())
        .select_from(DeviceCommand)
        .where(
            DeviceCommand.command_type.in_(["upload_file", "upload_bundle", "download_file"]),
            DeviceCommand.status == "delivered",
            DeviceCommand.delivered_at >= lease_expired,
        )
    ) or 0
    selected: list[DeviceCommand] = []
    has_device_transfer = False
    for command in commands:
        is_transfer = command.command_type in {"upload_file", "upload_bundle", "download_file"}
        if is_transfer and (active_global >= 2 or has_device_transfer):
            continue
        command.status = "delivered"
        command.delivered_at = utcnow()
        command.delivery_attempts += 1
        selected.append(command)
        if is_transfer:
            active_global += 1
            has_device_transfer = True
    db.commit()
    return [
        {"id": command.id, "type": command.command_type, "payload": command.payload}
        for command in selected
    ]


@router.post("/devices/commands/{command_id}/ack", response_model=dict)
def ack_command(
    command_id: str,
    payload: dict,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    command = db.get(DeviceCommand, command_id)
    if not command or command.device_id != device.id:
        raise ProblemException(404, "DEVICE_COMMAND_NOT_FOUND", "设备命令不存在", "命令已过期或不属于本设备。")
    ok = payload.get("ok") is True
    command.status = "completed" if ok else "failed"
    command.result = {
        key: payload[key]
        for key in ("ok", "error_code", "message")
        if key in payload
    }
    command.completed_at = utcnow()
    transfer_id = str(command.payload.get("transfer_id", ""))
    transfer = db.get(Transfer, transfer_id) if transfer_id else None
    cleanup_part = False
    if transfer:
        if ok and command.command_type == "download_file":
            transfer.status = "completed"
            transfer.completed_chunks = transfer.total_chunks
            transfer.error_code = ""
            transfer.error_message = ""
        elif not ok:
            transfer.status = "failed"
            transfer.error_code = str(payload.get("error_code", "AGENT_COMMAND_FAILED"))[:80]
            transfer.error_message = str(payload.get("message", "设备端执行失败。"))[:2_000]
        # 源设备上传成功后目标设备仍需读取主机中转文件；只有目标下载完成，
        # 或任一传输命令失败时，才可回收 .part。
        if command.command_type == "download_file" or not ok:
            cleanup_part = True
        transfer.version += 1
        if command.command_type in {"upload_file", "upload_bundle", "download_file"} and (
            ok or not ok
        ):
            dedupe = f"transfer:{transfer.id}:{'completed' if ok else 'failed'}"
            if not db.scalar(
                select(Notification.id).where(Notification.dedupe_key == dedupe)
            ):
                db.add(
                    Notification(
                        user_id=transfer.requested_by,
                        notification_type="transfer",
                        title="文件传输完成" if ok else "文件传输失败",
                        body=(
                            f"“{transfer.original_name}”已完成传输。"
                            if ok
                            else f"“{transfer.original_name}”传输失败，请查看错误原因。"
                        ),
                        entity_type="transfer",
                        entity_id=transfer.id,
                        dedupe_key=dedupe,
                    )
                )
    if command.command_type == "apply_update":
        run_id = str(command.payload.get("run_id", ""))
        update_run = db.get(UpdateRun, run_id) if run_id else None
        if update_run:
            update_run.status = "completed" if ok else "failed"
            update_run.progress = 100 if ok else 0
            update_run.message = str(
                payload.get(
                    "message",
                    "设备升级完成" if ok else "设备升级失败",
                )
            )[:2_000]
            update_run.completed_at = utcnow()
    db.commit()
    if cleanup_part and transfer:
        cleanup_transfer_part(transfer.id)
    return {"acknowledged": True}


@router.get("/transfers", response_model=typing.List[TransferOut])
def list_transfers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[Transfer]:
    statement = select(Transfer)
    if user.role.value != "admin":
        statement = statement.where(Transfer.requested_by == user.id)
    return list(db.scalars(statement.order_by(Transfer.created_at.desc()).limit(200)).all())


@router.get("/collaboration/options", response_model=dict)
def collaboration_options(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """返回当前账号可用于传输的设备和目录，不暴露本机绝对路径。"""

    all_devices = db.scalars(
        select(Device).where(Device.active.is_(True)).order_by(Device.name)
    ).all()
    all_roots = db.scalars(
        select(WorkspaceRoot).where(WorkspaceRoot.enabled.is_(True)).order_by(WorkspaceRoot.name)
    ).all()
    roots: list[dict] = []
    allowed_device_ids: set[str] = set()
    current = request_device(request, db)
    current_device_id = current.id if current else None
    if current_device_id:
        allowed_device_ids.add(current_device_id)
    for root in all_roots:
        permissions = workspace_root_permissions(db, root, user, current_device_id)
        if not permissions["browse"] and not permissions["manage_root"]:
            continue
        if root.device_id:
            allowed_device_ids.add(root.device_id)
        roots.append(
            {
                "id": root.id,
                "name": root.name,
                "source": root.source.value,
                "device_id": root.device_id,
                "remote_key": root.remote_key,
                "approval_status": root.approval_status,
                "approval_note": root.approval_note,
                "published_by_user_id": root.published_by_user_id,
                "share_scope": root.share_scope,
                "semantic_content_enabled": root.semantic_content_enabled,
                "enabled": root.enabled,
                "permissions": permissions,
            }
        )
    devices = [
        {
            "id": device.id,
            "name": device.name,
            "status": enum_value(device.status),
            "active": device.active,
            "allow_host_access": device.allow_host_access,
            "allow_device_transfer": device.allow_device_transfer,
            "allow_user_shares": device.allow_user_shares,
            "is_current": device.id == current_device_id,
        }
        for device in all_devices
        if user.role.value == "admin" or device.id in allowed_device_ids or device.allow_device_transfer
    ]
    return {
        "current_device": (
            {
                "id": current.id,
                "name": current.name,
                "status": enum_value(current.status),
                "allow_user_shares": current.allow_user_shares,
            }
            if current
            else None
        ),
        "devices": devices,
        "roots": roots,
    }


def _authorized_transfer(db: Session, transfer_id: str, user: User) -> Transfer:
    transfer = db.get(Transfer, transfer_id)
    if not transfer:
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "传输任务不存在", "未找到该传输任务。")
    if user.role.value != "admin" and transfer.requested_by != user.id:
        raise ProblemException(403, "TRANSFER_FORBIDDEN", "无权访问该传输", "只能处理自己发起的传输。")
    return transfer


def _require_current_transfer_source_access(db: Session, transfer: Transfer) -> None:
    """最终读取受管副本前再次校验源共享权限。

    旧传输和管理员手工创建的接收文件可能没有源索引，此时只能沿用传输所有权
    校验；凡是关联了协同机工作区文件的传输，都必须以当前目录状态和当前授权
    为准，不能把已经完成的中转副本当成永久授权。
    """

    item_ids = transfer.item_ids or (
        [transfer.source_file_id] if transfer.source_file_id else []
    )
    if not item_ids and not transfer.source_device_id:
        return
    if not transfer_sources_still_allowed(db, transfer):
        raise ProblemException(
            403,
            "GRANT_DENIED",
            "共享文件权限已失效",
            "源目录已停用、设备不可用或当前账号不再拥有下载权限。",
        )


def _completed_inbox_path(transfer: Transfer) -> Path:
    direction = enum_value(transfer.direction)
    completed_host_bundle = (
        direction == "host_to_device"
        and transfer.delivery_mode == "browser"
        and transfer.bundle_mode != "single"
    )
    if (
        enum_value(transfer.status) != "completed"
        or (direction != "device_to_host" and not completed_host_bundle)
    ):
        raise ProblemException(409, "INBOX_FILE_NOT_READY", "接收文件尚未就绪", "请等待设备传输完成。")
    path = get_settings().inbox_dir / f"{transfer.id}-{safe_name(transfer.original_name)}"
    if not path.is_file():
        raise ProblemException(410, "INBOX_FILE_MISSING", "接收文件已缺失", "请重新发起传输。")
    if transfer.size_bytes and path.stat().st_size != transfer.size_bytes:
        raise ProblemException(409, "INBOX_FILE_SIZE_MISMATCH", "接收文件大小不一致", "请重新发起传输。")
    digest = sha256_path(path)
    if transfer.sha256 and digest != transfer.sha256.lower():
        raise ProblemException(409, "INBOX_FILE_HASH_MISMATCH", "接收文件校验失败", "请重新发起传输。")
    return path


@router.get("/transfers/{transfer_id}/content")
def download_transfer_content(
    transfer_id: str,
    request: Request,
    inline: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    transfer = _authorized_transfer(db, transfer_id, user)
    _require_current_transfer_source_access(db, transfer)
    path = _completed_inbox_path(transfer)
    media_type = mimetypes.guess_type(transfer.original_name)[0] or "application/octet-stream"
    actual_inline = inline and may_render_inline(media_type)
    write_audit(db, user, "transfer.content_read", "transfer", transfer.id, {"inline": actual_inline}, client_ip(request))
    db.commit()
    disposition = "inline" if actual_inline else "attachment"
    return FileResponse(
        path,
        media_type=media_type,
        # 交给 Starlette 生成 RFC 5987 filename*=UTF-8 头，避免中文文件名
        # 被直接塞入 latin-1 HTTP 头而触发 500。
        filename=safe_name(transfer.original_name),
        content_disposition_type=disposition,
    )


@router.post("/transfers/{transfer_id}/freeze", response_model=TransferOut)
def freeze_transfer_content(
    transfer_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Transfer:
    transfer = _authorized_transfer(db, transfer_id, user)
    _require_current_transfer_source_access(db, transfer)
    path = _completed_inbox_path(transfer)
    mime_type = mimetypes.guess_type(transfer.original_name)[0] or "application/octet-stream"
    blob = store_managed_path(db, path, transfer.original_name, mime_type, transfer.sha256)
    transfer.handled_by = user.id
    transfer.handled_at = utcnow()
    transfer.linked_entity_type = "frozen"
    transfer.linked_entity_id = blob.sha256[:36]
    transfer.version += 1
    write_audit(db, user, "transfer.freeze", "transfer", transfer.id, {"sha256": blob.sha256}, client_ip(request))
    db.commit()
    db.refresh(transfer)
    return transfer


@router.post("/transfers/{transfer_id}/attach", response_model=TransferOut)
def attach_transfer_content(
    transfer_id: str,
    payload: TransferAttachCreate,
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Transfer:
    transfer = _authorized_transfer(db, transfer_id, user)
    _require_current_transfer_source_access(db, transfer)
    path = _completed_inbox_path(transfer)
    mime_type = mimetypes.guess_type(transfer.original_name)[0] or "application/octet-stream"
    linked_id: str
    if payload.target_type == "archive":
        record = db.get(ArchiveRecord, payload.target_id)
        if not record:
            raise ProblemException(404, "ARCHIVE_RECORD_NOT_FOUND", "档案不存在", "未找到目标档案。")
        category = category_for_record(db, record)
        current = request_device(request, db)
        if not can_contribute_category(db, category, user, current.id if current else None):
            raise ProblemException(403, "ARCHIVE_CONTRIBUTE_DENIED", "无权补充该类档案", "请联系管理员开通贡献权限。")
        blob = store_managed_path(db, path, transfer.original_name, mime_type, transfer.sha256)
        latest = db.scalar(
            select(func.max(ArchiveAttachment.version_no)).where(
                ArchiveAttachment.record_id == record.id
            )
        )
        attachment = ArchiveAttachment(
            record_id=record.id,
            blob_sha256=blob.sha256,
            version_no=int(latest or 0) + 1,
            display_name=transfer.original_name,
            note=payload.note,
            uploaded_by=user.id,
        )
        db.add(attachment)
        db.flush()
        record.version += 1
        record.updated_by = user.id
        linked_id = attachment.id
        background.add_task(index_archive_attachment, attachment.id)
    else:
        material = db.get(MaterialItem, payload.target_id)
        task = db.get(Task, material.task_id) if material else None
        if not material or not task or not can_edit_task(db, task, user):
            raise ProblemException(404, "MATERIAL_NOT_FOUND", "任务材料不存在", "未找到可补充的任务材料。")
        if task.status == TaskStatus.ARCHIVED:
            raise ProblemException(
                409,
                "TASK_ARCHIVED_IMMUTABLE",
                "归档事项不能修改材料",
                "如确需补充，请由主办人说明原因并重新打开事项。",
            )
        existing_final_id = db.scalar(
            select(AttachmentVersion.id)
            .where(
                AttachmentVersion.material_item_id == material.id,
                AttachmentVersion.is_final.is_(True),
            )
            .limit(1)
        )
        if existing_final_id:
            raise ProblemException(
                409,
                "FINAL_VERSION_LOCKED",
                "最终版本已锁定",
                "该材料已有最终版本，系统不会静默替换；如需更正，请由管理员按更正流程处理。",
            )
        if payload.is_final and payload.stage != MaterialStage.SUBMITTED:
            raise ProblemException(422, "FINAL_STAGE_INVALID", "最终版本阶段不正确", "只有实际报送稿才能确认为最终版本。")
        blob = store_managed_path(db, path, transfer.original_name, mime_type, transfer.sha256)
        # 接收箱是材料上传的另一条入口，必须与普通上传共用同一把写锁并
        # 在落库前再次校验，避免并发请求形成双终稿或写入已归档事项。
        with db_runtime.write_lock:
            db.refresh(task)
            if task.status == TaskStatus.ARCHIVED:
                raise ProblemException(
                    409,
                    "TASK_ARCHIVED_IMMUTABLE",
                    "归档事项不能修改材料",
                    "如确需补充，请由主办人说明原因并重新打开事项。",
                )
            existing_final_id = db.scalar(
                select(AttachmentVersion.id)
                .where(
                    AttachmentVersion.material_item_id == material.id,
                    AttachmentVersion.is_final.is_(True),
                )
                .limit(1)
            )
            if existing_final_id:
                raise ProblemException(
                    409,
                    "FINAL_VERSION_LOCKED",
                    "最终版本已锁定",
                    "该材料已有最终版本，系统不会静默替换；如需更正，请由管理员按更正流程处理。",
                )
            latest = db.scalar(
                select(func.max(AttachmentVersion.version_no)).where(
                    AttachmentVersion.material_item_id == material.id
                )
            )
            version = AttachmentVersion(
                material_item_id=material.id,
                blob_sha256=blob.sha256,
                version_no=int(latest or 0) + 1,
                stage=payload.stage,
                is_final=payload.is_final,
                uploaded_by=user.id,
                note=payload.note,
                display_name=transfer.original_name,
            )
            db.add(version)
            db.flush()
            material.version += 1
            task.version += 1
            task.updated_by = user.id
            linked_id = version.id
    transfer.handled_by = user.id
    transfer.handled_at = utcnow()
    transfer.linked_entity_type = payload.target_type
    transfer.linked_entity_id = linked_id
    transfer.version += 1
    write_audit(db, user, "transfer.attach", "transfer", transfer.id, {"target_type": payload.target_type, "target_id": payload.target_id, "blob_sha256": blob.sha256}, client_ip(request))
    db.commit()
    db.refresh(transfer)
    return transfer


def _workspace_download_items(
    db: Session,
    item_ids: list[str],
    user: User,
    current_device_id: str | None,
) -> list[tuple[WorkspaceFile, WorkspaceRoot]]:
    if len(set(item_ids)) != len(item_ids):
        raise ProblemException(422, "DUPLICATE_DOWNLOAD_ITEM", "存在重复文件", "请取消重复选择后重试。")
    values: list[tuple[WorkspaceFile, WorkspaceRoot]] = []
    total_known_size = 0
    for item_id in item_ids:
        item = db.get(WorkspaceFile, item_id)
        root = db.get(WorkspaceRoot, item.root_id) if item else None
        if not item or not root or not item.in_scope or item.status == "missing":
            raise ProblemException(404, "WORKSPACE_FILE_NOT_FOUND", "文件不存在", "所选文件已移动、停用或不在共享范围。")
        if not workspace_root_permissions(db, root, user, current_device_id)["download"]:
            raise ProblemException(403, "WORKSPACE_ACCESS_DENIED", "无权下载所选文件", "共享范围或下载权限已发生变化。")
        total_known_size += max(0, item.size_bytes)
        values.append((item, root))
    if total_known_size > get_settings().transfer_max_file_gb * 1024**3:
        raise ProblemException(413, "TRANSFER_FILE_TOO_LARGE", "所选文件超过20GB限制", "请减少所选文件后重试。")
    return values


def _host_bundle_to_transit(
    db: Session,
    transfer: Transfer,
    selected: list[tuple[WorkspaceFile, WorkspaceRoot]],
) -> Path:
    """将主机已纳管文件打入受管中转 ZIP；不跟随链接，也不修改原文件。"""

    max_bytes = get_settings().transfer_max_file_gb * 1024**3
    part = get_settings().transfers_dir / f"{transfer.id}.part"
    seen_paths: set[tuple[str, str]] = set()
    entries: list[tuple[Path, str, int]] = []
    for selected_item, root in selected:
        candidates = [selected_item]
        if selected_item.is_directory:
            prefix = selected_item.relative_path.rstrip("/") + "/"
            candidates.extend(
                item
                for item in db.scalars(
                    select(WorkspaceFile).where(
                        WorkspaceFile.root_id == root.id,
                        WorkspaceFile.in_scope.is_(True),
                        WorkspaceFile.status != "missing",
                    )
                ).all()
                if item.relative_path.startswith(prefix)
            )
        for item in candidates:
            key = (root.id, item.relative_path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            path = resolve_workspace_path(root, item.relative_path)
            if path.is_symlink():
                raise ProblemException(403, "WORKSPACE_SYMLINK_DENIED", "压缩范围含符号链接", "请移除链接后重试。")
            relative = PurePosixPath(item.relative_path.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ProblemException(403, "WORKSPACE_PATH_INVALID", "压缩范围含无效路径", "请重新扫描目录后重试。")
            archive_name = (PurePosixPath(safe_archive_component(root.name)) / relative).as_posix()
            entries.append((path, archive_name, max(0, item.size_bytes)))
    if sum(size for _path, _name, size in entries) > max_bytes:
        raise ProblemException(413, "TRANSFER_FILE_TOO_LARGE", "文件夹内容超过20GB限制", "请减少所选文件后重试。")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path, archive_name, _size in entries:
                if path.is_dir():
                    archive.writestr(archive_name.rstrip("/") + "/", b"")
                elif path.is_file():
                    archive.write(path, archive_name)
        if part.stat().st_size > max_bytes:
            raise ProblemException(413, "TRANSFER_FILE_TOO_LARGE", "压缩包超过20GB限制", "请减少所选文件后重试。")
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return part


@router.post("/workspace/downloads", response_model=WorkspaceDownloadOut, status_code=201)
def create_workspace_download(
    payload: WorkspaceDownloadCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceDownloadOut:
    """统一创建浏览器另存为或当前协同机接收目录下载。"""

    current = request_device(request, db)
    if payload.delivery == "current_device" and not current:
        raise ProblemException(409, "CLIENT_DEVICE_REQUIRED", "请在协同电脑上操作", "当前协同机下载需要有效设备上下文。")
    selected = _workspace_download_items(db, payload.item_ids, user, current.id if current else None)
    if payload.bundle_mode == "single" and (len(selected) != 1 or selected[0][0].is_directory):
        raise ProblemException(422, "SINGLE_FILE_REQUIRED", "请选择一个文件", "单文件下载不能包含文件夹或多个项目。")
    if payload.bundle_mode == "folder_zip" and (len(selected) != 1 or not selected[0][0].is_directory):
        raise ProblemException(422, "FOLDER_REQUIRED", "请选择一个文件夹", "文件夹 ZIP 只能选择一个文件夹。")
    source_kinds = {root.source.value for _item, root in selected}
    if len(source_kinds) != 1:
        raise ProblemException(422, "MIXED_SOURCE_BUNDLE", "不能混合主机和协同机文件", "请按来源分别下载。")
    bundle_name = (
        safe_name(selected[0][0].name)
        if payload.bundle_mode == "single"
        else safe_name(
            f"{selected[0][0].name if payload.bundle_mode == 'folder_zip' else 'PartyOps所选文件'}.zip"
        )
    )
    source_item, source_root = selected[0]
    if source_root.source.value == "device":
        device_ids = {root.device_id for _item, root in selected}
        root_ids = {root.id for _item, root in selected}
        if len(device_ids) != 1 or None in device_ids:
            raise ProblemException(422, "MULTIPLE_SOURCE_DEVICES", "所选文件来自不同协同机", "请按协同机分别下载。")
        if payload.bundle_mode != "single" and len(root_ids) != 1:
            raise ProblemException(422, "MULTIPLE_SOURCE_ROOTS", "压缩项目来自不同共享目录", "请按共享目录分别下载。")
        transfer = Transfer(
            direction="device_to_host" if payload.delivery == "browser" else "device_to_device",
            status="queued",
            source_device_id=source_root.device_id,
            destination_device_id=current.id if current else None,
            source_file_id=source_item.id,
            original_name=bundle_name,
            relative_path=source_item.relative_path,
            size_bytes=source_item.size_bytes if payload.bundle_mode == "single" else 0,
            sha256=(source_item.sha256 or "") if payload.bundle_mode == "single" else "",
            chunk_size=CHUNK_SIZE,
            total_chunks=(
                math.ceil(source_item.size_bytes / CHUNK_SIZE)
                if payload.bundle_mode == "single" and source_item.size_bytes
                else 0
            ),
            requested_by=user.id,
            expires_at=utcnow() + timedelta(days=7),
            delivery_mode=payload.delivery,
            bundle_mode=payload.bundle_mode,
            item_ids=payload.item_ids,
            result_name=bundle_name,
        )
        db.add(transfer)
        db.flush()
        queue_transfer_commands(db, transfer)
        content_url = f"/api/v1/transfers/{transfer.id}/content" if payload.delivery == "browser" else ""
    else:
        transfer = Transfer(
            direction="host_to_device",
            status="completed" if payload.delivery == "browser" and payload.bundle_mode == "single" else "queued",
            destination_device_id=current.id if current else None,
            source_file_id=source_item.id,
            original_name=bundle_name,
            relative_path=source_item.relative_path,
            size_bytes=source_item.size_bytes,
            sha256=source_item.sha256 or "",
            chunk_size=CHUNK_SIZE,
            total_chunks=math.ceil(source_item.size_bytes / CHUNK_SIZE) if source_item.size_bytes else 0,
            requested_by=user.id,
            expires_at=utcnow() + timedelta(days=7),
            delivery_mode="browser_direct" if payload.delivery == "browser" and payload.bundle_mode == "single" else payload.delivery,
            bundle_mode=payload.bundle_mode,
            item_ids=payload.item_ids,
            result_name=bundle_name,
        )
        db.add(transfer)
        db.flush()
        if payload.bundle_mode == "single":
            source_path = resolve_workspace_path(source_root, source_item.relative_path)
            if payload.delivery == "browser":
                transfer.result_sha256 = transfer.sha256
                transfer.completed_chunks = transfer.total_chunks
                content_url = f"/api/v1/workspace/files/{source_item.id}/download"
            else:
                ensure_transfer_storage_available(source_path.stat().st_size)
                part = get_settings().transfers_dir / f"{transfer.id}.part"
                shutil.copyfile(source_path, part)
                transfer.transit_path = part.name
                transfer.size_bytes = part.stat().st_size
                transfer.sha256 = transfer.sha256 or sha256_path(part)
                transfer.result_sha256 = transfer.sha256
                transfer.total_chunks = math.ceil(transfer.size_bytes / CHUNK_SIZE) if transfer.size_bytes else 0
                queue_transfer_commands(db, transfer)
                content_url = ""
        else:
            part = _host_bundle_to_transit(db, transfer, selected)
            transfer.transit_path = part.name
            transfer.size_bytes = part.stat().st_size
            transfer.sha256 = sha256_path(part)
            transfer.result_sha256 = transfer.sha256
            transfer.total_chunks = math.ceil(transfer.size_bytes / CHUNK_SIZE) if transfer.size_bytes else 0
            if payload.delivery == "browser":
                target = get_settings().inbox_dir / f"{transfer.id}-{bundle_name}"
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".incoming")
                shutil.copyfile(part, temporary)
                os.replace(temporary, target)
                transfer.status = "completed"
                transfer.completed_chunks = transfer.total_chunks
                content_url = f"/api/v1/transfers/{transfer.id}/content"
            else:
                queue_transfer_commands(db, transfer)
                content_url = ""
    write_audit(
        db,
        user,
        "workspace.download_create",
        "transfer",
        transfer.id,
        {
            "item_count": len(payload.item_ids),
            "bundle_mode": payload.bundle_mode,
            "delivery": payload.delivery,
        },
        client_ip(request),
    )
    emit_event(db, "transfer.created", transfer.id, {"status": enum_value(transfer.status)})
    db.commit()
    return WorkspaceDownloadOut(
        transfer_id=transfer.id,
        status=enum_value(transfer.status),
        delivery=payload.delivery,
        content_url=content_url,
    )


@router.post("/workspace/files/{file_id}/copy-to-inbox", response_model=TransferOut, status_code=201)
def copy_remote_file_to_inbox(
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Transfer:
    source_file = db.get(WorkspaceFile, file_id)
    root = db.get(WorkspaceRoot, source_file.root_id) if source_file else None
    if not source_file or not root or root.source.value != "device" or not root.device_id:
        raise ProblemException(422, "REMOTE_FILE_REQUIRED", "请选择协同电脑上的文件", "主机文件无需通过此接口复制。")
    if not grant_allows(db, user, root.device_id, root.id, "download"):
        raise ProblemException(403, "GRANT_DENIED", "没有下载此设备文件的权限", "请联系管理员授权。")
    needs_approval = Path(source_file.name).suffix.lower() in BLOCKED_EXTENSIONS
    transfer = Transfer(
        direction="device_to_host",
        status="awaiting_approval" if needs_approval else "queued",
        source_device_id=root.device_id,
        source_file_id=source_file.id,
        original_name=safe_name(source_file.name),
        relative_path=source_file.relative_path,
        size_bytes=source_file.size_bytes,
        sha256=source_file.sha256 or "",
        chunk_size=CHUNK_SIZE,
        total_chunks=math.ceil(source_file.size_bytes / CHUNK_SIZE) if source_file.size_bytes else 0,
        requested_by=user.id,
        expires_at=utcnow() + timedelta(days=7),
    )
    db.add(transfer)
    db.flush()
    if transfer.status == "queued":
        queue_transfer_commands(db, transfer)
    write_audit(db, user, "transfer.copy_to_inbox", "transfer", transfer.id, {"file_id": file_id}, client_ip(request))
    db.commit()
    db.refresh(transfer)
    return transfer


@router.post("/transfers", response_model=TransferOut, status_code=201)
def create_transfer(
    payload: TransferCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Transfer:
    name = safe_name(payload.original_name)
    if payload.size_bytes > get_settings().transfer_max_file_gb * 1024**3:
        raise ProblemException(413, "TRANSFER_FILE_TOO_LARGE", "文件超过20GB限制", "请拆分文件后重试。")
    source_file = db.get(WorkspaceFile, payload.source_file_id) if payload.source_file_id else None
    root = db.get(WorkspaceRoot, source_file.root_id) if source_file else None
    source_device_id = payload.source_device_id or (root.device_id if root and root.source.value == "device" else None)
    if source_file and root and root.source.value == "device" and source_device_id:
        if not grant_allows(db, user, source_device_id, root.id, "download"):
            raise ProblemException(403, "GRANT_DENIED", "没有远程文件下载权限", "请联系管理员授权当前设备和用户。")
    if payload.direction == "device_to_device" and source_device_id and not grant_allows(
        db, user, source_device_id, root.id if root else None, "share"
    ):
        raise ProblemException(403, "GRANT_DENIED", "没有跨设备分享权限", "请联系管理员授权源设备。")
    if payload.destination_device_id:
        destination = db.get(Device, payload.destination_device_id)
        receives_to_inbox = bool(
            destination
            and destination.active
            and destination.status not in {"revoked", "quarantined"}
            and destination.allow_device_transfer
            and not payload.destination_root_id
        )
        if not receives_to_inbox and not grant_allows(
            db,
            user,
            payload.destination_device_id,
            payload.destination_root_id,
            "upload",
        ):
            raise ProblemException(403, "GRANT_DENIED", "没有向目标设备发送权限", "请联系管理员开启目标设备接收能力。")
    ext = Path(name).suffix.lower()
    needs_approval = payload.require_approval or ext in BLOCKED_EXTENSIONS or payload.direction == "device_to_device"
    transfer = Transfer(
        direction=payload.direction,
        status="awaiting_approval" if needs_approval else "queued",
        source_device_id=source_device_id,
        destination_device_id=payload.destination_device_id,
        source_file_id=payload.source_file_id,
        destination_root_id=payload.destination_root_id,
        original_name=name,
        relative_path=payload.relative_path,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256.lower(),
        chunk_size=CHUNK_SIZE,
        total_chunks=math.ceil(payload.size_bytes / CHUNK_SIZE) if payload.size_bytes else 0,
        requested_by=user.id,
        expires_at=utcnow() + timedelta(days=7),
    )
    db.add(transfer)
    db.flush()
    if payload.direction == "host_to_device" and source_file and root and root.source.value == "host":
        source_path = resolve_workspace_path(root, source_file.relative_path)
        if not source_path.exists() or not source_path.is_file():
            raise ProblemException(409, "SOURCE_MISSING", "主机源文件不存在", "请重新扫描文件中心。")
        ensure_transfer_storage_available(source_path.stat().st_size)
        part = get_settings().transfers_dir / f"{transfer.id}.part"
        shutil.copyfile(source_path, part)
        transfer.transit_path = part.name
        if not transfer.size_bytes:
            transfer.size_bytes = part.stat().st_size
            transfer.total_chunks = math.ceil(transfer.size_bytes / transfer.chunk_size)
        if not transfer.sha256:
            transfer.sha256 = sha256_path(part)
    if transfer.status == "queued":
        queue_transfer_commands(db, transfer)
    write_audit(db, user, "transfer.create", "transfer", transfer.id, {"direction": enum_value(transfer.direction), "size_bytes": transfer.size_bytes, "requires_approval": needs_approval}, client_ip(request))
    emit_event(db, "transfer.created", transfer.id, {"status": transfer.status})
    db.commit()
    db.refresh(transfer)
    return transfer


@router.patch("/transfers/{transfer_id}", response_model=TransferOut)
def action_transfer(
    transfer_id: str,
    payload: TransferAction,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Transfer:
    transfer = db.get(Transfer, transfer_id)
    if not transfer:
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "传输任务不存在", "未找到该传输任务。")
    if user.role.value != "admin" and transfer.requested_by != user.id:
        raise ProblemException(403, "TRANSFER_FORBIDDEN", "无权操作该传输", "只能操作自己发起的传输。")
    if transfer.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "传输状态已变化", "请刷新后重试。")
    action = payload.action
    cleanup_part = False
    if action == "approve":
        if user.role.value != "admin":
            raise ProblemException(403, "ADMIN_APPROVAL_REQUIRED", "需要管理员审批", "危险或跨设备传输由管理员审批。")
        transfer.status = "queued"
        transfer.approved_by = user.id
        transfer.approval_note = payload.note
    elif action == "pause" and transfer.status in {"queued", "transferring"}:
        transfer.status = "paused"
    elif action == "resume" and transfer.status == "paused":
        transfer.status = "queued"
    elif action == "cancel" and transfer.status not in {"completed", "cancelled"}:
        transfer.status = "cancelled"
        cleanup_part = True
        db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
        transfer.completed_chunks = 0
        transfer.transit_path = ""
    elif action == "retry" and transfer.status in {"failed", "expired"}:
        if transfer.source_device_id:
            cleanup_part = True
            db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
            transfer.completed_chunks = 0
            transfer.transit_path = ""
        transfer.status = "queued"
        transfer.error_code = ""
        transfer.error_message = ""
    if transfer.status == "queued":
        queue_transfer_commands(db, transfer)
    transfer.version += 1
    write_audit(db, user, f"transfer.{action}", "transfer", transfer.id, {"note": payload.note}, client_ip(request))
    db.commit()
    if cleanup_part:
        cleanup_transfer_part(transfer.id)
    db.refresh(transfer)
    return transfer


@router.post("/devices/transfers/{transfer_id}/prepare", response_model=dict)
def prepare_device_bundle(
    transfer_id: str,
    payload: dict,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    """设备完成 ZIP 快照后登记真实大小和哈希，再进入统一分块上传。"""

    device = authenticated_device(token, db)
    transfer = db.get(Transfer, transfer_id)
    if (
        not transfer
        or transfer.source_device_id != device.id
        or transfer.bundle_mode == "single"
        or transfer.status not in {"queued", "transferring"}
    ):
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "批量传输不可用", "任务不存在、已停止或不属于本设备。")
    if not transfer_sources_still_allowed(db, transfer):
        raise ProblemException(403, "GRANT_DENIED", "传输权限已撤销", "主机已停止生成或上传压缩包。")
    try:
        size_bytes = int(payload.get("size_bytes", -1))
    except (TypeError, ValueError) as exc:
        raise ProblemException(422, "BUNDLE_SIZE_INVALID", "压缩包大小无效", "请重新生成压缩包。") from exc
    digest = str(payload.get("sha256", "")).lower()
    max_bytes = get_settings().transfer_max_file_gb * 1024**3
    if size_bytes < 0 or size_bytes > max_bytes:
        raise ProblemException(413, "TRANSFER_FILE_TOO_LARGE", "压缩包超过20GB限制", "请减少所选文件后重试。")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ProblemException(422, "BUNDLE_HASH_INVALID", "压缩包哈希无效", "请重新生成压缩包。")
    if transfer.completed_chunks and (
        transfer.size_bytes != size_bytes or (transfer.sha256 and transfer.sha256 != digest)
    ):
        raise ProblemException(409, "BUNDLE_CHANGED", "压缩包内容已变化", "请取消原传输后重新发起。")
    transfer.size_bytes = size_bytes
    transfer.sha256 = digest
    transfer.result_sha256 = digest
    transfer.total_chunks = math.ceil(size_bytes / transfer.chunk_size) if size_bytes else 0
    transfer.version += 1
    db.commit()
    return {
        "transfer_id": transfer.id,
        "size_bytes": transfer.size_bytes,
        "sha256": transfer.sha256,
        "chunk_size": transfer.chunk_size,
        "total_chunks": transfer.total_chunks,
        "status": enum_value(transfer.status),
    }


@router.put("/devices/transfers/{transfer_id}/chunks/{chunk_no}", response_model=dict)
async def upload_chunk(
    transfer_id: str,
    chunk_no: int,
    request: Request,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    chunk_sha256: str | None = Header(default=None, alias="X-Chunk-SHA256"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    transfer = db.get(Transfer, transfer_id)
    if not transfer or transfer.status not in {"queued", "transferring"} or transfer.source_device_id != device.id:
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "传输任务不可用", "任务不存在、已停止或不属于本设备。")
    if aware_utc(transfer.expires_at) <= utcnow():
        transfer.status = "expired"
        db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
        transfer.completed_chunks = 0
        transfer.transit_path = ""
        db.commit()
        await to_thread(cleanup_transfer_part, transfer.id)
        raise ProblemException(410, "TRANSFER_EXPIRED", "传输任务已过期", "请在主机重新发起传输。")
    if not transfer_sources_still_allowed(db, transfer):
        transfer.status = "paused"
        transfer.error_code = "GRANT_DENIED"
        transfer.error_message = "传输期间权限已被撤销。"
        db.commit()
        raise ProblemException(403, "GRANT_DENIED", "传输权限已撤销", "主机已在当前分块停止传输。")
    if chunk_no < 0 or (transfer.total_chunks and chunk_no >= transfer.total_chunks):
        raise ProblemException(422, "CHUNK_INVALID", "分块编号无效", "请重新获取传输任务。")
    body = await request.body()
    if len(body) > CHUNK_SIZE:
        raise ProblemException(413, "CHUNK_TOO_LARGE", "分块过大", "单个分块不得超过8MB。")
    expected_length = min(
        transfer.chunk_size,
        max(0, transfer.size_bytes - chunk_no * transfer.chunk_size),
    )
    if transfer.size_bytes and len(body) != expected_length:
        raise ProblemException(422, "CHUNK_SIZE_MISMATCH", "分块长度不正确", "请重新上传该分块。")
    digest = hashlib.sha256(body).hexdigest()
    if chunk_sha256 and digest != chunk_sha256.lower():
        raise ProblemException(422, "HASH_MISMATCH", "分块校验失败", "请重新上传该分块。")
    settings = get_settings()
    ensure_transfer_storage_available(len(body))
    part = settings.transfers_dir / f"{transfer.id}.part"
    await to_thread(
        write_transfer_chunk,
        part,
        chunk_no * transfer.chunk_size,
        body,
    )
    # SQLite 会串行化写入，但两个请求仍可能各自持有过期 ORM 计数。
    # 以唯一分块行作为事实来源，并在进程写锁内重新计数，避免 += 1 丢写。
    with db_runtime.write_lock:
        chunk = db.scalar(
            select(TransferChunk).where(
                TransferChunk.transfer_id == transfer.id,
                TransferChunk.chunk_no == chunk_no,
            )
        )
        if not chunk:
            chunk = TransferChunk(transfer_id=transfer.id, chunk_no=chunk_no)
            db.add(chunk)
        chunk.sha256 = digest
        chunk.size_bytes = len(body)
        chunk.received_at = utcnow()
        db.flush()
        transfer.completed_chunks = int(
            db.scalar(
                select(func.count(TransferChunk.id)).where(
                    TransferChunk.transfer_id == transfer.id
                )
            )
            or 0
        )
        transfer.status = "transferring"
        transfer.transit_path = str(part.name)
    cleanup_part_after_commit = False
    if transfer.total_chunks and transfer.completed_chunks >= transfer.total_chunks:
        part_size = await to_thread(lambda: part.stat().st_size)
        if part_size != transfer.size_bytes:
            transfer.status = "failed"
            transfer.error_code = "SOURCE_CHANGED"
            transfer.error_message = "源文件大小在传输期间发生变化。"
            db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
            transfer.completed_chunks = 0
            transfer.transit_path = ""
            db.commit()
            await to_thread(cleanup_transfer_part, transfer.id)
            return {
                "transfer_id": transfer.id,
                "chunk_no": chunk_no,
                "sha256": digest,
                "status": transfer.status,
                "completed_chunks": transfer.completed_chunks,
            }
        total_hash = await to_thread(sha256_path, part)
        if transfer.sha256 and total_hash != transfer.sha256:
            transfer.status = "failed"
            transfer.error_code = "HASH_MISMATCH"
            transfer.error_message = "文件整体校验失败。"
            db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
            transfer.completed_chunks = 0
            transfer.transit_path = ""
            cleanup_part_after_commit = True
        else:
            transfer.sha256 = total_hash
            transfer.result_sha256 = total_hash
            # 末块只证明主机收到了完整字节。Agent 还要在本机再次核对
            # inode/修改时间/大小，确认传输期间源文件没有变化，随后显式
            # 调用 finalize。此处提前完成会删除 .part，让协议规定的
            # finalize 必然 409，也可能过早向目标设备交付不稳定快照。
            transfer.status = "transferring"
        transfer.version += 1
    db.commit()
    if cleanup_part_after_commit:
        await to_thread(cleanup_transfer_part, transfer.id)
    return {"transfer_id": transfer.id, "chunk_no": chunk_no, "sha256": digest, "status": transfer.status, "completed_chunks": transfer.completed_chunks}


@router.get("/devices/transfers/{transfer_id}/status", response_model=dict)
def device_transfer_status(
    transfer_id: str,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    transfer = db.get(Transfer, transfer_id)
    if not transfer or device.id not in {
        transfer.source_device_id,
        transfer.destination_device_id,
    }:
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "传输任务不可用", "任务不存在或不属于本设备。")
    chunks = list(
        db.scalars(
            select(TransferChunk.chunk_no)
            .where(TransferChunk.transfer_id == transfer.id)
            .order_by(TransferChunk.chunk_no)
        ).all()
    )
    return {
        "id": transfer.id,
        "status": enum_value(transfer.status),
        "name": transfer.original_name,
        "size_bytes": transfer.size_bytes,
        "sha256": transfer.sha256,
        "chunk_size": transfer.chunk_size,
        "total_chunks": transfer.total_chunks,
        "completed_chunks": chunks,
        "expires_at": serialize_api_datetime(transfer.expires_at),
        "error_code": transfer.error_code,
    }


@router.post("/devices/transfers/{transfer_id}/finalize", response_model=dict)
def finalize_device_upload(
    transfer_id: str,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict:
    device = authenticated_device(token, db)
    transfer = db.get(Transfer, transfer_id)
    if not transfer or transfer.source_device_id != device.id:
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "传输任务不可用", "任务不存在或不属于本设备。")
    if not transfer_sources_still_allowed(db, transfer):
        raise ProblemException(403, "GRANT_DENIED", "传输权限已撤销", "主机已停止传输。")
    settings = get_settings()
    part = settings.transfers_dir / f"{transfer.id}.part"
    if transfer.size_bytes == 0 and not part.exists():
        part.touch(mode=0o600)
    transfer.transit_path = part.name
    if (
        not part.exists()
        or part.stat().st_size != transfer.size_bytes
        or transfer.completed_chunks < transfer.total_chunks
    ):
        raise ProblemException(409, "TRANSFER_INCOMPLETE", "文件尚未上传完成", "Agent 将继续断点续传。")
    total_hash = sha256_path(part)
    if transfer.sha256 and transfer.sha256 != total_hash:
        transfer.status = "failed"
        transfer.error_code = "HASH_MISMATCH"
        transfer.error_message = "文件整体校验失败。"
        db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
        transfer.completed_chunks = 0
        transfer.transit_path = ""
        db.commit()
        cleanup_transfer_part(transfer.id)
        raise ProblemException(422, "HASH_MISMATCH", "文件整体校验失败", "请重新发起传输。")
    transfer.sha256 = total_hash
    transfer.result_sha256 = total_hash
    if enum_value(transfer.direction) == "device_to_host":
        target = settings.inbox_dir / f"{transfer.id}-{safe_name(transfer.original_name)}"
        temporary = target.with_suffix(target.suffix + ".incoming")
        shutil.copyfile(part, temporary)
        os.replace(temporary, target)
        transfer.status = "completed"
    else:
        transfer.status = "transferring"
        queue_transfer_commands(db, transfer)
    transfer.version += 1
    db.commit()
    if enum_value(transfer.direction) == "device_to_host":
        cleanup_transfer_part(transfer.id)
    return {
        "transfer_id": transfer.id,
        "status": enum_value(transfer.status),
        "sha256": transfer.sha256,
    }


@router.get("/devices/transfers/{transfer_id}/chunks/{chunk_no}")
def download_chunk(
    transfer_id: str,
    chunk_no: int,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> FileResponse:
    device = authenticated_device(token, db)
    transfer = db.get(Transfer, transfer_id)
    if not transfer or transfer.destination_device_id != device.id or transfer.status not in {"queued", "transferring", "completed"}:
        raise ProblemException(404, "TRANSFER_NOT_FOUND", "传输任务不可用", "任务不存在或不属于本设备。")
    if aware_utc(transfer.expires_at) <= utcnow():
        transfer.status = "expired"
        db.execute(delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id))
        transfer.completed_chunks = 0
        transfer.transit_path = ""
        db.commit()
        cleanup_transfer_part(transfer.id)
        raise ProblemException(410, "TRANSFER_EXPIRED", "传输任务已过期", "请在主机重新发起传输。")
    if not transfer_permission_still_valid(
        db,
        transfer,
        "upload",
        transfer.destination_device_id,
        transfer.destination_root_id,
    ):
        transfer.status = "paused"
        transfer.error_code = "GRANT_DENIED"
        transfer.error_message = "传输期间目标设备权限已被撤销。"
        db.commit()
        raise ProblemException(403, "GRANT_DENIED", "传输权限已撤销", "主机已在当前分块停止传输。")
    part = get_settings().transfers_dir / f"{transfer.id}.part"
    if not part.exists():
        raise ProblemException(404, "CHUNK_NOT_READY", "文件分块尚未准备好", "请稍后重试。")
    offset = chunk_no * transfer.chunk_size
    if offset >= part.stat().st_size:
        raise ProblemException(404, "CHUNK_NOT_READY", "文件分块尚未准备好", "请稍后重试。")
    length = min(transfer.chunk_size, part.stat().st_size - offset)
    with part.open("rb") as handle:
        handle.seek(offset)
        content = handle.read(length)
    from fastapi.responses import Response as FastResponse

    return FastResponse(content, media_type="application/octet-stream", headers={"X-Chunk-SHA256": hashlib.sha256(content).hexdigest()})
