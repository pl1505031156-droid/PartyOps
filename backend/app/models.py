"""SQLAlchemy 领域模型。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .enums import (
    AiCapability,
    ArtLevel,
    ArchiveAccessMode,
    ArchiveAttachmentStatus,
    ArchiveRecordMode,
    ArchiveRecordStatus,
    CalendarEventType,
    ContentIndexStatus,
    DeviceStatus,
    FileIndexStatus,
    FileAvailability,
    LinkType,
    MaterialStage,
    ObjectType,
    ParticipantRole,
    PeriodReportStatus,
    PeriodType,
    Priority,
    RecurrenceExceptionAction,
    RecommendationGenerator,
    RecommendationStatus,
    RecurrenceKind,
    ReportSection,
    Sensitivity,
    SeasonTheme,
    TaskStatus,
    TaskType,
    TransferDirection,
    TransferStatus,
    UpdateStatus,
    UserRole,
    WorkspaceRootSource,
    ModelPackStatus,
)


def uuid_str() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


enum_kwargs = {"native_enum": False, "validate_strings": True}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, **enum_kwargs), default=UserRole.STAFF
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    archived_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserAppearancePreference(Base):
    """用户东方皮肤偏好；全局默认仍保存在 system_settings。"""

    __tablename__ = "user_appearance_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    art_level: Mapped[ArtLevel] = mapped_column(
        Enum(ArtLevel, **enum_kwargs), default=ArtLevel.STANDARD
    )
    reduce_motion: Mapped[bool] = mapped_column(Boolean, default=False)
    theme_override: Mapped[Optional[SeasonTheme]] = mapped_column(
        Enum(SeasonTheme, **enum_kwargs), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ClientPairing(Base):
    __tablename__ = "client_pairings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_pull_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Device(Base):
    """受主机管理的协同电脑；设备证书只用于 Agent 通道。"""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, **enum_kwargs), default=DeviceStatus.OFFLINE, index=True
    )
    architecture: Mapped[str] = mapped_column(String(16), default="")
    platform: Mapped[str] = mapped_column(String(40), default="uos")
    platform_family: Mapped[str] = mapped_column(String(24), default="")
    distribution: Mapped[str] = mapped_column(String(40), default="")
    distribution_version: Mapped[str] = mapped_column(String(40), default="")
    package_format: Mapped[str] = mapped_column(String(16), default="")
    runtime_profile: Mapped[str] = mapped_column(String(32), default="")
    capabilities: Mapped[List[str]] = mapped_column(JSON, default=list)
    kernel: Mapped[str] = mapped_column(String(120), default="")
    app_version: Mapped[str] = mapped_column(String(32), default="")
    agent_version: Mapped[str] = mapped_column(String(32), default="")
    protocol_version: Mapped[int] = mapped_column(Integer, default=1)
    credential_state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    credential_rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    local_username: Mapped[str] = mapped_column(String(120), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    certificate_fingerprint: Mapped[str] = mapped_column(String(128), default="")
    certificate_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    agent_token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    allow_host_access: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_device_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_user_shares: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    disk_free_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    device_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DeviceEnrollment(Base):
    __tablename__ = "device_enrollments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeviceGrant(Base):
    __tablename__ = "device_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    root_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workspace_roots.id", ondelete="CASCADE"), nullable=True, index=True
    )
    capabilities: Mapped[List[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DeviceCommand(Base):
    __tablename__ = "device_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    command_type: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    result: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(240), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, **enum_kwargs), default=TaskType.QUICK
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, **enum_kwargs), default=TaskStatus.IN_PROGRESS, index=True
    )
    sensitivity: Mapped[Sensitivity] = mapped_column(
        Enum(Sensitivity, **enum_kwargs), default=Sensitivity.NORMAL
    )
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, **enum_kwargs), default=Priority.NORMAL
    )
    source: Mapped[str] = mapped_column(String(240), default="")
    source_kind: Mapped[str] = mapped_column(String(32), default="manual")
    category: Mapped[str] = mapped_column(String(80), default="", index=True)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    formal_due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    internal_due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    planned_start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    planned_end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    work_area: Mapped[str] = mapped_column(String(100), default="", index=True)
    annual_focus: Mapped[str] = mapped_column(String(160), default="")
    reporting_scope: Mapped[str] = mapped_column(String(160), default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    parent_task_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    template_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("task_templates.id"), nullable=True, index=True
    )
    recurrence_rule_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("recurrence_rules.id"), nullable=True, index=True
    )
    experience_notes: Mapped[str] = mapped_column(Text, default="")
    contact_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    allow_sensitive_content: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_tasks_owner_status", "owner_id", "status"),
        Index("ix_tasks_due_status", "internal_due_at", "status"),
    )


class TaskParticipant(Base):
    __tablename__ = "task_participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[ParticipantRole] = mapped_column(
        Enum(ParticipantRole, **enum_kwargs)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("task_id", "user_id", "role"),)


class TaskStep(Base):
    __tablename__ = "task_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    assignee_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("task_comments.id"))
    body: Mapped[str] = mapped_column(Text)
    mentioned_user_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskStatusEvent(Base):
    __tablename__ = "task_status_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    from_status: Mapped[Optional[TaskStatus]] = mapped_column(
        Enum(TaskStatus, **enum_kwargs)
    )
    to_status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, **enum_kwargs))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConflictDraft(Base):
    __tablename__ = "conflict_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    submitted_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MaterialItem(Base):
    __tablename__ = "material_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(200))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    not_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    not_applicable_reason: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FileBlob(Base):
    __tablename__ = "file_blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(255), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    original_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttachmentVersion(Base):
    __tablename__ = "attachment_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    material_item_id: Mapped[str] = mapped_column(
        ForeignKey("material_items.id", ondelete="CASCADE"), index=True
    )
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("file_blobs.sha256"))
    version_no: Mapped[int] = mapped_column(Integer)
    stage: Mapped[MaterialStage] = mapped_column(
        Enum(MaterialStage, **enum_kwargs), default=MaterialStage.DRAFT
    )
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    display_name: Mapped[str] = mapped_column(String(255), default="")
    client_upload_id: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, unique=True, index=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    delete_reason: Mapped[str] = mapped_column(Text, default="")
    purge_after: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_was_final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("material_item_id", "version_no"),
        Index(
            "ux_attachment_final_per_material",
            "material_item_id",
            unique=True,
            sqlite_where=text("is_final = 1"),
        ),
    )


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    category: Mapped[str] = mapped_column(String(80), default="")
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, **enum_kwargs))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LocalShareAction(Base):
    """浏览器签发、由本机共享管理器消费的一次性动作。"""

    __tablename__ = "local_share_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TemplateStep(Base):
    __tablename__ = "template_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("task_templates.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class TemplateMaterial(Base):
    __tablename__ = "template_materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("task_templates.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(48))
    name: Mapped[str] = mapped_column(String(200))
    required: Mapped[bool] = mapped_column(Boolean, default=True)


class RecurrenceRule(Base):
    __tablename__ = "recurrence_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160))
    template_id: Mapped[str] = mapped_column(ForeignKey("task_templates.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[RecurrenceKind] = mapped_column(Enum(RecurrenceKind, **enum_kwargs))
    custom_days: Mapped[Optional[int]] = mapped_column(Integer)
    internal_lead_days: Mapped[int] = mapped_column(Integer, default=2)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_task_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tasks.id"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    contact_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    schedule_config: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    paused_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    max_occurrences: Mapped[Optional[int]] = mapped_column(Integer)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(80), default="")
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(120), index=True)
    organization: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class ReminderPreference(Base):
    """个人提醒偏好；提醒仅围绕节点和风险，不记录在线行为。"""

    __tablename__ = "reminder_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    advance_days: Mapped[int] = mapped_column(Integer, default=3)
    reminder_days: Mapped[List[int]] = mapped_column(JSON, default=lambda: [7, 3, 1, 0])
    quiet_start: Mapped[str] = mapped_column(String(5), default="22:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="07:30")
    desktop_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remind_overdue: Mapped[bool] = mapped_column(Boolean, default=True)
    remind_review: Mapped[bool] = mapped_column(Boolean, default=True)
    remind_feedback: Mapped[bool] = mapped_column(Boolean, default=True)
    remind_materials: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ArchiveSnapshot(Base):
    """归档时生成的逻辑目录索引，不重复复制附件实体。"""

    __tablename__ = "archive_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    task_version: Mapped[int] = mapped_column(Integer)
    relative_index_path: Mapped[str] = mapped_column(String(255), unique=True)
    manifest: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventOutbox(Base):
    __tablename__ = "event_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="manual")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(24), default="running")
    message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    detail: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PeriodReport(Base):
    """周、月、季度和年度共用的周期报告。"""

    __tablename__ = "period_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    period_type: Mapped[PeriodType] = mapped_column(
        Enum(PeriodType, **enum_kwargs), index=True
    )
    status: Mapped[PeriodReportStatus] = mapped_column(
        Enum(PeriodReportStatus, **enum_kwargs),
        default=PeriodReportStatus.DRAFT,
        index=True,
    )
    period_key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PeriodReportItem(Base):
    __tablename__ = "period_report_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("period_reports.id", ondelete="CASCADE"), index=True
    )
    section: Mapped[ReportSection] = mapped_column(
        Enum(ReportSection, **enum_kwargs), index=True
    )
    source_type: Mapped[str] = mapped_column(String(24), default="manual")
    source_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    carried_over: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index("ix_report_item_report_section", "report_id", "section", "sort_order"),
    )


class ReportTemplate(Base):
    __tablename__ = "report_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    period_type: Mapped[PeriodType] = mapped_column(Enum(PeriodType, **enum_kwargs))
    description: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[List[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkspaceRoot(Base):
    """主机或协同设备授权的只读目录；远程目录不保存本机绝对路径。"""

    __tablename__ = "workspace_roots"
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "remote_key",
            name="uq_workspace_roots_device_remote_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160))
    absolute_path: Mapped[str] = mapped_column(Text, unique=True)
    source: Mapped[WorkspaceRootSource] = mapped_column(
        Enum(WorkspaceRootSource, **enum_kwargs), default=WorkspaceRootSource.HOST, index=True
    )
    device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    remote_key: Mapped[str] = mapped_column(String(255), default="")
    approval_status: Mapped[str] = mapped_column(String(24), default="approved", index=True)
    approval_note: Mapped[str] = mapped_column(Text, default="")
    published_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_workspace_roots_published_by_users",
        ),
        nullable=True,
        index=True,
    )
    share_scope: Mapped[str] = mapped_column(String(16), default="team", index=True)
    semantic_content_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    selection_mode: Mapped[str] = mapped_column(
        String(16), default="all", index=True
    )
    included_paths: Mapped[List[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_status: Mapped[str] = mapped_column(String(24), default="pending")
    last_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    directory_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkspaceRootMember(Base):
    """指定共享范围内的用户能力；团队共享不需要逐人写记录。"""

    __tablename__ = "workspace_root_members"
    __table_args__ = (UniqueConstraint("root_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    root_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_roots.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    can_browse: Mapped[bool] = mapped_column(Boolean, default=True)
    can_download: Mapped[bool] = mapped_column(Boolean, default=True)
    can_send: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkspaceFile(Base):
    __tablename__ = "workspace_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    root_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_roots.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255), index=True)
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    extension: Mapped[str] = mapped_column(String(32), default="", index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    modified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    device_id: Mapped[str] = mapped_column(String(32), default="")
    remote_file_key: Mapped[str] = mapped_column(String(255), default="")
    availability: Mapped[FileAvailability] = mapped_column(
        Enum(FileAvailability, **enum_kwargs),
        default=FileAvailability.ONLINE,
        index=True,
    )
    inode: Mapped[str] = mapped_column(String(32), default="")
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    status: Mapped[FileIndexStatus] = mapped_column(
        Enum(FileIndexStatus, **enum_kwargs),
        default=FileIndexStatus.PENDING,
        index=True,
    )
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    content_status: Mapped[ContentIndexStatus] = mapped_column(
        Enum(ContentIndexStatus, **enum_kwargs),
        default=ContentIndexStatus.METADATA_ONLY,
        index=True,
    )
    content_error_code: Mapped[str] = mapped_column(String(64), default="")
    detected_type: Mapped[str] = mapped_column(
        String(160), default="application/octet-stream"
    )
    archive_member_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("root_id", "relative_path"),
        Index("ix_workspace_file_parent_name", "root_id", "parent_id", "name"),
    )


class WorkspaceFileTag(Base):
    __tablename__ = "workspace_file_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="CASCADE"), index=True
    )
    tag: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("file_id", "tag"),)


class WorkspaceLink(Base):
    __tablename__ = "workspace_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    relation: Mapped[str] = mapped_column(String(40), default="reference")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("file_id", "entity_type", "entity_id", "relation"),
    )


class ArchiveTemplate(Base):
    __tablename__ = "archive_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    category: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    structure: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    material_rules: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ArchiveCategory(Base):
    """重要档案类别及其可配置字段模板。"""

    __tablename__ = "archive_categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    record_mode: Mapped[ArchiveRecordMode] = mapped_column(
        Enum(ArchiveRecordMode, **enum_kwargs), default=ArchiveRecordMode.DOCUMENT
    )
    field_schema: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    directory_pattern: Mapped[str] = mapped_column(
        String(255), default="{year}/{category}"
    )
    access_mode: Mapped[ArchiveAccessMode] = mapped_column(
        Enum(ArchiveAccessMode, **enum_kwargs),
        default=ArchiveAccessMode.ALL_USERS,
    )
    allow_device_access: Mapped[bool] = mapped_column(Boolean, default=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ArchiveRecord(Base):
    """一条重要档案目录记录；正文和扫描件均通过受管关联保存。"""

    __tablename__ = "archive_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    category_id: Mapped[str] = mapped_column(
        ForeignKey("archive_categories.id"), index=True
    )
    archive_year: Mapped[int] = mapped_column(Integer, index=True)
    sequence_no: Mapped[int] = mapped_column(Integer, default=1)
    document_no: Mapped[str] = mapped_column(String(160), default="", index=True)
    title: Mapped[str] = mapped_column(String(240), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    involved_persons: Mapped[List[str]] = mapped_column(JSON, default=list)
    source_unit: Mapped[str] = mapped_column(String(160), default="")
    document_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    person_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    person_identifier: Mapped[str] = mapped_column(String(120), default="", index=True)
    personnel_type: Mapped[str] = mapped_column(String(64), default="")
    organization: Mapped[str] = mapped_column(String(160), default="")
    assessment_result: Mapped[str] = mapped_column(String(80), default="")
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    custom_fields: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    search_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ArchiveRecordStatus] = mapped_column(
        Enum(ArchiveRecordStatus, **enum_kwargs),
        default=ArchiveRecordStatus.ACTIVE,
        index=True,
    )
    void_reason: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("category_id", "archive_year", "sequence_no"),
        Index("ix_archive_record_category_year", "category_id", "archive_year"),
        Index("ix_archive_record_person_year", "category_id", "archive_year", "person_identifier"),
    )


class ArchiveRecordRevision(Base):
    """档案每次修改前的快照，原始记录不可抹除。"""

    __tablename__ = "archive_record_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    record_id: Mapped[str] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("record_id", "revision_no"),
        Index("ix_archive_revision_record_time", "record_id", "created_at"),
    )


class ArchiveAttachment(Base):
    """档案扫描件与哈希文件实体的关系；文件实体可被多个档案复用。"""

    __tablename__ = "archive_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    record_id: Mapped[str] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), index=True
    )
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("file_blobs.sha256"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[ArchiveAttachmentStatus] = mapped_column(
        Enum(ArchiveAttachmentStatus, **enum_kwargs),
        default=ArchiveAttachmentStatus.PENDING_OCR,
        index=True,
    )
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    client_upload_id: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, unique=True, index=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    delete_reason: Mapped[str] = mapped_column(Text, default="")
    purge_after: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("record_id", "version_no"),
        Index("ix_archive_attachment_record_status", "record_id", "status"),
    )


class ArchiveAccessGrant(Base):
    """按档案类别向人员或设备授予访问能力。"""

    __tablename__ = "archive_access_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    category_id: Mapped[str] = mapped_column(
        ForeignKey("archive_categories.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True
    )
    can_view: Mapped[bool] = mapped_column(Boolean, default=True)
    can_download: Mapped[bool] = mapped_column(Boolean, default=True)
    can_contribute: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("category_id", "user_id", "device_id"),
    )


class ArchiveLink(Base):
    """档案与任务、报告、日志和知识条目的通用关联。"""

    __tablename__ = "archive_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    record_id: Mapped[str] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    relation: Mapped[str] = mapped_column(String(40), default="reference")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("record_id", "entity_type", "entity_id", "relation"),
    )


class WorkJournalEntry(Base):
    __tablename__ = "work_journal_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    entry_type: Mapped[str] = mapped_column(String(20), default="manual", index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text, default="")
    event_code: Mapped[str] = mapped_column(String(64), default="", index=True)
    event_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id"), index=True)
    file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("workspace_files.id"), index=True)
    report_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("period_reports.id"), index=True
    )
    immutable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkJournalRevision(Base):
    """人工工作日志修订前快照；系统事件本身不可修改。"""

    __tablename__ = "work_journal_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("work_journal_entries.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("entry_id", "revision_no"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    notification_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text, default="")
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AIProviderConfig(Base):
    __tablename__ = "ai_provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(120), default="单位模型服务")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    model: Mapped[str] = mapped_column(String(160), default="")
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trusted_intranet: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_test_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(40), default="not_tested")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIPolicy(Base):
    __tablename__ = "ai_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(120), default="默认只读策略")
    allowed_root_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    allowed_task_categories: Mapped[List[str]] = mapped_column(JSON, default=list)
    allowed_file_types: Mapped[List[str]] = mapped_column(JSON, default=list)
    capabilities: Mapped[List[str]] = mapped_column(
        JSON,
        default=lambda: [
            AiCapability.SEARCH.value,
            AiCapability.SUMMARIZE.value,
        ],
    )
    allow_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIDraft(Base):
    __tablename__ = "ai_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    capability: Mapped[AiCapability] = mapped_column(Enum(AiCapability, **enum_kwargs))
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIInvocation(Base):
    __tablename__ = "ai_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("ai_provider_configs.id"), nullable=True
    )
    capability: Mapped[AiCapability] = mapped_column(Enum(AiCapability, **enum_kwargs))
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    source_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), index=True)
    error_code: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AIModelPack(Base):
    """签名本地模型包；浏览器永远看不到真实安装路径。"""

    __tablename__ = "ai_model_packs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(32), index=True)
    model_id: Mapped[str] = mapped_column(String(160), index=True)
    architecture: Mapped[str] = mapped_column(String(16), default="universal")
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    install_key: Mapped[str] = mapped_column(String(80), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    manifest: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities: Mapped[List[str]] = mapped_column(JSON, default=list)
    min_runtime_version: Mapped[str] = mapped_column(String(32), default="1.4.1")
    estimated_memory_mb: Mapped[int] = mapped_column(Integer, default=0)
    model_source: Mapped[str] = mapped_column(String(500), default="")
    license_name: Mapped[str] = mapped_column(String(80), default="")
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ModelPackStatus] = mapped_column(
        Enum(ModelPackStatus, **enum_kwargs),
        default=ModelPackStatus.VERIFYING,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AIModelActivation(Base):
    """按能力选择活动模型；兼容旧组合模型包。"""

    __tablename__ = "ai_model_activations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    capability: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    model_pack_id: Mapped[str] = mapped_column(
        ForeignKey("ai_model_packs.id", ondelete="CASCADE"), index=True
    )
    activated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemanticIndexCheckpoint(Base):
    __tablename__ = "semantic_index_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    object_type: Mapped[str] = mapped_column(String(40), index=True)
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    object_version: Mapped[int] = mapped_column(Integer, default=1)
    model_pack_id: Mapped[str] = mapped_column(
        ForeignKey("ai_model_packs.id", ondelete="CASCADE"), index=True
    )
    embedding_blob: Mapped[Optional[bytes]] = mapped_column(nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), default="")
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("object_type", "object_id", "model_pack_id"),
    )


class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    generator: Mapped[RecommendationGenerator] = mapped_column(
        Enum(RecommendationGenerator, **enum_kwargs), index=True
    )
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus, **enum_kwargs),
        default=RecommendationStatus.PENDING,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(240))
    reason: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    object_type: Mapped[str] = mapped_column(String(40), index=True)
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    object_version: Mapped[int] = mapped_column(Integer, default=1)
    route: Mapped[str] = mapped_column(String(500), default="")
    sources: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UpgradeRecord(Base):
    __tablename__ = "upgrade_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    from_version: Mapped[str] = mapped_column(String(32))
    to_version: Mapped[str] = mapped_column(String(32))
    schema_revision: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), index=True)
    backup_filename: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    direction: Mapped[TransferDirection] = mapped_column(
        Enum(TransferDirection, **enum_kwargs), index=True
    )
    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, **enum_kwargs), default=TransferStatus.QUEUED, index=True
    )
    source_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id"), nullable=True, index=True
    )
    destination_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id"), nullable=True, index=True
    )
    source_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workspace_files.id"), nullable=True, index=True
    )
    destination_root_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workspace_roots.id"), nullable=True, index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(Text, default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    chunk_size: Mapped[int] = mapped_column(Integer, default=8 * 1024 * 1024)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    approved_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approval_note: Mapped[str] = mapped_column(Text, default="")
    transit_path: Mapped[str] = mapped_column(String(255), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str] = mapped_column(String(80), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    handled_by: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", name="fk_transfers_handled_by_users"),
        nullable=True,
    )
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    linked_entity_type: Mapped[str] = mapped_column(String(40), default="")
    linked_entity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    delivery_mode: Mapped[str] = mapped_column(String(24), default="managed_inbox")
    bundle_mode: Mapped[str] = mapped_column(String(24), default="single")
    item_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    result_name: Mapped[str] = mapped_column(String(255), default="")
    result_sha256: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TransferChunk(Base):
    __tablename__ = "transfer_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    transfer_id: Mapped[str] = mapped_column(
        ForeignKey("transfers.id", ondelete="CASCADE"), index=True
    )
    chunk_no: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("transfer_id", "chunk_no"),)


class SavedView(Base):
    __tablename__ = "saved_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160))
    view_type: Mapped[str] = mapped_column(String(40), default="tasks")
    filters: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    columns: Mapped[List[str]] = mapped_column(JSON, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TopicSpace(Base):
    __tablename__ = "topic_spaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    task_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    file_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    journal_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    contact_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160))
    trigger: Mapped[str] = mapped_column(String(40))
    conditions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    actions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkCalendarEntry(Base):
    __tablename__ = "work_calendar_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    date_key: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24), default="holiday")
    is_workday: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("date_key", "kind"),)


class PartyDevelopmentProfile(Base):
    """单位党员发展补充材料模板；不得承载或覆盖国家法定期限。"""

    __tablename__ = "party_development_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    source_label: Mapped[str] = mapped_column(String(255), default="本单位补充")
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PartyDevelopmentMaterial(Base):
    """单位补充材料条目，只影响导出清单和提示，不参与日期计算。"""

    __tablename__ = "party_development_materials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("party_development_profiles.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(200))
    responsible_party: Mapped[str] = mapped_column(String(120), default="")
    guidance: Mapped[str] = mapped_column(Text, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("profile_id", "phase", "name", name="uq_party_development_material"),
    )


class DocumentComparison(Base):
    __tablename__ = "document_comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    left_file_id: Mapped[str] = mapped_column(ForeignKey("workspace_files.id"), index=True)
    right_file_id: Mapped[str] = mapped_column(ForeignKey("workspace_files.id"), index=True)
    comparison_type: Mapped[str] = mapped_column(String(24), default="text")
    result: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    algorithm: Mapped[str] = mapped_column(String(24), default="sha256", index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    file_ids: Mapped[List[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HandoverExport(Base):
    __tablename__ = "handover_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160))
    scope: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UpdatePackage(Base):
    __tablename__ = "update_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    version: Mapped[str] = mapped_column(String(32), index=True)
    min_version: Mapped[str] = mapped_column(String(32), default="")
    schema_revision: Mapped[str] = mapped_column(String(32), default="")
    manifest: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[UpdateStatus] = mapped_column(
        Enum(UpdateStatus, **enum_kwargs), default=UpdateStatus.UPLOADED, index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UpdateRun(Base):
    __tablename__ = "update_runs"
    __table_args__ = (
        Index(
            "uq_update_runs_one_active_host",
            "status",
            unique=True,
            # SQLAlchemy Enum 默认持久化枚举成员名（APPLYING），不是 API 字符串值。
            sqlite_where=text("target_device_id IS NULL AND status = 'APPLYING'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    package_id: Mapped[str] = mapped_column(ForeignKey("update_packages.id"), index=True)
    target_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id"), nullable=True, index=True
    )
    status: Mapped[UpdateStatus] = mapped_column(
        Enum(UpdateStatus, **enum_kwargs), default=UpdateStatus.UPLOADED, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ReleaseHistory(Base):
    """主机实际安装版本的追加式发布记录。"""

    __tablename__ = "release_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    schema_revision: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(160), default="")
    release_notes: Mapped[List[str]] = mapped_column(JSON, default=list)
    package_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("update_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="installed", index=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ObjectLink(Base):
    """跨领域对象关联；泛型标识避免把业务数据复制进专题或报告。"""

    __tablename__ = "object_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_type: Mapped[ObjectType] = mapped_column(
        Enum(ObjectType, **enum_kwargs), index=True
    )
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[ObjectType] = mapped_column(
        Enum(ObjectType, **enum_kwargs), index=True
    )
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    link_type: Mapped[LinkType] = mapped_column(
        Enum(LinkType, **enum_kwargs), default=LinkType.RELATES_TO, index=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "link_type",
            name="uq_object_link",
        ),
        Index("ix_object_links_source", "source_type", "source_id"),
        Index("ix_object_links_target", "target_type", "target_id"),
    )


class ActivityEvent(Base):
    """统一的追加式业务时间线。"""

    __tablename__ = "activity_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    object_type: Mapped[ObjectType] = mapped_column(
        Enum(ObjectType, **enum_kwargs), index=True
    )
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    event_code: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    happened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    event_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(160), unique=True, nullable=True
    )

    __table_args__ = (
        Index("ix_activity_object_time", "object_type", "object_id", "happened_at"),
    )


class CalendarPreference(Base):
    __tablename__ = "calendar_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    default_view: Mapped[str] = mapped_column(String(16), default="week")
    week_starts_on: Mapped[int] = mapped_column(Integer, default=1)
    visible_event_types: Mapped[List[str]] = mapped_column(
        JSON, default=lambda: [item.value for item in CalendarEventType]
    )
    compact_weekends: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RecurrenceException(Base):
    __tablename__ = "recurrence_exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("recurrence_rules.id", ondelete="CASCADE"), index=True
    )
    occurrence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    action: Mapped[RecurrenceExceptionAction] = mapped_column(
        Enum(RecurrenceExceptionAction, **enum_kwargs)
    )
    rescheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("rule_id", "occurrence_at"),)


class ProjectionCheckpoint(Base):
    __tablename__ = "projection_checkpoints"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="idle", index=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OnboardingProgress(Base):
    __tablename__ = "onboarding_progress"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    completed_steps: Mapped[List[str]] = mapped_column(JSON, default=list)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ClientDeviceCredential(Base):
    """协同终端设备令牌版本；数据库只保存哈希，明文仅在签发响应中出现一次。"""

    __tablename__ = "client_device_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    protocol_version: Mapped[int] = mapped_column(Integer, default=2)
    state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    replaced_by_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FileOpenGrant(Base):
    """文件打开的一次性授权；服务重启不会丢失已用、吊销或过期状态。"""

    __tablename__ = "file_open_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_files.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    open_method: Mapped[str] = mapped_column(String(24), default="local_helper")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowTemplate(Base):
    """可复用的业务流程模板；步骤定义包含负责人角色、计划偏移与交付物。"""

    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    system_key: Mapped[str] = mapped_column(String(80), default="", index=True)
    business_type: Mapped[str] = mapped_column(String(48), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    recurrence: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    template_version: Mapped[str] = mapped_column(String(32), default="1.0")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BusinessMeeting(Base):
    __tablename__ = "business_meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    meeting_type: Mapped[str] = mapped_column(String(48), index=True)
    organization: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(String(240))
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    workflow_template_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_templates.id"), nullable=True, index=True
    )
    host_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    recorder_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    venue: Mapped[str] = mapped_column(String(240), default="")
    study_plan_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("study_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    business_data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    recurrence_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        Index(
            "uq_business_meetings_recurrence_key",
            "recurrence_key",
            unique=True,
            sqlite_where=text("recurrence_key <> ''"),
        ),
    )


class MeetingTopic(Base):
    __tablename__ = "meeting_topics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("business_meetings.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    review_result: Mapped[str] = mapped_column(Text, default="")
    amount_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    amount_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingAttendee(Base):
    """会议人员及其出席、表决角色；不从普通用户列表推断实际出席。"""

    __tablename__ = "meeting_attendees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("business_meetings.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(48), default="member", index=True)
    attendance_status: Mapped[str] = mapped_column(
        String(24), default="expected", index=True
    )
    voting_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (UniqueConstraint("meeting_id", "user_id", "display_name"),)


class MeetingAction(Base):
    """会后决议落实项；可关联 PartyOps 事项但不重复保存事项正文。"""

    __tablename__ = "meeting_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("business_meetings.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240))
    responsible_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StudyPlan(Base):
    """党委（党组）理论学习中心组年度计划。"""

    __tablename__ = "study_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    organization: Mapped[str] = mapped_column(String(160), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(240))
    group_leader_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    secretary_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (UniqueConstraint("organization", "year"),)


class StudyPlanTopic(Base):
    __tablename__ = "study_plan_topics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("study_plans.id", ondelete="CASCADE"), index=True
    )
    quarter: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(240))
    learning_materials: Mapped[List[str]] = mapped_column(JSON, default=list)
    research_topic: Mapped[str] = mapped_column(Text, default="")
    conversion_goal: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BusinessDocument(Base):
    __tablename__ = "business_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    meeting_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("business_meetings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_step_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("task_steps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_type: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BusinessDocumentRevision(Base):
    __tablename__ = "business_document_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("business_documents.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("document_id", "revision_no"),)


class PartyDevelopmentCase(Base):
    """党员发展档案；计算计划与实际发生日期严格分栏。"""

    __tablename__ = "party_development_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    party_committee: Mapped[str] = mapped_column(String(160), index=True)
    party_branch: Mapped[str] = mapped_column(String(160), index=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    gender: Mapped[str] = mapped_column(String(16), default="")
    ethnicity: Mapped[str] = mapped_column(String(40), default="")
    birth_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    education: Mapped[str] = mapped_column(String(80), default="")
    application_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    activist_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    training_contacts: Mapped[List[str]] = mapped_column(JSON, default=list)
    introducers: Mapped[List[str]] = mapped_column(JSON, default=list)
    development_object_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    probationary_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(String(48), default="application", index=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    rule_version: Mapped[str] = mapped_column(String(32), default="2026.05")
    planning_profile_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("party_development_plan_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    planning_profile_snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PartyDevelopmentMilestone(Base):
    __tablename__ = "party_development_milestones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("party_development_cases.id", ondelete="CASCADE"), index=True
    )
    milestone_type: Mapped[str] = mapped_column(String(48), index=True)
    actual_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    legal_earliest_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    legal_deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    planned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    adjusted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rule_version: Mapped[str] = mapped_column(String(32), default="2026.05")
    legal_basis: Mapped[str] = mapped_column(Text, default="")
    planning_basis: Mapped[str] = mapped_column(Text, default="")
    plan_kind: Mapped[str] = mapped_column(String(24), default="reference")
    reminder_days: Mapped[List[int]] = mapped_column(JSON, default=lambda: [60, 30, 14, 7, 1, 0])
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("case_id", "milestone_type"),)


class PartyDevelopmentPlanProfile(Base):
    """参考计划口径。档案保存快照，后续模板升级不静默改写历史。"""

    __tablename__ = "party_development_plan_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    system_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class NotificationSource(Base):
    """提醒与业务规则的稳定关联，供更新、撤销和审计使用。"""

    __tablename__ = "notification_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_type: Mapped[str] = mapped_column(String(48), index=True)
    window_key: Mapped[str] = mapped_column(String(48), default="")
    source_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
