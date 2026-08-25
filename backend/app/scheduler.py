"""轻量后台调度：自动备份与周期事项。"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select

from .backups import create_backup
from .compat import to_thread
from .config import get_settings
from .database import db_runtime
from .enums import UserRole
from .local_ai import llm_runtime
from .models import (
    AutomationRule,
    BackgroundJob,
    BackupRun,
    Device,
    DeviceCommand,
    DeviceEnrollment,
    EventOutbox,
    LocalShareAction,
    LoginSession,
    Notification,
    ProjectionCheckpoint,
    Task,
    Transfer,
    TransferChunk,
    User,
    WorkspaceFile,
    utcnow,
)
from .notifications import refresh_notifications
from .projections import process_report_projection
from .recommendations import index_semantic_batch, refresh_rule_recommendations
from .recurrence import run_due_rules
from .routers.business import generate_due_recurring_meetings
from .storage import purge_expired_deleted_attachments


def cleanup_transfer_storage(db, settings) -> int:
    """回收终止/过期传输与孤儿分块，避免配额被历史 .part 占满。"""

    now = utcnow()
    candidates = db.scalars(
        select(Transfer).where(
            or_(
                Transfer.status.in_(["cancelled", "failed", "expired"]),
                Transfer.expires_at <= now,
            )
        )
    ).all()
    cleaned = 0
    for transfer in candidates:
        expires_at = (
            transfer.expires_at
            if transfer.expires_at.tzinfo
            else transfer.expires_at.replace(tzinfo=timezone.utc)
        )
        if expires_at <= now and transfer.status not in {"cancelled", "failed", "expired"}:
            transfer.status = "expired"
            transfer.version += 1
        part = settings.transfers_dir / f"{transfer.id}.part"
        try:
            existed = part.exists()
            part.unlink(missing_ok=True)
            cleaned += int(existed)
        except OSError:
            logging.getLogger("partyops.transfers").warning(
                "transfer_part_cleanup_failed transfer_id=%s",
                transfer.id,
                exc_info=True,
            )
        transfer.transit_path = ""
        db.execute(
            delete(TransferChunk).where(TransferChunk.transfer_id == transfer.id)
        )
        transfer.completed_chunks = 0

    known_ids = set(db.scalars(select(Transfer.id)).all())
    orphan_cutoff = now.timestamp() - 24 * 60 * 60
    for part in settings.transfers_dir.glob("*.part"):
        if part.stem in known_ids:
            continue
        try:
            if part.stat().st_mtime <= orphan_cutoff:
                part.unlink()
                cleaned += 1
        except OSError:
            logging.getLogger("partyops.transfers").warning(
                "orphan_transfer_part_cleanup_failed path=%s",
                part.name,
                exc_info=True,
            )
    return cleaned


def _remove_file(path: Path, logger: logging.Logger) -> bool:
    try:
        if not path.is_file() and not path.is_symlink():
            return False
        path.unlink(missing_ok=True)
        return True
    except OSError:
        logger.warning("retention_file_cleanup_failed path=%s", path.name, exc_info=True)
        return False


def _affected_rows(result) -> int:
    """兼容真实 CursorResult 与调度器隔离测试的轻量会话替身。"""

    return max(0, int(getattr(result, "rowcount", 0) or 0))


def cleanup_runtime_retention(db, settings, *, now: datetime | None = None) -> dict[str, int]:
    """回收可再生或生命周期已结束的数据，不删除审计与业务时间线。"""

    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    logger = logging.getLogger("partyops.retention")
    counts: dict[str, int] = {
        "notifications": 0,
        "sessions": 0,
        "transient_records": 0,
        "outbox": 0,
        "inbox_files": 0,
        "exports": 0,
        "upgrade_backups": 0,
        "deleted_task_versions": 0,
        "deleted_archive_attachments": 0,
        "unreferenced_blobs": 0,
    }

    result = db.execute(
        delete(Notification).where(
            Notification.read_at.is_not(None),
            Notification.read_at
            < current - timedelta(days=settings.notification_read_retention_days),
        )
    )
    counts["notifications"] = _affected_rows(result)

    session_cutoff = current - timedelta(days=settings.session_retention_days)
    result = db.execute(
        delete(LoginSession).where(
            or_(
                LoginSession.expires_at < session_cutoff,
                and_(
                    LoginSession.revoked_at.is_not(None),
                    LoginSession.revoked_at < session_cutoff,
                ),
            )
        )
    )
    counts["sessions"] = _affected_rows(result)

    transient_cutoff = current - timedelta(days=settings.transient_record_retention_days)
    for statement in (
        delete(DeviceEnrollment).where(DeviceEnrollment.expires_at < transient_cutoff),
        delete(LocalShareAction).where(LocalShareAction.expires_at < transient_cutoff),
        delete(DeviceCommand).where(
            DeviceCommand.status.in_(["completed", "failed"]),
            DeviceCommand.completed_at.is_not(None),
            DeviceCommand.completed_at < transient_cutoff,
        ),
        delete(BackgroundJob).where(
            BackgroundJob.status.in_(["completed", "failed"]),
            BackgroundJob.completed_at.is_not(None),
            BackgroundJob.completed_at < transient_cutoff,
        ),
    ):
        result = db.execute(statement)
        counts["transient_records"] += _affected_rows(result)

    # 只删除所有投影均已消费的旧事件。检查点失败或落后时，未消费事件保留。
    consumed_through = db.scalar(select(func.min(ProjectionCheckpoint.last_event_id)))
    if consumed_through is not None:
        result = db.execute(
            delete(EventOutbox).where(
                EventOutbox.id <= int(consumed_through),
                EventOutbox.created_at
                < current - timedelta(days=settings.event_outbox_retention_days),
            )
        )
        counts["outbox"] = _affected_rows(result)

    handled_cutoff = current - timedelta(days=settings.inbox_handled_retention_days)
    unhandled_cutoff = current - timedelta(days=settings.inbox_unhandled_retention_days)
    transfers = db.scalars(
        select(Transfer).where(
            Transfer.direction == "device_to_host",
            Transfer.status == "completed",
            or_(
                and_(Transfer.handled_at.is_not(None), Transfer.handled_at < handled_cutoff),
                and_(Transfer.handled_at.is_(None), Transfer.created_at < unhandled_cutoff),
            ),
        )
    ).all()
    for transfer in transfers:
        removed = False
        for path in settings.inbox_dir.glob(f"{transfer.id}-*"):
            removed = _remove_file(path, logger) or removed
        if removed or not any(settings.inbox_dir.glob(f"{transfer.id}-*")):
            transfer.error_code = (
                "INBOX_RETAINED_COPY_EXPIRED" if transfer.handled_at else "INBOX_EXPIRED"
            )
            transfer.error_message = (
                "已处理文件的接收箱副本已按保留策略清理。"
                if transfer.handled_at
                else "接收文件超过保留期，已清理；如仍需要请重新发起传输。"
            )
            transfer.transit_path = ""
            transfer.version += 1
        counts["inbox_files"] += int(removed)

    export_cutoff = (current - timedelta(days=settings.export_retention_days)).timestamp()
    for path in settings.exports_dir.iterdir():
        try:
            if path.stat().st_mtime < export_cutoff:
                counts["exports"] += int(_remove_file(path, logger))
        except OSError:
            logger.warning("retention_export_stat_failed path=%s", path.name, exc_info=True)

    upgrade_root = settings.data_dir / "upgrade-backups"
    update_lock = settings.data_dir / ".update.lock"
    if upgrade_root.is_dir() and not update_lock.exists():
        try:
            candidates = sorted(
                (item for item in upgrade_root.iterdir() if item.is_dir() or item.is_symlink()),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
            logger.warning("retention_upgrade_scan_failed", exc_info=True)
        keep = set(candidates[: settings.upgrade_backup_keep])
        upgrade_cutoff = (
            current - timedelta(days=settings.upgrade_backup_retention_days)
        ).timestamp()
        resolved_root = upgrade_root.resolve()
        for path in candidates:
            try:
                if path in keep or path.stat().st_mtime >= upgrade_cutoff:
                    continue
                resolved = path.resolve(strict=False)
                if resolved.parent != resolved_root:
                    logger.warning("retention_upgrade_path_rejected path=%s", path.name)
                    continue
                if path.is_symlink():
                    path.unlink()
                else:
                    shutil.rmtree(path)
                counts["upgrade_backups"] += 1
            except OSError:
                logger.warning(
                    "retention_upgrade_cleanup_failed path=%s",
                    path.name,
                    exc_info=True,
                )
    deleted_counts = purge_expired_deleted_attachments(db, now=current)
    counts["deleted_task_versions"] = deleted_counts["task_versions"]
    counts["deleted_archive_attachments"] = deleted_counts["archive_attachments"]
    counts["unreferenced_blobs"] = deleted_counts["blobs"]
    return counts


def run_automation_rules(db, admin: User) -> None:
    """只执行低风险提醒和归档建议，不自动删除、移动或改状态。"""

    now = datetime.now(timezone.utc)
    rules = db.scalars(
        select(AutomationRule).where(AutomationRule.enabled.is_(True))
    ).all()
    for rule in rules:
        if rule.trigger == "workspace_file_indexed":
            conditions = rule.conditions or {}
            actions = rule.actions or {}
            name_contains = str(conditions.get("name_contains", "")).strip().lower()
            path_contains = str(conditions.get("path_contains", "")).strip().lower()
            extensions = {
                str(item).lower()
                for item in conditions.get("extensions", [])
                if str(item).strip()
            }
            files = db.scalars(
                select(WorkspaceFile)
                .where(WorkspaceFile.is_directory.is_(False))
                .order_by(WorkspaceFile.last_seen_at.desc())
                .limit(1_000)
            ).all()
            for file in files:
                if name_contains and name_contains not in file.name.lower():
                    continue
                if path_contains and path_contains not in file.relative_path.lower():
                    continue
                if extensions and file.extension.lower() not in extensions:
                    continue
                dedupe = f"automation:file:{rule.id}:{file.id}:{file.version}"
                if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe)):
                    continue
                suggestions = [
                    f"建议事项：{actions['task_title']}"
                    for _ in [0]
                    if str(actions.get("task_title", "")).strip()
                ]
                if str(actions.get("material_category", "")).strip():
                    suggestions.append(f"材料类别：{actions['material_category']}")
                tags = [str(item) for item in actions.get("tags", []) if str(item).strip()]
                if tags:
                    suggestions.append(f"标签：{'、'.join(tags)}")
                db.add(
                    Notification(
                        user_id=rule.owner_id,
                        notification_type="archive_suggestion",
                        title="发现待确认的归档建议",
                        body=f"文件“{file.name}”符合规则“{rule.name}”。"
                        + ("；" + "；".join(suggestions) if suggestions else ""),
                        entity_type="workspace_file",
                        entity_id=file.id,
                        dedupe_key=dedupe,
                    )
                )
            continue
        if rule.trigger not in {"task_due_soon", "task_overdue"}:
            continue
        conditions = rule.conditions or {}
        days = max(0, min(365, int(conditions.get("days", 3) or 3)))
        deadline = now + timedelta(days=days)
        statement = select(Task).where(Task.deleted_at.is_(None))
        if rule.trigger == "task_overdue":
            statement = statement.where(Task.formal_due_at < now)
        else:
            statement = statement.where(
                Task.formal_due_at >= now,
                Task.formal_due_at <= deadline,
            )
        status_values = conditions.get("statuses")
        if isinstance(status_values, list) and status_values:
            statement = statement.where(Task.status.in_(status_values))
        for task in db.scalars(statement.limit(500)).all():
            user_id = task.owner_id or admin.id
            dedupe = f"automation:{rule.id}:{task.id}:{now.date().isoformat()}"
            if db.scalar(
                select(Notification.id).where(Notification.dedupe_key == dedupe)
            ):
                continue
            action = (rule.actions or {}).get("type", "notify")
            title = (
                "任务已逾期"
                if rule.trigger == "task_overdue"
                else "任务即将截止"
            )
            body = f"“{task.title}”{title}，请及时办理。"
            if action == "archive_suggestion":
                title = "建议归档"
                body = f"“{task.title}”满足自动归档规则，请人工确认材料完整后归档。"
            db.add(
                Notification(
                    user_id=user_id,
                    notification_type="automation",
                    title=title,
                    body=body,
                    entity_type="task",
                    entity_id=task.id,
                    dedupe_key=dedupe,
                )
            )


def _run_scheduler_cycle(settings, now: datetime, last_backup_day: str | None) -> str | None:
    """执行一个同步调度周期；调用方负责把它放入工作线程。"""

    current_day = now.strftime("%Y-%m-%d")
    if (
        now.hour == settings.backup_hour
        and now.minute >= settings.backup_minute
        and last_backup_day != current_day
    ):
        with db_runtime.session_factory() as db:
            latest = db.scalar(
                select(BackupRun)
                .where(
                    BackupRun.kind == "automatic",
                    BackupRun.deleted_at.is_(None),
                )
                .order_by(BackupRun.created_at.desc())
            )
            if not latest or latest.created_at.date() != now.date():
                create_backup(db, None, kind="automatic")
            last_backup_day = current_day
    with db_runtime.session_factory() as db:
        admin = db.scalar(
            select(User).where(
                User.role == UserRole.ADMIN, User.active.is_(True)
            )
        )
        if admin:
            generate_due_recurring_meetings(db, admin, now)
            run_due_rules(db, admin)
            run_automation_rules(db, admin)
            process_report_projection(db, admin)
            for current_user in db.scalars(
                select(User).where(User.active.is_(True))
            ).all():
                refresh_rule_recommendations(db, current_user)
            try:
                with db.begin_nested():
                    index_semantic_batch(db, limit=4)
            except Exception:
                logging.getLogger("partyops.local_ai").exception(
                    "semantic_index_cycle_failed"
                )
            llm_runtime.unload_if_idle()
        refresh_notifications(db)
        heartbeat_now = datetime.now(timezone.utc)
        for device in db.scalars(
            select(Device).where(Device.active.is_(True))
        ).all():
            if device.status.value in {"revoked", "quarantined", "updating"}:
                continue
            if not device.last_seen_at:
                device.status = "offline"
                continue
            last_seen = (
                device.last_seen_at
                if device.last_seen_at.tzinfo
                else device.last_seen_at.replace(tzinfo=timezone.utc)
            )
            age = (heartbeat_now - last_seen).total_seconds()
            device.status = "offline" if age > 45 else "stale" if age > 30 else "online"
        cleanup_transfer_storage(db, settings)
        cleanup_runtime_retention(db, settings, now=heartbeat_now)
        db.commit()
    return last_backup_day


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    last_backup_day: str | None = None
    while not stop_event.is_set():
        try:
            # 备份、SQLite 查询及本地模型推理都是同步工作，统一转入线程，
            # 保证同一进程中的 API、SSE 和 Agent 心跳仍能及时响应。
            last_backup_day = await to_thread(
                _run_scheduler_cycle,
                settings,
                datetime.now(),
                last_backup_day,
            )
        except Exception:
            logging.getLogger("partyops.scheduler").exception(
                "scheduler_cycle_failed"
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            pass
