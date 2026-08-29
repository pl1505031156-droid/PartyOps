"""Pydantic 请求与响应契约。"""

from __future__ import annotations

from datetime import date as dt_date
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_serializer, field_validator

from .enums import (
    AiCapability,
    ArchiveAccessMode,
    ArchiveAttachmentStatus,
    ArchiveRecordMode,
    ArchiveRecordStatus,
    ArtLevel,
    CalendarEventType,
    ContentIndexStatus,
    FileIndexStatus,
    LinkType,
    MaterialStage,
    ModelPackStatus,
    ObjectType,
    ParticipantRole,
    PeriodReportStatus,
    PeriodType,
    Priority,
    RecommendationGenerator,
    RecommendationStatus,
    RecurrenceExceptionAction,
    RecurrenceKind,
    ReportSection,
    SeasonTheme,
    Sensitivity,
    TaskStatus,
    TaskType,
    UserRole,
)
from .time_utils import BEIJING_TIMEZONE, UTC, beijing_iso


def _normalize_request_datetime(value: Any, field_name: str) -> Any:
    """把用户输入的墙上时间转换成 UTC，避免 18:00 被当成 UTC 产生次日偏移。

    仅处理明确表示瞬时点的 *_at 字段；birth_date/document_date 等业务日期
    不在此处加时区，防止日期型字段被错误挪动。
    """

    if not field_name.endswith("_at"):
        return value
    parsed: datetime | None = None
    # ORM/SQLite 返回的无时区 datetime 是既有内部 UTC 约定，不能在响应
    # 校验阶段再次当成北京时间平移。浏览器 JSON 输入必然是字符串，因此
    # 只在字符串请求边界解释“无时区墙上时间”；datetime 交给序列化器处理。
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return value
    if parsed is None:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TIMEZONE)
    return parsed.astimezone(UTC)


def serialize_api_datetime(value: datetime) -> str:
    """所有接口时间统一输出带 ``+08:00`` 的 RFC 3339 北京时间。"""

    return beijing_iso(value)


class BaseModel(PydanticBaseModel):
    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def normalize_request_datetimes(cls, value: Any, info) -> Any:
        return _normalize_request_datetime(value, str(info.field_name))

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetime_fields(self, value: Any) -> Any:
        return serialize_api_datetime(value) if isinstance(value, datetime) else value


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class HealthOut(BaseModel):
    status: str
    app_version: str
    mode: str
    host: str
    port: int
    service_url: str
    sqlite: dict[str, Any]


class BootstrapStatus(BaseModel):
    configured: bool
    mode: str
    app_name: str
    host: str
    port: int
    service_url: str
    lan_candidates: list[str] = Field(default_factory=list)


class BootstrapHostRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.STAFF


class UserOut(ORMModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    active: bool
    archived_at: datetime | None = None
    version: int
    created_at: datetime


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=80)
    role: UserRole | None = None
    active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class StepInput(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    assignee_id: str | None = None
    due_at: datetime | None = None


class MaterialInput(BaseModel):
    category: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=200)
    required: bool = False

    @field_validator("category", "name")
    @classmethod
    def normalize_material_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("材料类别和名称不能为空")
        return normalized


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=20_000)
    task_type: TaskType = TaskType.QUICK
    sensitivity: Sensitivity = Sensitivity.NORMAL
    priority: Priority = Priority.NORMAL
    source: str = Field(default="", max_length=240)
    source_kind: str = Field(default="manual", max_length=32)
    category: str = Field(default="", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=20)
    formal_due_at: datetime | None = None
    internal_due_at: datetime | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    work_area: str = Field(default="", max_length=100)
    annual_focus: str = Field(default="", max_length=160)
    reporting_scope: str = Field(default="", max_length=160)
    owner_id: str
    reviewer_id: str | None = None
    parent_task_id: str | None = None
    template_id: str | None = None
    recurrence_rule_id: str | None = None
    experience_notes: str = Field(default="", max_length=5_000)
    contact_ids: list[str] = Field(default_factory=list, max_length=50)
    collaborator_ids: list[str] = Field(default_factory=list)
    steps: list[StepInput] = Field(default_factory=list)
    materials: list[MaterialInput] = Field(default_factory=list)
    start_in_breakdown: bool = False

    @field_validator("description")
    @classmethod
    def restricted_description_is_confirmed_elsewhere(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip()[:40] for item in values if item.strip()))


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=20_000)
    task_type: TaskType | None = None
    sensitivity: Sensitivity | None = None
    priority: Priority | None = None
    source: str | None = Field(default=None, max_length=240)
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=20)
    formal_due_at: datetime | None = None
    internal_due_at: datetime | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    work_area: str | None = Field(default=None, max_length=100)
    annual_focus: str | None = Field(default=None, max_length=160)
    reporting_scope: str | None = Field(default=None, max_length=160)
    owner_id: str | None = None
    reviewer_id: str | None = None
    allow_sensitive_content: bool | None = None
    experience_notes: str | None = Field(default=None, max_length=5_000)
    contact_ids: list[str] | None = Field(default=None, max_length=50)

    @field_validator("tags")
    @classmethod
    def normalize_optional_tags(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(item.strip()[:40] for item in values if item.strip()))


class ParticipantOut(ORMModel):
    id: str
    user_id: str
    role: ParticipantRole


class ParticipantAdd(BaseModel):
    user_id: str
    role: ParticipantRole = ParticipantRole.COLLABORATOR


class StepCreate(StepInput):
    pass


class StepOut(ORMModel):
    id: str
    title: str
    assignee_id: str | None
    due_at: datetime | None
    done: bool
    sort_order: int
    version: int


class MaterialVersionOut(BaseModel):
    id: str
    version_no: int
    stage: MaterialStage
    is_final: bool
    original_name: str
    note: str
    size_bytes: int
    mime_type: str
    uploaded_by: str
    created_at: datetime
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    delete_reason: str = ""
    purge_after: datetime | None = None


class MaterialOut(ORMModel):
    id: str
    category: str
    name: str
    required: bool
    not_applicable: bool
    not_applicable_reason: str
    version: int
    versions: list[MaterialVersionOut] = Field(default_factory=list)
    deleted_versions: list[MaterialVersionOut] = Field(default_factory=list)
    complete: bool = False


class CommentOut(ORMModel):
    id: str
    author_id: str
    parent_id: str | None
    body: str
    mentioned_user_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class StatusEventOut(ORMModel):
    id: str
    actor_id: str
    from_status: TaskStatus | None
    to_status: TaskStatus
    note: str
    created_at: datetime


class SubtaskSummaryOut(ORMModel):
    id: str
    title: str
    status: TaskStatus
    owner_id: str
    formal_due_at: datetime | None
    internal_due_at: datetime | None
    version: int
    missing_required_materials: int = 0


class TaskOut(ORMModel):
    id: str
    title: str
    description: str
    task_type: TaskType
    status: TaskStatus
    sensitivity: Sensitivity
    priority: Priority
    source: str
    source_kind: str
    category: str
    tags: list[str]
    formal_due_at: datetime | None
    internal_due_at: datetime | None
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    work_area: str
    annual_focus: str
    reporting_scope: str
    owner_id: str
    reviewer_id: str | None
    parent_task_id: str | None
    template_id: str | None
    recurrence_rule_id: str | None
    experience_notes: str
    contact_ids: list[str]
    allow_sensitive_content: bool
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    archived_at: datetime | None
    participants: list[ParticipantOut] = Field(default_factory=list)
    steps: list[StepOut] = Field(default_factory=list)
    materials: list[MaterialOut] = Field(default_factory=list)
    comments: list[CommentOut] = Field(default_factory=list)
    events: list[StatusEventOut] = Field(default_factory=list)
    subtasks: list[SubtaskSummaryOut] = Field(default_factory=list)
    missing_required_materials: int = 0


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
    page: int
    page_size: int


class TaskAction(BaseModel):
    action: str
    note: str = Field(default="", max_length=2_000)


class StepPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    assignee_id: str | None = None
    due_at: datetime | None = None
    done: bool | None = None
    version: int | None = None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5_000)
    parent_id: str | None = None
    mentioned_user_ids: list[str] = Field(default_factory=list, max_length=50)


class MaterialCreate(MaterialInput):
    pass


class MaterialCategoryOut(BaseModel):
    value: str
    label: str
    custom: bool = False


class MaterialPatch(BaseModel):
    not_applicable: bool
    reason: str = Field(default="", max_length=1_000)
    version: int | None = None


class AttachmentRollbackRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1_000)


class AttachmentDeleteRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=2_000)


class DashboardBucket(BaseModel):
    key: str
    label: str
    count: int
    items: list[TaskOut]


class DashboardOut(BaseModel):
    buckets: list[DashboardBucket]
    updated_at: datetime
    this_week_completed: list[TaskOut] = Field(default_factory=list)
    next_week_planned: list[TaskOut] = Field(default_factory=list)
    carry_over: list[TaskOut] = Field(default_factory=list)
    unread_notifications: int = 0


class IntakeCandidate(BaseModel):
    title: str
    formal_due_at: datetime | None
    requirements: list[str]
    extracted_text: str
    source_kind: str
    warnings: list[str]
    source_filename: str = ""
    parser_label: str = "粘贴文本"


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(default="", max_length=80)
    task_type: TaskType = TaskType.STANDARD
    description: str = Field(default="", max_length=5_000)
    steps: list[str] = Field(default_factory=list)
    materials: list[MaterialInput] = Field(default_factory=list)


class TemplateOut(ORMModel):
    id: str
    name: str
    category: str
    task_type: TaskType
    description: str
    active: bool
    version: int
    steps: list[str] = Field(default_factory=list)
    materials: list[MaterialInput] = Field(default_factory=list)


class TemplateInstantiate(BaseModel):
    owner_id: str
    title: str | None = None
    formal_due_at: datetime | None = None


class TemplateUpdate(TemplateCreate):
    active: bool = True


class RecurrenceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    template_id: str
    owner_id: str
    kind: RecurrenceKind
    custom_days: int | None = Field(default=None, ge=1, le=3650)
    internal_lead_days: int = Field(default=2, ge=0, le=365)
    next_run_at: datetime
    notes: str = Field(default="", max_length=5_000)
    contact_ids: list[str] = Field(default_factory=list, max_length=50)
    schedule_config: dict[str, Any] = Field(default_factory=dict)
    paused_until: datetime | None = None
    end_at: datetime | None = None
    max_occurrences: int | None = Field(default=None, ge=1, le=10_000)


class RecurrenceOut(ORMModel):
    id: str
    name: str
    template_id: str
    owner_id: str
    kind: RecurrenceKind
    custom_days: int | None
    internal_lead_days: int
    next_run_at: datetime
    active: bool
    last_run_at: datetime | None
    last_task_id: str | None
    notes: str
    contact_ids: list[str]
    schedule_config: dict[str, Any]
    paused_until: datetime | None
    end_at: datetime | None
    max_occurrences: int | None
    occurrence_count: int
    last_error: str
    version: int


class RecurrenceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    owner_id: str | None = None
    kind: RecurrenceKind | None = None
    custom_days: int | None = Field(default=None, ge=1, le=3650)
    internal_lead_days: int | None = Field(default=None, ge=0, le=365)
    next_run_at: datetime | None = None
    active: bool | None = None
    notes: str | None = Field(default=None, max_length=5_000)
    contact_ids: list[str] | None = Field(default=None, max_length=50)
    schedule_config: dict[str, Any] | None = None
    paused_until: datetime | None = None
    end_at: datetime | None = None
    max_occurrences: int | None = Field(default=None, ge=1, le=10_000)


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=80)
    body: str = Field(min_length=1, max_length=30_000)


class KnowledgeOut(ORMModel):
    id: str
    title: str
    category: str
    body: str
    version: int
    updated_by: str
    updated_at: datetime


class KnowledgeUpdate(KnowledgeCreate):
    pass


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    organization: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=1_000)


class ContactOut(ORMModel):
    id: str
    name: str
    organization: str
    phone: str
    note: str
    version: int


class ContactUpdate(ContactCreate):
    pass


class BackupOut(ORMModel):
    id: str
    filename: str
    kind: str
    size_bytes: int
    sha256: str
    status: str
    message: str
    deleted_at: datetime | None
    delete_reason: str
    purge_after: datetime | None
    version: int
    created_at: datetime
    completed_at: datetime | None


class BackupLifecycleRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)


class PairingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PairingOut(BaseModel):
    id: str
    name: str
    token: str
    host_url: str
    expires_at: datetime
    config: dict[str, Any]


class PairingSummaryOut(ORMModel):
    id: str
    name: str
    active: bool
    last_pull_at: datetime | None
    created_at: datetime
    expires_at: datetime


class ReminderPreferenceOut(ORMModel):
    user_id: str
    enabled: bool
    advance_days: int
    reminder_days: list[int]
    quiet_start: str
    quiet_end: str
    desktop_enabled: bool
    remind_overdue: bool
    remind_review: bool
    remind_feedback: bool
    remind_materials: bool
    version: int
    updated_at: datetime


class ReminderPreferencePatch(BaseModel):
    enabled: bool | None = None
    advance_days: int | None = Field(default=None, ge=0, le=30)
    reminder_days: list[int] | None = Field(default=None, max_length=10)
    quiet_start: str | None = Field(
        default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    quiet_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    desktop_enabled: bool | None = None
    remind_overdue: bool | None = None
    remind_review: bool | None = None
    remind_feedback: bool | None = None
    remind_materials: bool | None = None

    @field_validator("reminder_days")
    @classmethod
    def normalize_reminder_days(cls, values: list[int] | None) -> list[int] | None:
        if values is None:
            return None
        if any(value < 0 or value > 30 for value in values):
            raise ValueError("提醒天数必须在 0—30 天之间")
        return sorted(set(values), reverse=True)


class ArchiveSnapshotOut(ORMModel):
    id: str
    task_id: str
    task_version: int
    manifest: dict[str, Any]
    created_at: datetime


class AuditOut(ORMModel):
    id: int
    actor_id: str | None
    action: str
    entity_type: str
    entity_id: str | None
    detail: dict[str, Any]
    ip_address: str
    created_at: datetime


class PeriodReportItemCreate(BaseModel):
    section: ReportSection
    source_type: str = Field(default="manual", pattern=r"^(manual|task|file|journal)$")
    source_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=20_000)
    sort_order: int = Field(default=0, ge=0, le=100_000)
    carried_over: bool = False


class PeriodReportItemPatch(BaseModel):
    section: ReportSection | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=20_000)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    carried_over: bool | None = None


class PeriodReportItemOut(ORMModel):
    id: str
    report_id: str
    section: ReportSection
    source_type: str
    source_id: str | None
    title: str
    content: str
    sort_order: int
    carried_over: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class PeriodReportCreate(BaseModel):
    period_type: PeriodType
    anchor_at: datetime | None = None
    title: str | None = Field(default=None, max_length=240)
    summary: str = Field(default="", max_length=30_000)
    auto_fill: bool = True
    template_id: str | None = None


class PeriodReportPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, max_length=30_000)


class PeriodReportAction(BaseModel):
    action: str = Field(pattern=r"^(publish|lock|reopen)$")
    note: str = Field(default="", max_length=2_000)


class PeriodReportOut(ORMModel):
    id: str
    period_type: PeriodType
    status: PeriodReportStatus
    period_key: str
    title: str
    start_at: datetime
    end_at: datetime
    summary: str
    snapshot: dict[str, Any]
    version: int
    created_by: str
    updated_by: str
    published_at: datetime | None
    locked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[PeriodReportItemOut] = Field(default_factory=list)


class ReportTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    period_type: PeriodType = PeriodType.WEEK
    description: str = Field(default="", max_length=5_000)
    sections: list[ReportSection] = Field(default_factory=lambda: list(ReportSection))


class ReportTemplateOut(ORMModel):
    id: str
    name: str
    period_type: PeriodType
    description: str
    sections: list[str]
    active: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class ReportTemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    sections: list[ReportSection] | None = None
    active: bool | None = None


class WorkJournalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(default="", max_length=30_000)
    occurred_at: datetime | None = None
    task_id: str | None = None
    file_id: str | None = None
    report_id: str | None = None


class WorkJournalPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=30_000)
    occurred_at: datetime | None = None
    task_id: str | None = None
    file_id: str | None = None
    report_id: str | None = None
    change_note: str = Field(default="", max_length=2_000)


class WorkJournalOut(ORMModel):
    id: str
    entry_type: str
    title: str
    content: str
    event_code: str = ""
    event_data: dict[str, Any] = Field(default_factory=dict)
    action_label: str = ""
    actor_name: str = ""
    actor_role_label: str = ""
    task_title: str = ""
    from_status: str = ""
    to_status: str = ""
    material_stage: str = ""
    occurred_at: datetime
    task_id: str | None
    file_id: str | None
    report_id: str | None
    immutable: bool
    archived_at: datetime | None = None
    archived_by: str | None = None
    archive_reason: str = ""
    created_by: str
    version: int
    created_at: datetime
    updated_at: datetime


class WorkJournalRevisionOut(ORMModel):
    id: str
    entry_id: str
    revision_no: int
    snapshot: dict[str, Any]
    change_note: str
    created_by: str
    created_at: datetime


class WorkspaceRootCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    absolute_path: str = Field(min_length=1, max_length=4_096)
    # 旧版 API 调用未传该字段时保持“全部接入”；新版界面明确传 selected，
    # 先发现目录再让管理员选择，兼容已部署的自动化脚本。
    selection_mode: str = Field(default="all", pattern=r"^(all|selected)$")


class WorkspaceRootPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    enabled: bool | None = None


class WorkspaceRootLifecycleAction(BaseModel):
    reason: str = Field(min_length=2, max_length=1_000)


class WorkspaceRootOut(ORMModel):
    id: str
    name: str
    source: str = "host"
    device_id: str | None = None
    remote_key: str = ""
    approval_status: str = "approved"
    approval_note: str = ""
    published_by_user_id: str | None = None
    share_scope: str = "team"
    semantic_content_enabled: bool = False
    published_at: datetime | None = None
    selection_mode: str = "all"
    included_paths: list[str] = Field(default_factory=list)
    enabled: bool
    read_only: bool
    scan_status: str
    last_scan_at: datetime | None
    file_count: int
    directory_count: int
    error_message: str
    version: int
    created_at: datetime
    permissions: dict[str, bool] = Field(default_factory=dict)


class WorkspaceRootSharingPatch(BaseModel):
    share_scope: str = Field(pattern=r"^(team|selected)$")
    semantic_content_enabled: bool = False


class WorkspaceRootMemberInput(BaseModel):
    user_id: str
    can_browse: bool = True
    can_download: bool = True
    can_send: bool = True


class WorkspaceRootMembersPatch(BaseModel):
    members: list[WorkspaceRootMemberInput] = Field(
        default_factory=list, max_length=500
    )


class WorkspaceRootMemberOut(ORMModel):
    id: str
    root_id: str
    user_id: str
    can_browse: bool
    can_download: bool
    can_send: bool
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class WorkspaceFileOut(BaseModel):
    id: str
    root_id: str
    parent_id: str | None
    relative_path: str
    name: str
    is_directory: bool
    in_scope: bool = True
    extension: str
    size_bytes: int
    modified_at: datetime | None
    mime_type: str
    sha256: str | None
    device_id: str = ""
    availability: str = "online"
    status: FileIndexStatus
    content_status: ContentIndexStatus = ContentIndexStatus.METADATA_ONLY
    content_error_code: str = ""
    detected_type: str = "application/octet-stream"
    archive_member_count: int = 0
    indexed_at: datetime | None
    last_seen_at: datetime | None
    version: int
    tags: list[str] = Field(default_factory=list)
    links: list[dict[str, str]] = Field(default_factory=list)
    preview_text: str = ""
    permissions: dict[str, bool] = Field(default_factory=dict)


class WorkspaceFolderOption(BaseModel):
    path: str
    name: str
    parent_path: str | None
    depth: int
    direct_file_count: int = 0
    selected: bool = False
    in_scope: bool = False


class WorkspaceSelectionPatch(BaseModel):
    selection_mode: str = Field(pattern=r"^(all|selected)$")
    included_paths: list[str] = Field(default_factory=list, max_length=2_000)


class WorkspaceFileLinkCreate(BaseModel):
    entity_type: str = Field(pattern=r"^(task|report|journal|material|knowledge)$")
    entity_id: str
    relation: str = Field(default="reference", max_length=40)


class WorkspaceFileTagsPatch(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("tags")
    @classmethod
    def normalize_file_tags(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip()[:64] for item in values if item.strip()))


class WorkspaceScanOut(BaseModel):
    root_id: str
    files: int
    directories: int
    changed: int
    missing: int
    content_indexed: int = 0
    metadata_only: int = 0
    pending_ocr: int = 0
    content_failed: int = 0
    skipped_directories: int = 0
    diagnostic_id: str = ""
    errors: list[str] = Field(default_factory=list)


class ArchiveFieldDefinition(BaseModel):
    key: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
    )
    label: str = Field(min_length=1, max_length=80)
    type: str = Field(
        default="text",
        pattern=r"^(text|textarea|date|number|select)$",
    )
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=50)


class ArchiveCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    description: str = Field(default="", max_length=5_000)
    record_mode: ArchiveRecordMode = ArchiveRecordMode.DOCUMENT
    field_schema: list[ArchiveFieldDefinition] = Field(
        default_factory=list, max_length=50
    )
    directory_pattern: str = Field(default="{year}/{category}", max_length=255)
    access_mode: ArchiveAccessMode = ArchiveAccessMode.ALL_USERS
    allow_device_access: bool = True


class ArchiveCategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    field_schema: list[ArchiveFieldDefinition] | None = Field(
        default=None, max_length=50
    )
    directory_pattern: str | None = Field(default=None, max_length=255)
    access_mode: ArchiveAccessMode | None = None
    allow_device_access: bool | None = None
    active: bool | None = None


class ArchiveCategoryOut(ORMModel):
    id: str
    name: str
    code: str
    description: str
    record_mode: ArchiveRecordMode
    field_schema: list[dict[str, Any]]
    directory_pattern: str
    access_mode: ArchiveAccessMode
    allow_device_access: bool
    built_in: bool
    active: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    permissions: dict[str, bool] = Field(default_factory=dict)


class ArchiveRecordCreate(BaseModel):
    category_id: str
    archive_year: int = Field(ge=1_000, le=9_999)
    sequence_no: int | None = Field(default=None, ge=1, le=999_999)
    document_no: str = Field(default="", max_length=160)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=50_000)
    involved_persons: list[str] = Field(default_factory=list, max_length=100)
    source_unit: str = Field(default="", max_length=160)
    document_date: datetime | None = None
    person_name: str = Field(default="", max_length=120)
    person_identifier: str = Field(default="", max_length=120)
    personnel_type: str = Field(default="", max_length=64)
    organization: str = Field(default="", max_length=160)
    assessment_result: str = Field(default="", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=50)
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("involved_persons")
    @classmethod
    def normalize_people(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(item.strip()[:120] for item in values if item.strip())
        )

    @field_validator("tags")
    @classmethod
    def normalize_archive_tags(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip()[:64] for item in values if item.strip()))


class ArchiveRecordPatch(BaseModel):
    archive_year: int | None = Field(default=None, ge=1_000, le=9_999)
    sequence_no: int | None = Field(default=None, ge=1, le=999_999)
    document_no: str | None = Field(default=None, max_length=160)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    summary: str | None = Field(default=None, max_length=50_000)
    involved_persons: list[str] | None = Field(default=None, max_length=100)
    source_unit: str | None = Field(default=None, max_length=160)
    document_date: datetime | None = None
    person_name: str | None = Field(default=None, max_length=120)
    person_identifier: str | None = Field(default=None, max_length=120)
    personnel_type: str | None = Field(default=None, max_length=64)
    organization: str | None = Field(default=None, max_length=160)
    assessment_result: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=50)
    custom_fields: dict[str, Any] | None = None
    change_note: str = Field(default="", max_length=2_000)


class ArchiveAction(BaseModel):
    reason: str = Field(min_length=1, max_length=2_000)


class ArchiveAttachmentOut(ORMModel):
    id: str
    record_id: str
    blob_sha256: str
    version_no: int
    display_name: str
    note: str
    status: ArchiveAttachmentStatus
    ocr_text: str
    uploaded_by: str
    size_bytes: int = 0
    mime_type: str = "application/octet-stream"
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    delete_reason: str = ""
    purge_after: datetime | None = None


class ArchiveRecordOut(ORMModel):
    id: str
    category_id: str
    archive_year: int
    sequence_no: int
    document_no: str
    title: str
    summary: str
    involved_persons: list[str]
    source_unit: str
    document_date: datetime | None
    person_name: str
    person_identifier: str
    personnel_type: str
    organization: str
    assessment_result: str
    tags: list[str]
    custom_fields: dict[str, Any]
    status: ArchiveRecordStatus
    void_reason: str
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    attachment_count: int = 0
    indexed_attachment_count: int = 0
    attachments: list[ArchiveAttachmentOut] = Field(default_factory=list)
    deleted_attachments: list[ArchiveAttachmentOut] = Field(default_factory=list)
    duplicate_warnings: list[str] = Field(default_factory=list)
    links: list[dict[str, str]] = Field(default_factory=list)
    permissions: dict[str, bool] = Field(default_factory=dict)


class ArchiveRevisionOut(ORMModel):
    id: str
    record_id: str
    revision_no: int
    snapshot: dict[str, Any]
    change_note: str
    created_by: str
    created_at: datetime


class ArchiveAccessPatch(BaseModel):
    access_mode: ArchiveAccessMode
    allow_device_access: bool


class ArchiveAccessGrantCreate(BaseModel):
    user_id: str | None = None
    device_id: str | None = None
    can_view: bool = True
    can_download: bool = True
    can_contribute: bool = False


class ArchiveAccessGrantPatch(BaseModel):
    can_view: bool | None = None
    can_download: bool | None = None
    can_contribute: bool | None = None
    active: bool | None = None


class ArchiveAccessGrantOut(ORMModel):
    id: str
    category_id: str
    user_id: str | None
    device_id: str | None
    can_view: bool
    can_download: bool
    can_contribute: bool
    active: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class ArchiveLinkCreate(BaseModel):
    entity_type: str = Field(pattern=r"^(task|report|journal|knowledge)$")
    entity_id: str
    relation: str = Field(default="reference", max_length=40)


class ArchiveTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=5_000)
    structure: list[dict[str, Any]] = Field(default_factory=list)
    material_rules: list[dict[str, Any]] = Field(default_factory=list)


class ArchiveTemplatePatch(BaseModel):
    """旧归档模板的可维护生命周期；停用不会改写既有档案。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=5_000)
    structure: list[dict[str, Any]] | None = None
    material_rules: list[dict[str, Any]] | None = None
    active: bool | None = None


class ArchiveTemplateOut(ORMModel):
    id: str
    name: str
    category: str
    description: str
    structure: list[dict[str, Any]]
    material_rules: list[dict[str, Any]]
    active: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class NotificationOut(ORMModel):
    id: str
    notification_type: str
    title: str
    body: str
    entity_type: str
    entity_id: str | None
    read_at: datetime | None
    created_at: datetime


class AIProviderPatch(BaseModel):
    name: str = Field(default="单位模型服务", max_length=120)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=160)
    api_key: str | None = Field(default=None, max_length=2_000)
    enabled: bool = False
    trusted_intranet: bool = False
    timeout_seconds: int = Field(default=60, ge=5, le=300)


class AIProviderOut(BaseModel):
    id: str | None
    name: str
    base_url: str
    model: str
    has_api_key: bool
    enabled: bool
    trusted_intranet: bool
    timeout_seconds: int
    version: int
    last_test_at: datetime | None
    last_status: str
    last_error: str


class AIPolicyPatch(BaseModel):
    name: str = Field(default="默认只读策略", max_length=120)
    allowed_root_ids: list[str] = Field(default_factory=list)
    allowed_task_categories: list[str] = Field(default_factory=list)
    allowed_file_types: list[str] = Field(default_factory=list)
    capabilities: list[AiCapability] = Field(default_factory=list)
    allow_restricted: bool = False
    active: bool = True


class AIPolicyOut(ORMModel):
    id: str
    name: str
    allowed_root_ids: list[str]
    allowed_task_categories: list[str]
    allowed_file_types: list[str]
    capabilities: list[str]
    allow_restricted: bool
    active: bool
    version: int
    created_by: str


class AIQueryRequest(BaseModel):
    capability: AiCapability
    instruction: str = Field(min_length=1, max_length=10_000)
    task_ids: list[str] = Field(default_factory=list, max_length=50)
    file_ids: list[str] = Field(default_factory=list, max_length=50)
    confirm_external: bool = False
    confirm_sensitive: bool = False


class AIOrchestrationCreate(BaseModel):
    """创建智能编排会话；只接收目标和业务对象 ID。"""

    goal: str = Field(min_length=1, max_length=10_000)
    context_scope: dict[str, Any] = Field(default_factory=dict)
    external_consent: bool = False


class AIOrchestrationReplan(BaseModel):
    goal: str | None = Field(default=None, min_length=1, max_length=10_000)
    context_scope: dict[str, Any] | None = None


class AIOrchestrationApprovalRequest(BaseModel):
    approved: bool = False
    scope_sha256: str = Field(default="", min_length=0, max_length=64)


class AIOrchestrationStepOut(BaseModel):
    id: str
    step_order: int
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float
    risk_level: str
    requires_confirmation: bool
    status: str
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    version: int
    scope_sha256: str = ""


class AIOrchestrationOut(BaseModel):
    id: str
    goal_summary: str
    input_sha256: str
    state: str
    model_id: str
    external_consented: bool
    risk_level: str
    context_scope: dict[str, Any]
    plan: dict[str, Any]
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    expires_at: datetime
    version: int
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[AIOrchestrationStepOut] = Field(default_factory=list)


class AIOrchestrationCapabilitiesOut(BaseModel):
    release: str
    planner: dict[str, Any]
    components: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    external_models: dict[str, Any]


class AIDraftOut(ORMModel):
    id: str
    capability: AiCapability
    title: str
    content: str
    sources: list[dict[str, Any]]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class AppearanceContextOut(BaseModel):
    effective_season: SeasonTheme
    art_level: ArtLevel
    reduce_motion: bool
    theme_mode: str


class UserAppearancePatch(BaseModel):
    art_level: ArtLevel = ArtLevel.STANDARD
    reduce_motion: bool = False
    theme_override: SeasonTheme | None = None


class UserAppearanceOut(ORMModel):
    user_id: str
    art_level: ArtLevel
    reduce_motion: bool
    theme_override: SeasonTheme | None
    version: int
    updated_at: datetime


class AdminAppearancePatch(BaseModel):
    theme_mode: str = Field(pattern=r"^(auto|fixed)$")
    fixed_theme: SeasonTheme = SeasonTheme.SPRING
    default_art_level: ArtLevel = ArtLevel.STANDARD
    default_reduce_motion: bool = False


class AdminAppearanceOut(AdminAppearancePatch):
    version: int


class AIModelPackOut(ORMModel):
    id: str
    name: str
    version: str
    model_id: str
    architecture: str
    filename: str
    sha256: str
    size_bytes: int
    capabilities: list[str] = Field(default_factory=list)
    min_runtime_version: str = "1.4.1"
    estimated_memory_mb: int = 0
    model_source: str = ""
    license_name: str = ""
    signature_valid: bool
    status: ModelPackStatus
    created_at: datetime
    activated_at: datetime | None
    active_capabilities: list[str] = Field(default_factory=list)


class LocalAIRuntimeOut(BaseModel):
    ready: bool
    state: str
    message: str
    model_pack_id: str | None = None
    model_id: str | None = None
    embedding_pack_id: str | None = None
    llm_pack_id: str | None = None
    intent_pack_id: str | None = None
    available_memory_mb: int | None = None
    llm_running: bool = False
    embedding_loaded: bool = False
    embedding_available: bool = False
    llm_available: bool = False
    intent_available: bool = False
    worker_scope: str = "host"
    max_threads: int = 4
    memory_limit_mb: int = 3584


class HardwareProfileOut(BaseModel):
    platform: str
    platform_version: str
    architecture: str
    cpu_name: str
    cpu_cores: int
    cpu_flags: list[str] = Field(default_factory=list)
    total_memory_mb: int
    available_memory_mb: int
    reserved_memory_mb: int
    model_disk_free_mb: int
    gpu_backends: list[str] = Field(default_factory=list)
    gpu_memory_mb: int | None = None
    gpu_names: list[str] = Field(default_factory=list)
    runtime_backends: list[str] = Field(default_factory=list)
    partyops_rss_mb: int
    detected_at: datetime
    privacy_notice: str


class HardwareBenchmarkOut(BaseModel):
    available: bool
    score: int
    duration_ms: int
    message: str


class ModelRecommendationOut(BaseModel):
    id: str
    name: str
    kind: str = Field(pattern=r"^(embedding|llm|intent_router)$")
    role: str = ""
    tier: str
    summary: str
    official_url: str
    source_url: str
    license: str
    min_memory_mb: int
    recommended_memory_mb: int
    disk_mb: int
    context_tokens: int
    recommended_threads: int
    delivery: str = Field(pattern=r"^(partyops_pack|official)$")
    hosted_url: str = ""
    platforms: list[str] = Field(default_factory=list)
    architectures: list[str] = Field(default_factory=list)
    status: str = Field(pattern=r"^(流畅|可用|不建议)$")
    reason: str
    effective_threads: int
    effective_context_tokens: int


class IntentPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class IntentPreviewOut(BaseModel):
    engine: str = Field(pattern=r"^(rules|needle)$")
    intent: str
    operation: str = Field(pattern=r"^(none|read|write)$")
    confidence: float = Field(ge=0, le=1)
    requires_confirmation: bool
    can_execute: bool
    clarification: str | None = None
    flags: list[str] = Field(default_factory=list)
    preview: dict[str, Any]
    notice: str


class AIRecommendationOut(ORMModel):
    id: str
    generator: RecommendationGenerator
    status: RecommendationStatus
    title: str
    reason: str
    content: str
    score: int
    object_type: str
    object_id: str
    object_version: int
    route: str
    sources: list[dict[str, Any]]
    expires_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime


class BackgroundJobOut(ORMModel):
    id: str
    job_type: str
    status: str
    progress: int
    message: str
    payload: dict[str, Any]
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DeviceEnrollmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    advertised_host: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="协同电脑能够访问的主机局域网地址",
    )


class DeviceEnrollmentOut(BaseModel):
    id: str
    name: str
    code: str
    expires_at: datetime
    host_url: str
    ca_fingerprint: str


class DeviceEnrollmentStatusOut(BaseModel):
    id: str
    status: str = Field(pattern=r"^(pending|enrolled|expired)$")
    expires_at: datetime
    used_at: datetime | None = None
    device_id: str | None = None
    device_name: str = ""
    device_status: str = ""
    last_seen_at: datetime | None = None


class DeviceOut(ORMModel):
    id: str
    name: str
    status: str
    architecture: str
    platform: str
    platform_family: str = ""
    distribution: str = ""
    distribution_version: str = ""
    package_format: str = ""
    runtime_profile: str = ""
    capabilities: list[str] = Field(default_factory=list)
    kernel: str
    app_version: str
    agent_version: str
    protocol_version: int = 1
    credential_state: str = "active"
    credential_rotated_at: datetime | None = None
    local_username: str
    ip_address: str
    certificate_fingerprint: str
    certificate_expires_at: datetime | None
    active: bool
    allow_host_access: bool
    allow_device_transfer: bool
    allow_user_shares: bool = True
    last_seen_at: datetime | None
    disk_free_bytes: int
    version: int
    created_at: datetime
    updated_at: datetime


class DeviceVersionStatus(BaseModel):
    device_id: str
    device_name: str
    current_version: str
    target_version: str
    version_state: str
    update_status: str
    update_message: str
    last_seen_at: datetime | None


class DeviceUpdateGate(BaseModel):
    identified: bool
    device_id: str | None = None
    device_name: str = ""
    current_version: str = ""
    target_version: str
    required: bool
    access_allowed: bool
    state: str
    status: str = ""
    message: str = ""
    package_id: str | None = None
    run_id: str | None = None
    release_title: str = ""
    release_notes: list[str] = Field(default_factory=list)
    installed_at: datetime | None = None


class RuntimeContextOut(BaseModel):
    node_mode: str = Field(pattern=r"^(host|personal|client|unknown)$")
    platform: str
    user_role: UserRole
    device_id: str | None = None
    device_name: str = ""
    capabilities: list[str] = Field(default_factory=list)


class LocalShareActionOut(BaseModel):
    open_uri: str
    expires_at: datetime


class DeviceBrowserTokenOut(BaseModel):
    token: str
    expires_at: datetime


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    allow_host_access: bool | None = None
    allow_device_transfer: bool | None = None
    allow_user_shares: bool | None = None


class DeviceCertificateRotateRequest(BaseModel):
    csr_pem: str = Field(min_length=100, max_length=20_000)


class DeviceCertificateOut(BaseModel):
    certificate_pem: str
    ca_certificate_pem: str
    certificate_fingerprint: str
    agent_url: str
    expires_at: datetime


class DeviceGrantCreate(BaseModel):
    device_id: str
    user_id: str | None = None
    root_id: str | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class DeviceGrantOut(ORMModel):
    id: str
    device_id: str
    user_id: str | None
    root_id: str | None
    capabilities: list[str]
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class DeviceHeartbeat(BaseModel):
    architecture: str = Field(default="", max_length=16)
    platform: str = Field(default="uos", max_length=40)
    # rc.2 Agent 不会上报这些 rc.3 字段。None 明确表示“旧客户端未提供”，
    # 心跳处理必须保留数据库中的已知值，不能用空串把平台事实抹掉。
    platform_family: str | None = Field(default=None, max_length=24)
    distribution: str | None = Field(default=None, max_length=40)
    distribution_version: str | None = Field(default=None, max_length=40)
    package_format: str | None = Field(default=None, max_length=16)
    runtime_profile: str | None = Field(default=None, max_length=32)
    capabilities: list[str] | None = Field(default=None, max_length=32)
    kernel: str = Field(default="", max_length=120)
    app_version: str = Field(default="", max_length=32)
    agent_version: str = Field(default="", max_length=32)
    protocol_version: int = Field(default=1, ge=1, le=100)
    local_username: str = Field(default="", max_length=120)
    ip_address: str = Field(default="", max_length=64)
    disk_free_bytes: int = Field(default=0, ge=0)
    root_count: int = Field(default=0, ge=0)
    indexed_file_count: int = Field(default=0, ge=0)


class DeviceEnrollRequest(DeviceHeartbeat):
    # 允许国产浏览器把中文标签和不可见字符一并复制，服务端会提取其中唯一
    # 的规范入网码。上限仍保持较小，避免将任意大段文本送入入网端点。
    code: str = Field(min_length=8, max_length=1024)
    name: str = Field(min_length=1, max_length=120)
    csr_pem: str | None = Field(default=None, max_length=20_000)


class DeviceEnrollOut(BaseModel):
    device_id: str
    device_token: str
    host_url: str
    expires_at: datetime
    certificate_pem: str = ""
    ca_certificate_pem: str = ""
    certificate_fingerprint: str = ""
    agent_url: str = ""


class RemoteRootRequest(BaseModel):
    device_id: str
    name: str = Field(min_length=1, max_length=160)
    remote_key: str = Field(min_length=1, max_length=255)


class DeviceRemoteRootCreate(BaseModel):
    """终端只上传不含绝对路径的目录别名和随机标识。"""

    name: str = Field(min_length=1, max_length=160)
    remote_key: str = Field(
        min_length=8,
        max_length=120,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    action_token: str = Field(default="", max_length=256)


class DeviceRemoteRootPatch(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class RemoteRootPatch(BaseModel):
    enabled: bool | None = None
    approval_status: str | None = Field(
        default=None, pattern=r"^(pending|approved|rejected)$"
    )
    approval_note: str | None = Field(default=None, max_length=2_000)


class RemoteIndexFile(BaseModel):
    relative_path: str = Field(min_length=1, max_length=4_096)
    name: str = Field(min_length=1, max_length=255)
    is_directory: bool = False
    parent_relative_path: str | None = None
    extension: str = Field(default="", max_length=32)
    size_bytes: int = Field(default=0, ge=0)
    modified_at: datetime | None = None
    mime_type: str = Field(default="application/octet-stream", max_length=160)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{0,64}$")
    content_changed: bool = False
    extracted_text: str = Field(default="", max_length=200_000)
    ocr_text: str = Field(default="", max_length=200_000)


class RemoteIndexDelta(BaseModel):
    root_id: str
    files: list[RemoteIndexFile] = Field(default_factory=list, max_length=5_000)
    removed_paths: list[str] = Field(default_factory=list, max_length=5_000)


class TransferCreate(BaseModel):
    direction: str = Field(
        pattern=r"^(device_to_host|host_to_device|device_to_device)$"
    )
    source_file_id: str | None = None
    source_device_id: str | None = None
    destination_device_id: str | None = None
    destination_root_id: str | None = None
    original_name: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(default="", max_length=4_096)
    size_bytes: int = Field(default=0, ge=0, le=20 * 1024 * 1024 * 1024)
    sha256: str = Field(default="", pattern=r"^[a-fA-F0-9]{0,64}$")
    require_approval: bool = False


class TransferOut(ORMModel):
    id: str
    direction: str
    status: str
    source_device_id: str | None
    destination_device_id: str | None
    source_file_id: str | None
    destination_root_id: str | None
    original_name: str
    relative_path: str
    size_bytes: int
    sha256: str
    chunk_size: int
    total_chunks: int
    completed_chunks: int
    requested_by: str
    approved_by: str | None
    approval_note: str
    expires_at: datetime
    error_code: str
    error_message: str
    handled_by: str | None
    handled_at: datetime | None
    linked_entity_type: str
    linked_entity_id: str | None
    delivery_mode: str = "managed_inbox"
    bundle_mode: str = "single"
    item_ids: list[str] = Field(default_factory=list)
    result_name: str = ""
    result_sha256: str = ""
    version: int
    created_at: datetime
    updated_at: datetime


class WorkspaceDownloadCreate(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=500)
    bundle_mode: str = Field(
        default="single", pattern=r"^(single|selection_zip|folder_zip)$"
    )
    delivery: str = Field(default="browser", pattern=r"^(browser|current_device)$")


class WorkspaceDownloadOut(BaseModel):
    transfer_id: str
    status: str
    delivery: str
    content_url: str = ""


class TransferAction(BaseModel):
    action: str = Field(pattern=r"^(approve|pause|resume|cancel|retry)$")
    note: str = Field(default="", max_length=2_000)


class TransferAttachCreate(BaseModel):
    target_type: str = Field(pattern=r"^(archive|task_material)$")
    target_id: str
    note: str = Field(default="", max_length=2_000)
    stage: MaterialStage = MaterialStage.DRAFT
    is_final: bool = False


class TaskBatchPatch(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=200)
    status: TaskStatus | None = None
    owner_id: str | None = None
    internal_due_at: datetime | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    note: str = Field(default="", max_length=2_000)


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    view_type: str = Field(default="tasks", max_length=40)
    filters: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)
    pinned: bool = False


class SavedViewOut(ORMModel):
    id: str
    name: str
    view_type: str
    filters: dict[str, Any]
    columns: list[str]
    pinned: bool
    owner_id: str
    version: int
    created_at: datetime
    updated_at: datetime


class TopicSpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=5_000)


class TopicSpacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    task_ids: list[str] | None = None
    file_ids: list[str] | None = None
    journal_ids: list[str] | None = None
    contact_ids: list[str] | None = None
    active: bool | None = None


class TopicSpaceOut(ORMModel):
    id: str
    name: str
    description: str
    task_ids: list[str]
    file_ids: list[str]
    journal_ids: list[str]
    contact_ids: list[str]
    active: bool
    owner_id: str
    version: int
    created_at: datetime
    updated_at: datetime


class AutomationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    trigger: str = Field(min_length=1, max_length=40)
    conditions: dict[str, Any] = Field(default_factory=dict)
    actions: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AutomationRulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    trigger: str | None = Field(default=None, min_length=1, max_length=40)
    conditions: dict[str, Any] | None = None
    actions: dict[str, Any] | None = None
    enabled: bool | None = None


class AutomationRuleOut(ORMModel):
    id: str
    name: str
    trigger: str
    conditions: dict[str, Any]
    actions: dict[str, Any]
    enabled: bool
    owner_id: str
    version: int
    created_at: datetime
    updated_at: datetime


class WorkCalendarEntryCreate(BaseModel):
    date_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="holiday", max_length=24)
    is_workday: bool = False
    note: str = Field(default="", max_length=1_000)


class WorkCalendarEntryPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    kind: str | None = Field(default=None, max_length=24)
    is_workday: bool | None = None
    note: str | None = Field(default=None, max_length=1_000)


class WorkCalendarEntryOut(ORMModel):
    id: str
    date_key: str
    title: str
    kind: str
    is_workday: bool
    note: str
    owner_id: str
    version: int
    created_at: datetime


class CalendarPreferencePatch(BaseModel):
    default_view: str | None = Field(default=None, pattern=r"^(week|month|year)$")
    week_starts_on: int | None = Field(default=None, ge=1, le=7)
    visible_event_types: list[CalendarEventType] | None = None
    compact_weekends: bool | None = None


class CalendarPreferenceOut(ORMModel):
    user_id: str
    default_view: str
    week_starts_on: int
    visible_event_types: list[str]
    compact_weekends: bool
    version: int
    updated_at: datetime


class WorkCalendarImportItem(BaseModel):
    date_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="holiday", pattern=r"^(holiday|adjusted_workday)$")
    is_workday: bool = False
    note: str = Field(default="", max_length=1_000)


class WorkCalendarImport(BaseModel):
    items: list[WorkCalendarImportItem] = Field(min_length=1, max_length=800)


class PartyDevelopmentActualDates(BaseModel):
    conversation_date: dt_date | None = None
    activist_date: dt_date | None = None
    publicity_start_date: dt_date | None = None
    development_object_date: dt_date | None = None
    training_completed_date: dt_date | None = None
    political_review_completed_date: dt_date | None = None
    pre_review_approved_date: dt_date | None = None
    branch_acceptance_date: dt_date | None = None
    committee_approval_date: dt_date | None = None
    oath_date: dt_date | None = None
    transition_application_date: dt_date | None = None
    transition_branch_meeting_date: dt_date | None = None
    transition_approval_date: dt_date | None = None
    training_days: int | None = Field(default=None, ge=0, le=365)
    training_hours: float | None = Field(default=None, ge=0, le=10_000)


class PartyDevelopmentCalculateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    application_date: dt_date
    actual_dates: PartyDevelopmentActualDates = Field(
        default_factory=PartyDevelopmentActualDates
    )
    profile_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("姓名不能为空")
        return normalized


class PartyDevelopmentNodeOut(BaseModel):
    key: str
    title: str
    phase: str
    date_kind: str
    date: dt_date | None = None
    end_date: dt_date | None = None
    provisional: bool = False
    status: str
    article: str
    basis: str
    actual_at: dt_date | None = None
    legal_earliest_at: dt_date | None = None
    legal_deadline_at: dt_date | None = None
    reference_at: dt_date | None = None
    reference_end_at: dt_date | None = None
    adjusted_at: dt_date | None = None
    rule_version: str = ""
    reference_basis: str = ""
    is_reference: bool = False
    requires_manual_confirmation: bool = False
    materials: list[dict[str, Any]] = Field(default_factory=list)


class PartyDevelopmentResultOut(BaseModel):
    name: str
    application_date: dt_date
    rule_version: str
    rule_published_at: dt_date
    rule_title: str
    source_url: str
    generated_at: datetime
    provisional: bool
    nodes: list[PartyDevelopmentNodeOut]
    warnings: list[dict[str, str]] = Field(default_factory=list)
    manual_confirmation_items: list[str] = Field(default_factory=list)


class PartyDevelopmentCaseCreate(BaseModel):
    party_committee: str = Field(min_length=1, max_length=160)
    party_branch: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=80)
    gender: str = Field(default="", max_length=16)
    ethnicity: str = Field(default="", max_length=40)
    birth_date: dt_date | None = None
    education: str = Field(default="", max_length=80)
    application_date: dt_date
    activist_date: dt_date | None = None
    training_contacts: list[str] = Field(default_factory=list, max_length=10)
    introducers: list[str] = Field(default_factory=list, max_length=10)
    development_object_date: dt_date | None = None
    probationary_date: dt_date | None = None
    converted_date: dt_date | None = None


class PartyDevelopmentCasePatch(BaseModel):
    party_committee: str | None = Field(default=None, min_length=1, max_length=160)
    party_branch: str | None = Field(default=None, min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    gender: str | None = Field(default=None, max_length=16)
    ethnicity: str | None = Field(default=None, max_length=40)
    birth_date: dt_date | None = None
    education: str | None = Field(default=None, max_length=80)
    application_date: dt_date | None = None
    activist_date: dt_date | None = None
    training_contacts: list[str] | None = Field(default=None, max_length=10)
    introducers: list[str] | None = Field(default=None, max_length=10)
    development_object_date: dt_date | None = None
    probationary_date: dt_date | None = None
    converted_date: dt_date | None = None
    stage: str | None = Field(default=None, max_length=48)
    status: str | None = Field(
        default=None, pattern=r"^(active|completed|archived|void)$"
    )


class PartyDevelopmentProgressEventCreate(BaseModel):
    """发展党员真实进度事实；计划日期不得通过该接口冒充事实。"""

    milestone_type: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9_]+$")
    actual_date: dt_date
    evidence_note: str = Field(default="", max_length=2_000)
    source_entity_type: str = Field(default="", max_length=32)
    source_entity_id: str | None = Field(default=None, max_length=36)


class PartyDevelopmentProgressEventCorrect(BaseModel):
    actual_date: dt_date
    evidence_note: str = Field(min_length=1, max_length=2_000)


class PartyDevelopmentProgressEventVoid(BaseModel):
    reason: str = Field(min_length=2, max_length=1_000)


class PartyDevelopmentCaseLifecycleAction(BaseModel):
    reason: str = Field(min_length=2, max_length=1_000)


class PartyDevelopmentFromCalculationCreate(PartyDevelopmentCaseCreate):
    """把未建档快速测算转换为人员档案，并保存已确认的详细事实。"""

    actual_dates: PartyDevelopmentActualDates = Field(
        default_factory=PartyDevelopmentActualDates
    )


class LedgerColumnMapping(BaseModel):
    source_column: str = Field(min_length=1, max_length=160)
    action: str = Field(pattern=r"^(map|create|ignore)$")
    target_field: str | None = Field(
        default=None, max_length=96, pattern=r"^[a-z][a-z0-9_]*$"
    )
    create_label: str | None = Field(default=None, max_length=80)
    create_type: str | None = Field(
        default=None, pattern=r"^(text|textarea|number|date|select)$"
    )
    confirmed: bool = False


class LedgerImportMappingPatch(BaseModel):
    sheet_name: str = Field(min_length=1, max_length=160)
    header_row: int = Field(ge=1, le=50)
    mappings: list[LedgerColumnMapping] = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)


class LedgerImportProfilePatch(BaseModel):
    sheet_name: str = Field(min_length=1, max_length=160)
    header_row: int = Field(ge=1, le=50)
    version: int = Field(ge=1)


class LedgerImportValidateRequest(BaseModel):
    version: int = Field(ge=1)
    derived_year: int | None = Field(default=None, ge=1900, le=2200)
    row_actions: dict[str, str] = Field(default_factory=dict, max_length=50_000)


class LedgerImportCommitRequest(BaseModel):
    version: int = Field(ge=1)
    confirm_shared_storage: bool = False
    confirm_new_fields: bool = False
    confirm_derived_candidates: bool = False
    derived_year: int | None = Field(default=None, ge=1900, le=2200)
    row_actions: dict[str, str] = Field(default_factory=dict, max_length=50_000)


class LedgerImportUndoRequest(BaseModel):
    version: int = Field(ge=1)


class LedgerImportTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_type: str = Field(pattern=r"^(party_development|archive)$")
    target_id: str | None = Field(default=None, max_length=36)
    header_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping: dict[str, Any] = Field(default_factory=dict)


class LedgerImportTemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    active: bool | None = None
    version: int = Field(ge=1)


class PartyDevelopmentMilestonePatch(BaseModel):
    actual_date: dt_date | None = None
    adjusted_date: dt_date | None = None
    reminder_days: list[int] | None = Field(default=None, max_length=20)


class PartyDevelopmentReferencePlanPatch(BaseModel):
    """用户确认后的参考日期调整；实际日期使用档案字段单独维护。"""

    adjustments: dict[str, dt_date | None] = Field(default_factory=dict, max_length=40)


class PartyDevelopmentMaterialInput(BaseModel):
    phase: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=200)
    responsible_party: str = Field(default="", max_length=120)
    guidance: str = Field(default="", max_length=2_000)
    required: bool = False
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=10_000)


class PartyDevelopmentMaterialOut(ORMModel):
    id: str
    profile_id: str
    phase: str
    name: str
    responsible_party: str
    guidance: str
    required: bool
    enabled: bool
    sort_order: int
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class PartyDevelopmentProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    source_label: str = Field(default="本单位补充", max_length=255)
    active: bool = False
    items: list[PartyDevelopmentMaterialInput] = Field(
        default_factory=list, max_length=200
    )


class PartyDevelopmentProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)
    source_label: str | None = Field(default=None, max_length=255)
    active: bool | None = None


class PartyDevelopmentProfileOut(ORMModel):
    id: str
    name: str
    description: str
    source_label: str
    active: bool
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    items: list[PartyDevelopmentMaterialOut] = Field(default_factory=list)


class CalendarEventOut(BaseModel):
    id: str
    event_type: CalendarEventType
    title: str
    start_at: datetime
    end_at: datetime | None = None
    all_day: bool = False
    object_type: ObjectType | None = None
    object_id: str | None = None
    route: str = ""
    status: str = ""
    owner_id: str | None = None
    work_area: str = ""
    topic_ids: list[str] = Field(default_factory=list)
    editable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObjectLinkCreate(BaseModel):
    target_type: ObjectType
    target_id: str = Field(min_length=1, max_length=64)
    link_type: LinkType = LinkType.RELATES_TO
    note: str = Field(default="", max_length=2_000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class ObjectLinkOut(ORMModel):
    id: str
    source_type: ObjectType
    source_id: str
    target_type: ObjectType
    target_id: str
    link_type: LinkType
    note: str
    version: int
    created_by: str
    created_at: datetime
    direction: str = "outgoing"
    title: str = ""
    route: str = ""


class ActivityEventOut(ORMModel):
    id: str
    object_type: ObjectType
    object_id: str
    event_code: str
    event_label: str = ""
    actor_id: str | None
    actor_name: str = "系统"
    actor_role: str = ""
    happened_at: datetime
    recorded_at: datetime
    event_data: dict[str, Any]
    correlation_id: str


class RecurrenceExceptionCreate(BaseModel):
    occurrence_at: datetime
    action: RecurrenceExceptionAction
    rescheduled_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=2_000)


class RecurrenceExceptionOut(ORMModel):
    id: str
    rule_id: str
    occurrence_at: datetime
    action: RecurrenceExceptionAction
    rescheduled_at: datetime | None
    reason: str
    created_by: str
    created_at: datetime


class OnboardingProgressPatch(BaseModel):
    completed_steps: list[str] | None = Field(default=None, max_length=50)
    dismissed: bool | None = None


class OnboardingProgressOut(ORMModel):
    user_id: str
    completed_steps: list[str]
    dismissed: bool
    version: int
    updated_at: datetime
    steps: list[dict[str, str]] = Field(default_factory=list)


class EnablementStepOut(BaseModel):
    key: str
    title: str
    description: str
    route: str
    action_label: str
    complete: bool


class EnablementOut(BaseModel):
    persona: str = Field(pattern=r"^(host_admin|host_staff|client_admin|client_staff)$")
    title: str
    summary: str
    completed_count: int
    total_count: int
    next_route: str
    steps: list[EnablementStepOut]


class ProjectionCheckpointOut(ORMModel):
    name: str
    last_event_id: int
    status: str
    processed_count: int
    failed_count: int
    last_error: str
    last_run_at: datetime | None
    updated_at: datetime


class UpdatePackageOut(ORMModel):
    id: str
    filename: str
    version: str
    min_version: str
    schema_revision: str
    manifest: dict[str, Any]
    sha256: str
    signature_valid: bool
    status: str
    created_by: str
    created_at: datetime


class UpdateApplyRequest(BaseModel):
    target_device_ids: list[str] = Field(default_factory=list, max_length=20)
    include_host: bool = True
    force: bool = False


class UpdateRunOut(ORMModel):
    id: str
    package_id: str
    target_device_id: str | None
    status: str
    progress: int
    message: str
    created_by: str
    created_at: datetime
    completed_at: datetime | None


class ReleaseHistoryOut(ORMModel):
    id: str
    version: str
    schema_revision: str
    title: str
    release_notes: list[str]
    package_id: str | None
    status: str
    installed_at: datetime
    created_at: datetime


class DocumentComparisonCreate(BaseModel):
    left_file_id: str
    right_file_id: str
    comparison_type: str = Field(default="text", pattern=r"^(text|metadata|image)$")


class DocumentComparisonOut(ORMModel):
    id: str
    left_file_id: str
    right_file_id: str
    comparison_type: str
    result: dict[str, Any]
    created_by: str
    created_at: datetime


class FileOpenGrantCompletion(BaseModel):
    """本机文件助手的脱敏完成回执；禁止携带文件名、路径或正文。"""

    result_code: str = Field(
        pattern=r"^(OPENED|DEFAULT_APP_FAILED|FILE_MISSING|UNSUPPORTED_FORMAT|HELPER_FAILED)$"
    )
    detail: str = Field(default="", max_length=300)


class DuplicateGroupOut(ORMModel):
    id: str
    algorithm: str
    fingerprint: str
    file_ids: list[str]
    status: str
    created_at: datetime
