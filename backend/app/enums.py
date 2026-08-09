"""业务枚举，字符串值同时作为 API 契约。"""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    STAFF = "staff"


class TaskType(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    PROJECT = "project"


class TaskStatus(StrEnum):
    PENDING_RECEIPT = "pending_receipt"
    PENDING_BREAKDOWN = "pending_breakdown"
    IN_PROGRESS = "in_progress"
    WAITING_FEEDBACK = "waiting_feedback"
    PENDING_REVIEW = "pending_review"
    RETURNED = "returned"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ParticipantRole(StrEnum):
    OWNER = "owner"
    COLLABORATOR = "collaborator"
    REVIEWER = "reviewer"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    RESTRICTED = "restricted"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class MaterialStage(StrEnum):
    DRAFT = "draft"
    REVISION = "revision"
    LEADER_APPROVED = "leader_approved"
    SUBMITTED = "submitted"


class RecurrenceKind(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    YEARLY = "yearly"
    CUSTOM_DAYS = "custom_days"


class PeriodType(StrEnum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    WEEK = "week"


class PeriodReportStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    LOCKED = "locked"


class ReportSection(StrEnum):
    COMPLETED = "completed"
    NEXT_PLAN = "next_plan"
    CARRY_OVER = "carry_over"
    RISK = "risk"
    COORDINATION = "coordination"


class FileIndexStatus(StrEnum):
    PENDING = "pending"
    INDEXED = "indexed"
    CHANGED = "changed"
    MISSING = "missing"
    ERROR = "error"


class ContentIndexStatus(StrEnum):
    """文件正文识别状态；文件元数据是否纳管由 WorkspaceFile.status 表示。"""

    PENDING = "pending"
    INDEXED = "indexed"
    METADATA_ONLY = "metadata_only"
    UNSUPPORTED = "unsupported"
    PENDING_OCR = "pending_ocr"
    ERROR = "error"


class AiCapability(StrEnum):
    SEARCH = "search"
    SUMMARIZE = "summarize"
    CLASSIFY = "classify"
    DRAFT_REPORT = "draft_report"
    SUGGEST_BREAKDOWN = "suggest_breakdown"
    CHECK_MATERIALS = "check_materials"


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    STALE = "stale"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"
    UPDATING = "updating"


class WorkspaceRootSource(StrEnum):
    HOST = "host"
    DEVICE = "device"


class FileAvailability(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    CHANGED = "changed"
    MISSING = "missing"
    ERROR = "error"


class TransferStatus(StrEnum):
    QUEUED = "queued"
    AWAITING_APPROVAL = "awaiting_approval"
    TRANSFERRING = "transferring"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TransferDirection(StrEnum):
    DEVICE_TO_HOST = "device_to_host"
    HOST_TO_DEVICE = "host_to_device"
    DEVICE_TO_DEVICE = "device_to_device"


class UpdateStatus(StrEnum):
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ArchiveRecordMode(StrEnum):
    """档案类别的记录粒度。"""

    DOCUMENT = "document"
    PERSON_YEAR = "person_year"


class ArchiveRecordStatus(StrEnum):
    ACTIVE = "active"
    VOIDED = "voided"


class ArchiveAttachmentStatus(StrEnum):
    PENDING_OCR = "pending_ocr"
    INDEXED = "indexed"
    OCR_ERROR = "ocr_error"
    VOIDED = "voided"


class ArchiveAccessMode(StrEnum):
    ALL_USERS = "all_users"
    ADMINS_ONLY = "admins_only"
    SELECTED = "selected"


class ObjectType(StrEnum):
    """可建立跨领域关联的业务对象。"""

    TASK = "task"
    WORKSPACE_FILE = "workspace_file"
    ARCHIVE_RECORD = "archive_record"
    JOURNAL = "journal"
    PERIOD_REPORT = "period_report"
    KNOWLEDGE = "knowledge"
    CONTACT = "contact"
    TOPIC = "topic"


class LinkType(StrEnum):
    """对象之间的稳定语义；展示层负责翻译为中文。"""

    RELATES_TO = "relates_to"
    SUPPORTS = "supports"
    PRODUCED_BY = "produced_by"
    BELONGS_TO = "belongs_to"
    MENTIONS = "mentions"
    SUPERSEDES = "supersedes"


class CalendarEventType(StrEnum):
    TASK_DUE = "task_due"
    TASK_PLAN = "task_plan"
    RECURRENCE = "recurrence"
    REPORT_BOUNDARY = "report_boundary"
    REMINDER = "reminder"
    HOLIDAY = "holiday"
    ADJUSTED_WORKDAY = "adjusted_workday"


class RecurrenceExceptionAction(StrEnum):
    SKIP = "skip"
    RESCHEDULE = "reschedule"


class SeasonTheme(StrEnum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class ArtLevel(StrEnum):
    STANDARD = "standard"
    REDUCED = "reduced"


class ModelPackStatus(StrEnum):
    MISSING = "missing"
    VERIFYING = "verifying"
    INSTALLED = "installed"
    ACTIVE = "active"
    CORRUPT = "corrupt"


class RecommendationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class RecommendationGenerator(StrEnum):
    RULES = "rules"
    EMBEDDING = "embedding"
    LOCAL_LLM = "local_llm"
    EXTERNAL_LLM = "external_llm"
