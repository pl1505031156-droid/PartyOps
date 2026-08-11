"""用户、审计、备份、恢复和终端配对。"""

from __future__ import annotations

import hashlib
import csv
import io
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
from ..config import get_settings
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
    FileBlob,
    LoginSession,
    Notification,
    ProjectionCheckpoint,
    ReminderPreference,
    Task,
    UpgradeRecord,
    User,
    WorkspaceFile,
    WorkspaceRoot,
    utcnow,
)
from ..networking import discover_lan_addresses, service_url
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


@router.get("/admin/users", response_model=list[UserOut])
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
    for field in payload.model_fields_set:
        setattr(target, field, getattr(payload, field))
    target.version += 1
    if payload.active is False:
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


@router.get("/admin/audit", response_model=list[AuditOut])
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


@router.get("/backups", response_model=list[BackupOut])
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
    )
    count = db.scalar(
        select(func.count()).select_from(Notification).where(*unread_filter)
    ) or 0
    latest = db.scalar(select(func.max(Notification.created_at)).where(*unread_filter))
    return {
        "unread_count": count,
        "revision": serialize_api_datetime(latest) if latest else "",
    }


@router.get("/admin/pairings", response_model=list[PairingSummaryOut])
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
        "service_url": service_url(settings.host, settings.port),
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
            "url": service_url(settings.host, settings.port),
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


@router.get("/admin/jobs", response_model=list[BackgroundJobOut])
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


@router.get("/admin/upgrades", response_model=list[dict])
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
