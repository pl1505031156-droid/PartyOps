"""原始文件中心 API。"""

from __future__ import annotations

import typing

import ipaddress
import os
import secrets
import socket
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..audit import emit_event, write_audit
from ..config import get_settings
from ..content_security import may_render_inline
from ..database import db_runtime, get_session
from ..device_versions import request_device
from ..enums import TransferStatus, UserRole
from ..models import (
    BackgroundJob,
    Device,
    FileOpenGrant,
    LocalShareAction,
    PeriodReport,
    SemanticIndexCheckpoint,
    Task,
    Transfer,
    User,
    WorkJournalEntry,
    WorkspaceFile,
    WorkspaceFileTag,
    WorkspaceLink,
    WorkspaceRoot,
    WorkspaceRootMember,
    utcnow,
)
from ..problems import ProblemException
from ..platform_info import detect_platform_info
from ..schemas import (
    BackgroundJobOut,
    FileOpenGrantCompletion,
    LocalShareActionOut,
    RuntimeContextOut,
    UserOut,
    WorkspaceFileLinkCreate,
    WorkspaceFileOut,
    WorkspaceFileTagsPatch,
    WorkspaceFolderOption,
    WorkspaceRootCreate,
    WorkspaceRootMemberOut,
    WorkspaceRootMembersPatch,
    WorkspaceRootOut,
    WorkspaceRootPatch,
    WorkspaceRootSharingPatch,
    WorkspaceScanOut,
    WorkspaceSelectionPatch,
)
from ..security import get_current_user, hash_token, require_admin
from ..task_service import can_view_task
from ..work_journal import record_system_entry
from ..workspace_access import workspace_root_permissions
from ..workspace import (
    file_to_out,
    freeze_workspace_file,
    resolve_workspace_path,
    run_scan_job,
    scan_root,
    search_workspace_files,
    validate_selection_paths,
    validate_root_path,
)


router = APIRouter(tags=["workspace-files"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def is_host_local_request(request: Request) -> bool:
    """只允许主机本机桌面助手取回原始路径。"""

    raw = client_ip(request).split("%", 1)[0]
    if get_settings().environment == "test" and raw == "testclient":
        return True
    try:
        address = ipaddress.ip_address(raw)
        if address.is_loopback:
            return True
    except ValueError:
        return False
    local_addresses = {get_settings().host}
    try:
        local_addresses.update(
            item[4][0].split("%", 1)[0]
            for item in socket.getaddrinfo(socket.gethostname(), None)
        )
    except OSError:
        pass
    return raw in local_addresses


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def current_device_id(request: Request, db: Session) -> str | None:
    device = request_device(request, db)
    return device.id if device else None


def root_to_out(
    db: Session,
    root: WorkspaceRoot,
    user: User,
    device_id: str | None,
) -> WorkspaceRootOut:
    return WorkspaceRootOut.model_validate(root).model_copy(
        update={"permissions": workspace_root_permissions(db, root, user, device_id)}
    )


def workspace_file_out(
    db: Session,
    item: WorkspaceFile,
    user: User,
    device_id: str | None,
) -> WorkspaceFileOut:
    root = db.get(WorkspaceRoot, item.root_id)
    permissions = (
        workspace_root_permissions(db, root, user, device_id)
        if root
        else {"browse": False, "download": False, "share": False, "upload": False}
    )
    return file_to_out(db, item).model_copy(update={"permissions": permissions})


def get_file(
    db: Session,
    file_id: str,
    user: User,
    device_id: str | None,
    capability: str = "browse",
) -> tuple[WorkspaceFile, WorkspaceRoot]:
    item = db.get(WorkspaceFile, file_id)
    if not item:
        raise ProblemException(404, "WORKSPACE_FILE_NOT_FOUND", "文件不存在", "未找到索引文件。")
    root = db.get(WorkspaceRoot, item.root_id)
    if not root or not root.enabled:
        raise ProblemException(410, "WORKSPACE_ROOT_DISABLED", "文件目录已停用", "请联系管理员。")
    if not item.in_scope:
        raise ProblemException(
            403,
            "WORKSPACE_FILE_OUT_OF_SCOPE",
            "文件尚未接入系统",
            "请由管理员在原始文件中心选择该文件所在文件夹后再访问。",
        )
    permissions = workspace_root_permissions(db, root, user, device_id)
    if not permissions.get(capability, False):
        raise ProblemException(
            403,
            "WORKSPACE_ACCESS_DENIED",
            "无权访问该共享目录",
            "请联系管理员批准目录并授予相应能力。",
        )
    return item, root


def require_root_manager(
    db: Session,
    root_id: str,
    user: User,
    device_id: str | None,
) -> WorkspaceRoot:
    root = db.get(WorkspaceRoot, root_id)
    if not root:
        raise ProblemException(404, "WORKSPACE_ROOT_NOT_FOUND", "目录不存在", "未找到共享目录。")
    if not workspace_root_permissions(db, root, user, device_id)["manage_root"]:
        raise ProblemException(
            403,
            "WORKSPACE_ROOT_MANAGE_DENIED",
            "无权管理该共享目录",
            "只有目录发布人或管理员可以修改共享范围。",
        )
    return root


@router.get("/runtime/context", response_model=RuntimeContextOut)
def runtime_context(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> RuntimeContextOut:
    """返回同一套导航和操作按钮所需的运行位置与有效能力。"""

    device = request_device(request, db)
    settings = get_settings()
    node_mode = (
        "client"
        if device
        else (
            settings.mode
            if settings.mode in {"host", "personal"} and is_host_local_request(request)
            else "unknown"
        )
    )
    capabilities = {
        "workspace.browse",
        "workspace.download.browser",
        "workspace.send",
        "transfer.own",
    }
    if device:
        capabilities.add("workspace.download.current_device")
        if device.allow_user_shares and device.active and device.status not in {"revoked", "quarantined"}:
            capabilities.update({"workspace.local_share", "workspace.manage_own_roots"})
    if user.role.value == "admin":
        capabilities.update(
            {
                "admin.access",
                "workspace.manage_all_roots",
                "updates.manage",
                "backups.manage",
                "ai.manage",
            }
        )
        if node_mode == "host":
            capabilities.update({"workspace.manage_host_roots", "fleet.manage"})
    return RuntimeContextOut(
        node_mode=node_mode,
        platform=str(detect_platform_info().get("platform") or "unsupported"),
        user_role=user.role,
        device_id=device.id if device else None,
        device_name=device.name if device else "",
        capabilities=sorted(capabilities),
    )


@router.post("/workspace/local-share-actions", response_model=LocalShareActionOut, status_code=201)
def create_local_share_action(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> LocalShareActionOut:
    """签发 60 秒单次令牌，只允许当前浏览器上下文对应的协同机消费。"""

    device = request_device(request, db)
    if not device:
        raise ProblemException(
            409,
            "CLIENT_DEVICE_REQUIRED",
            "请在协同电脑上操作",
            "共享本机文件夹必须从已入网协同电脑的 PartyOps 页面发起。",
        )
    if (
        not device.active
        or device.status in {"revoked", "quarantined"}
        or not device.allow_user_shares
    ):
        raise ProblemException(
            403,
            "LOCAL_SHARE_DISABLED",
            "本机目录发布已停用",
            "请联系管理员开启该协同电脑的普通用户目录发布能力。",
        )
    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(seconds=60)
    db.add(
        LocalShareAction(
            token_hash=hash_token(token),
            device_id=device.id,
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    write_audit(
        db,
        user,
        "workspace.local_share_action_create",
        "device",
        device.id,
        {"expires_at": expires_at.isoformat()},
        client_ip(request),
    )
    db.commit()
    return LocalShareActionOut(
        open_uri=f"partyops-client://manage-shares/{token}",
        expires_at=expires_at,
    )


@router.get("/collaboration/users", response_model=typing.List[UserOut])
def list_collaboration_users(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[User]:
    """共享范围选择只返回系统内有效账号，不开放用户管理能力。"""

    return list(
        db.scalars(select(User).where(User.active.is_(True)).order_by(User.display_name)).all()
    )


@router.get("/workspace/roots", response_model=typing.List[WorkspaceRootOut])
def list_workspace_roots(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkspaceRootOut]:
    device_id = current_device_id(request, db)
    roots = db.scalars(
        select(WorkspaceRoot)
        .where(WorkspaceRoot.enabled.is_(True))
        .order_by(WorkspaceRoot.name)
    ).all()
    return [
        root_to_out(db, root, user, device_id)
        for root in roots
        if workspace_root_permissions(db, root, user, device_id)["browse"]
    ]


@router.patch("/workspace/roots/{root_id}/sharing", response_model=WorkspaceRootOut)
def patch_workspace_root_sharing(
    root_id: str,
    payload: WorkspaceRootSharingPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceRootOut:
    device_id = current_device_id(request, db)
    root = require_root_manager(db, root_id, user, device_id)
    if root.source.value != "device":
        raise ProblemException(
            400,
            "HOST_ROOT_SHARING_FIXED",
            "主机目录共享范围不可在此修改",
            "主机系统目录继续由管理员通过目录纳管与设备授权设置。",
        )
    if root.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "共享设置已更新", "请刷新后重试。")
    semantic_was_enabled = root.semantic_content_enabled
    root.share_scope = payload.share_scope
    root.semantic_content_enabled = payload.semantic_content_enabled
    root.version += 1
    if semantic_was_enabled and not root.semantic_content_enabled:
        file_ids = list(
            db.scalars(select(WorkspaceFile.id).where(WorkspaceFile.root_id == root.id)).all()
        )
        if file_ids:
            checkpoints = db.scalars(
                select(SemanticIndexCheckpoint).where(
                    SemanticIndexCheckpoint.object_type == "workspace_file_content",
                    SemanticIndexCheckpoint.object_id.in_(file_ids),
                )
            ).all()
            for checkpoint in checkpoints:
                db.delete(checkpoint)
    write_audit(
        db,
        user,
        "workspace.root_sharing_update",
        "workspace_root",
        root.id,
        {
            "share_scope": root.share_scope,
            "semantic_content_enabled": root.semantic_content_enabled,
            "version": root.version,
        },
        client_ip(request),
    )
    emit_event(
        db,
        "workspace.root_sharing_updated",
        root.id,
        {"share_scope": root.share_scope},
    )
    db.commit()
    db.refresh(root)
    return root_to_out(db, root, user, device_id)


@router.get(
    "/workspace/roots/{root_id}/members",
    response_model=typing.List[WorkspaceRootMemberOut],
)
def list_workspace_root_members(
    root_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkspaceRootMember]:
    root = require_root_manager(db, root_id, user, current_device_id(request, db))
    return list(
        db.scalars(
            select(WorkspaceRootMember)
            .where(WorkspaceRootMember.root_id == root.id, WorkspaceRootMember.active.is_(True))
            .order_by(WorkspaceRootMember.created_at)
        ).all()
    )


@router.put(
    "/workspace/roots/{root_id}/members",
    response_model=typing.List[WorkspaceRootMemberOut],
)
def replace_workspace_root_members(
    root_id: str,
    payload: WorkspaceRootMembersPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkspaceRootMember]:
    device_id = current_device_id(request, db)
    root = require_root_manager(db, root_id, user, device_id)
    if root.source.value != "device":
        raise ProblemException(400, "ROOT_MEMBERS_UNSUPPORTED", "主机目录不使用成员表", "请使用设备授权管理。")
    if root.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "共享成员已更新", "请刷新后重试。")
    requested = {item.user_id: item for item in payload.members}
    if len(requested) != len(payload.members):
        raise ProblemException(422, "DUPLICATE_ROOT_MEMBER", "存在重复成员", "每位成员只能配置一次。")
    valid_user_ids = set(
        db.scalars(
            select(User.id).where(User.id.in_(requested), User.active.is_(True))
        ).all()
    ) if requested else set()
    missing = sorted(set(requested) - valid_user_ids)
    if missing:
        raise ProblemException(
            422,
            "ROOT_MEMBER_INVALID",
            "共享成员无效",
            "请选择仍在使用的系统用户。",
            extra={"user_ids": missing},
        )
    existing = {
        member.user_id: member
        for member in db.scalars(
            select(WorkspaceRootMember).where(WorkspaceRootMember.root_id == root.id)
        ).all()
    }
    for member in existing.values():
        member.active = False
        member.version += 1
    for user_id, item in requested.items():
        member = existing.get(user_id)
        if member is None:
            member = WorkspaceRootMember(
                root_id=root.id,
                user_id=user_id,
                created_by=user.id,
            )
            db.add(member)
        member.can_browse = item.can_browse
        member.can_download = item.can_download
        member.can_send = item.can_send
        member.active = True
    root.share_scope = "selected"
    root.version += 1
    write_audit(
        db,
        user,
        "workspace.root_members_replace",
        "workspace_root",
        root.id,
        {"member_count": len(requested), "version": root.version},
        client_ip(request),
    )
    db.commit()
    return list(
        db.scalars(
            select(WorkspaceRootMember)
            .where(WorkspaceRootMember.root_id == root.id, WorkspaceRootMember.active.is_(True))
            .order_by(WorkspaceRootMember.created_at)
        ).all()
    )


@router.post("/workspace/roots", response_model=WorkspaceRootOut, status_code=201)
def create_workspace_root(
    payload: WorkspaceRootCreate,
    request: Request,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> WorkspaceRoot:
    resolved = validate_root_path(payload.absolute_path)
    existing_path = db.scalar(
        select(WorkspaceRoot).where(WorkspaceRoot.absolute_path == str(resolved))
    )
    name_in_use = db.scalar(
        select(WorkspaceRoot.id).where(
            WorkspaceRoot.name == payload.name.strip(),
            WorkspaceRoot.enabled.is_(True),
            WorkspaceRoot.absolute_path != str(resolved),
        )
    )
    if name_in_use or (existing_path and existing_path.enabled):
        raise ProblemException(409, "WORKSPACE_ROOT_EXISTS", "目录已经纳管", "请直接使用现有目录。")
    if existing_path:
        root = existing_path
        root.name = payload.name.strip()
        root.selection_mode = payload.selection_mode
        root.included_paths = [] if payload.selection_mode == "selected" else ["."]
        root.enabled = True
        root.approval_status = "approved"
        root.approval_note = "管理员重新纳管"
        root.scan_status = "pending"
        root.error_message = ""
        root.version += 1
        audit_action = "workspace.root_reactivate"
    else:
        root = WorkspaceRoot(
            name=payload.name.strip(),
            absolute_path=str(resolved),
            selection_mode=payload.selection_mode,
            included_paths=[] if payload.selection_mode == "selected" else ["."],
            read_only=True,
            created_by=admin.id,
        )
        db.add(root)
        audit_action = "workspace.root_create"
    db.flush()
    # 目录纳管后立即建立轻量后台索引：只递归读取文件夹、文件名和基础
    # 属性，不读取正文或执行 OCR，也不阻塞管理员继续使用系统。
    job = BackgroundJob(
        job_type="workspace_scan",
        payload={
            "root_id": root.id,
            "root_name": root.name,
            "trigger": "root_created",
            "automatic": True,
        },
        created_by=admin.id,
    )
    db.add(job)
    write_audit(
        db,
        admin,
        audit_action,
        "workspace_root",
        root.id,
        {"name": root.name, "automatic_scan_job_id": job.id},
        client_ip(request),
    )
    emit_event(db, "workspace.root_created", root.id, {"name": root.name})
    db.commit()
    db.refresh(root)
    background.add_task(run_scan_job, job.id, root.id)
    return root


@router.get(
    "/workspace/roots/{root_id}/folder-options",
    response_model=typing.List[WorkspaceFolderOption],
)
def list_workspace_folder_options(
    root_id: str,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[WorkspaceFolderOption]:
    root = db.get(WorkspaceRoot, root_id)
    if not root or root.source.value != "host":
        raise ProblemException(
            404,
            "WORKSPACE_ROOT_NOT_FOUND",
            "目录不存在",
            "未找到可配置的主机目录。",
        )
    directories = list(
        db.scalars(
            select(WorkspaceFile)
            .where(
                WorkspaceFile.root_id == root.id,
                WorkspaceFile.is_directory.is_(True),
                WorkspaceFile.status != "missing",
            )
            .order_by(WorkspaceFile.relative_path)
            .limit(5_000)
        ).all()
    )
    direct_counts = {
        parent_id: count
        for parent_id, count in db.execute(
            select(WorkspaceFile.parent_id, func.count())
            .where(
                WorkspaceFile.root_id == root.id,
                WorkspaceFile.is_directory.is_(False),
                WorkspaceFile.status != "missing",
            )
            .group_by(WorkspaceFile.parent_id)
        ).all()
    }
    selected = set(root.included_paths or [])
    options = [
        WorkspaceFolderOption(
            path=".",
            name=f"{root.name}（根目录）",
            parent_path=None,
            depth=0,
            direct_file_count=int(direct_counts.get(None, 0)),
            selected=root.selection_mode == "all" or "." in selected,
            in_scope=root.selection_mode == "all" or "." in selected,
        )
    ]
    by_id = {item.id: item.relative_path for item in directories}
    options.extend(
        WorkspaceFolderOption(
            path=item.relative_path,
            name=item.name,
            parent_path=by_id.get(item.parent_id),
            depth=len(Path(item.relative_path).parts),
            direct_file_count=int(direct_counts.get(item.id, 0)),
            selected=item.relative_path in selected,
            in_scope=item.in_scope,
        )
        for item in directories
    )
    return options


@router.patch(
    "/workspace/roots/{root_id}/selection",
    response_model=BackgroundJobOut,
    status_code=202,
)
def patch_workspace_selection(
    root_id: str,
    payload: WorkspaceSelectionPatch,
    request: Request,
    background: BackgroundTasks,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> BackgroundJob:
    root = db.get(WorkspaceRoot, root_id)
    if not root or root.source.value != "host":
        raise ProblemException(
            404,
            "WORKSPACE_ROOT_NOT_FOUND",
            "目录不存在",
            "未找到可配置的主机目录。",
        )
    if root.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "目录配置已更新", "请刷新后重试。")
    paths = (
        ["."]
        if payload.selection_mode == "all"
        else validate_selection_paths(db, root, payload.included_paths)
    )
    root.selection_mode = payload.selection_mode
    root.included_paths = paths
    root.version += 1
    root.scan_status = "pending"
    job = BackgroundJob(
        job_type="workspace_scan",
        payload={
            "root_id": root.id,
            "root_name": root.name,
            "trigger": "selection_changed",
            "included_paths": paths,
        },
        created_by=admin.id,
    )
    db.add(job)
    write_audit(
        db,
        admin,
        "workspace.selection_update",
        "workspace_root",
        root.id,
        {
            "selection_mode": root.selection_mode,
            "included_paths": paths,
            "version": root.version,
        },
        client_ip(request),
    )
    emit_event(
        db,
        "workspace.selection_updated",
        root.id,
        {"selection_mode": root.selection_mode, "included_count": len(paths)},
    )
    db.commit()
    db.refresh(job)
    background.add_task(run_scan_job, job.id, root.id)
    return job


@router.patch("/workspace/roots/{root_id}", response_model=WorkspaceRootOut)
def patch_workspace_root(
    root_id: str,
    payload: WorkspaceRootPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> WorkspaceRoot:
    root = db.get(WorkspaceRoot, root_id)
    if not root:
        raise ProblemException(404, "WORKSPACE_ROOT_NOT_FOUND", "目录不存在", "未找到纳管目录。")
    expected = parse_version(if_match)
    if root.version != expected:
        raise ProblemException(409, "VERSION_CONFLICT", "目录配置已更新", "请刷新后重试。")
    for field in payload.model_fields_set:
        setattr(root, field, getattr(payload, field))
    root.version += 1
    write_audit(db, admin, "workspace.root_update", "workspace_root", root.id, {"version": root.version}, client_ip(request))
    db.commit()
    db.refresh(root)
    return root


@router.delete("/workspace/roots/{root_id}", response_model=dict)
def delete_workspace_root(
    root_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    root = db.get(WorkspaceRoot, root_id)
    if not root:
        raise ProblemException(404, "WORKSPACE_ROOT_NOT_FOUND", "目录不存在", "未找到纳管目录。")
    if root.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "目录配置已更新", "请刷新后重试。")
    file_ids = select(WorkspaceFile.id).where(WorkspaceFile.root_id == root.id)
    active_transfer = db.scalar(
        select(Transfer.id)
        .where(
            Transfer.status.in_(
                [
                    TransferStatus.QUEUED,
                    TransferStatus.AWAITING_APPROVAL,
                    TransferStatus.TRANSFERRING,
                    TransferStatus.PAUSED,
                ]
            ),
            or_(
                Transfer.destination_root_id == root.id,
                Transfer.source_file_id.in_(file_ids),
            ),
        )
        .limit(1)
    )
    if active_transfer:
        raise ProblemException(
            409,
            "ROOT_HAS_ACTIVE_TRANSFERS",
            "目录仍有活动传输",
            "请等待传输完成或先取消传输，再移除共享目录。",
        )
    name = root.name
    root.enabled = False
    root.approval_note = "管理员已移除共享目录"
    root.scan_status = "disabled"
    root.version += 1
    indexed_files = list(
        db.scalars(select(WorkspaceFile).where(WorkspaceFile.root_id == root.id)).all()
    )
    for item in indexed_files:
        item.in_scope = False
        item.status = "missing"
        checkpoints = db.scalars(
            select(SemanticIndexCheckpoint).where(
                SemanticIndexCheckpoint.object_type.in_(
                    ["workspace_file", "workspace_file_content"]
                ),
                SemanticIndexCheckpoint.object_id == item.id,
            )
        ).all()
        for checkpoint in checkpoints:
            db.delete(checkpoint)
    write_audit(
        db,
        admin,
        "workspace.root_disable",
        "workspace_root",
        root.id,
        {"name": name, "preserved_history": True},
        client_ip(request),
    )
    emit_event(db, "workspace.root_deleted", root.id, {"name": name})
    db.commit()
    return {"deleted": True, "root_id": root_id, "original_files_changed": False}


@router.post("/workspace/roots/{root_id}/scan", response_model=BackgroundJobOut, status_code=202)
def start_workspace_scan(
    root_id: str,
    background: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> BackgroundJob:
    root = db.get(WorkspaceRoot, root_id)
    if not root or not root.enabled:
        raise ProblemException(404, "WORKSPACE_ROOT_NOT_FOUND", "目录不存在", "未找到可扫描目录。")
    running = db.scalar(
        select(BackgroundJob).where(
            BackgroundJob.job_type == "workspace_scan",
            BackgroundJob.status.in_(["pending", "running"]),
        )
    )
    if running:
        raise ProblemException(
            409,
            "SCAN_ALREADY_RUNNING",
            "已有目录扫描正在进行",
            "请等待当前扫描完成。",
            extra={"job_id": running.id},
        )
    job = BackgroundJob(
        job_type="workspace_scan",
        payload={"root_id": root.id, "root_name": root.name},
        created_by=admin.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background.add_task(run_scan_job, job.id, root.id)
    return job


@router.post("/workspace/roots/{root_id}/scan-now", response_model=WorkspaceScanOut)
def scan_workspace_now(
    root_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> WorkspaceScanOut:
    root = db.get(WorkspaceRoot, root_id)
    if not root or not root.enabled:
        raise ProblemException(404, "WORKSPACE_ROOT_NOT_FOUND", "目录不存在", "未找到可扫描目录。")
    result = scan_root(db, root)
    write_audit(
        db,
        admin,
        "workspace.scan",
        "workspace_root",
        root.id,
        {"files": result.files, "directories": result.directories, "changed": result.changed},
        client_ip(request),
    )
    emit_event(db, "workspace.scan_completed", root.id, result.model_dump(mode="json"))
    db.commit()
    return result


@router.get("/workspace/files", response_model=typing.List[WorkspaceFileOut])
def list_workspace_files(
    request: Request,
    root_id: str,
    parent_id: str | None = None,
    include_missing: bool = False,
    limit: int = Query(default=500, ge=1, le=2000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkspaceFileOut]:
    device_id = current_device_id(request, db)
    root = db.get(WorkspaceRoot, root_id)
    if not root or not workspace_root_permissions(db, root, user, device_id)["browse"]:
        raise ProblemException(403, "WORKSPACE_ACCESS_DENIED", "无权访问该共享目录", "请联系管理员批准目录并授权。")
    statement = select(WorkspaceFile).where(
        WorkspaceFile.root_id == root_id,
        WorkspaceFile.parent_id == parent_id,
        WorkspaceFile.in_scope.is_(True),
    )
    if not include_missing:
        statement = statement.where(WorkspaceFile.status != "missing")
    items = db.scalars(
        statement.order_by(WorkspaceFile.is_directory.desc(), WorkspaceFile.name).limit(limit)
    ).all()
    return [workspace_file_out(db, item, user, device_id) for item in items]


@router.get("/workspace/files/{file_id}", response_model=WorkspaceFileOut)
def get_workspace_file(
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceFileOut:
    device_id = current_device_id(request, db)
    item, _root = get_file(db, file_id, user, device_id)
    return workspace_file_out(db, item, user, device_id)


@router.get("/workspace/search", response_model=typing.List[WorkspaceFileOut])
def search_workspace(
    request: Request,
    keyword: str = "",
    root_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[WorkspaceFileOut]:
    device_id = current_device_id(request, db)
    return [
        workspace_file_out(db, item, user, device_id)
        for item in search_workspace_files(db, keyword, root_id, limit)
        if (
            (root := db.get(WorkspaceRoot, item.root_id)) is not None
            and workspace_root_permissions(db, root, user, device_id)["browse"]
        )
    ]


@router.get("/workspace/files/{file_id}/preview", response_model=None)
def preview_workspace_file(
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse | PlainTextResponse:
    item, root = get_file(db, file_id, user, current_device_id(request, db), "download")
    if item.is_directory:
        raise ProblemException(422, "DIRECTORY_PREVIEW_DENIED", "目录不能预览", "请选择文件。")
    if root.source.value == "device":
        text_value = item.extracted_text or item.ocr_text
        if text_value:
            return PlainTextResponse(text_value[:20_000])
        raise ProblemException(
            409,
            "REMOTE_PREVIEW_REQUIRES_TRANSFER",
            "远程文件需要先拉取",
            "请使用“复制到主机接收箱”后再预览。",
            extra={"copy_endpoint": f"/api/v1/workspace/files/{item.id}/copy-to-inbox"},
        )
    path = resolve_workspace_path(root, item.relative_path)
    if may_render_inline(item.mime_type):
        return FileResponse(
            path,
            media_type=item.mime_type,
            filename=Path(item.name).name,
            content_disposition_type="inline",
        )
    if item.mime_type in {"image/svg+xml", "image/svg"}:
        return FileResponse(
            path,
            media_type="image/svg+xml",
            filename=Path(item.name).name,
            content_disposition_type="attachment",
        )
    text_value = item.extracted_text or item.ocr_text
    return PlainTextResponse(text_value or "该文件不在系统内读取正文，请使用主机默认程序打开。")


@router.post("/files/{file_id}/open-grants", response_model=dict, status_code=201)
@router.post("/workspace/files/{file_id}/open-local", response_model=dict)
def create_local_open_link(
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    """为主机桌面签发一次性打开链接，由系统默认程序处理文件。"""

    device_id = current_device_id(request, db)
    item, root = get_file(db, file_id, user, device_id)
    if item.is_directory:
        raise ProblemException(422, "DIRECTORY_OPEN_DENIED", "目录不能按文件打开", "请在目录树中进入文件夹。")
    if root.source.value != "host" or not is_host_local_request(request):
        raise ProblemException(
            403,
            "LOCAL_OPEN_HOST_ONLY",
            "只能在主机电脑直接打开原文件",
            "协同电脑请先将文件复制到本机接收箱，再使用本机默认程序打开。",
        )
    # 签发前再次确认文件存在且仍位于已授权根目录内。
    resolve_workspace_path(root, item.relative_path)
    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=5)
    grant = FileOpenGrant(
        token_hash=hash_token(token),
        file_id=item.id,
        created_by=user.id,
        target_device_id=device_id,
        open_method="local_helper",
        status="created",
        expires_at=expires_at,
    )
    db.add(grant)
    write_audit(
        db,
        user,
        "workspace.open_local_request",
        "workspace_file",
        item.id,
        {"root_id": root.id, "name": item.name},
        client_ip(request),
    )
    db.commit()
    return {
        "grant_id": grant.id,
        "open_uri": f"partyops-file://open/{token}",
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": 300,
        "open_method": "local_helper",
        "status": "created",
        "status_url": f"/api/v1/files/open-grants/{grant.id}",
    }


def _open_grant_status(grant: FileOpenGrant) -> dict[str, object]:
    status = grant.status or "created"
    expires_at = grant.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if status == "created" and expires_at <= utcnow():
        status = "expired"
    return {
        "grant_id": grant.id,
        "status": status,
        "result_code": grant.result_code,
        "result_detail": grant.result_detail,
        "expires_at": expires_at.isoformat(),
        "redeemed_at": grant.redeemed_at.isoformat() if grant.redeemed_at else None,
        "opened_at": grant.opened_at.isoformat() if grant.opened_at else None,
        "completed_at": grant.completed_at.isoformat() if grant.completed_at else None,
    }


@router.get("/files/open-grants/{grant_id}", response_model=dict)
def get_local_open_status(
    grant_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """让发起页面准确区分等待助手、已兑换、已打开、失败和过期。"""

    grant = db.get(FileOpenGrant, grant_id)
    if not grant or (grant.created_by != user.id and user.role != UserRole.ADMIN):
        raise ProblemException(404, "OPEN_GRANT_NOT_FOUND", "文件打开任务不存在", "请重新发起打开。")
    return _open_grant_status(grant)


@router.get("/workspace/open-tokens/{token}", response_class=PlainTextResponse)
def resolve_local_open_token(
    token: str,
    request: Request,
    db: Session = Depends(get_session),
) -> PlainTextResponse:
    """仅供主机本地 URI 助手调用；授权持久化、一次性且五分钟过期。"""

    if not is_host_local_request(request):
        raise ProblemException(403, "LOCAL_OPEN_HOST_ONLY", "拒绝远程打开", "该链接只能由主机本地桌面助手使用。")
    if len(token) > 128 or not token.replace("-", "").replace("_", "").isalnum():
        raise ProblemException(400, "OPEN_TOKEN_INVALID", "打开链接无效", "请回到原始文件中心重新点击打开。")
    grant = db.scalar(select(FileOpenGrant).where(FileOpenGrant.token_hash == hash_token(token)))
    if not grant:
        raise ProblemException(404, "OPEN_GRANT_INVALID", "文件打开授权不存在", "请回到原始文件中心重新点击打开。")
    if grant.revoked_at is not None:
        raise ProblemException(410, "OPEN_GRANT_REVOKED", "文件打开授权已撤销", "文件权限可能已变化，请重新发起打开。")
    if grant.used_at is not None:
        raise ProblemException(410, "OPEN_GRANT_ALREADY_USED", "文件打开授权已使用", "一次性打开链接不能重复使用，请重新点击打开。")
    expires_at = grant.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        raise ProblemException(410, "OPEN_GRANT_EXPIRED", "文件打开授权已过期", "请回到原始文件中心重新点击打开。")
    item = db.get(WorkspaceFile, grant.file_id)
    root = db.get(WorkspaceRoot, item.root_id) if item else None
    if not item or not root or not root.enabled or not item.in_scope:
        raise ProblemException(410, "WORKSPACE_FILE_UNAVAILABLE", "文件已不可用", "请回到原始文件中心重新选择。")
    if item.is_directory or root.source.value != "host":
        raise ProblemException(403, "LOCAL_OPEN_DENIED", "文件不能直接打开", "请使用系统内提供的可用操作。")
    path = resolve_workspace_path(root, item.relative_path)
    consumed_at = utcnow()
    # 使用条件更新完成真正的一次性兑换，避免两个本机助手并发读取后都成功打开。
    consumed = db.execute(
        update(FileOpenGrant)
        .where(
            FileOpenGrant.id == grant.id,
            FileOpenGrant.used_at.is_(None),
            FileOpenGrant.revoked_at.is_(None),
            FileOpenGrant.expires_at > consumed_at,
        )
        .values(used_at=consumed_at, redeemed_at=consumed_at, status="redeemed")
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        db.expire_all()
        current = db.get(FileOpenGrant, grant.id)
        if current and current.revoked_at is not None:
            raise ProblemException(410, "OPEN_GRANT_REVOKED", "文件打开授权已撤销", "文件权限可能已变化，请重新发起打开。")
        if current and current.used_at is not None:
            raise ProblemException(410, "OPEN_GRANT_ALREADY_USED", "文件打开授权已使用", "一次性打开链接不能重复使用，请重新点击打开。")
        raise ProblemException(410, "OPEN_GRANT_EXPIRED", "文件打开授权已过期", "请回到原始文件中心重新点击打开。")
    db.commit()
    response = PlainTextResponse(str(path), media_type="text/plain; charset=utf-8")
    response.headers["X-PartyOps-Open-Grant-Id"] = grant.id
    return response


@router.post("/workspace/open-tokens/{token}/complete", response_model=dict)
def complete_local_open_token(
    token: str,
    payload: FileOpenGrantCompletion,
    request: Request,
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """本机助手使用原一次性令牌回执打开结果，不接收文件信息。"""

    if not is_host_local_request(request):
        raise ProblemException(403, "LOCAL_OPEN_HOST_ONLY", "拒绝远程回执", "文件打开结果只能由主机本机助手提交。")
    if len(token) > 128 or not token.replace("-", "").replace("_", "").isalnum():
        raise ProblemException(400, "OPEN_TOKEN_INVALID", "打开链接无效", "请重新发起打开。")
    grant = db.scalar(select(FileOpenGrant).where(FileOpenGrant.token_hash == hash_token(token)))
    if not grant:
        raise ProblemException(404, "OPEN_GRANT_INVALID", "文件打开任务不存在", "请重新发起打开。")
    if grant.used_at is None:
        raise ProblemException(409, "OPEN_GRANT_NOT_REDEEMED", "文件尚未兑换", "本机助手必须先取得文件路径。")
    if grant.completed_at is not None:
        return _open_grant_status(grant)
    completed_at = utcnow()
    successful = payload.result_code == "OPENED"
    grant.status = "completed" if successful else "failed"
    grant.opened_at = completed_at if successful else None
    grant.completed_at = completed_at
    grant.result_code = payload.result_code
    grant.result_detail = payload.detail.strip()
    db.commit()
    return _open_grant_status(grant)


@router.get("/workspace/files/{file_id}/download")
def download_workspace_file(
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> FileResponse:
    device_id = current_device_id(request, db)
    item, root = get_file(db, file_id, user, device_id, "download")
    if item.is_directory:
        raise ProblemException(422, "DIRECTORY_DOWNLOAD_DENIED", "目录不能直接下载", "请选择具体文件。")
    if root.source.value == "device":
        raise ProblemException(
            409,
            "REMOTE_DOWNLOAD_REQUIRES_TRANSFER",
            "远程文件需要先拉取",
            "请使用“复制到主机接收箱”创建可审计传输。",
            extra={"copy_endpoint": f"/api/v1/workspace/files/{item.id}/copy-to-inbox"},
        )
    path = resolve_workspace_path(root, item.relative_path)
    write_audit(
        db,
        user,
        "workspace.download",
        "workspace_file",
        item.id,
        {"root_id": root.id, "name": item.name},
        client_ip(request),
    )
    db.commit()
    return FileResponse(path, media_type=item.mime_type, filename=item.name)


@router.patch("/workspace/files/{file_id}/tags", response_model=WorkspaceFileOut)
def patch_workspace_tags(
    file_id: str,
    payload: WorkspaceFileTagsPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceFileOut:
    device_id = current_device_id(request, db)
    item, _root = get_file(db, file_id, user, device_id, "share")
    if item.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "文件标签已更新", "请刷新后重试。")
    for tag in db.scalars(
        select(WorkspaceFileTag).where(WorkspaceFileTag.file_id == item.id)
    ).all():
        db.delete(tag)
    for tag in payload.tags:
        db.add(WorkspaceFileTag(file_id=item.id, tag=tag, created_by=user.id))
    item.version += 1
    write_audit(db, user, "workspace.tags_update", "workspace_file", item.id, {"tags": payload.tags}, client_ip(request))
    emit_event(db, "workspace.file_updated", item.id, {"version": item.version})
    db.commit()
    return workspace_file_out(db, item, user, device_id)


def validate_link_target(db: Session, payload: WorkspaceFileLinkCreate, user: User) -> None:
    if payload.entity_type == "task":
        target = db.get(Task, payload.entity_id)
        if not target or not can_view_task(db, target, user):
            raise ProblemException(404, "TASK_NOT_FOUND", "事项不存在", "未找到可关联事项。")
    elif payload.entity_type == "report" and not db.get(PeriodReport, payload.entity_id):
        raise ProblemException(404, "PERIOD_REPORT_NOT_FOUND", "报告不存在", "未找到可关联报告。")
    elif payload.entity_type == "journal" and not db.get(WorkJournalEntry, payload.entity_id):
        raise ProblemException(404, "JOURNAL_NOT_FOUND", "日志不存在", "未找到可关联日志。")


@router.post("/workspace/files/{file_id}/links", response_model=WorkspaceFileOut, status_code=201)
def link_workspace_file(
    file_id: str,
    payload: WorkspaceFileLinkCreate,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceFileOut:
    device_id = current_device_id(request, db)
    item, _root = get_file(db, file_id, user, device_id, "share")
    if item.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "文件关联已更新", "请刷新后重试。")
    validate_link_target(db, payload, user)
    existing = db.scalar(
        select(WorkspaceLink).where(
            WorkspaceLink.file_id == item.id,
            WorkspaceLink.entity_type == payload.entity_type,
            WorkspaceLink.entity_id == payload.entity_id,
            WorkspaceLink.relation == payload.relation,
        )
    )
    if not existing:
        db.add(WorkspaceLink(file_id=item.id, created_by=user.id, **payload.model_dump()))
        item.version += 1
        record_system_entry(
            db,
            user,
            f"关联原始文件：{item.name}",
            "原始文件已加入业务关联。",
            task_id=payload.entity_id if payload.entity_type == "task" else None,
            file_id=item.id,
            report_id=payload.entity_id if payload.entity_type == "report" else None,
            event_code="workspace.file_linked",
            event_data={
                "entity_type": payload.entity_type,
                "relation": payload.relation,
                "filename": item.name,
            },
        )
        write_audit(db, user, "workspace.link_create", "workspace_file", item.id, payload.model_dump(), client_ip(request))
        emit_event(db, "workspace.file_updated", item.id, {"version": item.version})
        db.commit()
    return workspace_file_out(db, item, user, device_id)


@router.delete("/workspace/files/{file_id}/links/{link_id}", response_model=WorkspaceFileOut)
def unlink_workspace_file(
    file_id: str,
    link_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceFileOut:
    device_id = current_device_id(request, db)
    item, _root = get_file(db, file_id, user, device_id, "share")
    if item.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "文件关联已更新", "请刷新后重试。")
    link = db.get(WorkspaceLink, link_id)
    if not link or link.file_id != item.id or link.entity_type == "frozen":
        raise ProblemException(404, "WORKSPACE_LINK_NOT_FOUND", "文件关联不存在", "未找到可移除关联。")
    db.delete(link)
    item.version += 1
    write_audit(db, user, "workspace.link_delete", "workspace_file", item.id, {"link_id": link_id}, client_ip(request))
    emit_event(db, "workspace.file_updated", item.id, {"version": item.version})
    db.commit()
    return workspace_file_out(db, item, user, device_id)


@router.post("/workspace/files/{file_id}/freeze", response_model=WorkspaceFileOut)
def freeze_file(
    file_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> WorkspaceFileOut:
    device_id = current_device_id(request, db)
    item, root = get_file(db, file_id, user, device_id, "download")
    if item.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "原始文件已发生变化", "请刷新并确认后重新固化。")
    if root.source.value == "device":
        raise ProblemException(
            409,
            "REMOTE_FREEZE_REQUIRES_TRANSFER",
            "远程文件需要先拉取",
            "请先复制到主机接收箱，再从收件箱固化归档。",
            extra={"copy_endpoint": f"/api/v1/workspace/files/{item.id}/copy-to-inbox"},
        )
    with db_runtime.write_lock:
        blob = freeze_workspace_file(db, item, root, user)
        record_system_entry(
            db,
            user,
            f"固化归档：{item.name}",
            "文件已复制到受管附件库并完成 SHA-256 校验。",
            file_id=item.id,
            event_code="workspace.file_frozen",
            event_data={"filename": item.name, "sha256": blob.sha256},
        )
        write_audit(
            db,
            user,
            "workspace.freeze",
            "workspace_file",
            item.id,
            {"sha256": blob.sha256, "name": item.name},
            client_ip(request),
        )
        emit_event(db, "workspace.file_frozen", item.id, {"sha256": blob.sha256})
        db.commit()
    return workspace_file_out(db, item, user, device_id)
