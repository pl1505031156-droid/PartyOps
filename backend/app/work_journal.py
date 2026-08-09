"""工作日志写入辅助；系统事件只追加、不覆盖。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .enums import ObjectType
from .models import ActivityEvent, Task, User, WorkJournalEntry, utcnow
from .schemas import WorkJournalOut


ROLE_LABELS = {"admin": "管理员", "staff": "协同人员"}
STATUS_LABELS = {
    "pending_receipt": "待接收",
    "pending_breakdown": "待拆解",
    "in_progress": "办理中",
    "waiting_feedback": "等待反馈",
    "pending_review": "待审核",
    "returned": "退回修改",
    "completed": "已完成",
    "archived": "已归档",
}
MATERIAL_LABELS = {
    "draft": "初稿",
    "revision": "修改稿",
    "leader_approved": "领导审定稿",
    "submitted": "实际报送稿",
}
ACTION_LABELS = {
    "task.created": "新建事项",
    "task.updated": "修改事项",
    "task.transferred": "转交事项",
    "task.participant_added": "添加协办人员",
    "task.participant_removed": "移除协办人员",
    "task.step_added": "添加办理步骤",
    "task.step_completed": "完成办理步骤",
    "task.step_updated": "修改办理步骤",
    "task.step_removed": "移除办理步骤",
    "task.accept": "接收事项",
    "task.start": "开始办理",
    "task.wait_feedback": "转为等待反馈",
    "task.resume": "恢复办理",
    "task.submit_review": "提交审核",
    "task.return": "退回修改",
    "task.approve": "审核通过",
    "task.complete": "完成办理",
    "task.archive": "归档事项",
    "task.reopen": "重新打开",
    "material.uploaded": "上传材料",
    "material.finalized": "确认最终稿",
    "workspace.file_linked": "关联原始文件",
    "workspace.file_frozen": "固化归档",
    "report.published": "发布周期报告",
    "report.updated": "修改周期报告",
    "report.publish": "发布周期报告",
    "report.lock": "锁定周期报告",
    "report.reopen": "重新打开周期报告",
}


def _legacy_event(entry: WorkJournalEntry) -> tuple[str, dict]:
    if entry.event_code:
        return entry.event_code, dict(entry.event_data or {})
    if entry.title.startswith("新建事项："):
        return "task.created", {}
    if entry.title.startswith("更新事项："):
        return "task.updated", {}
    if entry.title.startswith("状态变更："):
        values = entry.content.split("；", 1)[0].split("→")
        return "task.status_changed", {
            "from_status": values[0].strip() if values else "",
            "to_status": values[1].strip() if len(values) > 1 else "",
        }
    if entry.title.startswith("上传材料："):
        return "material.uploaded", {}
    if entry.title.startswith("关联原始文件："):
        return "workspace.file_linked", {}
    if entry.title.startswith("固化归档："):
        return "workspace.file_frozen", {}
    return "", {}


def journal_to_out(db: Session, entry: WorkJournalEntry) -> WorkJournalOut:
    event_code, event_data = _legacy_event(entry)
    action = str(event_data.get("action", ""))
    effective_code = f"task.{action}" if event_code == "task.status_changed" and action else event_code
    actor = db.get(User, entry.created_by)
    task = db.get(Task, entry.task_id) if entry.task_id else None
    return WorkJournalOut.model_validate(entry).model_copy(
        update={
            "event_code": event_code,
            "event_data": event_data,
            "action_label": ACTION_LABELS.get(effective_code, entry.title),
            "actor_name": actor.display_name if actor else "系统",
            "actor_role_label": ROLE_LABELS.get(
                str(getattr(getattr(actor, "role", ""), "value", getattr(actor, "role", ""))),
                "系统",
            ),
            "task_title": task.title if task else "",
            "from_status": STATUS_LABELS.get(
                str(event_data.get("from_status", "")),
                "未知状态" if event_data.get("from_status") else "",
            ),
            "to_status": STATUS_LABELS.get(
                str(event_data.get("to_status", "")),
                "未知状态" if event_data.get("to_status") else "",
            ),
            "material_stage": MATERIAL_LABELS.get(
                str(event_data.get("material_stage", "")),
                "未知阶段" if event_data.get("material_stage") else "",
            ),
        }
    )


def record_system_entry(
    db: Session,
    actor: User,
    title: str,
    content: str = "",
    *,
    task_id: str | None = None,
    file_id: str | None = None,
    report_id: str | None = None,
    occurred_at: datetime | None = None,
    event_code: str = "",
    event_data: dict | None = None,
) -> WorkJournalEntry:
    entry = WorkJournalEntry(
        entry_type="system",
        title=title[:240],
        content=content[:30_000],
        event_code=event_code[:64],
        event_data=event_data or {},
        occurred_at=occurred_at or utcnow(),
        task_id=task_id,
        file_id=file_id,
        report_id=report_id,
        immutable=True,
        created_by=actor.id,
    )
    db.add(entry)
    object_type: ObjectType | None = None
    object_id: str | None = None
    if task_id:
        object_type, object_id = ObjectType.TASK, task_id
    elif file_id:
        object_type, object_id = ObjectType.WORKSPACE_FILE, file_id
    elif report_id:
        object_type, object_id = ObjectType.PERIOD_REPORT, report_id
    if object_type and object_id:
        db.add(
            ActivityEvent(
                object_type=object_type,
                object_id=object_id,
                event_code=(event_code or "journal.recorded")[:80],
                actor_id=actor.id,
                happened_at=occurred_at or utcnow(),
                event_data=event_data or {"title": title[:240]},
            )
        )
    return entry
