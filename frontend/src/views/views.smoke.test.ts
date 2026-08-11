import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Component } from "vue";
import ArcoVue from "@arco-design/web-vue";
import { dayjs } from "../utils/datetime";
import { useSessionStore } from "../stores/session";

const apiMocks = vi.hoisted(() => ({
  get: vi.fn<(path: string) => Promise<unknown>>(),
  post: vi.fn<(path: string, body?: unknown) => Promise<unknown>>(),
  patch: vi.fn<(path: string, body?: unknown) => Promise<unknown>>(),
  put: vi.fn<(path: string, body?: unknown) => Promise<unknown>>(),
  delete: vi.fn<(path: string) => Promise<unknown>>(),
}));

vi.mock("../api", () => ({
  ApiError: class ApiError extends Error {
    status = 400;
    code = "TEST_ERROR";
    fields: Record<string, string> = {};
  },
  api: apiMocks,
  downloadUrl: (path: string) => `/api/v1${path}`,
}));

import DashboardView from "./DashboardView.vue";
import ArchivesView from "./ArchivesView.vue";
import AssistantView from "./AssistantView.vue";
import CalendarView from "./CalendarView.vue";
import EfficiencyView from "./EfficiencyView.vue";
import FleetView from "./FleetView.vue";
import ForbiddenView from "./ForbiddenView.vue";
import HelpView from "./HelpView.vue";
import InboxView from "./InboxView.vue";
import InspectionView from "./InspectionView.vue";
import JournalView from "./JournalView.vue";
import KnowledgeView from "./KnowledgeView.vue";
import LoginView from "./LoginView.vue";
import MyWorkView from "./MyWorkView.vue";
import NotificationsView from "./NotificationsView.vue";
import ReportsView from "./ReportsView.vue";
import RequiredUpdateView from "./RequiredUpdateView.vue";
import SettingsView from "./SettingsView.vue";
import TaskDetailView from "./TaskDetailView.vue";
import TasksView from "./TasksView.vue";
import TemplatesView from "./TemplatesView.vue";
import TodayView from "./TodayView.vue";
import WorkbenchView from "./WorkbenchView.vue";
import WorkspaceView from "./WorkspaceView.vue";

const now = "2026-08-11T08:00:00Z";
const task = {
  id: "task-1",
  title: "测试事项",
  description: "用于验证页面渲染与操作入口",
  task_type: "standard",
  status: "in_progress",
  sensitivity: "normal",
  priority: "high",
  owner_id: "user-1",
  owner_name: "测试管理员",
  category: "组织工作",
  tags: ["测试"],
  work_area: "党建协同",
  source: "测试通知",
  source_kind: "document",
  formal_due_at: now,
  internal_due_at: now,
  planned_start_at: now,
  planned_end_at: null,
  completed_at: null,
  annual_focus: "年度重点",
  reporting_scope: "月度报告",
  reviewer_id: "user-1",
  parent_task_id: null,
  template_id: null,
  recurrence_rule_id: null,
  experience_notes: "",
  contact_ids: [],
  allow_sensitive_content: false,
  created_by: "user-1",
  updated_by: "user-1",
  created_at: now,
  updated_at: now,
  archived_at: null,
  participants: [],
  steps: [],
  materials: [],
  comments: [],
  events: [],
  subtasks: [],
  missing_required_materials: 1,
  route: "/tasks/task-1",
  version: 1,
};
const bucket = { key: "today", label: "今天必须办", count: 1, items: [task] };
const dashboard = {
  buckets: [bucket],
  updated_at: now,
  this_week_completed: [task],
  next_week_planned: [task],
  carry_over: [task],
  unread_notifications: 1,
};
const notification = { id: "notice-1", notification_type: "comment", title: "收到新评论", body: "请复核", entity_type: "task", entity_id: "task-1", read_at: null, created_at: now };
const knowledge = { id: "knowledge-1", title: "办理规范", category: "办理经验", body: "先核验材料", version: 1, updated_at: now };
const contact = { id: "contact-1", name: "测试联系人", organization: "组织科", phone: "10086", note: "工作时间联系", version: 1 };
const journal = { id: "journal-1", title: "完成初审", content: "材料已核验", occurred_at: now, created_at: now, created_by: "user-1", actor_name: "测试管理员", actor_role_label: "管理员", action_label: "记录工作", entry_type: "manual", immutable: false, task_id: "task-1", task_title: "测试事项", from_status: null, to_status: null, material_stage: null, version: 1 };
const report = { id: "report-1", title: "本周工作报告", period_type: "weekly", period_start: "2026-08-10", period_end: "2026-08-16", status: "draft", summary: "", version: 1, sections: [{ key: "completed", label: "本期完成" }, { key: "next", label: "下期计划" }], items: [] };
const template = { id: "template-1", name: "季度台账模板", category: "组织工作", task_type: "standard", description: "", active: true, version: 1, steps: ["收集材料"], materials: [{ category: "report", name: "报送稿", required: true }] };
const recurrence = { id: "recurrence-1", name: "季度台账", template_id: "template-1", owner_id: "user-1", kind: "quarterly", custom_days: null, internal_lead_days: 5, next_run_at: now, active: true, last_run_at: null, version: 1 };
const workspaceFile = { id: "file-1", root_id: "root-1", parent_id: null, name: "通知.pdf", relative_path: "通知.pdf", is_directory: false, in_scope: true, extension: "pdf", mime_type: "application/pdf", size_bytes: 100, modified_at: now, sha256: "abc", device_id: null, availability: "online", status: "indexed", content_status: "indexed", content_error_code: "", detected_type: "pdf", archive_member_count: 0, indexed_at: now, last_seen_at: now, version: 1, tags: [], links: [], preview_text: "通知正文", permissions: { browse: true, download: true, send: true, receive: true, manage_root: true } };
const workspaceRoot = { id: "root-1", name: "年度资料", source: "host", device_id: null, remote_key: "host-root", approval_status: "approved", approval_note: "", published_by_user_id: "user-1", share_scope: "team", semantic_content_enabled: false, published_at: now, selection_mode: "selected", included_paths: ["."], enabled: true, read_only: true, scan_status: "completed", last_scan_at: now, file_count: 1, directory_count: 1, error_message: "", version: 1, created_at: now, permissions: { browse: true, download: true, send: true, receive: true, manage_root: true } };
const device = { id: "device-1", name: "协同机", status: "online", architecture: "amd64", platform: "windows", kernel: "10", app_version: "1.4.2", agent_version: "1.4.2", local_username: "tester", ip_address: "192.168.1.20", certificate_fingerprint: "abc", certificate_expires_at: now, active: true, allow_host_access: true, allow_device_transfer: true, allow_user_shares: true, last_seen_at: now, disk_free_bytes: 1024, version: 1, created_at: now, updated_at: now };
const transfer = {
  id: "transfer-1", direction: "host_to_device", status: "completed", source_device_id: null,
  destination_device_id: "device-1", source_file_id: "file-1", destination_root_id: "root-1",
  original_name: "通知.pdf", relative_path: "通知.pdf", size_bytes: 100, sha256: "abc",
  chunk_size: 50, total_chunks: 2, completed_chunks: 2, requested_by: "user-1", approved_by: "user-1",
  handled_by: null, handled_at: null, linked_entity_type: "", linked_entity_id: null, approval_note: "",
  expires_at: now, error_code: "", error_message: "", delivery_mode: "managed_inbox",
  bundle_mode: "single", item_ids: ["file-1"], result_name: "通知.pdf", result_sha256: "abc",
  version: 1, created_at: now, updated_at: now,
};
const deviceGrant = { id: "device-grant-1", device_id: "device-1", user_id: "user-2", root_id: "root-1", capabilities: ["download", "share"], active: true, version: 1, created_at: now, updated_at: now };
const deviceVersionStatus = { device_id: "device-1", device_name: "协同机", current_version: "1.4.2", target_version: "1.4.2", version_state: "current", update_status: "completed", update_message: "已是最新版本", last_seen_at: now };
const archiveCategory = { id: "category-1", name: "年度考核", code: "assessment", description: "年度考核档案", record_mode: "person_year", field_schema: [{ key: "assessment_result", label: "考核结果", type: "select", required: true, options: ["优秀", "称职"] }], directory_pattern: "{year}/{category}", access_mode: "selected", allow_device_access: true, built_in: true, active: true, version: 1, created_by: "user-1", created_at: now, updated_at: now, permissions: { view: true, download: true, contribute: true, manage: true } };
const archiveAttachment = { id: "attachment-1", record_id: "record-1", blob_sha256: "abc", version_no: 1, display_name: "扫描件.pdf", note: "", status: "indexed", ocr_text: "正文", uploaded_by: "user-1", size_bytes: 100, mime_type: "application/pdf", created_at: now, updated_at: now };
const archiveRecord = { id: "record-1", category_id: "category-1", archive_year: 2026, sequence_no: 1, document_no: "党字〔2026〕1号", title: "张三年度考核", summary: "年度考核归档", involved_persons: ["张三"], source_unit: "组织科", document_date: now, person_name: "张三", person_identifier: "001", personnel_type: "公务员", organization: "测试单位", assessment_result: "优秀", tags: ["年度"], custom_fields: { assessment_result: "优秀" }, status: "active", void_reason: "", version: 1, created_by: "user-1", updated_by: "user-1", created_at: now, updated_at: now, attachment_count: 1, indexed_attachment_count: 1, duplicate_warnings: [], attachments: [archiveAttachment], links: [], permissions: { view: true, download: true, contribute: true, manage: true } };
const archiveGrant = { id: "grant-1", category_id: "category-1", user_id: "user-2", device_id: null, can_view: true, can_download: true, can_contribute: true, active: true, version: 1, created_at: now, updated_at: now };
const material = { id: "material-1", category: "final", name: "最终报送稿", required: true, not_applicable: false, not_applicable_reason: "", version: 1, versions: [], complete: false };
const recommendation = { id: "recommendation-1", generator: "rule", title: "补齐材料", reason: "缺少报送稿", content: "建议上传报送稿", object_type: "task", object_id: "task-1", object_version: 1, route: "/tasks/task-1", sources: [], score: 0.9, status: "pending", expires_at: now, version: 1, created_at: now, updated_at: now };
const draft = { id: "draft-1", capability: "summarize", title: "本地智能草稿", content: "请核验后使用", status: "draft", sources: [], version: 1, created_at: now, updated_at: now };
const today = {
  updated_at: now,
  dashboard,
  today_tasks: [task],
  overdue_tasks: [task],
  pending_review_feedback: [task],
  completed_this_week: [task],
  next_week_plan: [task],
  recent_files: [{ id: "file-1", name: "通知.pdf", extension: "pdf", availability: "online", route: "/workspace" }],
  pending_transfers: [{ id: "transfer-1", name: "通知.pdf", status: "ready", route: "/fleet/inbox" }],
  risks: {
    incomplete_materials: 1,
    recurrence_anomalies: 1,
    draft_reports: 1,
    backup_stale: true,
    device_alerts: [{ id: "device-1", name: "协同机", status: "offline", app_version: "1.4.1", reason: "离线", route: "/fleet" }],
  },
};

function responseFor(path: string): unknown {
  if (path === "/dashboard") return dashboard;
  if (path === "/today") return today;
  if (path === "/workbench") {
    return {
      updated_at: now,
      dashboard,
      pending_transfers: [{ id: "transfer-1", name: "通知.pdf", status: "ready", direction: "device_to_host", progress: 100 }],
      devices: [{ id: "device-1", name: "协同机", status: "online", last_seen_at: now }],
      recent_files: [{ id: "file-1", name: "通知.pdf", relative_path: "通知.pdf", status: "active", availability: "online" }],
    };
  }
  if (path === "/me/enablement") {
    return {
      persona: "host_admin",
      title: "主机管理员发布准备",
      summary: "按真实状态逐项检查",
      completed_count: 1,
      total_count: 2,
      next_route: "/settings",
      steps: [
        { key: "network", title: "确认主机地址", description: "已确认", route: "/settings", action_label: "去设置", complete: true },
        { key: "backup", title: "完成首次备份", description: "尚未备份", route: "/settings", action_label: "去备份", complete: false },
      ],
    };
  }
  if (path === "/tasks/my-work-summary") return { owned: 1, collaborating: 1, reviewing: 1, step_assigned: 1 };
  if (path.startsWith("/tasks?")) return { items: [task] };
  if (path.startsWith("/search?")) return { items: [task], total: 1 };
  if (path === "/bootstrap/status") return { configured: true, mode: "host", app_name: "PartyOps", host: "127.0.0.1", port: 18765, service_url: "http://127.0.0.1:18765", lan_candidates: ["192.168.1.10"] };
  if (path === "/saved-views") return [{ id: "view-1", name: "本周", view_type: "task", filters: {}, columns: [], pinned: true, owner_id: "user-1", version: 1, created_at: now, updated_at: now }];
  if (path.startsWith("/notifications")) return [notification];
  if (path === "/device/update-gate") {
    return {
      identified: true,
      required: true,
      access_allowed: false,
      state: "outdated",
      status: "ready",
      current_version: "1.4.1",
      target_version: "1.4.2",
      package_id: "package-1",
      message: "需要更新",
      release_title: "稳定性更新",
      release_notes: ["修复协同共享"],
      installed_at: now,
    };
  }
  if (path === "/auth/me") return { id: "user-1", username: "admin", display_name: "测试管理员", role: "admin", active: true, version: 1 };
  if (path === "/runtime/context") return { node_mode: "host", platform: "windows", user_role: "admin", device_id: null, device_name: "主机", capabilities: ["admin"] };
  if (path === "/calendar/preferences") return { user_id: "user-1", default_view: "week", week_starts_on: 1, visible_event_types: ["task_due"], compact_weekends: false, version: 1, updated_at: now };
  if (path.startsWith("/calendar/events?")) return [];
  if (path.startsWith("/knowledge")) return [knowledge];
  if (path === "/contacts") return [contact];
  if (path.startsWith("/work-journal")) return [journal];
  if (path.startsWith("/period-reports")) return [report];
  if (path.startsWith("/report-templates")) return [];
  if (path.startsWith("/templates")) return [template];
  if (path.startsWith("/recurrences")) return [recurrence];
  if (path === "/ai/drafts") return [draft];
  if (path.startsWith("/ai/recommendations")) return [recommendation];
  if (path.startsWith("/workspace/search")) return [workspaceFile];
  if (path === "/archives/years") return { years: [{ year: 2026, count: 1, categories: [] }] };
  if (path === "/collaboration/options") return { current_device: null, devices: [], roots: [] };
  if (path === "/health") return { status: "ok", app_version: "1.4.2", sqlite: { version: "3.53.4", safe_version: true, fts5: true } };
  if (path === "/ai/settings") return { id: null, name: "本地智能", base_url: "", model: "", has_api_key: false, enabled: false, trusted_intranet: true, timeout_seconds: 30, version: 1, last_test_at: null, last_status: "disabled", last_error: "" };
  if (path === "/ai/runtime/status") return { embedding: { active: false }, llm: { active: false }, degraded: false };
  if (path === "/admin/diagnostics") return { checks: [], generated_at: now };
  if (path === "/admin/system-status") return { services: [], storage: {}, generated_at: now };
  if (path.startsWith("/workspace/files/")) return { id: "file-1", name: "通知.pdf", relative_path: "通知.pdf", extension: "pdf", size_bytes: 100, availability: "online", permissions: { browse: true, download: true, send: true } };
  if (path.includes("/tasks/")) return task;
  return [];
}

async function mountView(component: Component, path = "/", admin = false) {
  const pinia = createPinia();
  setActivePinia(pinia);
  if (admin) {
    const session = useSessionStore();
    session.user = { id: "user-1", username: "admin", display_name: "测试管理员", role: "admin", active: true, version: 1, created_at: now };
    session.bootstrap = { configured: true, mode: "host", app_name: "PartyOps", host: "192.168.1.10", port: 18765, service_url: "http://192.168.1.10:18765", lan_candidates: ["192.168.1.10"] };
    session.runtimeContext = {
      node_mode: "host", platform: "windows", user_role: "admin", device_id: null, device_name: "主机",
      capabilities: ["admin.access", "fleet.manage", "workspace.local_share", "workspace.manage_host_roots", "updates.manage", "backups.manage", "ai.manage"],
    };
    session.ready = true;
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/:pathMatch(.*)*", component: { template: "<div />" } }],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = shallowMount(component, {
    global: {
      plugins: [pinia, router, ArcoVue],
      stubs: { RouterLink: { props: ["to"], template: "<a><slot /></a>" } },
    },
  });
  await flushPromises();
  return wrapper;
}

function setupState(wrapper: Awaited<ReturnType<typeof mountView>>): Record<string, unknown> {
  return (wrapper.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState;
}

async function runAction(state: Record<string, unknown>, name: string, ...args: unknown[]) {
  const action = state[name];
  expect(action, `${name} 应由页面显式提供`).toBeTypeOf("function");
  await (action as (...values: unknown[]) => unknown)(...args);
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.get.mockImplementation(async (path) => responseFor(path));
  apiMocks.post.mockImplementation(async (path) => responseFor(path));
  apiMocks.patch.mockImplementation(async (path) => responseFor(path));
  apiMocks.put.mockImplementation(async (path) => responseFor(path));
  apiMocks.delete.mockResolvedValue({});
});

describe("核心页面真实挂载", () => {
  it.each([
    ["今日工作台", TodayView, "/"],
    ["周工作总览", DashboardView, "/dashboard"],
    ["工作台", WorkbenchView, "/workbench"],
    ["登录", LoginView, "/login"],
    ["事项清单", TasksView, "/tasks"],
    ["事项详情", TaskDetailView, "/tasks/task-1"],
    ["快速收件", InboxView, "/inbox"],
    ["周期模板", TemplatesView, "/templates"],
    ["日历", CalendarView, "/calendar"],
    ["工作日志", JournalView, "/journal"],
    ["报告", ReportsView, "/reports"],
    ["知识与联系人", KnowledgeView, "/knowledge"],
    ["原始文件中心", WorkspaceView, "/workspace"],
    ["重要档案", ArchivesView, "/archives"],
    ["设备协同", FleetView, "/fleet"],
    ["效率工具", EfficiencyView, "/efficiency"],
    ["本地智能助手", AssistantView, "/assistant"],
    ["系统设置", SettingsView, "/settings"],
    ["我的工作", MyWorkView, "/my-work"],
    ["通知中心", NotificationsView, "/notifications"],
    ["迎检归档", InspectionView, "/inspection"],
    ["统一帮助中心", HelpView, "/help?section=setup"],
    ["无权限页", ForbiddenView, "/forbidden"],
    ["强制更新页", RequiredUpdateView, "/required-update"],
  ])("渲染%s并发出真实初始化请求", async (_name, component, path) => {
    const wrapper = await mountView(component as Component, path as string);
    expect(wrapper.text().length).toBeGreaterThan(10);
    expect(wrapper.html()).not.toContain("undefined");
    wrapper.unmount();
  });

  it("通知中心完成全部已读动作并重新加载", async () => {
    const wrapper = await mountView(NotificationsView, "/notifications");
    const buttons = wrapper.findAll("button");
    await buttons.at(-1)?.trigger("click");
    await flushPromises();
    expect(apiMocks.post).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("登录页覆盖表单校验、首次管理员创建和既有账号登录", async () => {
    apiMocks.get.mockImplementation(async (path) => {
      if (path === "/bootstrap/status") return { configured: true, mode: "host", app_name: "PartyOps", host: "192.168.1.10", port: 18765, service_url: "http://192.168.1.10:18765", lan_candidates: ["192.168.1.10"] };
      if (path === "/auth/me") throw new Error("未登录");
      if (path === "/runtime/context") return { node_mode: "host", platform: "windows", user_role: "admin", device_id: null, device_name: "主机", capabilities: ["admin.access"] };
      return responseFor(path);
    });
    apiMocks.post.mockImplementation(async (path) => path === "/auth/login" || path === "/bootstrap/host"
      ? { id: "user-1", username: "admin", display_name: "测试管理员", role: "admin", active: true, version: 1, created_at: now }
      : {});
    const wrapper = await mountView(LoginView, "/login?redirect=/tasks");
    const state = setupState(wrapper);
    await runAction(state, "submit");
    expect(Object.keys(state.fieldErrors as object).length).toBeGreaterThan(0);
    await runAction(state, "clearFieldError", "username");
    const form = state.form as { username: string; password: string; displayName: string };
    Object.assign(form, { username: "admin", password: "StrongPassword123!", displayName: "测试管理员" });
    await runAction(state, "submit");
    expect(apiMocks.post).toHaveBeenCalledWith("/auth/login", { username: "admin", password: "StrongPassword123!" });

    state.configured = false;
    Object.assign(form, { username: "first-admin", password: "AnotherStrongPassword123!", displayName: "首位管理员" });
    await runAction(state, "submit");
    expect(apiMocks.post).toHaveBeenCalledWith("/bootstrap/host", expect.objectContaining({ username: "first-admin", display_name: "首位管理员" }));
    wrapper.unmount();
  });

  it("强制更新页覆盖待更新、升级中、隔离和启动更新状态", async () => {
    const outdatedGate = responseFor("/device/update-gate") as object;
    apiMocks.get.mockResolvedValue(outdatedGate);
    apiMocks.post.mockResolvedValue({ ...outdatedGate, state: "updating", status: "applying", message: "正在安装" });
    const wrapper = await mountView(RequiredUpdateView, "/required-update?redirect=/tasks");
    const state = setupState(wrapper);
    await runAction(state, "loadGate");
    await runAction(state, "startUpdate");
    state.gate = { ...outdatedGate, state: "quarantined", status: "failed", package_id: "package-1" };
    await flushPromises();
    expect(wrapper.text()).toContain("设备不可用");
    state.gate = { ...outdatedGate, state: "outdated", status: "ready", package_id: null };
    await flushPromises();
    expect(wrapper.text()).toContain("等待主机准备");
    await runAction(state, "schedulePoll", 60_000);
    wrapper.unmount();
  });

  it("日历视图执行切换、筛选、定位与工作日维护", async () => {
    const wrapper = await mountView(CalendarView, "/calendar");
    const state = setupState(wrapper);
    await runAction(state, "setMode", "month");
    await runAction(state, "applyFilters");
    await runAction(state, "move", 1);
    await runAction(state, "goToday");
    await runAction(state, "selectDay", dayjs("2026-08-11"));
    (state.workdayForm as { title: string }).title = "调休工作日";
    await runAction(state, "saveWorkday");
    expect(apiMocks.patch).toHaveBeenCalled();
    expect(apiMocks.post).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("快速收件箱覆盖校验、识别、重置与建档", async () => {
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/intake/parse") return { title: "通知事项", formal_due_at: now, requirements: ["上传报送稿"], extracted_text: "通知正文", source_kind: "document", warnings: [], source_filename: "通知.txt", parser_label: "本地文本" };
      if (path === "/tasks") return task;
      return {};
    });
    const wrapper = await mountView(InboxView, "/inbox");
    const state = setupState(wrapper);
    await runAction(state, "parse");
    const form = state.form as { pastedText: string; title: string; ownerId: string };
    form.pastedText = "请于明天下午报送台账";
    await runAction(state, "parse");
    expect(form.title).toBe("通知事项");
    form.title = "通知事项";
    form.ownerId = "user-1";
    await runAction(state, "create");
    expect(apiMocks.post).toHaveBeenCalledWith("/tasks", expect.objectContaining({ title: "通知事项" }));
    await runAction(state, "selectFile", null);
    await runAction(state, "resetIntake");
    wrapper.unmount();
  });

  it("知识、联系人和日志执行新建、编辑、保存与删除", async () => {
    const knowledgeWrapper = await mountView(KnowledgeView, "/knowledge");
    const knowledgeState = setupState(knowledgeWrapper);
    await runAction(knowledgeState, "openEntry", knowledge);
    await runAction(knowledgeState, "createEntry");
    await runAction(knowledgeState, "deleteEntry", knowledge);
    await runAction(knowledgeState, "openContact", contact);
    await runAction(knowledgeState, "saveContact");
    await runAction(knowledgeState, "deleteContact", contact);
    knowledgeWrapper.unmount();

    const journalWrapper = await mountView(JournalView, "/journal");
    const journalState = setupState(journalWrapper);
    await runAction(journalState, "openCreate");
    await runAction(journalState, "openEdit", journal);
    await runAction(journalState, "saveEntry");
    await runAction(journalState, "markRead", notification);
    await runAction(journalState, "readAll");
    await runAction(journalState, "changeTab", "notifications");
    await runAction(journalState, "enableDesktopNotice");
    expect(apiMocks.post).toHaveBeenCalled();
    journalWrapper.unmount();
  });

  it("智能助手执行推荐、文件选择、检索、草稿复制与废弃", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async () => undefined) } });
    apiMocks.post.mockImplementation(async (path) => path === "/ai/query" ? draft : {});
    const wrapper = await mountView(AssistantView, "/assistant");
    const state = setupState(wrapper);
    (state.form as { instruction: string }).instruction = "检索本周材料";
    (state.fileKeyword as string) = "通知";
    await runAction(state, "handleRecommendation", { ...recommendation, route: "" }, "accept");
    await runAction(state, "handleRecommendation", recommendation, "dismiss");
    await runAction(state, "searchFiles");
    await runAction(state, "toggleFile", workspaceFile);
    await runAction(state, "sendQuery", false);
    await runAction(state, "copyDraft");
    await runAction(state, "discardDraft");
    expect(apiMocks.post).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("事项清单与报告执行筛选、批量、视图和发布动作", async () => {
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/tasks/batch") return { count: 1 };
      if (path === "/period-reports") return report;
      return {};
    });
    const tasksWrapper = await mountView(TasksView, "/tasks");
    const tasksState = setupState(tasksWrapper);
    await runAction(tasksState, "shiftCalendar", 1);
    await runAction(tasksState, "filterSnapshot");
    await runAction(tasksState, "createSavedView");
    await runAction(tasksState, "applySavedView", responseFor("/saved-views") instanceof Array ? (responseFor("/saved-views") as unknown[])[0] : {});
    await runAction(tasksState, "applyBuiltInSmart", "overdue");
    await runAction(tasksState, "deleteSavedView", (responseFor("/saved-views") as unknown[])[0]);
    tasksState.selectedTaskIds = ["task-1"];
    await runAction(tasksState, "applyBatch");
    tasksWrapper.unmount();

    const reportsWrapper = await mountView(ReportsView, "/reports");
    const reportsState = setupState(reportsWrapper);
    const reportItem = { id: "report-item-1", report_id: "report-1", section: "completed", task_id: "task-1", task_version: 1, snapshot: { title: "测试事项" }, manual_text: "", sort_order: 1, version: 1 };
    await runAction(reportsState, "statusLabel", "draft");
    await runAction(reportsState, "itemsFor", "completed");
    await runAction(reportsState, "createReport");
    reportsState.selectedId = report.id;
    await runAction(reportsState, "addItem");
    await runAction(reportsState, "saveSummary", "本周完成重点任务");
    await runAction(reportsState, "reportAction", "publish");
    await runAction(reportsState, "removeItem", reportItem);
    expect(apiMocks.post).toHaveBeenCalled();
    reportsWrapper.unmount();
  });

  it("周期模板执行实例化、模板维护、周期规则与异常日处理", async () => {
    const recurrencePreview = { occurrence_at: now, effective_at: now, action: "", reason: "" };
    apiMocks.get.mockImplementation(async (path) => path.includes("/preview?") ? [recurrencePreview] : responseFor(path));
    apiMocks.post.mockImplementation(async (path) => {
      if (path.includes("/instantiate")) return task;
      if (path === "/recurrences/run-due") return ["task-1"];
      return {};
    });
    const wrapper = await mountView(TemplatesView, "/templates");
    const state = setupState(wrapper);
    await runAction(state, "openInstantiate", template);
    await runAction(state, "instantiate");
    await runAction(state, "openTemplate", template);
    await runAction(state, "templatePayload");
    await runAction(state, "saveTemplate");
    await runAction(state, "openRecurrenceCreate");
    Object.assign(state.recurrenceForm as object, { name: "季度台账", template_id: "template-1", owner_id: "user-1", next_run_at: now, kind: "custom_days", custom_days: 30 });
    await runAction(state, "saveRecurrence");
    await runAction(state, "toggleRecurrence", recurrence);
    await runAction(state, "openRecurrenceSettings", recurrence);
    await runAction(state, "saveRecurrenceSettings");
    await runAction(state, "openRecurrencePreview", recurrence);
    await runAction(state, "openRecurrenceException", recurrencePreview, "skip");
    (state.exceptionForm as { reason: string }).reason = "节假日暂停一次";
    await runAction(state, "saveRecurrenceException");
    await runAction(state, "openRecurrenceException", recurrencePreview, "reschedule");
    Object.assign(state.exceptionForm as object, { reason: "改至工作日", rescheduled_at: now });
    await runAction(state, "saveRecurrenceException");
    await runAction(state, "runDue");
    expect(apiMocks.post).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("事项详情执行编辑、状态、评论、步骤、材料、协办和冲突草稿闭环", async () => {
    apiMocks.post.mockImplementation(async (path) => path === "/tasks" || path.includes("/actions") || path.includes("/participants") || path.includes("/versions") || path.includes("/apply") ? task : {});
    apiMocks.patch.mockImplementation(async (path) => path === "/tasks/task-1" ? task : {});
    apiMocks.delete.mockResolvedValue(task);
    const wrapper = await mountView(TaskDetailView, "/tasks/task-1");
    const state = setupState(wrapper);

    await runAction(state, "materialCategoryLabel", "final");
    await runAction(state, "openEdit");
    await runAction(state, "saveEdit");
    await runAction(state, "openAction", "complete");
    await runAction(state, "applyAction");
    await runAction(state, "toggleStep", "step-1", true, 1);

    state.comment = "请协办人复核";
    state.mentionedUserIds = ["user-1"];
    await runAction(state, "addComment");
    const materialForm = state.materialForm as { name: string; category: string };
    materialForm.name = "最终报送稿";
    materialForm.category = "final";
    await runAction(state, "addMaterial");
    (state.stepForm as { title: string }).title = "复核材料";
    await runAction(state, "addStep");

    await runAction(state, "openSubtask");
    const subtaskForm = state.subtaskForm as { title: string; owner_id: string };
    subtaskForm.title = "汇总附件";
    subtaskForm.owner_id = "user-1";
    await runAction(state, "addSubtask");
    state.participantUserId = "user-1";
    await runAction(state, "addParticipant");
    await runAction(state, "removeParticipant", "participant-1");

    await runAction(state, "openNotApplicable", material);
    state.notApplicableReason = "本事项不产生纸质附件";
    await runAction(state, "markNotApplicable");
    await runAction(state, "openUpload", material);
    state.uploadFile = new File(["正文"], "报送稿.txt", { type: "text/plain" });
    await runAction(state, "toggleFinal", true);
    await runAction(state, "uploadVersion");
    state.conflict = { draft_id: "draft-1", current_version: "2", current: { title: "新版" }, submitted: { title: "草稿" } };
    await runAction(state, "applyConflictDraft");

    expect(apiMocks.post).toHaveBeenCalled();
    expect(apiMocks.patch).toHaveBeenCalled();
    expect(apiMocks.delete).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("文件中心执行目录、搜索、范围、标签、关联、下载、共享和跨机发送闭环", async () => {
    apiMocks.get.mockImplementation(async (path) => {
      if (path === "/workspace/roots") return [workspaceRoot];
      if (path.startsWith("/workspace/files?")) return [workspaceFile];
      if (path === "/workspace/files/file-1") return workspaceFile;
      if (path.startsWith("/workspace/search")) return [workspaceFile];
      if (path === "/tasks?page_size=100") return { items: [task] };
      if (path === "/collaboration/options") return { current_device: null, devices: [device], roots: [{ ...workspaceRoot, id: "remote-root", source: "device", device_id: "device-1" }] };
      if (path.includes("/folder-options")) return [{ path: ".", name: "全部", depth: 0, file_count: 1, directory_count: 1 }];
      if (path === "/admin/jobs?limit=20") return [{ id: "job-1", status: "completed", message: "扫描完成", payload: {} }];
      if (path === "/transfers") return [transfer];
      if (path.includes("/members")) return [{ id: "member-1", root_id: "root-1", user_id: "user-2", can_browse: true, can_download: true, can_send: true, active: true, version: 1, created_at: now, updated_at: now }];
      if (path === "/collaboration/users") return [{ id: "user-2", username: "staff", display_name: "协同人员", role: "staff", active: true, version: 1, created_at: now }];
      return responseFor(path);
    });
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/workspace/roots") return workspaceRoot;
      if (path.includes("/scan")) return { id: "job-1" };
      if (path === "/workspace/downloads") return { transfer_id: "transfer-1", status: "completed", delivery: "browser", content_url: "/api/v1/transfers/transfer-1/content" };
      if (path.includes("/links") || path.includes("/freeze")) return workspaceFile;
      if (path === "/transfers") return transfer;
      if (path.includes("open-local") || path === "/workspace/local-share-actions") return { open_uri: "#opened" };
      return {};
    });
    apiMocks.patch.mockImplementation(async (path) => {
      if (path.includes("/selection")) return { id: "job-1" };
      if (path.includes("/sharing")) return workspaceRoot;
      return workspaceFile;
    });
    apiMocks.put.mockResolvedValue([]);

    const wrapper = await mountView(WorkspaceView, "/workspace");
    const state = setupState(wrapper);
    await runAction(state, "formatSize", 10);
    await runAction(state, "formatSize", 2048);
    await runAction(state, "formatSize", 2 * 1024 * 1024);
    await runAction(state, "formatSize", 2 * 1024 * 1024 * 1024);
    await runAction(state, "statusLabel", "indexed");
    await runAction(state, "pdfTypeLabel", "Scanned");
    await runAction(state, "rootStatusLabel", "completed");
    await runAction(state, "changeRoot", "root-1");
    await runAction(state, "openItem", workspaceFile);
    await runAction(state, "goToLevel", -1);
    state.keyword = "通知";
    await runAction(state, "search");
    await runAction(state, "createRoot");
    await runAction(state, "pollScan", "job-1");
    await runAction(state, "openSelection");
    await runAction(state, "folderChecked", ".");
    await runAction(state, "toggleFolder", ".", true);
    await runAction(state, "toggleFolder", ".", false);
    state.selectedFolderPaths = ["."];
    await runAction(state, "saveSelection");
    await runAction(state, "scanRoot");

    state.selectedFile = workspaceFile;
    state.tagsText = "通知、重点";
    await runAction(state, "saveTags");
    state.linkTaskId = "task-1";
    await runAction(state, "linkTask");
    state.selectedFile = workspaceFile;
    await runAction(state, "freezeFile");
    await runAction(state, "freezeSelectedFile");
    await runAction(state, "rootSourceLabel", workspaceRoot);
    const remoteWorkspaceRoot = { ...workspaceRoot, id: "remote-root", source: "device", device_id: "device-1", remote_key: "device-share" };
    await runAction(state, "rootSourceLabel", remoteWorkspaceRoot);
    state.roots = [remoteWorkspaceRoot];
    state.selectedRootId = "remote-root";
    state.selectedFile = { ...workspaceFile, root_id: "remote-root", device_id: "device-1" };
    await runAction(state, "freezeSelectedFile");
    await runAction(state, "openLocalShareManager");
    state.selectedFile = { ...workspaceFile, name: "未知.bin", extension: "bin", mime_type: "application/octet-stream" };
    await runAction(state, "openDocumentPreview");
    expect(state.previewError).toBeTruthy();
    await runAction(state, "closeDocumentPreview");

    const previewFile = { ...workspaceFile, name: "通知.docx", extension: "docx", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
    state.selectedFile = previewFile;
    state.roots = [workspaceRoot];
    state.selectedRootId = "root-1";
    await runAction(state, "prepareDocumentPreviewContent", previewFile);
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), { status: 200 }));
    await runAction(state, "readPreviewSource", "/api/v1/workspace/files/file-1/download");
    class PreviewWorker {
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      terminate = vi.fn();
      postMessage(payload: { requestId: string }) {
        queueMicrotask(() => this.onmessage?.({ data: { requestId: payload.requestId, ok: true, format: "docx", engine: "anydoc", engineVersion: "0.1.7", markdown: "# 通知正文", warnings: [] } } as MessageEvent));
      }
    }
    vi.stubGlobal("Worker", PreviewWorker);
    await runAction(state, "openDocumentPreview");
    expect(state.previewHtml).toContain("通知正文");
    await runAction(state, "previewSelectedFile");
    await runAction(state, "disposePreviewRuntime");
    fetchSpy.mockRestore();

    state.files = [workspaceFile];
    await runAction(state, "toggleChecked", "file-1", true);
    await runAction(state, "toggleChecked", "file-1", false);
    await runAction(state, "createDownload", ["file-1"], "current_device", false);
    await runAction(state, "createDownload", ["file-1"], "browser", false);
    const targetWindow = { closed: false, location: { href: "" } };
    await runAction(state, "waitForBrowserTransfer", "transfer-1", "/api/v1/transfers/transfer-1/content", targetWindow, true);
    await runAction(state, "waitForTransferCompletion", "transfer-1");
    await runAction(state, "openSharing", workspaceRoot);
    (state.sharingForm as { share_scope: string }).share_scope = "selected";
    state.sharingUserIds = ["user-2"];
    await runAction(state, "saveSharing");
    state.selectedFile = workspaceFile;
    await runAction(state, "downloadFile");
    await runAction(state, "openSend");
    (state.sendForm as { destination_device_id: string }).destination_device_id = "device-1";
    await runAction(state, "sendToDevice");
    state.selectedFile = workspaceFile;
    await runAction(state, "openWithDefaultApp");
    expect(apiMocks.post).toHaveBeenCalled();
    expect(apiMocks.patch).toHaveBeenCalled();
    expect(apiMocks.put).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("重要档案执行年度、录入、字段、类别、授权、历史、作废与恢复闭环", async () => {
    apiMocks.get.mockImplementation(async (path) => {
      if (path === "/archives/categories") return [archiveCategory];
      if (path === "/archives/years") return { years: [{ year: 2026, categories: [{ id: "category-1", name: "年度考核", record_count: 1, attachment_count: 1, missing_attachment_count: 0, last_updated: now }] }] };
      if (path.startsWith("/archives/records?") || path.includes("archive_year=")) return [archiveRecord];
      if (path.startsWith("/archives/records/record-1/history")) return [{ revision_no: 1, change_note: "建立档案", created_at: now, snapshot: {} }];
      if (path.startsWith("/archives/records/")) return archiveRecord;
      if (path.includes("/grants")) return [archiveGrant];
      if (path === "/admin/users") return [{ id: "user-2", username: "staff", display_name: "协同人员", role: "staff", active: true, version: 1, created_at: now }];
      if (path === "/admin/devices") return [device];
      return responseFor(path);
    });
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/archives/records" || path.includes("/void") || path.includes("/restore")) return archiveRecord;
      if (path === "/archives/categories") return archiveCategory;
      if (path.includes("/grants")) return archiveGrant;
      if (path.includes("/attachments")) return archiveAttachment;
      return {};
    });
    apiMocks.patch.mockImplementation(async (path) => path.includes("/grants/") ? archiveGrant : path.includes("/categories/") ? archiveCategory : archiveRecord);

    const wrapper = await mountView(ArchivesView, "/archives");
    const state = setupState(wrapper);
    await runAction(state, "errorFor", "title", "document_no");
    await runAction(state, "fieldElementId", "custom.字段");
    await runAction(state, "categoryName", "category-1");
    await runAction(state, "formatDate", now);
    await runAction(state, "statusLabel", "active");
    await runAction(state, "setCustomField", "assessment_result", "优秀");
    await runAction(state, "customFieldText", "assessment_result");
    await runAction(state, "selectRecord", archiveRecord);
    await runAction(state, "chooseYear", 2026);
    await runAction(state, "chooseCategory", "category-1");
    await runAction(state, "openCreate");
    const recordForm = state.recordForm as { category_id: string; archive_year: number; title: string; assessment_result: string };
    recordForm.category_id = "category-1";
    recordForm.archive_year = 2026;
    recordForm.title = "张三年度考核";
    recordForm.assessment_result = "优秀";
    await runAction(state, "payloadFromForm");
    await runAction(state, "saveRecord");
    state.selectedRecord = archiveRecord;
    await runAction(state, "openEdit");
    await runAction(state, "saveRecord");

    await runAction(state, "openCategoryManager");
    await runAction(state, "editCategory", archiveCategory);
    await runAction(state, "grantTargetLabel", archiveGrant);
    (state.grantForm as { target_id: string }).target_id = "user-2";
    await runAction(state, "saveGrant");
    await runAction(state, "updateGrant", archiveGrant, { can_contribute: false });
    const newField = state.newField as { key: string; label: string; type: string; required: boolean; options: string };
    Object.assign(newField, { key: "remark", label: "备注", type: "textarea", required: false, options: "" });
    await runAction(state, "addField");
    const categoryForm = state.categoryForm as { name: string; code: string };
    categoryForm.name = "年度考核";
    categoryForm.code = "assessment";
    await runAction(state, "saveCategory");
    state.editingCategory = archiveCategory;
    await runAction(state, "saveCategory");

    state.fileInput = { click: vi.fn() };
    await runAction(state, "chooseUpload");
    state.selectedRecord = archiveRecord;
    await runAction(state, "openHistory");
    state.voidReason = "重复录入";
    await runAction(state, "voidRecord");
    state.selectedRecord = archiveRecord;
    await runAction(state, "restoreRecord");
    state.selectedRecord = archiveRecord;
    await runAction(state, "voidAttachment", archiveAttachment);
    await runAction(state, "pollAttachmentRecognition", "record-1", 0);
    await runAction(state, "exportYear");
    expect(apiMocks.post).toHaveBeenCalled();
    expect(apiMocks.patch).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("设备协同执行入网、目录审批、授权、传输、接收转换和设备治理闭环", async () => {
    const deviceRoot = {
      ...workspaceRoot,
      id: "device-root-1",
      name: "协同机共享资料",
      source: "device",
      device_id: "device-1",
      remote_key: "share-1",
      permissions: { browse: true, download: true, send: true, receive: true, share: true, upload: true, manage_root: true },
    };
    const pendingRoot = { id: "pending-root-1", name: "待审批资料", device_id: "device-1", remote_key: "pending-1", approval_status: "pending", approval_note: "", enabled: false, file_count: 0, last_scan_at: null, version: 1 };
    const enrolled = { status: "enrolled", device_id: "device-1", device_name: "协同机", device_status: "online", last_seen_at: now };

    apiMocks.get.mockImplementation(async (path) => {
      if (path === "/admin/devices") return [device];
      if (path === "/admin/devices/config") return { max_devices: 20 };
      if (path === "/admin/device-grants") return [deviceGrant];
      if (path === "/admin/devices/version-status") return [deviceVersionStatus];
      if (path === "/transfers") return [transfer];
      if (path === "/workspace/roots") return [workspaceRoot, deviceRoot];
      if (path === "/admin/workspace/remote-roots") return [pendingRoot];
      if (path === "/admin/users") return [{ id: "user-2", username: "staff", display_name: "协同人员", role: "staff", active: true, version: 1, created_at: now }];
      if (path.startsWith("/workspace/search")) return [{ ...workspaceFile, root_id: "device-root-1", device_id: "device-1" }];
      if (path === "/tasks?page_size=100") return { items: [{ ...task, materials: [material] }] };
      if (path === "/archives/years") return { years: [{ year: 2026 }] };
      if (path.startsWith("/archives/records?")) return [archiveRecord];
      if (path === "/admin/devices/enrollments/enroll-1/status") return enrolled;
      return responseFor(path);
    });
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/admin/devices/enrollments") return { id: "enroll-1", code: "PARTYOPS-TEST-CODE", host_url: "http://192.168.1.10:18765", expires_at: now, ca_fingerprint: "abc" };
      if (path === "/workspace/local-share-actions") return { open_uri: "#share-manager" };
      if (path === "/admin/device-grants") return deviceGrant;
      if (path === "/transfers" || path.includes("/freeze") || path.includes("/attach")) return transfer;
      return {};
    });
    apiMocks.patch.mockImplementation(async (path) => {
      if (path.includes("/remote-roots/")) return pendingRoot;
      if (path.includes("/device-grants/")) return { ...deviceGrant, active: false, version: 2 };
      if (path.includes("/transfers/")) return transfer;
      if (path.includes("/devices/")) return { ...device, allow_user_shares: false, version: 2 };
      return {};
    });

    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async () => undefined) } });
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    const wrapper = await mountView(FleetView, "/fleet", true);
    const state = setupState(wrapper);

    await runAction(state, "load");
    await runAction(state, "approveRoot", pendingRoot, "approved");
    await runAction(state, "openLocalShareManager");
    const grantForm = state.grantForm as { device_id: string; user_id: string; root_id: string; capabilities: string[] };
    Object.assign(grantForm, { device_id: "device-1", user_id: "user-2", root_id: "device-root-1", capabilities: ["download", "share"] });
    await runAction(state, "createGrant");
    await runAction(state, "toggleGrant", { ...deviceGrant });

    const transferForm = state.transferForm as { direction: string; source_root_id: string; source_file_id: string; destination_device_id: string; destination_root_id: string; require_approval: boolean };
    Object.assign(transferForm, { direction: "device_to_host", source_root_id: "device-root-1", source_file_id: "file-1", destination_device_id: "", destination_root_id: "", require_approval: false });
    await runAction(state, "loadSourceFiles", "device-root-1");
    transferForm.source_file_id = "file-1";
    await runAction(state, "handleSourceRootChange", "device-root-1");
    transferForm.source_file_id = "file-1";
    await runAction(state, "createTransfer");
    await runAction(state, "resetTransferSource");
    await runAction(state, "openTransfer");
    await runAction(state, "freezeTransfer", { ...transfer });
    await runAction(state, "openAttach", { ...transfer });
    const attachForm = state.attachForm as { target_type: string; target_id: string };
    Object.assign(attachForm, { target_type: "task_material", target_id: "material-1" });
    await runAction(state, "attachReceivedFile");

    await runAction(state, "versionStatus", "device-1");
    for (const status of ["current", "outdated", "updating", "unknown", "revoked", "quarantined", "other"]) await runAction(state, "versionStateLabel", status);
    await runAction(state, "switchValue", "true");
    await runAction(state, "switchValue", 0);
    await runAction(state, "capabilityLabels", ["download", "share"]);
    await runAction(state, "transferDirectionLabel", { ...transfer, delivery_mode: "browser", source_device_id: "device-1" });
    await runAction(state, "transferDirectionLabel", { ...transfer, delivery_mode: "current_device", source_device_id: null });
    await runAction(state, "transferDirectionLabel", { ...transfer, delivery_mode: "managed_inbox", direction: "device_to_device" });
    await runAction(state, "transferProgress", { ...transfer, status: "running", completed_chunks: 1, total_chunks: 2 });
    await runAction(state, "transferAction", { ...transfer }, "approve");

    await runAction(state, "openEnrollment");
    await runAction(state, "createEnrollment");
    await runAction(state, "copyEnrollmentCode");
    await runAction(state, "checkEnrollmentStatus", "enroll-1");
    await runAction(state, "stopEnrollmentWatch");
    await runAction(state, "saveDevice", { ...device }, "allow_user_shares", false);
    await runAction(state, "saveMaxDevices");
    await runAction(state, "rotateCertificate", { ...device });
    await runAction(state, "requestDeleteDevice", { ...device });
    await runAction(state, "deleteDevice");

    expect(apiMocks.get).toHaveBeenCalled();
    expect(apiMocks.post).toHaveBeenCalled();
    expect(apiMocks.patch).toHaveBeenCalled();
    expect(apiMocks.delete).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("效率工具执行专题、自动建议、工作日历、文档比较、查重、报告模板与 AI 审批", async () => {
    const topic = { id: "topic-1", name: "年度专项", description: "年度材料归集", task_ids: ["task-1"], file_ids: ["file-1"], journal_ids: ["journal-1"], contact_ids: ["contact-1"], version: 1 };
    const rule = { id: "rule-1", name: "归档提醒", trigger: "workspace_file_indexed", conditions: {}, actions: {}, enabled: true, version: 1 };
    const calendarEntry = { id: "calendar-1", date_key: "2026-08-11", title: "调休工作日", kind: "workday", is_workday: true, note: "", version: 1 };
    const comparison = { id: "comparison-1", left_file_id: "file-1", right_file_id: "file-2", comparison_type: "text", result: { changed: true, lines: ["新增一行"] }, created_at: now };
    const duplicate = { id: "duplicate-1", algorithm: "sha256", fingerprint: "abc", file_ids: ["file-1", "file-2"], status: "open", created_at: now };
    const reportTemplate = { id: "report-template-1", name: "周报模板", period_type: "week", description: "标准周报", sections: ["completed", "next_plan"], active: true, version: 1 };
    apiMocks.get.mockImplementation(async (path) => {
      if (path === "/tasks?page_size=100") return { items: [task] };
      if (path === "/workspace/search?limit=200") return [workspaceFile, { ...workspaceFile, id: "file-2", name: "通知-修改版.pdf" }];
      if (path === "/contacts") return [contact];
      if (path === "/work-journal?limit=200") return [journal];
      if (path === "/topics") return [topic];
      if (path === "/automation-rules") return [rule];
      if (path.startsWith("/work-calendar?")) return [calendarEntry];
      if (path === "/document-comparisons") return [comparison];
      if (path === "/duplicates") return [duplicate];
      if (path === "/report-templates") return [reportTemplate];
      if (path === "/ai/approvals") return [draft];
      return responseFor(path);
    });
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/topics") return topic;
      if (path === "/duplicates/scan") return [duplicate];
      if (path === "/document-comparisons") return comparison;
      return {};
    });

    const wrapper = await mountView(EfficiencyView, "/efficiency");
    const state = setupState(wrapper);
    await runAction(state, "splitValues", "通知、台账,报告");
    const topicForm = state.topicForm as { name: string; description: string; task_ids: string[]; file_ids: string[]; journal_ids: string[]; contact_ids: string[] };
    Object.assign(topicForm, { name: "年度专项", description: "年度材料归集", task_ids: ["task-1"], file_ids: ["file-1"], journal_ids: ["journal-1"], contact_ids: ["contact-1"] });
    await runAction(state, "createTopic");

    state.activeTab = "automation";
    await runAction(state, "load");
    const ruleForm = state.ruleForm as { name: string; trigger: string; name_contains: string; path_contains: string; extensions: string; task_title: string; material_category: string; tags: string };
    Object.assign(ruleForm, { name: "归档提醒", trigger: "workspace_file_indexed", name_contains: "通知", path_contains: "年度", extensions: "pdf,docx", task_title: "归档事项", material_category: "final", tags: "重点、归档" });
    await runAction(state, "createRule");
    await runAction(state, "toggleRule", rule);
    await runAction(state, "deleteRule", rule);

    state.activeTab = "calendar";
    await runAction(state, "load");
    (state.calendarForm as { title: string }).title = "调休工作日";
    await runAction(state, "createCalendarEntry");
    await runAction(state, "deleteCalendarEntry", calendarEntry);

    state.activeTab = "documents";
    await runAction(state, "load");
    Object.assign(state.compareForm as object, { left_file_id: "file-1", right_file_id: "file-2", comparison_type: "text" });
    await runAction(state, "compareDocuments");
    await runAction(state, "scanDuplicates");

    state.activeTab = "templates";
    await runAction(state, "load");
    await runAction(state, "moveSection", 0, 1);
    await runAction(state, "moveSection", 0, -1);
    await runAction(state, "toggleTemplateSection", "risk");
    await runAction(state, "toggleTemplateSection", "risk");
    await runAction(state, "editTemplate", reportTemplate);
    await runAction(state, "saveTemplate");
    Object.assign(state.templateForm as object, { name: "月报模板", period_type: "month", description: "月度模板", sections: ["completed"] });
    await runAction(state, "saveTemplate");

    state.activeTab = "ai";
    await runAction(state, "load");
    await runAction(state, "approveDraft", draft);
    expect(apiMocks.post).toHaveBeenCalled();
    expect(apiMocks.patch).toHaveBeenCalled();
    expect(apiMocks.delete).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("系统设置执行诊断、偏好、模型、更新、备份、用户、配对与 AI 白名单闭环", async () => {
    const reminder = { user_id: "user-1", enabled: true, advance_days: 3, reminder_days: [7, 3, 1, 0], quiet_start: "22:00", quiet_end: "07:30", desktop_enabled: true, remind_overdue: true, remind_review: true, remind_feedback: true, remind_materials: true, version: 1, updated_at: now };
    const userAppearance = { user_id: "user-1", art_level: "standard", reduce_motion: false, theme_override: null, version: 1, updated_at: now };
    const adminAppearance = { theme_mode: "auto", fixed_theme: "spring", default_art_level: "standard", default_reduce_motion: false, version: 1 };
    const appearanceContext = { effective_season: "spring", art_level: "standard", reduce_motion: false, theme_mode: "auto" };
    const backup = { id: "backup-1", filename: "partyops-backup.zip", kind: "manual", size_bytes: 1024, sha256: "abc", status: "completed", message: "已完成", created_at: now, completed_at: now };
    const pairing = { id: "pairing-1", name: "协同终端", active: true, last_pull_at: null, created_at: now, expires_at: now };
    const diagnostics = { mode: "host", bind: { host: "0.0.0.0", port: 18765 }, service_url: "http://192.168.1.10:18765", lan_candidates: ["192.168.1.10"], disk: { total_bytes: 1024 ** 4, free_bytes: 512 * 1024 ** 3 }, counts: { users: 2, tasks: 1, attachments: 1, unique_files: 1 }, latest_backup: { id: "backup-1", created_at: now, status: "completed" }, fault_tips: [] };
    const localRuntime = { ready: true, state: "ready", message: "本地能力可用", model_pack_id: "model-pack-1", model_id: "bge-small-zh-v1.5", embedding_pack_id: "model-pack-1", llm_pack_id: null, available_memory_mb: 8192, llm_running: false, embedding_loaded: true, embedding_available: true, llm_available: false, worker_scope: "host", max_threads: 4, memory_limit_mb: 2048 };
    const systemStatus = {
      status: "ok", ready: true, readiness: { database: true, foreign_keys: true, schema: true, data_directories: true, backup_fresh: true },
      mode: "host", app_version: "1.4.2", schema_revision: "0017", architecture: "amd64", system: "Windows", kernel: "10.0", platform: "windows", uptime_seconds: 3600,
      service: { host: "0.0.0.0", port: 18765, agent_port: 18766, url: "http://192.168.1.10:18765", sse_clients: 1, tls_enabled: true },
      storage: { database_bytes: 1024, attachments_bytes: 2048, backups_bytes: 4096, indexed_files: 1 }, database: { quick_check: "ok", foreign_key_errors: 0, migration_head: "0017" },
      devices: { max: 20, total: 1, online: 1, items: [{ id: "device-1", name: "协同机", status: "online", last_seen_at: now }] },
      projections: [{ name: "dashboard", status: "ready", processed_count: 1, failed_count: 0, last_error: "", last_run_at: now }],
      workspace_roots: [{ id: "root-1", name: "年度资料", enabled: true, scan_status: "completed", file_count: 1, last_scan_at: now }], latest_job: null,
      backup: { last_at: now, last_status: "completed", next_schedule: "02:00" }, ai: { configured: true, enabled: true, trusted_intranet: true, last_status: "ok", last_test_at: now, local: localRuntime },
      load_average: [0.1, 0.2, 0.3], executable_frozen: true,
    };
    const aiProvider = { id: "provider-1", name: "本地模型", base_url: "http://127.0.0.1:18767", model: "qwen3", has_api_key: true, enabled: true, trusted_intranet: true, timeout_seconds: 30, version: 1, last_test_at: now, last_status: "ok", last_error: "" };
    const aiPolicy = { id: "policy-1", name: "默认只读策略", allowed_root_ids: ["root-1"], allowed_task_categories: ["组织工作"], allowed_file_types: [".pdf"], capabilities: ["search", "summarize"], allow_restricted: false, active: true, version: 1, created_by: "user-1" };
    const modelPack = { id: "model-pack-1", name: "中文向量模型", version: "1.0.0", model_id: "BAAI/bge-small-zh-v1.5", architecture: "onnx", filename: "bge.partyops-modelpack", sha256: "abc", size_bytes: 1024, capabilities: ["embedding"], active_capabilities: ["embedding"], min_runtime_version: "1.4.2", estimated_memory_mb: 512, model_source: "BAAI", license_name: "MIT", signature_valid: true, status: "active", created_at: now, activated_at: now };
    const updatePackage = { id: "package-1", filename: "partyops_1.4.2.partyops-update", version: "1.4.2", schema_revision: "0017", sha256: "abc", signature_valid: true, status: "ready", created_at: now, manifest: { release_title: "稳定版", release_notes: ["增强协同共享"] } };
    const updateRun = { id: "run-1", package_id: "package-1", target_device_id: "device-1", status: "completed", progress: 100, message: "完成", created_at: now };
    const releaseHistory = { id: "history-1", version: "1.4.2", schema_revision: "0017", title: "稳定版", release_notes: ["增强协同共享"], package_id: "package-1", status: "installed", installed_at: now, created_at: now };

    apiMocks.get.mockImplementation(async (path) => {
      if (path === "/health") return responseFor(path);
      if (path === "/reminders/preferences") return reminder;
      if (path === "/me/appearance") return userAppearance;
      if (path === "/appearance/context") return appearanceContext;
      if (path === "/admin/appearance") return adminAppearance;
      if (path === "/admin/users") return [{ id: "user-2", username: "staff", display_name: "协同人员", role: "staff", active: true, version: 1, created_at: now }];
      if (path === "/backups") return [backup];
      if (path.startsWith("/admin/audit")) return [{ id: 1, actor_id: "user-1", action: "user.create", entity_type: "user", entity_id: "user-2", detail: {}, created_at: now }];
      if (path === "/admin/pairings") return [pairing];
      if (path === "/admin/diagnostics") return diagnostics;
      if (path === "/admin/system-status") return systemStatus;
      if (path === "/ai/settings") return aiProvider;
      if (path === "/ai/policies") return [aiPolicy];
      if (path === "/workspace/roots") return [workspaceRoot];
      if (path === "/admin/updates") return [updatePackage];
      if (path === "/admin/update-runs") return [updateRun];
      if (path === "/admin/devices") return [device];
      if (path === "/admin/update-history") return [releaseHistory];
      if (path === "/admin/ai/model-packs") return [modelPack];
      if (path === "/ai/runtime/status") return localRuntime;
      return responseFor(path);
    });
    apiMocks.patch.mockImplementation(async (path) => {
      if (path === "/me/appearance") return { ...userAppearance, version: 2 };
      if (path === "/admin/appearance") return { ...adminAppearance, version: 2 };
      if (path === "/reminders/preferences") return { ...reminder, version: 2 };
      if (path === "/ai/settings") return { ...aiProvider, version: 2 };
      return {};
    });
    apiMocks.post.mockImplementation(async (path) => {
      if (path === "/admin/pairings") return { token: "pair-token", config: { service_url: "http://192.168.1.10:18765", token: "pair-token" } };
      if (path === "/admin/ai/model-packs" || path.includes("/activate")) return modelPack;
      return {};
    });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("PartyOps 运行正常", { status: 200 }));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn(async () => undefined) } });
    const wrapper = await mountView(SettingsView, "/settings", true);
    const state = setupState(wrapper);

    await runAction(state, "load");
    await runAction(state, "saveAppearance");
    await runAction(state, "saveAdminAppearance");
    await runAction(state, "uploadModelPack", new File(["model"], "bge.partyops-modelpack"));
    await runAction(state, "activateModelPack", modelPack, "embedding");
    await runAction(state, "deactivateModelCapability", "embedding");
    await runAction(state, "rebuildProjections");
    await runAction(state, "uploadUpdate", new File(["update"], "partyops_1.4.2.partyops-update"));
    await runAction(state, "applyUpdate", updatePackage);
    await runAction(state, "scheduleUpdatePoll", 60_000);
    await runAction(state, "startUpdateMonitor", "package-1", "1.4.2", false);
    await runAction(state, "pollUpdateProgress");
    localStorage.setItem("partyops.pending-update", "{broken");
    await runAction(state, "pollUpdateProgress");
    await runAction(state, "updateTargetName", null);
    await runAction(state, "updateTargetName", "device-1");

    await runAction(state, "createBackup");
    await runAction(state, "saveReminder");
    await runAction(state, "saveAISettings");
    await runAction(state, "testAISettings");
    await runAction(state, "splitValues", "组织工作、年度重点,年度重点");
    await runAction(state, "saveAIPolicy");
    Object.assign(state.userForm as object, { username: "new-staff", display_name: "新用户", password: "StrongPassword123!", role: "staff" });
    await runAction(state, "createUser");
    const editableUser = { id: "user-2", username: "staff", display_name: "协同人员", role: "staff", active: true, version: 1, created_at: now };
    await runAction(state, "openUserEdit", editableUser);
    (state.editUserForm as { password: string }).password = "NewStrongPassword123!";
    await runAction(state, "saveUserEdit");
    await runAction(state, "createPairing");
    await runAction(state, "copyPairingConfig");
    await runAction(state, "revokePairing", pairing);
    await runAction(state, "verifyBackup", backup);
    apiMocks.post.mockRejectedValueOnce(new Error("恢复演练失败"));
    await runAction(state, "restoreBackup", backup);
    await runAction(state, "importBackup", new File(["backup"], "partyops-backup.zip"));
    await runAction(state, "loadLogs");

    expect(apiMocks.get).toHaveBeenCalled();
    expect(apiMocks.post).toHaveBeenCalled();
    expect(apiMocks.patch).toHaveBeenCalled();
    expect(apiMocks.delete).toHaveBeenCalled();
    expect(fetchSpy).toHaveBeenCalled();
    wrapper.unmount();
    fetchSpy.mockRestore();
    localStorage.removeItem("partyops.pending-update");
  });
});
