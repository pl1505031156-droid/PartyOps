<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { IconCloudDownload, IconDownload, IconPlus, IconRefresh, IconSafe, IconUpload } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import { useSessionStore } from "../stores/session";
import { useAppearanceStore } from "../stores/appearance";
import type {
  AIModelPack,
  AIPolicy,
  AIProvider,
  LocalAIRuntime,
  Backup,
  Device,
  Pairing,
  ReleaseHistory,
  ReminderPreference,
  User,
  WorkspaceRoot,
} from "../types";
import { formatServerTime } from "../utils/datetime";
import { auditActionLabel, auditEntityLabel, zhLabel } from "../utils/labels";
import PageHelp from "../components/PageHelp.vue";

interface Audit {
  id: number;
  actor_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  detail?: Record<string, unknown>;
  created_at: string;
}

interface Diagnostics {
  mode: string;
  bind: { host: string; port: number };
  service_url: string;
  lan_candidates: string[];
  disk: { total_bytes: number; free_bytes: number };
  counts: { users: number; tasks: number; attachments: number; unique_files: number };
  latest_backup: { id: string; created_at: string; status: string } | null;
  fault_tips: string[];
}

interface SystemStatus {
  status: string;
  ready: boolean;
  readiness: {
    database: boolean;
    foreign_keys: boolean;
    schema: boolean;
    data_directories: boolean;
    backup_fresh: boolean;
  };
  mode: string;
  app_version: string;
  schema_revision: string;
  architecture: string;
  system: string;
  kernel: string;
  platform: string;
  uptime_seconds: number;
  service: {
    host: string;
    port: number;
    agent_port: number;
    url: string;
    sse_clients: number;
    tls_enabled: boolean;
  };
  storage: { database_bytes: number; attachments_bytes: number; backups_bytes: number; indexed_files: number };
  database: { quick_check: string; foreign_key_errors: number; migration_head: string };
  devices: {
    max: number;
    total: number;
    online: number;
    items: Array<{ id: string; name: string; status: string; last_seen_at: string | null }>;
  };
  projections: Array<{
    name: string;
    status: string;
    processed_count: number;
    failed_count: number;
    last_error: string;
    last_run_at: string | null;
  }>;
  workspace_roots: Array<{ id: string; name: string; enabled: boolean; scan_status: string; file_count: number; last_scan_at: string | null }>;
  latest_job: { id: string; type: string; status: string; progress: number; message: string } | null;
  backup: { last_at: string | null; last_status: string; next_schedule: string };
  ai: { configured: boolean; enabled: boolean; trusted_intranet: boolean; last_status: string; last_test_at: string | null; local?: LocalAIRuntime };
  load_average: number[];
  executable_frozen: boolean;
}

const props = withDefaults(defineProps<{ initialTab?: string }>(), {
  initialTab: "diagnostics",
});
const session = useSessionStore();
const appearance = useAppearanceStore();
const tab = ref(props.initialTab);
const health = ref<Record<string, unknown>>({});
const users = ref<User[]>([]);
const backups = ref<Backup[]>([]);
const audits = ref<Audit[]>([]);
const pairings = ref<Pairing[]>([]);
const diagnostics = ref<Diagnostics | null>(null);
const systemStatus = ref<SystemStatus | null>(null);
const aiProvider = ref<AIProvider | null>(null);
const aiPolicies = ref<AIPolicy[]>([]);
const workspaceRoots = ref<WorkspaceRoot[]>([]);
const reminder = ref<ReminderPreference | null>(null);
const logs = ref("");
const loadingBackup = ref(false);
const userVisible = ref(false);
const userEditVisible = ref(false);
const pairingVisible = ref(false);
const pairingToken = ref("");
const pairingConfig = ref<Record<string, unknown> | null>(null);

watch(
  () => props.initialTab,
  (value) => {
    tab.value = value;
  },
);
const updatePackages = ref<Array<{
  id: string;
  filename: string;
  version: string;
  schema_revision: string;
  sha256: string;
  signature_valid: boolean;
  status: string;
  created_at: string;
  manifest: {
    release_title?: string;
    release_notes?: string[];
  };
}>>([]);
const updateRuns = ref<Array<{
  id: string;
  package_id: string;
  target_device_id: string | null;
  status: string;
  progress: number;
  message: string;
  created_at: string;
}>>([]);
const updateDevices = ref<Device[]>([]);
const releaseHistory = ref<ReleaseHistory[]>([]);
const updateInput = ref<HTMLInputElement | null>(null);
const updatePolling = ref(false);
const projectionRebuilding = ref(false);
const UPDATE_TASK_KEY = "partyops.pending-update";
let updatePollTimer: number | undefined;
const userForm = reactive({ username: "", display_name: "", password: "", role: "staff" });
const editingUser = ref<User | null>(null);
const editUserForm = reactive({ display_name: "", role: "staff", active: true, password: "" });
const pairingForm = reactive({ name: "协同终端" });
const importInput = ref<HTMLInputElement | null>(null);
const modelPackInput = ref<HTMLInputElement | null>(null);
const modelPacks = ref<AIModelPack[]>([]);
const localAIRuntime = ref<LocalAIRuntime | null>(null);
const loadWarning = ref("");
const modelPackUploading = ref(false);
const appearanceForm = reactive({
  art_level: "standard" as "standard" | "reduced",
  reduce_motion: false,
  theme_override: null as "spring" | "summer" | "autumn" | "winter" | null,
});
const adminAppearanceForm = reactive({
  theme_mode: "auto" as "auto" | "fixed",
  fixed_theme: "spring" as "spring" | "summer" | "autumn" | "winter",
  default_art_level: "standard" as "standard" | "reduced",
  default_reduce_motion: false,
});
const reminderForm = reactive({
  enabled: true,
  advance_days: 3,
  reminder_days: [7, 3, 1, 0] as number[],
  quiet_start: "22:00",
  quiet_end: "07:30",
  desktop_enabled: true,
  remind_overdue: true,
  remind_review: true,
  remind_feedback: true,
  remind_materials: true,
});
const aiKey = ref("");
const aiForm = reactive({
  name: "DeepSeek 模型服务",
  base_url: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  enabled: false,
  trusted_intranet: false,
  timeout_seconds: 60,
});
const policyForm = reactive({
  name: "默认只读策略",
  allowed_root_ids: [] as string[],
  allowed_task_categories_text: "",
  allowed_file_types_text: ".docx,.pdf,.xlsx,.txt,.md,.png,.jpg,.jpeg",
  capabilities: ["search", "summarize", "classify", "draft_report", "suggest_breakdown", "check_materials"] as string[],
  active: true,
});
const diskUsage = computed(() => {
  if (!diagnostics.value) return "检查中";
  const free = diagnostics.value.disk.free_bytes / 1024 / 1024 / 1024;
  const total = diagnostics.value.disk.total_bytes / 1024 / 1024 / 1024;
  return `${free.toFixed(1)} GB 可用 / ${total.toFixed(1)} GB`;
});

async function load() {
  const failures: string[] = [];
  const recordFailure = (label: string) => {
    failures.push(label);
    console.warn(`PartyOps 设置页部分数据暂不可用：${label}`);
  };
  try {
    health.value = await api.get("/health");
  } catch {
    recordFailure("服务状态");
  }
  try {
    reminder.value = await api.get<ReminderPreference>("/reminders/preferences");
    Object.assign(reminderForm, reminder.value);
  } catch {
    recordFailure("提醒偏好");
  }
  try {
    await appearance.loadUser();
  } catch {
    recordFailure("个人外观偏好");
  }
  if (appearance.user) Object.assign(appearanceForm, {
    art_level: appearance.user.art_level,
    reduce_motion: appearance.user.reduce_motion,
    theme_override: appearance.user.theme_override,
  });
  if (session.user?.role === "admin") {
    const results = await Promise.allSettled([
      api.get<User[]>("/admin/users"),
      api.get<Backup[]>("/backups"),
      api.get<Audit[]>("/admin/audit?limit=100"),
      api.get<Pairing[]>("/admin/pairings"),
      api.get<Diagnostics>("/admin/diagnostics"),
      api.get<SystemStatus>("/admin/system-status"),
      api.get<AIProvider>("/ai/settings"),
      api.get<AIPolicy[]>("/ai/policies"),
      api.get<WorkspaceRoot[]>("/workspace/roots"),
      api.get<typeof updatePackages.value>("/admin/updates"),
      api.get<typeof updateRuns.value>("/admin/update-runs"),
      api.get<Device[]>("/admin/devices"),
      api.get<ReleaseHistory[]>("/admin/update-history"),
      api.get<AIModelPack[]>("/admin/ai/model-packs"),
      api.get<LocalAIRuntime>("/ai/runtime/status"),
    ]);
    const valueOr = <T, F>(result: PromiseSettledResult<T>, fallback: F, label: string): T | F => {
      if (result.status === "fulfilled") return result.value;
      recordFailure(label);
      return fallback;
    };
    users.value = valueOr(results[0], users.value, "用户与权限");
    backups.value = valueOr(results[1], backups.value, "备份记录");
    audits.value = valueOr(results[2], audits.value, "审计日志");
    pairings.value = valueOr(results[3], pairings.value, "协同终端");
    diagnostics.value = valueOr(results[4], diagnostics.value, "运行诊断");
    systemStatus.value = valueOr(results[5], systemStatus.value, "系统状态");
    aiProvider.value = valueOr(results[6], aiProvider.value, "AI 服务配置");
    aiPolicies.value = valueOr(results[7], aiPolicies.value, "AI 权限策略");
    workspaceRoots.value = valueOr(results[8], workspaceRoots.value, "原始目录授权");
    updatePackages.value = valueOr(results[9], updatePackages.value, "更新包列表");
    updateRuns.value = valueOr(results[10], updateRuns.value, "升级运行记录");
    updateDevices.value = valueOr(results[11], updateDevices.value, "协同设备版本");
    releaseHistory.value = valueOr(results[12], releaseHistory.value, "版本更新历史");
    modelPacks.value = valueOr(results[13], modelPacks.value, "本地模型包");
    localAIRuntime.value = valueOr(results[14], localAIRuntime.value, "本地智能状态");
    try {
      await appearance.loadAdmin();
    } catch {
      recordFailure("全局主题默认值");
    }
    if (appearance.admin) Object.assign(adminAppearanceForm, {
      theme_mode: appearance.admin.theme_mode,
      fixed_theme: appearance.admin.fixed_theme,
      default_art_level: appearance.admin.default_art_level,
      default_reduce_motion: appearance.admin.default_reduce_motion,
    });
    if (aiProvider.value) {
      Object.assign(aiForm, {
        name: aiProvider.value.id ? aiProvider.value.name : "DeepSeek 模型服务",
        base_url: aiProvider.value.id ? aiProvider.value.base_url : "https://api.deepseek.com",
        model: aiProvider.value.id ? aiProvider.value.model : "deepseek-v4-flash",
        enabled: aiProvider.value.enabled,
        trusted_intranet: aiProvider.value.trusted_intranet,
        timeout_seconds: aiProvider.value.timeout_seconds,
      });
    }
    const policy = aiPolicies.value[0];
    if (policy) {
      Object.assign(policyForm, {
        name: policy.name,
        allowed_root_ids: [...policy.allowed_root_ids],
        allowed_task_categories_text: policy.allowed_task_categories.join("、"),
        allowed_file_types_text: policy.allowed_file_types.join(","),
        capabilities: [...policy.capabilities],
        active: policy.active,
      });
    }
  }
  loadWarning.value = failures.length
    ? `部分状态暂时不可用：${failures.join("、")}。其他设置仍可正常查看和操作。`
    : "";
}

async function saveAppearance() {
  try {
    await appearance.saveUser({ ...appearanceForm });
    Message.success("个人外观偏好已保存");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "外观偏好保存失败");
  }
}

async function saveAdminAppearance() {
  try {
    await appearance.saveAdmin({ ...adminAppearanceForm });
    Message.success("全局主题默认值已保存");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "全局主题保存失败");
  }
}

async function uploadModelPack(file: File | null) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  modelPackUploading.value = true;
  try {
    await api.post<AIModelPack>("/admin/ai/model-packs", form);
    Message.success("本地模型包已完成签名和哈希校验");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "模型包导入失败");
  } finally {
    modelPackUploading.value = false;
    if (modelPackInput.value) modelPackInput.value.value = "";
  }
}

async function activateModelPack(pack: AIModelPack, capability: "embedding" | "llm") {
  try {
    await api.post<AIModelPack>(`/admin/ai/model-packs/${pack.id}/activate?capability=${capability}`);
    Message.success(capability === "embedding" ? "中文向量模型已启用" : "本地 LLM 已启用；只生成带来源草稿");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "模型包启用失败");
  }
}

async function deactivateModelCapability(capability: "embedding" | "llm") {
  try {
    await api.delete(`/admin/ai/model-activations/${capability}`);
    Message.success(capability === "embedding" ? "中文向量能力已停用" : "本地 LLM 已停用并卸载");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "模型能力停用失败");
  }
}

async function rebuildProjections() {
  projectionRebuilding.value = true;
  try {
    await api.post("/admin/projections/rebuild");
    Message.success("汇总与日历投影已重新核对");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "投影重建失败");
  } finally {
    projectionRebuilding.value = false;
  }
}

async function uploadUpdate(file: File | null) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await api.post("/admin/updates/upload", form);
    Message.success("更新包已校验并登记");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "更新包校验失败");
  } finally {
    if (updateInput.value) updateInput.value.value = "";
  }
}

async function applyUpdate(item: typeof updatePackages.value[number]) {
  const onlineOrActiveDevices = updateDevices.value.filter((device) => device.active);
  const targetDeviceIds = onlineOrActiveDevices.map((device) => device.id);
  try {
    const includeHost = true;
    await api.post(`/admin/updates/${item.id}/apply`, {
      target_device_ids: targetDeviceIds,
      include_host: includeHost,
    });
    const label = `主机及 ${targetDeviceIds.length} 台已启用终端`;
    Message.success(`已进入升级队列：${label} → ${item.version}`);
    startUpdateMonitor(item.id, item.version, includeHost);
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "升级任务创建失败");
  }
}

function scheduleUpdatePoll(delay = 3000) {
  window.clearTimeout(updatePollTimer);
  updatePollTimer = window.setTimeout(pollUpdateProgress, delay);
}

function startUpdateMonitor(packageId: string, version: string, includeHost: boolean) {
  localStorage.setItem(
    UPDATE_TASK_KEY,
    JSON.stringify({ packageId, version, includeHost, startedAt: new Date().toISOString() }),
  );
  updatePolling.value = true;
  scheduleUpdatePoll(1500);
}

async function pollUpdateProgress() {
  const raw = localStorage.getItem(UPDATE_TASK_KEY);
  if (!raw) {
    updatePolling.value = false;
    return;
  }
  let pending: { packageId: string; version: string; includeHost: boolean };
  try {
    pending = JSON.parse(raw);
  } catch {
    localStorage.removeItem(UPDATE_TASK_KEY);
    updatePolling.value = false;
    return;
  }
  try {
    updateRuns.value = await api.get<typeof updateRuns.value>("/admin/update-runs");
    const relevant = updateRuns.value.filter((run) => run.package_id === pending.packageId);
    const hostRun = relevant.find((run) => !run.target_device_id);
    const terminalStates = new Set(["completed", "failed", "rolled_back"]);
    const finished = relevant.length > 0 && relevant.every((run) => terminalStates.has(run.status));
    if (pending.includeHost && hostRun?.status === "completed") {
      localStorage.removeItem(UPDATE_TASK_KEY);
      updatePolling.value = false;
      Message.success(`主机已升级至 ${pending.version}，正在重新载入新版界面`);
      window.setTimeout(() => window.location.reload(), 800);
      return;
    }
    if (
      pending.includeHost
      && hostRun
      && ["failed", "rolled_back"].includes(hostRun.status)
    ) {
      localStorage.removeItem(UPDATE_TASK_KEY);
      updatePolling.value = false;
      Message.warning(hostRun.message || "升级未完成，系统已保留或恢复原版本");
      return;
    }
    if (!pending.includeHost && finished) {
      localStorage.removeItem(UPDATE_TASK_KEY);
      updatePolling.value = false;
      Message.success("所选协同电脑升级任务已完成");
      return;
    }
  } catch {
    // 主机安装和重启期间短暂无法访问是正常现象，任务编号保存在浏览器中。
  }
  updatePolling.value = true;
  scheduleUpdatePoll();
}

function updateTargetName(deviceId: string | null): string {
  if (!deviceId) return "主机";
  return updateDevices.value.find((device) => device.id === deviceId)?.name || "已移除设备";
}

async function createBackup() {
  loadingBackup.value = true;
  try {
    await api.post("/backups");
    Message.success("一致性备份已完成");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "备份失败");
  } finally {
    loadingBackup.value = false;
  }
}

async function saveReminder() {
  if (!reminder.value) return;
  try {
    reminder.value = await api.patch<ReminderPreference>(
      "/reminders/preferences",
      reminderForm,
      { "If-Match": String(reminder.value.version) },
    );
    Message.success("提醒偏好已保存");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "保存失败");
  }
}

async function saveAISettings() {
  if (!aiProvider.value) return;
  try {
    aiProvider.value = await api.patch<AIProvider>(
      "/ai/settings",
      {
        ...aiForm,
        api_key: aiKey.value || null,
      },
      { "If-Match": String(aiProvider.value.version) },
    );
    aiKey.value = "";
    Message.success("AI 服务配置已加密保存");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "AI 配置保存失败");
  }
}

async function testAISettings() {
  try {
    await api.post("/ai/settings/test");
    Message.success("AI 服务连通性测试通过，未发送任何业务资料");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "AI 服务连接失败");
  }
}

function splitValues(value: string): string[] {
  return [...new Set(value.split(/[、,，\s]+/).map((item) => item.trim()).filter(Boolean))];
}

async function saveAIPolicy() {
  const payload = {
    name: policyForm.name,
    allowed_root_ids: policyForm.allowed_root_ids,
    allowed_task_categories: splitValues(policyForm.allowed_task_categories_text),
    allowed_file_types: splitValues(policyForm.allowed_file_types_text).map((item) => item.startsWith(".") ? item.toLowerCase() : `.${item.toLowerCase()}`),
    capabilities: policyForm.capabilities,
    allow_restricted: false,
    active: policyForm.active,
  };
  try {
    const current = aiPolicies.value[0];
    if (current) {
      await api.patch(`/ai/policies/${current.id}`, payload, { "If-Match": String(current.version) });
    } else {
      await api.post("/ai/policies", payload);
    }
    Message.success("AI 只读白名单已更新；敏感事项仍保持禁止");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "AI 权限保存失败");
  }
}

async function createUser() {
  try {
    await api.post("/admin/users", userForm);
    userVisible.value = false;
    Object.assign(userForm, { username: "", display_name: "", password: "", role: "staff" });
    Message.success("用户已创建");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "用户创建失败");
  }
}

function openUserEdit(user: User) {
  editingUser.value = user;
  Object.assign(editUserForm, {
    display_name: user.display_name,
    role: user.role,
    active: user.active,
    password: "",
  });
  userEditVisible.value = true;
}

async function saveUserEdit() {
  if (!editingUser.value) return;
  try {
    await api.patch(
      `/admin/users/${editingUser.value.id}`,
      {
        display_name: editUserForm.display_name,
        role: editUserForm.role,
        active: editUserForm.active,
      },
      { "If-Match": String(editingUser.value.version) },
    );
    if (editUserForm.password) {
      await api.post(`/admin/users/${editingUser.value.id}/reset-password`, {
        password: editUserForm.password,
      });
    }
    userEditVisible.value = false;
    Message.success("用户权限已更新");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "用户更新失败");
  }
}

async function createPairing() {
  try {
    const result = await api.post<{ token: string; config: Record<string, unknown> }>("/admin/pairings", { name: pairingForm.name });
    pairingToken.value = result.token;
    pairingConfig.value = result.config;
    Message.success("终端配对令牌已生成，仅显示一次");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "配对失败");
  }
}

async function revokePairing(pairing: Pairing) {
  try {
    await api.delete(`/admin/pairings/${pairing.id}`);
    Message.success("终端配对已撤销");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "撤销失败");
  }
}

async function verifyBackup(backup: Backup) {
  try {
    await api.post(`/admin/backups/${backup.id}/verify`);
    Message.success("备份格式、数据库与所有文件哈希均通过");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "备份校验失败");
  }
}

async function restoreBackup(backup: Backup) {
  try {
    await api.post(`/admin/backups/restore?backup_id=${encodeURIComponent(backup.id)}`);
    Message.success("恢复完成，请重新登录并抽查最近任务与终稿");
    window.location.assign("/login");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "恢复失败，现有数据未改变");
  }
}

async function importBackup(file: File | null) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await api.post("/admin/backups/import", form);
    Message.success("外部备份已导入并完成校验，可选择恢复");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "导入失败");
  } finally {
    if (importInput.value) importInput.value.value = "";
  }
}

async function loadLogs() {
  try {
    const response = await fetch("/api/v1/admin/logs?lines=300", { credentials: "include" });
    logs.value = await response.text();
  } catch {
    logs.value = "日志读取失败。";
  }
}

async function copyPairingConfig() {
  if (!pairingConfig.value) return;
  await navigator.clipboard.writeText(JSON.stringify(pairingConfig.value, null, 2));
  Message.success("终端配置已复制");
}

onMounted(async () => {
  try {
    await load();
  } finally {
    if (localStorage.getItem(UPDATE_TASK_KEY)) {
      updatePolling.value = true;
      scheduleUpdatePoll(500);
    }
  }
});
onBeforeUnmount(() => window.clearTimeout(updatePollTimer));
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">系统设置</h1>
        <p class="page-description">本地运行状态、用户、备份、终端配对与审计记录。</p>
      </div>
      <a-space>
        <PageHelp
          title="系统管理怎么用"
          :tips="['先查看运行诊断，再处理更新、备份或权限问题。', '升级前系统会自动建立可校验备份。', '诊断包会自动脱敏，不包含密钥和业务正文。']"
          help-query="系统设置"
        />
        <a-button aria-label="刷新系统状态" @click="load"><template #icon><IconRefresh /></template>刷新状态</a-button>
      </a-space>
    </header>
    <a-alert v-if="loadWarning" class="partial-load-alert" type="warning" show-icon>
      {{ loadWarning }}
    </a-alert>
    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="diagnostics" title="运行诊断">
        <div class="diagnostic-grid">
          <article>
            <IconSafe />
            <div><span>服务状态</span><strong>{{ health.status === "ok" ? "运行正常" : "待检查" }}</strong></div>
          </article>
          <article>
            <IconCloudDownload />
            <div><span>运行模式</span><strong>{{ health.mode === "host" ? "主机模式" : "协同终端" }}</strong></div>
          </article>
          <article>
            <IconSafe />
            <div><span>SQLite</span><strong>{{ (health.sqlite as any)?.version || "检查中" }}</strong></div>
          </article>
          <article>
            <IconSafe />
            <div><span>应用版本</span><strong>{{ health.app_version || "检查中" }}</strong></div>
          </article>
        </div>
        <div v-if="diagnostics" class="diagnostic-detail">
          <p><span>访问地址</span><strong>{{ diagnostics.service_url }}</strong></p>
          <p><span>可用局域网地址</span><strong>{{ diagnostics.lan_candidates.join("、") || "仅本机回环地址" }}</strong></p>
          <p><span>磁盘空间</span><strong>{{ diskUsage }}</strong></p>
          <p><span>数据规模</span><strong>{{ diagnostics.counts.tasks }} 个事项 · {{ diagnostics.counts.attachments }} 个附件版本 · {{ diagnostics.counts.unique_files }} 个去重文件</strong></p>
          <p v-if="systemStatus"><span>系统与架构</span><strong>{{ systemStatus.system }} · {{ systemStatus.architecture }} · 内核 {{ systemStatus.kernel }}</strong></p>
          <p v-if="systemStatus"><span>数据库模式</span><strong>{{ systemStatus.schema_revision }} · 服务已运行 {{ Math.floor(systemStatus.uptime_seconds / 60) }} 分钟</strong></p>
          <p v-if="systemStatus"><span>服务端口</span><strong>业务 {{ systemStatus.service.port }} · 设备 {{ systemStatus.service.agent_port }} · {{ systemStatus.service.tls_enabled ? "HTTPS 已启用" : "仅可信局域网 HTTP" }}</strong></p>
          <p v-if="systemStatus"><span>实时协同</span><strong>{{ systemStatus.service.sse_clients }} 个实时连接 · {{ systemStatus.devices.online }} / {{ systemStatus.devices.total }} 台协同电脑在线 · {{ systemStatus.latest_job?.message || "后台任务空闲" }}</strong></p>
          <p v-if="systemStatus"><span>文件中心</span><strong>{{ systemStatus.storage.indexed_files }} 个索引节点 · {{ systemStatus.workspace_roots.length }} 个授权根目录</strong></p>
          <p v-if="systemStatus"><span>数据库完整性</span><strong>{{ systemStatus.database.quick_check === "ok" ? "检查通过" : "需要处理" }} · 外键异常 {{ systemStatus.database.foreign_key_errors }} 项 · 迁移目标 {{ systemStatus.database.migration_head }}</strong></p>
          <p v-if="systemStatus"><span>汇总与日历投影</span><strong>{{ systemStatus.projections.length ? `${systemStatus.projections.filter((item) => item.status === "failed").length} 个失败 · ${systemStatus.projections.reduce((total, item) => total + item.processed_count, 0)} 个事件已处理` : "等待首次事件" }}</strong></p>
          <p v-if="systemStatus"><span>自动备份</span><strong>每日 {{ systemStatus.backup.next_schedule }} · {{ systemStatus.backup.last_at ? `最近 ${formatServerTime(systemStatus.backup.last_at, "MM-DD HH:mm")}` : "尚无备份" }}</strong></p>
          <p v-if="systemStatus"><span>AI 状态</span><strong>{{ systemStatus.ai.enabled ? "外部模型已启用" : systemStatus.ai.configured ? "外部模型已配置、未启用" : "外部模型未配置" }} · {{ systemStatus.ai.local?.message || "本地模型未启用" }}</strong></p>
        </div>
        <a-alert v-if="systemStatus && !systemStatus.ready" type="error" class="update-note">
          系统尚未达到可用状态：请检查数据库、迁移版本和数据目录。修复前系统会阻止高风险后台任务继续运行。
        </a-alert>
        <a-alert v-else-if="systemStatus && !systemStatus.readiness.backup_fresh" type="warning" class="update-note">
          最近 36 小时没有可用备份，请先创建备份再执行系统更新或大批量操作。
        </a-alert>
        <a-alert v-if="systemStatus?.projections.some((item) => item.status === 'failed')" type="warning" class="update-note">
          周期汇总或日历投影存在失败任务。请先保留诊断信息，再执行幂等重建。
          <a-button size="mini" :loading="projectionRebuilding" @click="rebuildProjections">重新核对汇总</a-button>
        </a-alert>
        <a-alert v-if="(health.sqlite as any)?.safe_version === false" type="warning">
          当前为开发环境 SQLite；UOS 生产包会静态链接已修复版本并强制校验。
        </a-alert>
        <a-collapse v-if="diagnostics" class="fault-tips">
          <a-collapse-item key="fault" header="常见故障排查">
            <p v-for="tip in diagnostics.fault_tips" :key="tip">{{ tip }}</p>
          </a-collapse-item>
          <a-collapse-item key="logs" header="最近运行日志" @click="loadLogs">
            <a-button size="mini" @click.stop="loadLogs">刷新日志</a-button>
            <pre>{{ logs || "展开后点击刷新日志。" }}</pre>
          </a-collapse-item>
        </a-collapse>
      </a-tab-pane>

      <a-tab-pane key="appearance" title="外观与东方主题">
        <div class="appearance-settings">
          <section>
            <h3>个人显示偏好</h3>
            <p>东方艺术层不会改变页面结构；减少装饰后仍保留农历和节气文字。</p>
            <a-form :model="appearanceForm" layout="vertical">
              <a-form-item label="装饰级别">
                <a-radio-group v-model="appearanceForm.art_level" type="button">
                  <a-radio value="standard">标准装饰</a-radio>
                  <a-radio value="reduced">减少装饰</a-radio>
                </a-radio-group>
              </a-form-item>
              <a-form-item label="个人季节覆盖">
                <a-select v-model="appearanceForm.theme_override" allow-clear placeholder="跟随系统与节气自动切换">
                  <a-option value="spring">春 · 花枝与新绿</a-option>
                  <a-option value="summer">夏 · 烟岚与水岸</a-option>
                  <a-option value="autumn">秋 · 远山与飞鸟</a-option>
                  <a-option value="winter">冬 · 梅枝与淡雪</a-option>
                </a-select>
              </a-form-item>
              <a-form-item label="减少动态"><a-switch v-model="appearanceForm.reduce_motion" /></a-form-item>
              <a-button type="primary" @click="saveAppearance">保存个人偏好</a-button>
            </a-form>
          </section>
          <section v-if="session.user?.role === 'admin'">
            <h3>全局默认主题</h3>
            <p>新用户继承这里的默认值；个人已保存的偏好不会被静默覆盖。</p>
            <a-form :model="adminAppearanceForm" layout="vertical">
              <a-form-item label="季节切换">
                <a-radio-group v-model="adminAppearanceForm.theme_mode" type="button">
                  <a-radio value="auto">按节气自动切换</a-radio>
                  <a-radio value="fixed">固定主题</a-radio>
                </a-radio-group>
              </a-form-item>
              <a-form-item v-if="adminAppearanceForm.theme_mode === 'fixed'" label="固定季节">
                <a-select v-model="adminAppearanceForm.fixed_theme">
                  <a-option value="spring">春</a-option><a-option value="summer">夏</a-option>
                  <a-option value="autumn">秋</a-option><a-option value="winter">冬</a-option>
                </a-select>
              </a-form-item>
              <a-form-item label="新用户默认装饰">
                <a-radio-group v-model="adminAppearanceForm.default_art_level" type="button">
                  <a-radio value="standard">标准装饰</a-radio>
                  <a-radio value="reduced">减少装饰</a-radio>
                </a-radio-group>
              </a-form-item>
              <a-form-item label="新用户默认减少动态"><a-switch v-model="adminAppearanceForm.default_reduce_motion" /></a-form-item>
              <a-button type="primary" @click="saveAdminAppearance">保存全局默认值</a-button>
            </a-form>
          </section>
        </div>
      </a-tab-pane>

      <a-tab-pane v-if="session.user?.role === 'admin'" key="updates" title="系统更新">
        <div class="tab-toolbar">
          <p>主机是唯一更新权威：先完成主机备份、迁移和健康检查，再要求全部协同电脑更新到同一版本。</p>
          <a-space>
            <a-button aria-label="导入 PartyOps 更新包" @click="updateInput?.click()"><template #icon><IconUpload /></template>导入 .partyops-update</a-button>
            <input ref="updateInput" type="file" accept=".partyops-update" hidden @change="uploadUpdate(($event.target as HTMLInputElement).files?.[0] || null)" />
          </a-space>
        </div>
        <a-alert type="info" class="update-note">
          1.0.0 电脑没有更新中心，需先手动原位安装一次带更新助手的桥接包；已安装 1.1.3
          及以上版本后，只由主机导入签名更新包，全部协同电脑按主机版本自动校验和更新。
        </a-alert>
        <a-alert v-if="updatePolling" type="warning" class="update-note">
          升级正在进行。主机重启期间页面会自动等待，服务恢复后自动载入新版，无需重复点击。
        </a-alert>
        <div class="update-targets">
          <strong>固定升级范围</strong>
          <span>主机 + 全部 {{ updateDevices.filter((item) => item.active).length }} 台已启用协同电脑</span>
          <small>主机升级成功后，协同电脑下次进入系统会看到版本和更新内容；完成更新前不能进入业务页面。离线设备不会丢失任务。</small>
        </div>
        <a-table :data="updatePackages" row-key="id" :pagination="false">
          <template #columns>
            <a-table-column title="版本与内容">
              <template #cell="{ record }">
                <strong>{{ record.version }}</strong>
                <small class="update-content">{{ record.manifest?.release_title || "党建智办功能与稳定性更新" }}</small>
                <ul v-if="record.manifest?.release_notes?.length" class="compact-notes">
                  <li v-for="note in record.manifest.release_notes.slice(0, 3)" :key="note">{{ note }}</li>
                </ul>
              </template>
            </a-table-column>
            <a-table-column title="签名"><template #cell="{ record }">{{ record.signature_valid ? "已验证" : "开发环境未配置签名" }}</template></a-table-column>
            <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.status) }}</template></a-table-column>
            <a-table-column title="校验值"><template #cell="{ record }"><code>{{ record.sha256.slice(0, 16) }}…</code></template></a-table-column>
            <a-table-column title="操作"><template #cell="{ record }"><a-button size="mini" type="primary" :disabled="record.status === 'applying'" @click="applyUpdate(record)">开始升级</a-button></template></a-table-column>
          </template>
        </a-table>
        <h3 class="subheading">已安装版本历史</h3>
        <a-timeline class="release-history">
          <a-timeline-item v-for="release in releaseHistory" :key="release.id">
            <div class="release-heading">
              <strong>版本 {{ release.version }}</strong>
              <span>{{ formatServerTime(release.installed_at, "YYYY-MM-DD HH:mm:ss") }}（北京时间）</span>
            </div>
            <p>{{ release.title }} · 数据库模式 {{ release.schema_revision }}</p>
            <ul>
              <li v-for="note in release.release_notes" :key="note">{{ note }}</li>
            </ul>
          </a-timeline-item>
        </a-timeline>
        <h3 class="subheading">升级运行记录</h3>
        <a-table :data="updateRuns" row-key="id" :pagination="{ pageSize: 8 }">
          <template #columns>
            <a-table-column title="目标"><template #cell="{ record }">{{ updateTargetName(record.target_device_id) }}</template></a-table-column>
            <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.status) }}</template></a-table-column>
            <a-table-column title="进度"><template #cell="{ record }">{{ record.progress }}%</template></a-table-column>
            <a-table-column title="说明" data-index="message" />
            <a-table-column title="时间"><template #cell="{ record }">{{ formatServerTime(record.created_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="reminders" title="提醒偏好">
        <div class="reminder-panel">
          <div><strong>站内风险提醒</strong><p>只提醒节点、审核、反馈和材料风险，不弹出高频干扰窗口。</p></div>
          <a-switch v-model="reminderForm.enabled" />
        </div>
        <a-form :model="reminderForm" layout="vertical" class="reminder-form">
          <a-form-item label="提前提醒天数"><a-input-number v-model="reminderForm.advance_days" :min="0" :max="30" /></a-form-item>
          <a-form-item label="分级提醒节点">
            <a-checkbox-group v-model="reminderForm.reminder_days">
              <a-checkbox :value="7">提前 7 天</a-checkbox>
              <a-checkbox :value="3">提前 3 天</a-checkbox>
              <a-checkbox :value="1">提前 1 天</a-checkbox>
              <a-checkbox :value="0">截止当天</a-checkbox>
            </a-checkbox-group>
          </a-form-item>
          <a-form-item label="免打扰时间">
            <a-space>
              <a-input v-model="reminderForm.quiet_start" placeholder="22:00" />
              <span>至</span>
              <a-input v-model="reminderForm.quiet_end" placeholder="07:30" />
            </a-space>
          </a-form-item>
          <a-form-item label="桌面通知"><a-switch v-model="reminderForm.desktop_enabled" /></a-form-item>
          <a-form-item label="提醒类型">
            <a-space direction="vertical">
              <a-checkbox v-model="reminderForm.remind_overdue">逾期事项</a-checkbox>
              <a-checkbox v-model="reminderForm.remind_review">待审核事项</a-checkbox>
              <a-checkbox v-model="reminderForm.remind_feedback">等待反馈事项</a-checkbox>
              <a-checkbox v-model="reminderForm.remind_materials">已办但材料不完整</a-checkbox>
            </a-space>
          </a-form-item>
          <a-button type="primary" @click="saveReminder">保存提醒偏好</a-button>
        </a-form>
      </a-tab-pane>

      <a-tab-pane v-if="session.user?.role === 'admin'" key="ai" title="AI 与权限">
        <div class="ai-warning">
          <IconSafe />
          <div>
            <strong>默认拒绝，最小授权</strong>
            <p>AI 只能读取下方白名单中的非敏感资料，只生成草稿。接口、密钥、提示内容不会写入运行日志。</p>
          </div>
        </div>
        <div class="ai-settings-grid">
          <section>
            <h3>模型服务</h3>
            <a-form :model="aiForm" layout="vertical">
              <a-form-item label="配置名称"><a-input v-model="aiForm.name" /></a-form-item>
              <a-form-item label="OpenAI 兼容接口地址"><a-input v-model="aiForm.base_url" placeholder="http://单位内网模型:端口/v1" /></a-form-item>
              <a-form-item label="模型名称"><a-input v-model="aiForm.model" placeholder="由模型服务提供" /></a-form-item>
              <a-form-item :label="`API 密钥（${aiProvider?.has_api_key ? '已保存，留空不修改' : '尚未保存'}）`">
                <a-input-password v-model="aiKey" autocomplete="new-password" placeholder="密钥加密保存在主机，不进入备份" />
              </a-form-item>
              <a-form-item label="请求超时（秒）"><a-input-number v-model="aiForm.timeout_seconds" :min="5" :max="300" /></a-form-item>
              <a-form-item label="接口属于单位可信内网"><a-switch v-model="aiForm.trusted_intranet" /></a-form-item>
              <a-form-item label="启用 AI"><a-switch v-model="aiForm.enabled" /></a-form-item>
              <a-space>
                <a-button type="primary" @click="saveAISettings">加密保存</a-button>
                <a-button :disabled="!aiProvider?.base_url" @click="testAISettings">仅测试连接</a-button>
              </a-space>
              <p class="ai-status">最近状态：{{ zhLabel(aiProvider?.last_status, "未配置") }}<template v-if="aiProvider?.last_test_at"> · {{ formatServerTime(aiProvider.last_test_at, "YYYY-MM-DD HH:mm") }}</template></p>
            </a-form>
          </section>
          <section>
            <h3>AI 只读白名单</h3>
            <a-form :model="policyForm" layout="vertical">
              <a-form-item label="策略名称"><a-input v-model="policyForm.name" /></a-form-item>
              <a-form-item label="允许的原始目录">
                <a-select v-model="policyForm.allowed_root_ids" multiple allow-clear placeholder="默认不允许任何原始目录">
                  <a-option v-for="root in workspaceRoots" :key="root.id" :value="root.id">{{ root.name }}</a-option>
                </a-select>
              </a-form-item>
              <a-form-item label="允许的任务类别"><a-input v-model="policyForm.allowed_task_categories_text" placeholder="留空表示非敏感任务不限类别" /></a-form-item>
              <a-form-item label="允许的文件类型"><a-input v-model="policyForm.allowed_file_types_text" /></a-form-item>
              <a-form-item label="允许能力">
                <a-checkbox-group v-model="policyForm.capabilities" direction="vertical">
                  <a-checkbox value="search">本地检索</a-checkbox>
                  <a-checkbox value="summarize">内容摘要</a-checkbox>
                  <a-checkbox value="classify">材料分类</a-checkbox>
                  <a-checkbox value="draft_report">周报草拟</a-checkbox>
                  <a-checkbox value="suggest_breakdown">任务拆解建议</a-checkbox>
                  <a-checkbox value="check_materials">材料缺项检查</a-checkbox>
                </a-checkbox-group>
              </a-form-item>
              <a-alert type="warning">敏感事项和附件固定禁止进入 AI，此限制不能在界面中关闭。</a-alert>
              <a-button type="primary" class="policy-save" @click="saveAIPolicy">保存白名单</a-button>
            </a-form>
          </section>
          <section class="local-ai-section">
            <div class="local-ai-heading">
              <div>
                <h3>主机本地智能</h3>
                <p>模型只安装在主机；无模型、内存不足或系统繁忙时自动退回规则推荐。</p>
              </div>
              <a-button :loading="modelPackUploading" @click="modelPackInput?.click()">
                <template #icon><IconUpload /></template>导入 .partyops-modelpack
              </a-button>
              <input ref="modelPackInput" type="file" accept=".partyops-modelpack" hidden @change="uploadModelPack(($event.target as HTMLInputElement).files?.[0] || null)" />
            </div>
            <a-alert :type="localAIRuntime?.ready ? 'success' : 'info'" class="update-note">
              {{ localAIRuntime?.message || "正在读取本地智能状态" }}
              <template v-if="localAIRuntime"> · 最多 {{ localAIRuntime.max_threads }} 线程 · 内存上限 {{ (localAIRuntime.memory_limit_mb / 1024).toFixed(1) }}GB</template>
            </a-alert>
            <p v-if="localAIRuntime" class="runtime-capabilities">
              规则推荐：始终可用 · 中文语义：{{ localAIRuntime.embedding_available ? "可用" : "已降级" }} · 本地草稿：{{ localAIRuntime.llm_available ? "可用" : "已降级" }}
            </p>
            <a-table :data="modelPacks" row-key="id" :pagination="false">
              <template #columns>
                <a-table-column title="模型包"><template #cell="{ record }"><strong>{{ record.name }}</strong><small class="update-content">{{ record.model_id }} · {{ record.version }} · {{ record.license_name || '许可待核对' }}</small><small class="update-content">{{ record.model_source || '未登记来源' }}</small></template></a-table-column>
                <a-table-column title="组件"><template #cell="{ record }">{{ record.capabilities.map((item: string) => item === 'embedding' ? '中文向量' : '本地 LLM').join('、') }}</template></a-table-column>
                <a-table-column title="架构" data-index="architecture" :width="100" />
                <a-table-column title="签名" :width="100"><template #cell="{ record }">{{ record.signature_valid ? "已验证" : "开发包" }}</template></a-table-column>
                <a-table-column title="资源" :width="120"><template #cell="{ record }">{{ record.estimated_memory_mb ? `${(record.estimated_memory_mb / 1024).toFixed(1)}GB` : '按运行时判断' }}</template></a-table-column>
                <a-table-column title="操作" :width="240"><template #cell="{ record }"><a-space><a-button v-if="record.capabilities.includes('embedding')" size="mini" :type="record.active_capabilities.includes('embedding') ? 'outline' : 'primary'" @click="record.active_capabilities.includes('embedding') ? deactivateModelCapability('embedding') : activateModelPack(record, 'embedding')">{{ record.active_capabilities.includes('embedding') ? '停用向量' : '启用向量' }}</a-button><a-button v-if="record.capabilities.includes('llm')" size="mini" :type="record.active_capabilities.includes('llm') ? 'outline' : 'primary'" @click="record.active_capabilities.includes('llm') ? deactivateModelCapability('llm') : activateModelPack(record, 'llm')">{{ record.active_capabilities.includes('llm') ? '停用 LLM' : '启用 LLM' }}</a-button></a-space></template></a-table-column>
              </template>
            </a-table>
            <p v-if="!modelPacks.length" class="empty-state">尚未导入本地模型包。规则推荐不依赖模型，仍可正常工作。</p>
          </section>
        </div>
      </a-tab-pane>

      <a-tab-pane v-if="session.user?.role === 'admin'" key="users" title="用户与权限">
        <div class="tab-toolbar"><p>系统面向小团队使用，不建立多层级组织体系。</p><a-button type="primary" @click="userVisible = true"><template #icon><IconPlus /></template>新增用户</a-button></div>
        <a-table :data="users" :pagination="false">
          <template #columns>
            <a-table-column title="姓名" data-index="display_name" />
            <a-table-column title="用户名" data-index="username" />
            <a-table-column title="角色"><template #cell="{ record }">{{ record.role === "admin" ? "管理员" : "协同人员" }}</template></a-table-column>
            <a-table-column title="状态"><template #cell="{ record }">{{ record.active ? "启用" : "停用" }}</template></a-table-column>
            <a-table-column title="操作"><template #cell="{ record }"><a-button size="mini" type="text" @click="openUserEdit(record)">权限与密码</a-button></template></a-table-column>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane v-if="session.user?.role === 'admin'" key="backup" title="备份与灾备">
        <div class="tab-toolbar">
          <p>数据库、附件与配置打包校验；恢复前会自动再备份当前数据。</p>
          <a-space>
            <a-button @click="pairingVisible = true">配置协同终端</a-button>
            <a-button @click="importInput?.click()"><template #icon><IconUpload /></template>导入灾备包</a-button>
            <input ref="importInput" type="file" accept=".partyops-backup" hidden @change="importBackup(($event.target as HTMLInputElement).files?.[0] || null)" />
            <a-button type="primary" :loading="loadingBackup" @click="createBackup">立即备份</a-button>
          </a-space>
        </div>
        <a-table :data="backups" row-key="id" :pagination="{ pageSize: 10 }">
          <template #columns>
            <a-table-column title="备份文件" data-index="filename" :width="360" />
            <a-table-column title="类型"><template #cell="{ record }">{{ record.kind === "manual" ? "手动" : record.kind === "automatic" ? "自动" : "恢复前" }}</template></a-table-column>
            <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.status) }}</template></a-table-column>
            <a-table-column title="大小"><template #cell="{ record }">{{ (record.size_bytes / 1024 / 1024).toFixed(2) }} MB</template></a-table-column>
            <a-table-column title="时间"><template #cell="{ record }">{{ formatServerTime(record.created_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
            <a-table-column title="操作" :width="260">
              <template #cell="{ record }">
                <a-space>
                  <a-button size="mini" :href="downloadUrl(`/backups/${record.id}/download`)" target="_blank"><template #icon><IconDownload /></template>下载</a-button>
                  <a-button size="mini" @click="verifyBackup(record)">校验</a-button>
                  <a-popconfirm content="恢复会替换当前数据；系统会先自动备份现状。确认继续？" @ok="restoreBackup(record)">
                    <a-button size="mini" status="danger">恢复</a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <h3 class="subheading">已配对终端</h3>
        <a-table :data="pairings" row-key="id" :pagination="false">
          <template #columns>
            <a-table-column title="终端" data-index="name" />
            <a-table-column title="状态"><template #cell="{ record }">{{ record.active ? "有效" : "已撤销" }}</template></a-table-column>
            <a-table-column title="最近拉取"><template #cell="{ record }">{{ formatServerTime(record.last_pull_at, "YYYY-MM-DD HH:mm", "尚未拉取") }}</template></a-table-column>
            <a-table-column title="创建时间"><template #cell="{ record }">{{ formatServerTime(record.created_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
            <a-table-column title="有效期至"><template #cell="{ record }">{{ formatServerTime(record.expires_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
            <a-table-column title="操作"><template #cell="{ record }"><a-popconfirm v-if="record.active" content="撤销后终端需重新配对。" @ok="revokePairing(record)"><a-button size="mini" type="text">撤销</a-button></a-popconfirm></template></a-table-column>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane v-if="session.user?.role === 'admin'" key="audit" title="审计日志">
        <div class="tab-toolbar"><p>新增、修改、删除、审核、下载、恢复与权限变更均追加记录。</p><a-button :href="downloadUrl('/admin/audit.csv')" target="_blank">导出审计 CSV</a-button></div>
        <a-table :data="audits" row-key="id" :pagination="{ pageSize: 15 }">
          <template #columns>
            <a-table-column title="时间"><template #cell="{ record }">{{ formatServerTime(record.created_at, "MM-DD HH:mm:ss") }}</template></a-table-column>
            <a-table-column title="操作"><template #cell="{ record }">{{ auditActionLabel(record.action) }}</template></a-table-column>
            <a-table-column title="对象"><template #cell="{ record }">{{ auditEntityLabel(record.entity_type) }}</template></a-table-column>
            <a-table-column title="对象编号" data-index="entity_id" />
            <a-table-column title="操作者"><template #cell="{ record }">{{ users.find((item) => item.id === record.actor_id)?.display_name || "系统" }}</template></a-table-column>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:visible="userVisible" title="新增用户" @ok="createUser">
      <a-form :model="userForm" layout="vertical">
        <a-form-item label="姓名"><a-input v-model="userForm.display_name" /></a-form-item>
        <a-form-item label="用户名"><a-input v-model="userForm.username" /></a-form-item>
        <a-form-item label="初始密码">
          <a-input-password v-model="userForm.password" autocomplete="new-password" />
        </a-form-item>
        <a-form-item label="角色"><a-select v-model="userForm.role"><a-option value="staff">协同人员</a-option><a-option value="admin">管理员</a-option></a-select></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="userEditVisible" title="用户权限与密码" @ok="saveUserEdit">
      <a-form :model="editUserForm" layout="vertical">
        <a-form-item label="姓名"><a-input v-model="editUserForm.display_name" /></a-form-item>
        <a-form-item label="角色"><a-select v-model="editUserForm.role"><a-option value="staff">协同人员</a-option><a-option value="admin">管理员</a-option></a-select></a-form-item>
        <a-form-item label="账号启用"><a-switch v-model="editUserForm.active" /></a-form-item>
        <a-form-item label="重置密码（不修改请留空）">
          <a-input-password v-model="editUserForm.password" autocomplete="new-password" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="pairingVisible" title="协同终端配对" :footer="false">
      <a-form :model="pairingForm" layout="vertical">
        <a-form-item field="name" label="终端名称"><a-input v-model="pairingForm.name" /></a-form-item>
        <a-button type="primary" @click="createPairing">生成只读备份令牌</a-button>
      </a-form>
      <a-alert v-if="pairingToken" type="warning" class="token-alert">
        令牌仅显示一次。可直接复制完整终端配置：
        <code>{{ pairingToken }}</code>
        <a-button size="small" @click="copyPairingConfig">复制完整配置 JSON</a-button>
        <pre v-if="pairingConfig">{{ JSON.stringify(pairingConfig, null, 2) }}</pre>
      </a-alert>
    </a-modal>
  </div>
</template>

<style scoped>
.diagnostic-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  margin: 8px 0 20px;
  background: var(--line);
  border: 1px solid var(--line);
}

.diagnostic-grid article {
  display: flex;
  min-height: 120px;
  align-items: center;
  gap: 14px;
  padding: 22px;
  background: rgba(251, 248, 241, 0.88);
}

.diagnostic-grid article > svg {
  color: var(--cinnabar);
  font-size: 24px;
}

.diagnostic-grid span,
.diagnostic-grid strong {
  display: block;
}

.diagnostic-grid span {
  margin-bottom: 7px;
  color: var(--muted);
  font-size: 11px;
}

.diagnostic-detail {
  display: grid;
  margin: 0 0 18px;
  grid-template-columns: 1fr 1fr;
  border-top: 1px solid var(--line);
}

.update-content {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
}

.compact-notes,
.release-history ul {
  margin: 7px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.65;
}

.release-history {
  margin: 16px 4px 28px;
}

.release-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.release-heading span,
.release-history p {
  color: var(--muted);
  font-size: 11px;
}

.diagnostic-detail p {
  margin: 0;
  padding: 14px 4px;
  border-bottom: 1px solid var(--line-light);
}

.diagnostic-detail span,
.diagnostic-detail strong {
  display: block;
}

.diagnostic-detail span {
  color: var(--muted);
  font-size: 11px;
}

.diagnostic-detail strong {
  margin-top: 4px;
  font-size: 13px;
}

.fault-tips {
  margin-top: 18px;
}

.fault-tips pre,
.token-alert pre {
  max-height: 280px;
  overflow: auto;
  padding: 12px;
  white-space: pre-wrap;
  background: #eee6d9;
}

.reminder-panel {
  display: flex;
  max-width: 660px;
  align-items: center;
  justify-content: space-between;
  padding: 20px 0;
  border-bottom: 1px solid var(--line);
}

.reminder-panel p {
  margin: 5px 0 0;
  color: var(--muted);
}

.reminder-form {
  max-width: 660px;
  padding-top: 20px;
}

.appearance-settings {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 28px;
}

.appearance-settings section {
  padding: 22px;
  background: rgba(251, 248, 241, 0.68);
  border: 1px solid var(--line);
}

.appearance-settings h3,
.appearance-settings p {
  margin-top: 0;
}

.appearance-settings p,
.runtime-capabilities,
.local-ai-heading p {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.tab-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.tab-toolbar p {
  color: var(--muted);
}

.token-alert {
  margin-top: 20px;
}

.token-alert code {
  display: block;
  margin-top: 8px;
  padding: 8px;
  overflow-wrap: anywhere;
  color: var(--charcoal);
  background: #eee6d9;
}

.token-alert button {
  margin-top: 10px;
}

.subheading {
  margin: 28px 0 10px;
  font-size: 17px;
}

.ai-warning {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
  padding: 16px 18px;
  color: #f8efe4;
  background: var(--charcoal);
}

.ai-warning > svg {
  flex: 0 0 auto;
  color: #f2b3a9;
  font-size: 25px;
}

.ai-warning strong,
.ai-warning p {
  display: block;
  margin: 0;
}

.ai-warning p {
  margin-top: 4px;
  color: #c8beb2;
  font-size: 11px;
}

.ai-settings-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 28px;
}

.ai-settings-grid section {
  padding: 22px;
  background: rgba(251, 248, 241, 0.68);
  border: 1px solid var(--line);
}

.ai-settings-grid h3 {
  margin: 0 0 18px;
  padding-bottom: 10px;
  font-size: 16px;
  border-bottom: 1px solid var(--line);
}

.local-ai-section {
  grid-column: 1 / -1;
}

.local-ai-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.local-ai-heading h3 {
  margin-bottom: 6px;
  border-bottom: 0;
}

.ai-status {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 10px;
}

.policy-save {
  margin-top: 16px;
}

.update-targets {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin: 18px 0;
  padding: 14px 16px;
  background: rgba(238, 230, 217, 0.52);
  border: 1px solid var(--line);
}

.update-targets small {
  color: var(--muted);
  font-size: 11px;
}
</style>
