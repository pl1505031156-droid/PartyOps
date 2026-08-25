"""设备版本门禁、浏览器设备上下文与发布历史。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .backups import SCHEMA_VERSION
from .config import get_settings
from .enums import UpdateStatus
from .models import (
    Device,
    DeviceCommand,
    ReleaseHistory,
    SystemSetting,
    UpdatePackage,
    UpdateRun,
    User,
    utcnow,
)
from .problems import ProblemException

DEVICE_CONTEXT_COOKIE = "partyops_device_context"
DEVICE_CONTEXT_SECRET_KEY = "device_context_hmac_secret"
CURRENT_RELEASE_TITLE = "1.4.5-rc.3 台账导入与真实进度升级"
CURRENT_RELEASE_NOTES = [
    "协同凭据升级到协议 v2；旧令牌被拒绝时停止无限重试并进入可恢复的重新授权状态",
    "页面启动、心跳和灾备同步相互解耦，单项失败不再拖死 UOS、麒麟 ARM64 桌面入口",
    "文件打开授权改为持久化哈希令牌和数据库原子一次性兑换，PDF 同源预览并明确分流 WPS",
    "用户删除采用停用、责任移交与归档流程，禁止删除当前用户和最后一名管理员",
    "新增党委会等会议六步筹备、负责人进度、周期实例、年度议题与资金统计",
    "结构化议程、通知和纪要支持在线编辑、自动保存、修订历史、并发校验和 DOCX 导出",
    "发展党员档案分开保存实际日期、法定边界、参考计划和人工调整，并生成分阶段提醒",
    "任务日期或参与人修改后立即更新或撤销未读通知，避免新旧时间通知并存",
    "数据库增量升级到 0020，升级前快照与失败回滚保留原配置、证书、绑定和业务数据",
    "Windows、Linux 与 macOS 启动链输出机器可读状态；macOS 原生探针在 Python 前写启动日志",
]


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def ensure_device_context_secret(db: Session) -> str:
    setting = db.get(SystemSetting, DEVICE_CONTEXT_SECRET_KEY)
    if setting and isinstance(setting.value, str) and len(setting.value) >= 64:
        return setting.value
    value = secrets.token_hex(32)
    if setting:
        setting.value = value
    else:
        db.add(SystemSetting(key=DEVICE_CONTEXT_SECRET_KEY, value=value))
    db.flush()
    return value


def issue_device_context_token(
    db: Session,
    device: Device,
    *,
    lifetime: timedelta = timedelta(days=30),
    purpose: str = "context",
) -> tuple[str, datetime]:
    if purpose not in {"context", "launch"}:
        raise ValueError("设备上下文令牌用途无效")
    expires_at = utcnow() + lifetime
    payload = {
        "device_id": device.id,
        "expires": int(expires_at.timestamp()),
        "nonce": secrets.token_urlsafe(12),
        "purpose": purpose,
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    secret = ensure_device_context_secret(db).encode("ascii")
    signature = _b64encode(hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}", expires_at


def verify_device_context_token(
    db: Session,
    token: str,
    *,
    purpose: str = "context",
) -> Device | None:
    try:
        encoded, signature = token.split(".", 1)
        setting = db.get(SystemSetting, DEVICE_CONTEXT_SECRET_KEY)
        if not setting or not isinstance(setting.value, str):
            return None
        expected = _b64encode(
            hmac.new(
                setting.value.encode("ascii"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        if int(payload.get("expires", 0)) <= int(utcnow().timestamp()):
            return None
        if not hmac.compare_digest(str(payload.get("purpose", "")), purpose):
            return None
        device = db.get(Device, str(payload.get("device_id", "")))
        return device if device and device.active else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def request_device(
    request: Request,
    db: Session,
    *,
    allow_ip_fallback: bool = False,
) -> Device | None:
    """解析签名设备上下文；IP 回退仅供只读升级引导兼容旧客户端。"""

    token = request.cookies.get(DEVICE_CONTEXT_COOKIE, "")
    if token:
        device = verify_device_context_token(db, token)
        if device:
            return device
    if not allow_ip_fallback:
        return None
    ip_address = request.client.host if request.client else ""
    if not ip_address or ip_address in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return None
    matches = list(
        db.scalars(
            select(Device).where(
                Device.ip_address == ip_address,
                Device.active.is_(True),
            )
        ).all()
    )
    return matches[0] if len(matches) == 1 else None


def release_notes_from_manifest(manifest: object) -> list[str]:
    if not isinstance(manifest, dict):
        return []
    values = manifest.get("release_notes", [])
    if not isinstance(values, list):
        return []
    return [str(value).strip()[:500] for value in values if str(value).strip()][:50]


def ensure_current_release(db: Session) -> ReleaseHistory:
    settings = get_settings()
    existing = db.scalar(
        select(ReleaseHistory).where(ReleaseHistory.version == settings.app_version)
    )
    if existing:
        # 1.1.3 早期安装没有稳定的应用内更新桥接，本次同版本原位修复
        # 通过标题变化只更新一次安装时间和内容；后续正式版本仍按版本追加。
        if (
            existing.package_id is None
            and existing.title != CURRENT_RELEASE_TITLE
        ):
            existing.title = CURRENT_RELEASE_TITLE
            existing.release_notes = CURRENT_RELEASE_NOTES
            existing.schema_revision = SCHEMA_VERSION
            existing.installed_at = utcnow()
            db.flush()
        return existing
    package = db.scalar(
        select(UpdatePackage)
        .where(UpdatePackage.version == settings.app_version)
        .order_by(UpdatePackage.created_at.desc())
    )
    notes = release_notes_from_manifest(package.manifest) if package else []
    history = ReleaseHistory(
        version=settings.app_version,
        schema_revision=SCHEMA_VERSION,
        title=(
            str(package.manifest.get("release_title", "")).strip()[:160]
            if package and isinstance(package.manifest, dict)
            else CURRENT_RELEASE_TITLE
        )
        or CURRENT_RELEASE_TITLE,
        release_notes=notes or CURRENT_RELEASE_NOTES,
        package_id=package.id if package else None,
        status="installed",
        installed_at=utcnow(),
    )
    db.add(history)
    db.flush()
    return history


def latest_target_package(db: Session) -> UpdatePackage | None:
    settings = get_settings()
    return db.scalar(
        select(UpdatePackage)
        .where(
            UpdatePackage.version == settings.app_version,
            UpdatePackage.status == UpdateStatus.COMPLETED,
        )
        .order_by(UpdatePackage.created_at.desc())
    )


def device_version_state(device: Device) -> str:
    target = get_settings().app_version
    status = getattr(device.status, "value", device.status)
    if not device.active or status == "revoked":
        return "revoked"
    if status == "quarantined":
        return "quarantined"
    if device.app_version == target and device.agent_version == target:
        return "current"
    if status == "updating":
        return "updating"
    if not device.app_version:
        return "unknown"
    return "outdated"


def latest_device_run(db: Session, device_id: str) -> tuple[UpdateRun | None, UpdatePackage | None]:
    run = db.scalar(
        select(UpdateRun)
        .where(UpdateRun.target_device_id == device_id)
        .order_by(UpdateRun.created_at.desc())
    )
    return (run, db.get(UpdatePackage, run.package_id) if run else None)


def build_device_gate(db: Session, device: Device | None) -> dict[str, object]:
    settings = get_settings()
    history = ensure_current_release(db)
    if not device:
        return {
            "identified": False,
            "target_version": settings.app_version,
            "required": False,
            "access_allowed": True,
            "state": "host_or_unknown",
            "release_title": history.title,
            "release_notes": history.release_notes,
            "installed_at": history.installed_at,
        }
    state = device_version_state(device)
    run, package = latest_device_run(db, device.id)
    target_package = latest_target_package(db)
    available_package = package or target_package
    required = state != "current"
    access_allowed = not required and state not in {"revoked", "quarantined"}
    message = ""
    if state == "revoked":
        message = "该协同电脑已被主机管理员撤销。"
    elif state == "quarantined":
        message = "该协同电脑已被主机管理员隔离。"
    elif required and target_package is None:
        message = "主机尚未保留当前版本的终端更新包，请联系管理员重新导入。"
    elif run:
        message = run.message
    else:
        message = "检测到主机版本较新，请开始更新本机。"
    return {
        "identified": True,
        "device_id": device.id,
        "device_name": device.name,
        "current_version": device.app_version or "未上报",
        "target_version": settings.app_version,
        "required": required,
        "access_allowed": access_allowed,
        "state": state,
        "status": getattr(run.status, "value", run.status) if run else "",
        "message": message,
        "package_id": available_package.id if available_package else None,
        "run_id": run.id if run else None,
        "release_title": history.title,
        "release_notes": history.release_notes,
        "installed_at": history.installed_at,
    }


def start_device_update(db: Session, device: Device, actor: User | None = None) -> UpdateRun:
    if device_version_state(device) == "current":
        run, _package = latest_device_run(db, device.id)
        if run:
            return run
        raise ProblemException(
            409,
            "DEVICE_ALREADY_CURRENT",
            "协同电脑已经是最新版本",
            "无需重复执行更新。",
        )
    if not device.active or getattr(device.status, "value", device.status) in {
        "revoked",
        "quarantined",
    }:
        raise ProblemException(
            403,
            "DEVICE_UPDATE_DENIED",
            "协同电脑当前不可更新",
            "请联系主机管理员解除撤销或隔离状态。",
        )
    package = latest_target_package(db)
    if not package:
        raise ProblemException(
            409,
            "DEVICE_UPDATE_PACKAGE_UNAVAILABLE",
            "当前版本更新包不可用",
            "请联系主机管理员重新导入与主机版本一致的更新包。",
        )
    run = db.scalar(
        select(UpdateRun).where(
            UpdateRun.package_id == package.id,
            UpdateRun.target_device_id == device.id,
        )
    )
    if not run:
        run = UpdateRun(
            package_id=package.id,
            target_device_id=device.id,
            status=UpdateStatus.UPLOADED,
            message="等待协同电脑确认更新",
            created_by=actor.id if actor else package.created_by,
        )
        db.add(run)
        db.flush()
    command = db.scalar(
        select(DeviceCommand).where(
            DeviceCommand.idempotency_key == f"update:{package.id}:{device.id}"
        )
    )
    if not command:
        package_manifest = getattr(package, "manifest", {})
        online_state = (
            package_manifest.get("online_download", package_manifest)
            if isinstance(package_manifest, dict)
            else {}
        )
        command = DeviceCommand(
            device_id=device.id,
            command_type="apply_update",
            idempotency_key=f"update:{package.id}:{device.id}",
            payload={
                "package": package.filename,
                "version": package.version,
                "run_id": run.id,
                # 协同机只接收“使用官方签名目录”这一布尔意图；下载地址、
                # 大小和哈希由协同机用内置公钥重新读取并验证，主机不能注入 URL。
                "official_online": online_state.get("source") == "official-online-catalog",
            },
        )
        db.add(command)
    elif command.status in {"failed", "completed"} and device.app_version != package.version:
        command.status = "queued"
        command.result = {}
        command.completed_at = None
        command.delivered_at = None
    run.status = UpdateStatus.APPLYING
    run.progress = max(run.progress, 5)
    run.message = "已确认更新，等待本机 Agent 下载并安装"
    device.status = "updating"
    return run


def reconcile_device_update(db: Session, device: Device) -> None:
    """以新版 Agent 心跳为最终事实，修复安装重启导致确认回执丢失。"""

    if not device.app_version:
        return
    runs = list(
        db.scalars(
            select(UpdateRun).where(
                UpdateRun.target_device_id == device.id,
                UpdateRun.status.in_([UpdateStatus.UPLOADED, UpdateStatus.APPLYING]),
            )
        ).all()
    )
    for run in runs:
        package = db.get(UpdatePackage, run.package_id)
        if not package or package.version != device.app_version:
            continue
        run.status = UpdateStatus.COMPLETED
        run.progress = 100
        run.message = "设备已上报新版本，升级完成"
        run.completed_at = utcnow()
        command = db.scalar(
            select(DeviceCommand).where(
                DeviceCommand.idempotency_key == f"update:{package.id}:{device.id}"
            )
        )
        if command:
            command.status = "completed"
            command.result = {"ok": True, "message": "设备已上报新版本"}
            command.completed_at = utcnow()
    if device.app_version == get_settings().app_version:
        device.status = "online"
