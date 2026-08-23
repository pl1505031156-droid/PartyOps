"""用户、审计、备份、恢复和终端配对。"""

from __future__ import annotations

import typing

import hashlib
import csv
import io
import ipaddress
import json
import os
import secrets
import shutil
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..backups import (
    SCHEMA_VERSION,
    create_backup,
    current_schema_version,
    restore_backup,
    sha256_file,
    verify_backup,
)
from ..config import get_settings, write_network_override
from ..database import db_runtime, get_session
from ..models import (
    AttachmentVersion,
    ArchiveAttachment,
    ArchiveCategory,
    ArchiveRecord,
    AIProviderConfig,
    AuditLog,
    BackgroundJob,
    BackupRun,
    ClientPairing,
    Device,
    DeviceGrant,
    FileBlob,
    LoginSession,
    Notification,
    ProjectionCheckpoint,
    ReminderPreference,
    SystemSetting,
    Task,
    TaskParticipant,
    TaskStep,
    UpgradeRecord,
    User,
    WorkspaceFile,
    WorkspaceRoot,
    utcnow,
)
from ..networking import (
    discover_lan_addresses,
    service_url,
    validate_bind_host,
    validate_transport_security,
)
from ..local_ai import local_runtime_status
from ..notifications import desktop_notifications_allowed
from ..pairings import authenticate_backup_pairing, pairing_expires_at
from ..problems import ProblemException
from ..schemas import (
    AuditOut,
    BackupOut,
    PairingCreate,
    PairingOut,
    PairingSummaryOut,
    PasswordReset,
    UserCreate,
    UserOut,
    UserPatch,
    BackgroundJobOut,
    serialize_api_datetime,
)
from ..security import (
    get_current_user_optional,
    hash_password,
    hash_token,
    require_admin,
)
from ..spreadsheet_security import safe_spreadsheet_row
from .events import active_stream_count


router = APIRouter(tags=["admin"])


async def _write_bounded_upload(file: UploadFile, path: Path, max_bytes: int) -> int:
    """以异步分块方式保存管理端上传，避免阻塞事件循环并限制磁盘占用。"""

    written = 0
    with path.open("xb") as handle:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                raise ProblemException(
                    413,
                    "BACKUP_UPLOAD_TOO_LARGE",
                    "备份包过大",
                    "上传超过管理员配置的备份导入上限。",
                )
            handle.write(chunk)
    return written
PROCESS_STARTED_AT = datetime.now(timezone.utc)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _role_reconfigure_marker_path() -> Path:
    """返回桌面启动器和当前服务共同可见的短期角色重配请求。"""

    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "PartyOps" / "Config"
    elif os.name == "nt":
        root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "partyops"
    return root / "reconfigure-request.json"


def _request_from_host_desktop(request: Request) -> bool:
    """只允许主机本机页面请求重配，局域网协同机不能远程改变主机角色。"""

    raw = client_ip(request).strip()
    if raw == "testclient" and get_settings().environment == "test":
        return True
    try:
        address = ipaddress.ip_address(raw)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_loopback:
            return True
        return str(address) in set(discover_lan_addresses())
    except ValueError:
        return False


@router.get("/admin/users", response_model=typing.List[UserOut])
def list_users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)).all())


@router.post("/admin/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> User:
    if db.scalar(select(User).where(User.username == payload.username.lower())):
        raise ProblemException(409, "USERNAME_EXISTS", "用户名已存在", "请使用其他用户名。")
    user = User(
        username=payload.username.lower(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    write_audit(
        db,
        admin,
        "user.create",
        "user",
        user.id,
        {"username": user.username, "role": user.role.value},
        client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return user


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改用户必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


@router.patch("/admin/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> User:
    target = db.get(User, user_id)
    if not target:
        raise ProblemException(404, "USER_NOT_FOUND", "用户不存在", "未找到该用户。")
    if target.version != parse_if_match(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "用户信息已更新", "请刷新后重试。")
    if target.id == admin.id and payload.active is False:
        raise ProblemException(409, "SELF_DISABLE_DENIED", "不能停用自己", "请由另一位管理员操作。")
    removes_admin = target.role.value == "admin" and (
        payload.active is False
        or (payload.role is not None and payload.role.value != "admin")
    )
    if removes_admin:
        remaining_admins = db.scalar(
            select(func.count()).select_from(User).where(
                User.role == "admin",
                User.active.is_(True),
                User.id != target.id,
            )
        ) or 0
        if remaining_admins == 0:
            raise ProblemException(409, "LAST_ADMIN_DENIED", "不能移除最后一名管理员", "请先创建或提升另一名管理员。")
    for field in payload.model_fields_set:
        setattr(target, field, getattr(payload, field))
    target.version += 1
    if payload.active is False or "role" in payload.model_fields_set:
        for session in db.scalars(
            select(LoginSession).where(
                LoginSession.user_id == target.id,
                LoginSession.revoked_at.is_(None),
            )
        ).all():
            session.revoked_at = utcnow()
    write_audit(
        db,
        admin,
        "user.update",
        "user",
        target.id,
        {"fields": sorted(payload.model_fields_set), "version": target.version},
        client_ip(request),
    )
    db.commit()
    db.refresh(target)
    return target


def user_deletion_impact(db: Session, target: User) -> dict[str, typing.Any]:
    """列出需要移交的当前责任；历史审计和业务记录不会物理删除。"""

    counts = {
        "owned_tasks": db.scalar(select(func.count()).select_from(Task).where(Task.owner_id == target.id, Task.deleted_at.is_(None))) or 0,
        "review_tasks": db.scalar(select(func.count()).select_from(Task).where(Task.reviewer_id == target.id, Task.deleted_at.is_(None))) or 0,
        "assigned_steps": db.scalar(select(func.count()).select_from(TaskStep).where(TaskStep.assignee_id == target.id)) or 0,
        "participations": db.scalar(select(func.count()).select_from(TaskParticipant).where(TaskParticipant.user_id == target.id)) or 0,
        "device_grants": db.scalar(select(func.count()).select_from(DeviceGrant).where(DeviceGrant.user_id == target.id, DeviceGrant.active.is_(True))) or 0,
    }
    return {
        "user_id": target.id,
        "active": target.active,
        "counts": counts,
        "requires_transfer": any(counts[key] for key in ("owned_tasks", "review_tasks", "assigned_steps")),
        "history_preserved": True,
    }


@router.get("/users/{user_id}/deletion-impact", response_model=dict)
@router.get("/admin/users/{user_id}/deletion-impact", response_model=dict)
def get_user_deletion_impact(
    user_id: str,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    target = db.get(User, user_id)
    if not target:
        raise ProblemException(404, "USER_NOT_FOUND", "用户不存在", "未找到该用户。")
    return user_deletion_impact(db, target)


@router.delete("/users/{user_id}", response_model=dict)
@router.delete("/admin/users/{user_id}", response_model=dict)
def archive_user(
    user_id: str,
    request: Request,
    transfer_to: str | None = Query(default=None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, typing.Any]:
    target = db.get(User, user_id)
    if not target:
        raise ProblemException(404, "USER_NOT_FOUND", "用户不存在", "未找到该用户。")
    if target.id == admin.id:
        raise ProblemException(409, "SELF_DELETE_DENIED", "不能删除当前用户", "请由另一名管理员操作。")
    if target.archived_at is not None:
        return {"deleted": True, "user_id": target.id, "history_preserved": True}
    if target.role.value == "admin":
        remaining_admins = db.scalar(
            select(func.count()).select_from(User).where(
                User.role == "admin", User.active.is_(True), User.id != target.id
            )
        ) or 0
        if remaining_admins == 0:
            raise ProblemException(409, "LAST_ADMIN_DENIED", "不能删除最后一名管理员", "请先创建或提升另一名管理员。")
    impact = user_deletion_impact(db, target)
    receiver = db.get(User, transfer_to) if transfer_to else None
    if impact["requires_transfer"] and (not receiver or not receiver.active or receiver.id == target.id):
        raise ProblemException(
            409,
            "USER_TRANSFER_REQUIRED",
            "删除前必须移交责任",
            "请选择一名启用用户接收事项负责人、审核人与步骤负责人责任。",
            extra={"impact": impact},
        )
    if receiver:
        for task in db.scalars(select(Task).where(Task.owner_id == target.id)).all():
            task.owner_id = receiver.id
            task.version += 1
        for task in db.scalars(select(Task).where(Task.reviewer_id == target.id)).all():
            task.reviewer_id = receiver.id
            task.version += 1
        for step in db.scalars(select(TaskStep).where(TaskStep.assignee_id == target.id)).all():
            step.assignee_id = receiver.id
            step.version += 1
    now = utcnow()
    target.active = False
    target.archived_at = now
    target.archived_by = admin.id
    target.version += 1
    for session in db.scalars(select(LoginSession).where(LoginSession.user_id == target.id, LoginSession.revoked_at.is_(None))).all():
        session.revoked_at = now
    for grant in db.scalars(select(DeviceGrant).where(DeviceGrant.user_id == target.id, DeviceGrant.active.is_(True))).all():
        grant.active = False
        grant.version += 1
    write_audit(
        db,
        admin,
        "user.archive",
        "user",
        target.id,
        {"transfer_to": receiver.id if receiver else None, "impact": impact["counts"]},
        client_ip(request),
    )
    db.commit()
    return {"deleted": True, "user_id": target.id, "transferred_to": receiver.id if receiver else None, "history_preserved": True}


@router.post("/users/{user_id}/restore", response_model=UserOut)
@router.post("/admin/users/{user_id}/restore", response_model=UserOut)
def restore_user(
    user_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> User:
    target = db.get(User, user_id)
    if not target:
        raise ProblemException(404, "USER_NOT_FOUND", "用户不存在", "未找到该用户。")
    target.active = True
    target.archived_at = None
    target.archived_by = None
    target.version += 1
    write_audit(db, admin, "user.restore", "user", target.id, {}, client_ip(request))
    db.commit()
    db.refresh(target)
    return target


@router.post("/admin/users/{user_id}/reset-password", response_model=dict)
def reset_user_password(
    user_id: str,
    payload: PasswordReset,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise ProblemException(404, "USER_NOT_FOUND", "用户不存在", "未找到该用户。")
    target.password_hash = hash_password(payload.password)
    target.version += 1
    for session in db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == target.id,
            LoginSession.revoked_at.is_(None),
        )
    ).all():
        session.revoked_at = utcnow()
    write_audit(db, admin, "user.password_reset", "user", target.id, {}, client_ip(request))
    db.commit()
    return {"reset": True, "user_id": user_id}


@router.get("/admin/audit", response_model=typing.List[AuditOut])
def list_audit(
    action: str | None = None,
    actor_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AuditLog]:
    statement = select(AuditLog)
    if action:
        statement = statement.where(AuditLog.action.contains(action))
    if actor_id:
        statement = statement.where(AuditLog.actor_id == actor_id)
    return list(db.scalars(statement.order_by(AuditLog.id.desc()).limit(limit)).all())


@router.get("/admin/audit.csv")
def export_audit(
    limit: int = Query(default=1000, ge=1, le=10000),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["编号", "时间", "操作者", "操作", "对象类型", "对象编号", "来源地址"])
    for item in db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    ).all():
        writer.writerow(
            safe_spreadsheet_row([
                item.id,
                serialize_api_datetime(item.created_at),
                item.actor_id or "系统",
                item.action,
                item.entity_type,
                item.entity_id or "",
                item.ip_address,
            ])
        )
    payload = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="partyops-audit.csv"'},
    )


@router.get("/backups", response_model=typing.List[BackupOut])
def list_backups(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[BackupRun]:
    return list(db.scalars(select(BackupRun).order_by(BackupRun.created_at.desc())).all())


@router.post("/backups", response_model=BackupOut, status_code=201)
def post_backup(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> BackupRun:
    return create_backup(db, admin, "manual", client_ip(request))


@router.get("/backups/{backup_id}/download")
def download_backup(
    backup_id: str,
    pairing_token: str | None = Header(default=None, alias="X-PartyOps-Pairing"),
    device_token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    admin: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_session),
) -> FileResponse:
    if device_token:
        from .fleet import authenticated_device

        authenticated_device(device_token, db)
    elif pairing_token:
        pairing = authenticate_backup_pairing(db, pairing_token)
        pairing.last_pull_at = utcnow()
        db.commit()
    elif not admin or admin.role.value != "admin":
        raise ProblemException(
            401,
            "BACKUP_DOWNLOAD_FORBIDDEN",
            "无权下载备份",
            "请使用管理员账号或有效终端配对令牌。",
        )
    record = db.get(BackupRun, backup_id)
    if not record or record.status != "completed":
        raise ProblemException(404, "BACKUP_NOT_FOUND", "备份不存在", "未找到可下载备份。")
    path = get_settings().backups_dir / record.filename
    if not path.exists():
        raise ProblemException(410, "BACKUP_FILE_MISSING", "备份文件缺失", "请重新创建备份。")
    response = FileResponse(path, media_type="application/zip", filename=record.filename)
    response.headers["X-PartyOps-SHA256"] = record.sha256
    return response


@router.get("/backups/latest", response_model=None)
def latest_backup(
    pairing_token: str | None = Header(default=None, alias="X-PartyOps-Pairing"),
    device_token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_session),
) -> FileResponse | Response:
    pairing = None
    if device_token:
        from .fleet import authenticated_device

        authenticated_device(device_token, db)
    else:
        pairing = authenticate_backup_pairing(db, pairing_token)
    record = db.scalar(
        select(BackupRun)
        .where(BackupRun.status == "completed")
        .order_by(BackupRun.created_at.desc())
    )
    if not record:
        raise ProblemException(404, "BACKUP_NOT_FOUND", "暂无备份", "请先在主机创建备份。")
    if pairing:
        pairing.last_pull_at = utcnow()
    db.commit()
    if if_none_match and if_none_match.strip('"') == record.sha256:
        return Response(status_code=304, headers={"ETag": f'"{record.sha256}"'})
    path = get_settings().backups_dir / record.filename
    response = FileResponse(path, media_type="application/zip", filename=record.filename)
    response.headers["X-PartyOps-SHA256"] = record.sha256
    response.headers["X-PartyOps-Backup-Id"] = record.id
    response.headers["ETag"] = f'"{record.sha256}"'
    return response


@router.get("/notifications/paired-summary", response_model=dict)
def paired_notification_summary(
    pairing_token: str | None = Header(default=None, alias="X-PartyOps-Pairing"),
    device_token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """供终端伴随进程轮询，只返回数量与修订号，不返回任务标题或正文。"""

    if device_token:
        from .fleet import authenticated_device

        authenticated_device(device_token, db)
    else:
        authenticate_backup_pairing(db, pairing_token)
    eligible_user_ids = [
        user.id
        for user in db.scalars(select(User).where(User.active.is_(True))).all()
        if desktop_notifications_allowed(db.get(ReminderPreference, user.id))
    ]
    if not eligible_user_ids:
        return {"unread_count": 0, "revision": ""}
    unread_filter = (
        Notification.user_id.in_(eligible_user_ids),
        Notification.read_at.is_(None),
        Notification.revoked_at.is_(None),
    )
    count = db.scalar(
        select(func.count()).select_from(Notification).where(*unread_filter)
    ) or 0
    latest = db.scalar(select(func.max(Notification.created_at)).where(*unread_filter))
    return {
        "unread_count": count,
        "revision": serialize_api_datetime(latest) if latest else "",
    }


@router.get("/admin/pairings", response_model=typing.List[PairingSummaryOut])
def list_pairings(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[PairingSummaryOut]:
    pairings = list(
        db.scalars(select(ClientPairing).order_by(ClientPairing.created_at.desc())).all()
    )
    return [
        PairingSummaryOut(
            id=pairing.id,
            name=pairing.name,
            active=pairing.active,
            last_pull_at=pairing.last_pull_at,
            created_at=pairing.created_at,
            expires_at=pairing_expires_at(pairing),
        )
        for pairing in pairings
    ]


@router.post("/admin/pairings", response_model=PairingOut, status_code=201)
def create_pairing(
    payload: PairingCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> PairingOut:
    token = secrets.token_urlsafe(32)
    pairing = ClientPairing(name=payload.name, token_hash=hash_token(token))
    db.add(pairing)
    db.flush()
    write_audit(
        db,
        admin,
        "pairing.create",
        "pairing",
        pairing.id,
        {"name": pairing.name},
        client_ip(request),
    )
    db.commit()
    host_url = str(request.base_url).rstrip("/")
    expires_at = pairing_expires_at(pairing)
    return PairingOut(
        id=pairing.id,
        name=pairing.name,
        token=token,
        host_url=host_url,
        expires_at=expires_at,
        config={
            "host_url": host_url,
            "pairing_token": token,
            "pairing_expires_at": serialize_api_datetime(expires_at),
            "backup_dir": "~/PartyOps-灾备副本",
            "open_browser": True,
            "interval_seconds": 600,
        },
    )


@router.delete("/admin/pairings/{pairing_id}", response_model=dict)
def revoke_pairing(
    pairing_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    pairing = db.get(ClientPairing, pairing_id)
    if not pairing:
        raise ProblemException(404, "PAIRING_NOT_FOUND", "终端配对不存在", "未找到该终端。")
    pairing.active = False
    write_audit(db, admin, "pairing.revoke", "pairing", pairing.id, {"name": pairing.name}, client_ip(request))
    db.commit()
    return {"revoked": True, "id": pairing_id}


@router.post("/admin/backups/verify", response_model=dict)
async def verify_uploaded_backup(
    file: UploadFile = File(...),
    _admin: User = Depends(require_admin),
) -> dict:
    settings = get_settings()
    path = settings.backups_dir / f"verify-{secrets.token_hex(8)}.partyops-backup"
    try:
        await _write_bounded_upload(file, path, settings.backup_import_max_gb * 1024**3)
        manifest = verify_backup(path)
        return {"valid": True, "manifest": manifest}
    finally:
        path.unlink(missing_ok=True)


@router.post("/admin/backups/import", response_model=BackupOut, status_code=201)
async def import_backup(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> BackupRun:
    settings = get_settings()
    safe_name = Path(file.filename or "import.partyops-backup").name
    if not safe_name.endswith(".partyops-backup"):
        safe_name += ".partyops-backup"
    filename = f"PartyOps-imported-{secrets.token_hex(6)}-{safe_name}"[:255]
    path = settings.backups_dir / filename
    try:
        await _write_bounded_upload(file, path, settings.backup_import_max_gb * 1024**3)
        verify_backup(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    record = BackupRun(
        filename=filename,
        kind="imported",
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        status="completed",
        created_by=admin.id,
        completed_at=utcnow(),
    )
    db.add(record)
    db.flush()
    write_audit(db, admin, "backup.import", "backup", record.id, {"filename": filename}, client_ip(request))
    db.commit()
    db.refresh(record)
    return record


@router.post("/admin/backups/{backup_id}/verify", response_model=dict)
def verify_existing_backup(
    backup_id: str,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    record = db.get(BackupRun, backup_id)
    if not record or record.status != "completed":
        raise ProblemException(404, "BACKUP_NOT_FOUND", "备份不存在", "未找到可校验备份。")
    manifest = verify_backup(get_settings().backups_dir / record.filename)
    return {"valid": True, "manifest": manifest, "sha256": record.sha256}


@router.post("/admin/backups/restore", response_model=dict)
def restore_existing_backup(
    backup_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    record = db.get(BackupRun, backup_id)
    if not record or record.status != "completed":
        raise ProblemException(404, "BACKUP_NOT_FOUND", "备份不存在", "未找到可恢复备份。")
    path = get_settings().backups_dir / record.filename
    actor_id = admin.id
    filename = record.filename
    db.close()
    restore_backup(path, actor_id)
    with db_runtime.session_factory() as restored_db:
        restored_actor = restored_db.get(User, actor_id)
        write_audit(
            restored_db,
            restored_actor,
            "backup.restore",
            "backup",
            backup_id,
            {"filename": filename},
        )
        restored_db.commit()
    return {"restored": True, "filename": filename}


@router.get("/admin/diagnostics", response_model=dict)
def diagnostics(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    usage = shutil.disk_usage(settings.data_dir)
    latest = db.scalar(
        select(BackupRun)
        .where(BackupRun.status == "completed")
        .order_by(BackupRun.created_at.desc())
    )
    return {
        "mode": settings.mode,
        "bind": {"host": settings.host, "port": settings.port},
        "service_url": service_url(
            settings.host,
            settings.port,
            tls_enabled=settings.tls_enabled,
        ),
        "lan_candidates": discover_lan_addresses(),
        "disk": {"total_bytes": usage.total, "free_bytes": usage.free},
        "counts": {
            "users": db.scalar(select(func.count()).select_from(User)) or 0,
            "tasks": db.scalar(select(func.count()).select_from(Task)) or 0,
            "attachments": db.scalar(select(func.count()).select_from(AttachmentVersion)) or 0,
            "unique_files": db.scalar(select(func.count()).select_from(FileBlob)) or 0,
            "archive_records": db.scalar(select(func.count()).select_from(ArchiveRecord))
            or 0,
            "archive_attachments": db.scalar(
                select(func.count()).select_from(ArchiveAttachment)
            )
            or 0,
        },
        "latest_backup": {
            "id": latest.id,
            "created_at": serialize_api_datetime(latest.created_at),
            "status": latest.status,
        }
        if latest
        else None,
        "fault_tips": [
            "终端打不开：确认设备在同一局域网，并使用主机明确的局域网 IP、正确架构包和一次性入网码。",
            "实时状态中断：页面会自动切换为 10 秒轮询，不会产生第二份数据库。",
            "附件缺失：先停止写入，再校验并恢复最近完整备份。",
        ],
    }


def validate_network_payload(payload: dict[str, object]) -> dict[str, object]:
    settings = get_settings()
    bind_host = str(payload.get("bind_host", settings.network_bind_host)).strip()
    advertise_host = str(payload.get("advertise_host", settings.network_advertise_host)).strip()
    try:
        port = int(payload.get("port", settings.port))
    except (TypeError, ValueError) as exc:
        raise ProblemException(422, "NETWORK_PORT_INVALID", "端口无效", "端口必须是 1024 到 65535 的整数。") from exc
    if not bind_host or not advertise_host or "://" in bind_host or "://" in advertise_host:
        raise ProblemException(422, "NETWORK_HOST_INVALID", "主机地址无效", "请只填写 IP 或主机名，不要包含协议和路径。")
    if not 1024 <= port <= 65535:
        raise ProblemException(422, "NETWORK_PORT_INVALID", "端口无效", "端口必须在 1024 到 65535 之间。")
    try:
        validate_bind_host(
            bind_host,
            settings.environment == "production",
            advertised_host=advertise_host,
        )
        validate_transport_security(
            host=advertise_host,
            production=settings.environment == "production",
            tls_enabled=settings.tls_enabled,
        )
    except RuntimeError as exc:
        raise ProblemException(422, "NETWORK_POLICY_DENIED", "网络配置不符合安全边界", str(exc)) from exc
    return {
        "bind_host": bind_host,
        "advertise_host": advertise_host,
        "port": port,
    }


@router.get("/system/network", response_model=dict)
def get_network_configuration(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    settings = get_settings()
    pending = db.get(SystemSetting, "network.pending")
    return {
        "automatic_addresses": discover_lan_addresses(),
        "bind_host": settings.network_bind_host,
        "advertise_host": settings.network_advertise_host,
        "port": settings.port,
        "tls_enabled": settings.tls_enabled,
        "service_url": service_url(settings.network_advertise_host, settings.port, tls_enabled=settings.tls_enabled),
        "pending": pending.value if pending else None,
    }


@router.post("/system/reconfigure-request", response_model=dict)
def request_role_reconfiguration(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """为 macOS 等 URL 事件不透传参数的平台写入一次性短期启动意图。"""

    if not _request_from_host_desktop(request):
        raise ProblemException(
            403,
            "ROLE_RECONFIGURE_LOCAL_REQUIRED",
            "只能在这台电脑上重新配置运行角色",
            "请回到 PartyOps 所在电脑，从系统设置打开配置向导；协同机不能远程改变主机角色。",
        )
    marker = _role_reconfigure_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    requested_at = int(utcnow().timestamp())
    payload = {
        "format_version": 1,
        "requested_at": requested_at,
        "expires_at": requested_at + 120,
        "requested_by": admin.id,
    }
    temporary = marker.with_name(f".{marker.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(marker)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ProblemException(
            500,
            "ROLE_RECONFIGURE_MARKER_FAILED",
            "无法准备配置向导",
            "本机配置目录暂时不可写；请从桌面重新打开 PartyOps 后重试。",
        ) from exc
    write_audit(
        db,
        admin,
        "system.role_reconfigure_request",
        "system",
        None,
        {"expires_at": payload["expires_at"]},
        client_ip(request),
    )
    db.commit()
    return {
        "deep_link": "partyops-client://reconfigure",
        "expires_at": payload["expires_at"],
        "current_mode": get_settings().mode,
    }


@router.post("/system/network/validate", response_model=dict)
def validate_network_configuration(
    payload: dict[str, object],
    _admin: User = Depends(require_admin),
) -> dict[str, object]:
    value = validate_network_payload(payload)
    return {
        **value,
        "valid": True,
        "certificate_rotation_required": value["advertise_host"] != get_settings().network_advertise_host,
        "restart_required": value["bind_host"] != get_settings().network_bind_host or value["port"] != get_settings().port,
    }


@router.patch("/system/network", response_model=dict)
def patch_network_configuration(
    payload: dict[str, object],
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """保存、更新证书 SAN 并留下旧地址宽限/失败回滚所需快照。"""

    settings = get_settings()
    new_value = validate_network_payload(payload)
    old_value = {
        "bind_host": settings.network_bind_host,
        "advertise_host": settings.network_advertise_host,
        "port": settings.port,
    }
    if new_value == old_value:
        return {**new_value, "changed": False, "restart_required": False}
    try:
        write_network_override(new_value)
        if new_value["advertise_host"] != old_value["advertise_host"] and settings.tls_enabled:
            from ..pki import ensure_tls_material

            candidate = settings.model_copy(
                update={
                    "bind_host": new_value["bind_host"],
                    "advertise_host": new_value["advertise_host"],
                    "port": new_value["port"],
                }
            )
            ensure_tls_material(candidate)
    except Exception as exc:
        write_network_override(old_value)
        raise ProblemException(
            500,
            "NETWORK_UPDATE_ROLLED_BACK",
            "网络配置更新失败并已回滚",
            "原监听地址和证书配置已经保留，请查看服务日志。",
        ) from exc
    pending_value = {
        "previous": old_value,
        "requested": new_value,
        "requested_at": utcnow().isoformat(),
        "requested_by": admin.id,
        "migration_grace_hours": max(1, min(168, int(payload.get("migration_grace_hours", 24)))),
        "state": "restart_required",
    }
    pending = db.get(SystemSetting, "network.pending")
    if pending:
        pending.value = pending_value
    else:
        db.add(SystemSetting(key="network.pending", value=pending_value))
    write_audit(db, admin, "system.network_update", "system_setting", "network.pending", {"previous": old_value, "requested": new_value}, client_ip(request))
    db.commit()
    return {
        **new_value,
        "changed": True,
        "restart_required": True,
        "certificate_rotated": bool(settings.tls_enabled and new_value["advertise_host"] != old_value["advertise_host"]),
        "migration_grace_hours": pending_value["migration_grace_hours"],
        "rollback": old_value,
    }


@router.get("/admin/logs", response_class=PlainTextResponse)
def recent_logs(
    lines: int = Query(default=200, ge=1, le=2000),
    _admin: User = Depends(require_admin),
) -> str:
    path = get_settings().logs_dir / "partyops.log"
    if not path.exists():
        return "暂无运行日志。"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


@router.get("/admin/system-status", response_model=dict)
def system_status(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    """返回可展示的运行状态，不包含密钥和主机绝对路径。"""

    settings = get_settings()
    now = datetime.now(timezone.utc)
    latest_backup = db.scalar(
        select(BackupRun)
        .where(BackupRun.status == "completed")
        .order_by(BackupRun.created_at.desc())
    )
    latest_job = db.scalar(
        select(BackgroundJob).order_by(BackgroundJob.created_at.desc())
    )
    provider = db.scalar(
        select(AIProviderConfig).order_by(AIProviderConfig.created_at)
    )
    roots = db.scalars(select(WorkspaceRoot).order_by(WorkspaceRoot.name)).all()
    devices = db.scalars(select(Device).order_by(Device.name)).all()
    schema_revision = current_schema_version()
    projections = db.scalars(
        select(ProjectionCheckpoint).order_by(ProjectionCheckpoint.name)
    ).all()
    # SQLite 的 quick_check 在发现多个问题时会返回多行；不能使用
    # scalar_one_or_none，否则诊断接口本身会因 MultipleResultsFound 变成 500。
    # 对外只返回稳定状态，不泄露数据库内部诊断文本。
    quick_check_rows = db.execute(text("PRAGMA quick_check")).scalars().all()
    quick_check = (
        "ok"
        if quick_check_rows == ["ok"]
        else ("failed" if quick_check_rows else "unknown")
    )
    foreign_key_errors = len(db.execute(text("PRAGMA foreign_key_check")).fetchall())
    backup_time = (
        latest_backup.completed_at or latest_backup.created_at
        if latest_backup
        else None
    )
    if backup_time and backup_time.tzinfo is None:
        backup_time = backup_time.replace(tzinfo=timezone.utc)
    backup_age_hours = (
        round((now - backup_time).total_seconds() / 3600, 1)
        if backup_time
        else None
    )
    disk = shutil.disk_usage(settings.data_dir)
    readiness = {
        "database": quick_check == "ok",
        "foreign_keys": foreign_key_errors == 0,
        "schema": schema_revision == SCHEMA_VERSION,
        "data_directories": all(
            item.exists()
            for item in (
                settings.data_dir,
                settings.attachments_dir,
                settings.backups_dir,
                settings.updates_dir,
            )
        ),
        "backup_fresh": backup_age_hours is not None and backup_age_hours <= 36,
        "storage_headroom": disk.free >= settings.minimum_free_gb * 1024**3,
    }
    ready = all(value for key, value in readiness.items() if key != "backup_fresh")
    try:
        load_average = list(os.getloadavg())
    except (AttributeError, OSError):
        load_average = []
    return {
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "readiness": readiness,
        "mode": settings.mode,
        "app_version": settings.app_version,
        "schema_revision": schema_revision,
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "system": platform.system(),
        "kernel": platform.release(),
        "platform": platform.platform(),
        "uptime_seconds": int((now - PROCESS_STARTED_AT).total_seconds()),
        "service": {
            "host": settings.host,
            "port": settings.port,
            "agent_port": settings.agent_port,
            "url": service_url(
                settings.host,
                settings.port,
                tls_enabled=settings.tls_enabled,
            ),
            "sse_clients": active_stream_count(),
            "tls_enabled": settings.tls_enabled,
        },
        "storage": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "minimum_free_bytes": settings.minimum_free_gb * 1024**3,
            "database_bytes": settings.database_path.stat().st_size
            if settings.database_path.exists()
            else 0,
            "attachments_bytes": _directory_size(settings.attachments_dir),
            "backups_bytes": _directory_size(settings.backups_dir),
            "indexed_files": db.scalar(select(func.count()).select_from(WorkspaceFile))
            or 0,
            "updates_bytes": _directory_size(settings.updates_dir),
            "transfers_bytes": _directory_size(settings.transfers_dir),
            "archive_records": db.scalar(
                select(func.count()).select_from(ArchiveRecord)
            )
            or 0,
            "archive_attachments": db.scalar(
                select(func.count()).select_from(ArchiveAttachment)
            )
            or 0,
            "archive_categories": db.scalar(
                select(func.count()).select_from(ArchiveCategory)
            )
            or 0,
        },
        "devices": {
            "max": settings.max_devices,
            "total": len(devices),
            "online": sum(1 for device in devices if device.status.value == "online"),
            "items": [
                {
                    "id": device.id,
                    "name": device.name,
                    "status": device.status.value,
                    "last_seen_at": serialize_api_datetime(device.last_seen_at)
                    if device.last_seen_at
                    else None,
                }
                for device in devices
            ],
        },
        "workspace_roots": [
            {
                "id": root.id,
                "name": root.name,
                "enabled": root.enabled,
                "scan_status": root.scan_status,
                "file_count": root.file_count,
                "last_scan_at": serialize_api_datetime(root.last_scan_at)
                if root.last_scan_at
                else None,
            }
            for root in roots
        ],
        "latest_job": {
            "id": latest_job.id,
            "type": latest_job.job_type,
            "status": latest_job.status,
            "progress": latest_job.progress,
            "message": latest_job.message,
        }
        if latest_job
        else None,
        "backup": {
            "last_at": serialize_api_datetime(latest_backup.created_at)
            if latest_backup
            else None,
            "last_status": latest_backup.status if latest_backup else "never",
            "next_schedule": f"{settings.backup_hour:02d}:{settings.backup_minute:02d}",
            "age_hours": backup_age_hours,
        },
        "database": {
            "quick_check": quick_check or "unknown",
            "foreign_key_errors": foreign_key_errors,
            "migration_head": SCHEMA_VERSION,
        },
        "projections": [
            {
                "name": item.name,
                "status": item.status,
                "last_event_id": item.last_event_id,
                "processed_count": item.processed_count,
                "failed_count": item.failed_count,
                "last_error": item.last_error,
                "last_run_at": serialize_api_datetime(item.last_run_at)
                if item.last_run_at
                else None,
            }
            for item in projections
        ],
        "ai": {
            "configured": bool(provider and provider.base_url),
            "enabled": bool(provider and provider.enabled),
            "trusted_intranet": bool(provider and provider.trusted_intranet),
            "last_status": provider.last_status if provider else "not_configured",
            "last_test_at": serialize_api_datetime(provider.last_test_at)
            if provider and provider.last_test_at
            else None,
            "local": local_runtime_status(db),
        },
        "load_average": load_average,
        "executable_frozen": bool(getattr(sys, "frozen", False)),
    }


@router.get("/admin/jobs", response_model=typing.List[BackgroundJobOut])
def list_background_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[BackgroundJob]:
    return list(
        db.scalars(
            select(BackgroundJob)
            .order_by(BackgroundJob.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.get("/admin/upgrades", response_model=typing.List[dict])
def list_upgrades(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[dict]:
    records = db.scalars(
        select(UpgradeRecord).order_by(UpgradeRecord.created_at.desc()).limit(100)
    ).all()
    return [
        {
            "id": item.id,
            "from_version": item.from_version,
            "to_version": item.to_version,
            "schema_revision": item.schema_revision,
            "status": item.status,
            "backup_filename": item.backup_filename,
            "message": item.message,
            "created_at": serialize_api_datetime(item.created_at),
            "completed_at": serialize_api_datetime(item.completed_at)
            if item.completed_at
            else None,
        }
        for item in records
    ]
