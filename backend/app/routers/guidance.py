"""用户引导进度与管理员投影维护接口。"""

from __future__ import annotations

import typing

import ipaddress

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import write_audit
from ..config import get_settings
from ..database import db_runtime, get_session
from ..device_versions import request_device
from ..enums import TransferStatus
from ..models import (
    BackupRun,
    Device,
    OnboardingProgress,
    ProjectionCheckpoint,
    Task,
    TaskParticipant,
    Transfer,
    User,
    WorkspaceRoot,
)
from ..problems import ProblemException
from ..projections import rebuild_report_projection
from ..schemas import (
    OnboardingProgressOut,
    OnboardingProgressPatch,
    EnablementOut,
    EnablementStepOut,
    ProjectionCheckpointOut,
)
from ..security import get_current_user, require_admin
from ..workspace_access import workspace_root_permissions
from .router_utils import client_ip, parse_if_match


router = APIRouter(tags=["guidance-and-projections"])
ONBOARDING_STEPS = [
    {"key": "profile", "title": "确认账号和提醒时间", "route": "/settings"},
    {"key": "first_task", "title": "建立第一项工作", "route": "/tasks"},
    {"key": "calendar", "title": "查看本周工作日历", "route": "/calendar"},
    {"key": "workspace", "title": "接入只读原始文件夹", "route": "/workspace"},
    {"key": "report", "title": "检查本周完成和下周计划", "route": "/reports"},
    {"key": "start", "title": "掌握第一次使用流程", "route": "/help"},
    {"key": "inbox", "title": "掌握快速收件建档", "route": "/help"},
    {"key": "daily", "title": "掌握每日使用流程", "route": "/help"},
    {"key": "recurrence", "title": "掌握长期周期事项", "route": "/help"},
    {"key": "files", "title": "掌握文件中心与版本", "route": "/help"},
    {"key": "reports", "title": "掌握周期报告与交接", "route": "/help"},
    {"key": "archives", "title": "掌握重要档案归档", "route": "/help"},
    {"key": "devices", "title": "掌握多设备协同", "route": "/help"},
    {"key": "ai", "title": "掌握 AI 安全边界", "route": "/help"},
    {"key": "admin", "title": "掌握管理员维护", "route": "/help"},
]


def host_network_ready() -> bool:
    """判断主机是否具备可供其他电脑访问的明确加密局域网地址。"""

    settings = get_settings()
    host = settings.host.strip().lower()
    if host in {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}:  # nosec B104 - 此处拒绝不可供协同机访问的地址。
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_loopback or address.is_link_local or not address.is_private
    ):
        return False
    return settings.tls_enabled


def enablement_step(
    key: str,
    title: str,
    description: str,
    route: str,
    action_label: str,
    complete: bool,
) -> EnablementStepOut:
    return EnablementStepOut(
        key=key,
        title=title,
        description=description,
        route=route,
        action_label=action_label,
        complete=complete,
    )


@router.get("/me/enablement", response_model=EnablementOut)
def get_enablement(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> EnablementOut:
    """按当前电脑和账号返回由真实业务状态计算的上手闭环。"""

    device = request_device(request, db)
    is_admin = user.role.value == "admin"
    persona = f"{'client' if device else 'host'}_{'admin' if is_admin else 'staff'}"
    task_exists = bool(
        db.scalar(
            select(func.count()).select_from(Task).where(
                Task.deleted_at.is_(None),
                or_(
                    Task.owner_id == user.id,
                    Task.reviewer_id == user.id,
                    Task.created_by == user.id,
                    Task.id.in_(
                        select(TaskParticipant.task_id).where(
                            TaskParticipant.user_id == user.id
                        )
                    ),
                ),
            )
        )
    )
    accessible_root = False
    for root in db.scalars(
        select(WorkspaceRoot).where(
            WorkspaceRoot.enabled.is_(True),
            WorkspaceRoot.approval_status == "approved",
        )
    ).all():
        if workspace_root_permissions(
            db, root, user, device.id if device else None
        )["browse"]:
            accessible_root = True
            break

    if device:
        own_root_exists = bool(
            db.scalar(
                select(func.count()).select_from(WorkspaceRoot).where(
                    WorkspaceRoot.device_id == device.id,
                    WorkspaceRoot.published_by_user_id == user.id,
                    WorkspaceRoot.enabled.is_(True),
                    WorkspaceRoot.approval_status == "approved",
                )
            )
        )
        transfer_completed = bool(
            db.scalar(
                select(func.count()).select_from(Transfer).where(
                    Transfer.requested_by == user.id,
                    Transfer.status == TransferStatus.COMPLETED,
                )
            )
        )
        steps = [
            enablement_step("device", "确认本机已安全入网", "设备身份、版本和首次心跳均由主机确认。", "/fleet/devices", "查看本机状态", True),
            enablement_step("local_share", "发布第一个本机共享文件夹", "使用系统文件夹选择器，主机不会保存本机绝对路径。", "/fleet/devices", "共享本机文件夹", own_root_exists),
            enablement_step("team_files", "打开团队共享文件", "只展示当前账号和本机均获授权的目录。", "/workspace", "进入文件中心", accessible_root),
            enablement_step("first_download", "完成一次真实下载", "远端文件先经主机中转、权限复核和哈希校验。", "/workspace", "选择文件下载", transfer_completed),
            enablement_step("first_work", "进入我的协同工作", "查看主办、协办、审核和步骤分派。", "/my-work", "打开我的工作", task_exists),
        ]
        title = "协同机上手检查"
        summary = "完成本机共享、团队文件和真实下载后，这台电脑才算具备完整协同能力。"
    elif is_admin:
        backup_ready = bool(
            db.scalar(
                select(func.count()).select_from(BackupRun).where(
                    BackupRun.status == "completed"
                )
            )
        )
        device_ready = bool(
            db.scalar(
                select(func.count()).select_from(Device).where(Device.active.is_(True))
            )
        )
        steps = [
            enablement_step("account", "首位管理员已创建", "管理员负责成员、设备、备份和正式更新。", "/help", "查看管理员职责", True),
            enablement_step("network", "启用可信局域网与 HTTPS", "必须绑定真实私网地址，127.0.0.1 不能用于跨机协同。", "/help?q=主机配置", "查看主机配置", host_network_ready()),
            enablement_step("backup", "完成首次可恢复备份", "正式录入数据前先生成并验证一份本机备份。", "/settings/backups", "创建首次备份", backup_ready),
            enablement_step("device", "接入第一台协同电脑", "生成入网码后等待设备安全入网和首次心跳。", "/fleet/devices", "新增协同电脑", device_ready),
            enablement_step("team_files", "建立第一个可访问共享目录", "主机目录由管理员纳管，协同机目录由用户发布。", "/workspace", "进入文件中心", accessible_root),
            enablement_step("first_work", "建立第一项真实工作", "用事项验证负责人、材料、审核和通知闭环。", "/tasks", "新建第一项工作", task_exists),
        ]
        title = "主机管理员上线检查"
        summary = "依次完成网络、备份、设备、共享目录和首项工作，团队才具备可恢复的协同闭环。"
    else:
        steps = [
            enablement_step("account", "账号登录正常", "当前账号已通过主机身份验证。", "/help", "查看使用帮助", True),
            enablement_step("first_work", "找到我的第一项工作", "只汇总主办、协办、审核和步骤分派给我的事项。", "/my-work", "打开我的工作", task_exists),
            enablement_step("team_files", "访问获授权的团队文件", "文件中心会隐藏没有账号或设备权限的操作。", "/workspace", "进入文件中心", accessible_root),
        ]
        title = "协同办公上手检查"
        summary = "从我的工作和团队文件开始，不需要接触主机管理设置。"

    completed_count = sum(1 for step in steps if step.complete)
    next_step = next((step for step in steps if not step.complete), None)
    return EnablementOut(
        persona=persona,
        title=title,
        summary=summary,
        completed_count=completed_count,
        total_count=len(steps),
        next_route=next_step.route if next_step else "/",
        steps=steps,
    )


@router.get("/me/onboarding", response_model=OnboardingProgressOut)
def get_onboarding(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> object:
    item = db.scalar(
        select(OnboardingProgress).where(OnboardingProgress.user_id == user.id)
    )
    if item is None:
        item = OnboardingProgress(user_id=user.id)
        db.add(item)
        db.commit()
        db.refresh(item)
    return OnboardingProgressOut.model_validate(item).model_copy(
        update={"steps": ONBOARDING_STEPS}
    )


@router.patch("/me/onboarding", response_model=OnboardingProgressOut)
def patch_onboarding(
    payload: OnboardingProgressPatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> object:
    item = db.scalar(
        select(OnboardingProgress).where(OnboardingProgress.user_id == user.id)
    )
    if item is None:
        item = OnboardingProgress(user_id=user.id)
        db.add(item)
        db.flush()
    if item.version != parse_if_match(if_match):
        raise ProblemException(
            409, "VERSION_CONFLICT", "引导进度已变化", "请刷新后重试。"
        )
    if payload.completed_steps is not None:
        allowed = {step["key"] for step in ONBOARDING_STEPS}
        item.completed_steps = sorted(
            allowed & {value for value in payload.completed_steps if value}
        )
    if payload.dismissed is not None:
        item.dismissed = payload.dismissed
    item.version += 1
    db.commit()
    db.refresh(item)
    return OnboardingProgressOut.model_validate(item).model_copy(
        update={"steps": ONBOARDING_STEPS}
    )


@router.get(
    "/admin/projections/status",
    response_model=typing.List[ProjectionCheckpointOut],
)
def projection_status(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[ProjectionCheckpoint]:
    return list(
        db.scalars(
            select(ProjectionCheckpoint).order_by(ProjectionCheckpoint.name)
        ).all()
    )


@router.post(
    "/admin/projections/rebuild",
    response_model=ProjectionCheckpointOut,
)
def rebuild_projections(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ProjectionCheckpoint:
    with db_runtime.write_lock:
        checkpoint = rebuild_report_projection(db, admin)
        write_audit(
            db,
            admin,
            "projection.rebuild",
            "projection",
            checkpoint.name,
            {"processed_count": checkpoint.processed_count},
            client_ip(request),
        )
        db.commit()
        db.refresh(checkpoint)
        return checkpoint
