export type UserRole = "admin" | "staff";
export type TaskType = "quick" | "standard" | "project";
export type TaskStatus =
  | "pending_receipt"
  | "pending_breakdown"
  | "in_progress"
  | "waiting_feedback"
  | "pending_review"
  | "returned"
  | "completed"
  | "archived";

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  active: boolean;
  version: number;
  created_at: string;
}

export interface Contact {
  id: string;
  name: string;
  organization: string;
  phone: string;
  note: string;
  version: number;
}

export interface Participant {
  id: string;
  user_id: string;
  role: "owner" | "collaborator" | "reviewer";
}

export interface TaskStep {
  id: string;
  title: string;
  assignee_id: string | null;
  due_at: string | null;
  done: boolean;
  sort_order: number;
  version: number;
}

export interface MaterialVersion {
  id: string;
  version_no: number;
  stage: "draft" | "revision" | "leader_approved" | "submitted";
  is_final: boolean;
  original_name: string;
  note: string;
  size_bytes: number;
  mime_type: string;
  uploaded_by: string;
  created_at: string;
}

export interface Material {
  id: string;
  category: string;
  name: string;
  required: boolean;
  not_applicable: boolean;
  not_applicable_reason: string;
  version: number;
  versions: MaterialVersion[];
  complete: boolean;
}

export interface Comment {
  id: string;
  author_id: string;
  parent_id: string | null;
  body: string;
  mentioned_user_ids: string[];
  created_at: string;
}

export interface StatusEvent {
  id: string;
  actor_id: string;
  from_status: TaskStatus | null;
  to_status: TaskStatus;
  note: string;
  created_at: string;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  task_type: TaskType;
  status: TaskStatus;
  sensitivity: "normal" | "restricted";
  priority: "low" | "normal" | "high" | "urgent";
  source: string;
  source_kind: string;
  category: string;
  tags: string[];
  formal_due_at: string | null;
  internal_due_at: string | null;
  planned_start_at: string | null;
  planned_end_at: string | null;
  work_area: string;
  annual_focus: string;
  reporting_scope: string;
  owner_id: string;
  reviewer_id: string | null;
  parent_task_id: string | null;
  template_id: string | null;
  recurrence_rule_id: string | null;
  experience_notes: string;
  contact_ids: string[];
  allow_sensitive_content: boolean;
  version: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  archived_at: string | null;
  participants: Participant[];
  steps: TaskStep[];
  materials: Material[];
  comments: Comment[];
  events: StatusEvent[];
  subtasks: Subtask[];
  missing_required_materials: number;
}

export interface Subtask {
  id: string;
  title: string;
  status: TaskStatus;
  owner_id: string;
  formal_due_at: string | null;
  internal_due_at: string | null;
  version: number;
  missing_required_materials: number;
}

export interface DashboardBucket {
  key: string;
  label: string;
  count: number;
  items: Task[];
}

export interface DashboardData {
  buckets: DashboardBucket[];
  updated_at: string;
  this_week_completed: Task[];
  next_week_planned: Task[];
  carry_over: Task[];
  unread_notifications: number;
}

export interface Template {
  id: string;
  name: string;
  category: string;
  task_type: TaskType;
  description: string;
  active: boolean;
  version: number;
  steps: string[];
  materials: Array<{ category: string; name: string; required: boolean }>;
}

export interface Backup {
  id: string;
  filename: string;
  kind: string;
  size_bytes: number;
  sha256: string;
  status: string;
  message: string;
  created_at: string;
  completed_at: string | null;
}

export interface Recurrence {
  id: string;
  name: string;
  template_id: string;
  owner_id: string;
  kind: "monthly" | "quarterly" | "half_yearly" | "yearly" | "custom_days";
  custom_days: number | null;
  internal_lead_days: number;
  next_run_at: string;
  active: boolean;
  last_run_at: string | null;
  last_task_id: string | null;
  notes: string;
  contact_ids: string[];
  schedule_config: Record<string, unknown>;
  paused_until: string | null;
  end_at: string | null;
  max_occurrences: number | null;
  occurrence_count: number;
  last_error: string;
  version: number;
}

export interface ReminderPreference {
  user_id: string;
  enabled: boolean;
  advance_days: number;
  reminder_days: number[];
  quiet_start: string;
  quiet_end: string;
  desktop_enabled: boolean;
  remind_overdue: boolean;
  remind_review: boolean;
  remind_feedback: boolean;
  remind_materials: boolean;
  version: number;
  updated_at: string;
}

export type PeriodType = "year" | "quarter" | "month" | "week";
export type PeriodReportStatus = "draft" | "published" | "locked";
export type ReportSection =
  | "completed"
  | "next_plan"
  | "carry_over"
  | "risk"
  | "coordination";

export interface PeriodReportItem {
  id: string;
  report_id: string;
  section: ReportSection;
  source_type: "manual" | "task" | "file" | "journal";
  source_id: string | null;
  title: string;
  content: string;
  sort_order: number;
  carried_over: boolean;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface PeriodReport {
  id: string;
  period_type: PeriodType;
  status: PeriodReportStatus;
  period_key: string;
  title: string;
  start_at: string;
  end_at: string;
  summary: string;
  snapshot: Record<string, unknown>;
  version: number;
  created_by: string;
  updated_by: string;
  published_at: string | null;
  locked_at: string | null;
  created_at: string;
  updated_at: string;
  items: PeriodReportItem[];
}

export interface WorkJournal {
  id: string;
  entry_type: "manual" | "system";
  title: string;
  content: string;
  event_code: string;
  event_data: Record<string, unknown>;
  action_label: string;
  actor_name: string;
  actor_role_label: string;
  task_title: string;
  from_status: string;
  to_status: string;
  material_stage: string;
  occurred_at: string;
  task_id: string | null;
  file_id: string | null;
  report_id: string | null;
  immutable: boolean;
  created_by: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceRoot {
  id: string;
  name: string;
  source: "host" | "device";
  device_id: string | null;
  remote_key: string;
  approval_status: string;
  approval_note: string;
  published_by_user_id: string | null;
  share_scope: "team" | "selected";
  semantic_content_enabled: boolean;
  published_at: string | null;
  selection_mode: "all" | "selected";
  included_paths: string[];
  enabled: boolean;
  read_only: boolean;
  scan_status: string;
  last_scan_at: string | null;
  file_count: number;
  directory_count: number;
  error_message: string;
  version: number;
  created_at: string;
  permissions: Record<string, boolean>;
}

export interface WorkspaceRootMember {
  id: string;
  root_id: string;
  user_id: string;
  can_browse: boolean;
  can_download: boolean;
  can_send: boolean;
  active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceLink {
  id: string;
  entity_type: string;
  entity_id: string;
  relation: string;
}

export interface WorkspaceFile {
  id: string;
  root_id: string;
  parent_id: string | null;
  relative_path: string;
  name: string;
  is_directory: boolean;
  in_scope: boolean;
  extension: string;
  size_bytes: number;
  modified_at: string | null;
  mime_type: string;
  sha256: string | null;
  device_id: string | null;
  availability: "online" | "offline" | "changed" | "missing" | "error";
  status: "pending" | "indexed" | "changed" | "missing" | "error";
  content_status:
    | "pending"
    | "indexed"
    | "metadata_only"
    | "unsupported"
    | "pending_ocr"
    | "error";
  content_error_code: string;
  detected_type: string;
  archive_member_count: number;
  indexed_at: string | null;
  last_seen_at: string | null;
  version: number;
  tags: string[];
  links: WorkspaceLink[];
  preview_text: string;
  permissions: Record<string, boolean>;
}

export interface WorkspaceFolderOption {
  path: string;
  name: string;
  parent_path: string | null;
  depth: number;
  direct_file_count: number;
  selected: boolean;
  in_scope: boolean;
}

export interface ArchiveFieldDefinition {
  key: string;
  label: string;
  type: "text" | "textarea" | "date" | "number" | "select";
  required: boolean;
  options: string[];
}

export interface ArchiveCategory {
  id: string;
  name: string;
  code: string;
  description: string;
  record_mode: "document" | "person_year";
  field_schema: ArchiveFieldDefinition[];
  directory_pattern: string;
  access_mode: "all_users" | "admins_only" | "selected";
  allow_device_access: boolean;
  built_in: boolean;
  active: boolean;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  permissions: Record<string, boolean>;
}

export interface ArchiveAttachment {
  id: string;
  record_id: string;
  blob_sha256: string;
  version_no: number;
  display_name: string;
  note: string;
  status: "pending_ocr" | "indexed" | "ocr_error" | "voided";
  ocr_text: string;
  uploaded_by: string;
  size_bytes: number;
  mime_type: string;
  created_at: string;
  updated_at: string;
}

export interface ArchiveRecord {
  id: string;
  category_id: string;
  archive_year: number;
  sequence_no: number;
  document_no: string;
  title: string;
  summary: string;
  involved_persons: string[];
  source_unit: string;
  document_date: string | null;
  person_name: string;
  person_identifier: string;
  personnel_type: string;
  organization: string;
  assessment_result: string;
  tags: string[];
  custom_fields: Record<string, unknown>;
  status: "active" | "voided";
  void_reason: string;
  version: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
  attachment_count: number;
  indexed_attachment_count: number;
  duplicate_warnings: string[];
  attachments: ArchiveAttachment[];
  links: WorkspaceLink[];
  permissions: Record<string, boolean>;
}

export interface ArchiveAccessGrant {
  id: string;
  category_id: string;
  user_id: string | null;
  device_id: string | null;
  can_view: boolean;
  can_download: boolean;
  can_contribute: boolean;
  active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ArchiveYearSummary {
  year: number;
  categories: Array<{
    id: string;
    name: string;
    record_count: number;
    attachment_count: number;
    missing_attachment_count: number;
    last_updated: string;
  }>;
}

export interface NotificationItem {
  id: string;
  notification_type: string;
  title: string;
  body: string;
  entity_type: string;
  entity_id: string | null;
  read_at: string | null;
  created_at: string;
}

export interface EnablementStep {
  key: string;
  title: string;
  description: string;
  route: string;
  action_label: string;
  complete: boolean;
}

export interface EnablementStatus {
  persona: "host_admin" | "host_staff" | "client_admin" | "client_staff";
  title: string;
  summary: string;
  completed_count: number;
  total_count: number;
  next_route: string;
  steps: EnablementStep[];
}

export interface AIProvider {
  id: string | null;
  name: string;
  base_url: string;
  model: string;
  has_api_key: boolean;
  enabled: boolean;
  trusted_intranet: boolean;
  timeout_seconds: number;
  version: number;
  last_test_at: string | null;
  last_status: string;
  last_error: string;
}

export interface AIPolicy {
  id: string;
  name: string;
  allowed_root_ids: string[];
  allowed_task_categories: string[];
  allowed_file_types: string[];
  capabilities: string[];
  allow_restricted: boolean;
  active: boolean;
  version: number;
  created_by: string;
}

export interface AIDraft {
  id: string;
  capability: string;
  title: string;
  content: string;
  sources: Array<Record<string, string>>;
  status: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Pairing {
  id: string;
  name: string;
  active: boolean;
  last_pull_at: string | null;
  created_at: string;
  expires_at: string;
}

export interface Device {
  id: string;
  name: string;
  status: "online" | "offline" | "stale" | "revoked" | "quarantined" | "updating";
  architecture: string;
  platform: string;
  kernel: string;
  app_version: string;
  agent_version: string;
  local_username: string;
  ip_address: string;
  certificate_fingerprint: string;
  certificate_expires_at: string | null;
  active: boolean;
  allow_host_access: boolean;
  allow_device_transfer: boolean;
  allow_user_shares: boolean;
  last_seen_at: string | null;
  disk_free_bytes: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface DeviceVersionStatus {
  device_id: string;
  device_name: string;
  current_version: string;
  target_version: string;
  version_state: "current" | "outdated" | "updating" | "unknown" | "revoked" | "quarantined";
  update_status: string;
  update_message: string;
  last_seen_at: string | null;
}

export interface DeviceUpdateGate {
  identified: boolean;
  device_id: string | null;
  device_name: string;
  current_version: string;
  target_version: string;
  required: boolean;
  access_allowed: boolean;
  state: string;
  status: string;
  message: string;
  package_id: string | null;
  run_id: string | null;
  release_title: string;
  release_notes: string[];
  installed_at: string | null;
}

export interface ReleaseHistory {
  id: string;
  version: string;
  schema_revision: string;
  title: string;
  release_notes: string[];
  package_id: string | null;
  status: string;
  installed_at: string;
  created_at: string;
}

export interface DeviceGrant {
  id: string;
  device_id: string;
  user_id: string | null;
  root_id: string | null;
  capabilities: string[];
  active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface Transfer {
  id: string;
  direction: "device_to_host" | "host_to_device" | "device_to_device";
  status: string;
  source_device_id: string | null;
  destination_device_id: string | null;
  source_file_id: string | null;
  destination_root_id: string | null;
  original_name: string;
  relative_path: string;
  size_bytes: number;
  sha256: string;
  chunk_size: number;
  total_chunks: number;
  completed_chunks: number;
  requested_by: string;
  approved_by: string | null;
  handled_by: string | null;
  handled_at: string | null;
  linked_entity_type: string;
  linked_entity_id: string | null;
  approval_note: string;
  expires_at: string;
  error_code: string;
  error_message: string;
  delivery_mode: "managed_inbox" | "browser" | "browser_direct" | "current_device";
  bundle_mode: "single" | "selection_zip" | "folder_zip";
  item_ids: string[];
  result_name: string;
  result_sha256: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface RuntimeContext {
  node_mode: "host" | "client" | "unknown";
  platform: "windows" | "uos" | string;
  user_role: "admin" | "staff";
  device_id: string | null;
  device_name: string;
  capabilities: string[];
}

export interface WorkbenchData {
  updated_at: string;
  dashboard: DashboardData;
  pending_transfers: Array<{
    id: string;
    name: string;
    status: string;
    direction: string;
    progress: number;
  }>;
  devices: Array<{ id: string; name: string; status: string; last_seen_at: string | null }>;
  recent_files: Array<{
    id: string;
    name: string;
    relative_path: string;
    status: string;
    availability: string;
  }>;
}

export interface SavedView {
  id: string;
  name: string;
  view_type: string;
  filters: Record<string, unknown>;
  columns: string[];
  pinned: boolean;
  owner_id: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export type ObjectType =
  | "task"
  | "workspace_file"
  | "archive_record"
  | "journal"
  | "period_report"
  | "knowledge"
  | "contact"
  | "topic";

export type CalendarEventType =
  | "task_due"
  | "task_plan"
  | "recurrence"
  | "report_boundary"
  | "reminder"
  | "holiday"
  | "adjusted_workday";

export interface CalendarEvent {
  id: string;
  event_type: CalendarEventType;
  title: string;
  start_at: string;
  end_at: string | null;
  all_day: boolean;
  object_type: ObjectType | null;
  object_id: string | null;
  route: string;
  status: string;
  owner_id: string | null;
  work_area: string;
  topic_ids: string[];
  editable: boolean;
  metadata: Record<string, unknown>;
}

export interface CalendarPreference {
  user_id: string;
  default_view: "week" | "month" | "year";
  week_starts_on: number;
  visible_event_types: CalendarEventType[];
  compact_weekends: boolean;
  version: number;
  updated_at: string;
}

export interface TodayTask {
  id: string;
  title: string;
  status: TaskStatus;
  priority: "low" | "normal" | "high" | "urgent";
  owner_id: string;
  formal_due_at: string | null;
  internal_due_at: string | null;
  planned_start_at: string | null;
  planned_end_at: string | null;
  work_area: string;
  route: string;
}

export interface TodayData {
  updated_at: string;
  dashboard: DashboardData;
  today_tasks: TodayTask[];
  overdue_tasks: TodayTask[];
  pending_review_feedback: TodayTask[];
  completed_this_week: TodayTask[];
  next_week_plan: TodayTask[];
  recent_files: Array<{
    id: string;
    name: string;
    extension: string;
    availability: string;
    route: string;
  }>;
  pending_transfers: Array<{
    id: string;
    name: string;
    status: string;
    route: string;
  }>;
  risks: {
    incomplete_materials: number;
    recurrence_anomalies: number;
    draft_reports: number;
    backup_stale: boolean;
    device_alerts: Array<{
      id: string;
      name: string;
      status: string;
      app_version: string;
      reason: string;
      route: string;
    }>;
  };
}

export interface ObjectLink {
  id: string;
  source_type: ObjectType;
  source_id: string;
  target_type: ObjectType;
  target_id: string;
  link_type:
    | "relates_to"
    | "supports"
    | "produced_by"
    | "belongs_to"
    | "mentions"
    | "supersedes";
  note: string;
  version: number;
  created_by: string;
  created_at: string;
  direction: "incoming" | "outgoing";
  title: string;
  route: string;
}

export interface ActivityEvent {
  id: string;
  object_type: ObjectType;
  object_id: string;
  event_code: string;
  event_label: string;
  actor_id: string | null;
  actor_name: string;
  actor_role: string;
  happened_at: string;
  recorded_at: string;
  event_data: Record<string, unknown>;
  correlation_id: string;
}

export interface OnboardingProgress {
  user_id: string;
  completed_steps: string[];
  dismissed: boolean;
  version: number;
  updated_at: string;
  steps: Array<{ key: string; title: string; route: string }>;
}
export type SeasonTheme = "spring" | "summer" | "autumn" | "winter";
export type ArtLevel = "standard" | "reduced";
export type ModelPackStatus = "missing" | "verifying" | "installed" | "active" | "corrupt";
export type RecommendationStatus = "pending" | "accepted" | "dismissed" | "expired";
export type RecommendationGenerator = "rules" | "embedding" | "local_llm" | "external_llm";

export interface AppearanceContext {
  effective_season: SeasonTheme;
  art_level: ArtLevel;
  reduce_motion: boolean;
  theme_mode: "auto" | "fixed";
}

export interface UserAppearance {
  user_id: string;
  art_level: ArtLevel;
  reduce_motion: boolean;
  theme_override: SeasonTheme | null;
  version: number;
  updated_at: string;
}

export interface AdminAppearance {
  theme_mode: "auto" | "fixed";
  fixed_theme: SeasonTheme;
  default_art_level: ArtLevel;
  default_reduce_motion: boolean;
  version: number;
}

export interface AIModelPack {
  id: string;
  name: string;
  version: string;
  model_id: string;
  architecture: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  capabilities: Array<"embedding" | "llm">;
  active_capabilities: Array<"embedding" | "llm">;
  min_runtime_version: string;
  estimated_memory_mb: number;
  model_source: string;
  license_name: string;
  signature_valid: boolean;
  status: ModelPackStatus;
  created_at: string;
  activated_at: string | null;
}

export interface LocalAIRuntime {
  ready: boolean;
  state: string;
  message: string;
  model_pack_id?: string | null;
  model_id?: string | null;
  embedding_pack_id?: string | null;
  llm_pack_id?: string | null;
  available_memory_mb?: number | null;
  llm_running: boolean;
  embedding_loaded: boolean;
  embedding_available: boolean;
  llm_available: boolean;
  worker_scope: "host";
  max_threads: number;
  memory_limit_mb: number;
}

export interface AIRecommendation {
  id: string;
  generator: RecommendationGenerator;
  status: RecommendationStatus;
  title: string;
  reason: string;
  content: string;
  score: number;
  object_type: string;
  object_id: string;
  object_version: number;
  route: string;
  sources: Array<Record<string, unknown>>;
  expires_at: string;
  version: number;
  created_at: string;
  updated_at: string;
}
