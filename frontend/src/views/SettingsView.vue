<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { IconCloudDownload, IconDownload, IconPlus, IconRefresh, IconSafe, IconUpload } from "@arco-design/web-vue/es/icon";
import { Message, Modal } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import { useSessionStore } from "../stores/session";
import { useAppearanceStore } from "../stores/appearance";
import type {
  AIModelPack,
  AIPolicy,
  AIProvider,
  HardwareBenchmark,
  HardwareProfile,
  LocalAIRuntime,
  ModelRecommendation,
  Backup,
  Device,
  Pairing,
  ReleaseHistory,
  ReminderPreference,
  User,
  WorkspaceRoot,
} from "../types";
import { beijingNowIso, formatServerTime } from "../utils/datetime";
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

interface NetworkConfiguration {
  automatic_addresses: string[];
  bind_host: string;
  advertise_host: string;
  port: number;
  tls_enabled: boolean;
  local_browser_url: string;
  service_url: string;
  pending: {
    transaction_id?: string;
    state?: string;
    device_notifications?: number;
    requested?: Record<string, unknown>;
  } | null;
}

interface UserDeletionImpact {
  user_id: string;
  counts: Record<string, number>;
  requires_transfer: boolean;
  history_preserved: boolean;
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
const backupLifecycle = ref<"active" | "deleted">("active");
const backupDeleteVisible = ref(false);
const backupDeleteTarget = ref<Backup | null>(null);
const backupDeleteReason = ref("");
const backupDeletionImpact = ref<{ size_bytes: number; remaining_completed_backups: number; recoverable_days: number } | null>(null);
const audits = ref<Audit[]>([]);
const pairings = ref<Pairing[]>([]);
const diagnostics = ref<Diagnostics | null>(null);
const systemStatus = ref<SystemStatus | null>(null);
const network = ref<NetworkConfiguration | null>(null);
const networkActionLoading = ref(false);
const aiProvider = ref<AIProvider | null>(null);
const aiPolicies = ref<AIPolicy[]>([]);
const editingPolicyId = ref("");
const workspaceRoots = ref<WorkspaceRoot[]>([]);
const reminder = ref<ReminderPreference | null>(null);
const logs = ref("");
const loadingBackup = ref(false);
const userVisible = ref(false);
const userEditVisible = ref(false);
const userDeleteVisible = ref(false);
const deletingUser = ref<User | null>(null);
const deletionImpact = ref<UserDeletionImpact | null>(null);
const transferToUserId = ref("");
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
    download_state?: string;
    download_message?: string;
    download_received?: number;
    download_total?: number;
    online_download?: {
      download_state?: string;
      download_message?: string;
      download_received?: number;
      download_total?: number;
    };
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
const onlineUpdateChecking = ref(false);
const onlineUpdatePreparing = ref(false);
const onlineUpdate = ref<{
  available: boolean;
  target_available?: boolean;
  availability_message?: string;
  current_version: string;
  version: string;
  title: string;
  release_notes: string[];
  published_at: string;
  package_size: number;
} | null>(null);
const projectionRebuilding = ref(false);
const UPDATE_TASK_KEY = "partyops.pending-update";
const ONLINE_UPDATE_TASK_KEY = "partyops.pending-online-update";
const ONLINE_UPDATE_LAST_CHECK_KEY = "partyops.online-update-last-check";
let updatePollTimer: number | undefined;
let onlineUpdatePollTimer: number | undefined;
let onlineUpdatePollFailures = 0;
let onlineUpdateMissingPolls = 0;
let updatePollFailures = 0;
let updateMissingPolls = 0;
const PREPARE_POLL_DEADLINE_MS = 30 * 60 * 1000;
const APPLY_POLL_DEADLINE_MS = 60 * 60 * 1000;
const MAX_POLL_FAILURES = 8;
const MAX_MISSING_POLLS = 8;
const userForm = reactive({ username: "", display_name: "", password: "", role: "staff" });
const editingUser = ref<User | null>(null);
const editUserForm = reactive({ display_name: "", role: "staff", active: true, password: "" });
const networkForm = reactive({ bind_host: "", advertise_host: "", port: 18765, migration_grace_hours: 24 });
const pairingForm = reactive({ name: "协同终端" });
const importInput = ref<HTMLInputElement | null>(null);
const modelPackInput = ref<HTMLInputElement | null>(null);
const modelPacks = ref<AIModelPack[]>([]);
const localAIRuntime = ref<LocalAIRuntime | null>(null);
const loadWarning = ref("");
const modelPackUploading = ref(false);
const hardwareProfile = ref<HardwareProfile | null>(null);
const modelRecommendations = ref<ModelRecommendation[]>([]);
const hardwareChecking = ref(false);
const benchmarkRunning = ref(false);
const hardwareBenchmark = ref<HardwareBenchmark | null>(null);
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

function resetPolicyForm(policy?: AIPolicy) {
  Object.assign(policyForm, policy ? {
    name: policy.name,
    allowed_root_ids: [...policy.allowed_root_ids],
    allowed_task_categories_text: policy.allowed_task_categories.join("、"),
    allowed_file_types_text: policy.allowed_file_types.join(","),
    capabilities: [...policy.capabilities],
    active: policy.active,
  } : {
    name: "默认只读策略",
    allowed_root_ids: [],
    allowed_task_categories_text: "",
    allowed_file_types_text: ".docx,.pdf,.xlsx,.txt,.md,.png,.jpg,.jpeg",
    capabilities: ["search", "summarize", "classify", "draft_report", "suggest_breakdown", "check_materials"],
    active: true,
  });
}
const runtimeModeLabel = computed(() => {
  if (health.value.mode === "host") return "主机模式";
  if (health.value.mode === "personal") return "个人模式";
  if (health.value.mode === "client") return "协同机模式";
  return "待确认";
});

function openRoleConfigurationWizard() {
  Modal.confirm({
    title: "重新配置这台电脑的运行角色",
    content:
      "配置向导会在独立的本机页面中打开。请先保存正在编辑的内容；切换失败时系统会恢复当前角色，且不会删除原业务数据。主机模式可能需要一次系统管理员授权。",
    okText: "打开配置向导",
    cancelText: "暂不切换",
    onOk: async () => {
      let deepLink = "partyops-client://reconfigure";
      if (session.user?.role === "admin") {
        try {
          const prepared = await api.post<{ deep_link: string }>("/system/reconfigure-request");
          if (prepared.deep_link) deepLink = prepared.deep_link;
        } catch {
          Message.warning("当前页面无法写入本机启动标记，将直接唤起这台电脑上的配置向导");
        }
      }
      const launcher = document.createElement("a");
      launcher.href = deepLink;
      launcher.setAttribute("aria-hidden", "true");
      launcher.style.display = "none";
      document.body.appendChild(launcher);
      launcher.click();
      launcher.remove();
      Message.info("正在打开 PartyOps 配置向导；若系统询问是否打开，请选择允许");
    },
  });
}

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
      api.get<Backup[]>(`/backups?lifecycle=${backupLifecycle.value}`),
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
      api.get<NetworkConfiguration>("/system/network"),
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
    network.value = valueOr(results[15], network.value, "网络与协同");
    if (network.value) Object.assign(networkForm, {
      bind_host: network.value.bind_host,
      advertise_host: network.value.advertise_host,
      port: network.value.port,
    });
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
    const policy = aiPolicies.value.find((item) => item.id === editingPolicyId.value)
      || aiPolicies.value[0];
    if (policy) {
      editingPolicyId.value = policy.id;
      resetPolicyForm(policy);
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

async function detectLocalHardware() {
  hardwareChecking.value = true;
  try {
    const [profile, recommendations] = await Promise.all([
      api.get<HardwareProfile>("/ai/hardware-profile"),
      api.get<ModelRecommendation[]>("/ai/model-recommendations"),
    ]);
    hardwareProfile.value = profile;
    modelRecommendations.value = recommendations;
    Message.success("本机能力检测完成，硬件信息未离开这台电脑");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "本机能力检测失败");
  } finally {
    hardwareChecking.value = false;
  }
}

async function runHardwareBenchmark() {
  benchmarkRunning.value = true;
  try {
    hardwareBenchmark.value = await api.post<HardwareBenchmark>("/ai/hardware-profile/benchmark");
    Message.info(hardwareBenchmark.value.message);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "性能测试未完成");
  } finally {
    benchmarkRunning.value = false;
  }
}

function memoryText(megabytes: number) {
  return megabytes >= 1024
    ? `${(megabytes / 1024).toFixed(megabytes >= 10240 ? 0 : 1)} GB`
    : `${megabytes} MB`;
}

function recommendationColor(status: ModelRecommendation["status"]) {
  return status === "流畅" ? "green" : status === "可用" ? "orange" : "red";
}

async function activateModelPack(pack: AIModelPack, capability: "embedding" | "llm" | "intent_router") {
  try {
    await api.post<AIModelPack>(`/admin/ai/model-packs/${pack.id}/activate?capability=${capability}`);
    Message.success(capability === "embedding" ? "中文向量模型已启用" : capability === "intent_router" ? "受控意图助手已启用；写操作仍需确认" : "本地 LLM 已启用；只生成带来源草稿");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "模型包启用失败");
  }
}

async function deactivateModelCapability(capability: "embedding" | "llm" | "intent_router") {
  try {
    await api.delete(`/admin/ai/model-activations/${capability}`);
    Message.success(capability === "embedding" ? "中文向量能力已停用" : capability === "intent_router" ? "受控意图助手已停用" : "本地 LLM 已停用并卸载");
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

async function checkOnlineUpdate(showMessage = true) {
  onlineUpdateChecking.value = true;
  try {
    onlineUpdate.value = await api.get<typeof onlineUpdate.value>("/admin/updates/online");
    localStorage.setItem(ONLINE_UPDATE_LAST_CHECK_KEY, Date.now().toString());
    if (showMessage) {
      if (onlineUpdate.value?.available) {
        Message.success(`发现新版本 ${onlineUpdate.value.version}`);
      } else if (onlineUpdate.value?.target_available === false) {
        Message.warning(onlineUpdate.value.availability_message || "当前系统的更新包暂未通过发布门禁");
      } else {
        Message.success(`当前 ${onlineUpdate.value?.current_version || "版本"} 已是最新`);
      }
    }
  } catch (error) {
    if (showMessage) {
      Message.error(error instanceof Error ? error.message : "暂时无法检查官方更新，请稍后重试");
    }
  } finally {
    onlineUpdateChecking.value = false;
  }
}

function scheduleOnlineUpdatePoll(delay = 1500) {
  window.clearTimeout(onlineUpdatePollTimer);
  onlineUpdatePollTimer = window.setTimeout(pollOnlineUpdatePreparation, delay);
}

function pollStartedAt(value: unknown): number {
  if (typeof value !== "string") return Date.now();
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Date.now();
}

function stopOnlineUpdateMonitor(message: string) {
  window.clearTimeout(onlineUpdatePollTimer);
  localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
  window.dispatchEvent(new CustomEvent("partyops:update-task-changed"));
  onlineUpdatePreparing.value = false;
  onlineUpdatePollFailures = 0;
  onlineUpdateMissingPolls = 0;
  Message.warning(message);
}

async function prepareOnlineUpdate() {
  onlineUpdatePreparing.value = true;
  try {
    const prepared = await api.post<{ id: string; version: string }>("/admin/updates/online/prepare");
    localStorage.setItem(
      ONLINE_UPDATE_TASK_KEY,
      JSON.stringify({
        packageId: prepared.id,
        version: prepared.version,
        startedAt: beijingNowIso(),
      }),
    );
    onlineUpdatePollFailures = 0;
    onlineUpdateMissingPolls = 0;
    window.dispatchEvent(new CustomEvent("partyops:update-task-changed"));
    Message.success("已开始安全下载；可以继续使用系统，下载完成后再确认安装");
    scheduleOnlineUpdatePoll(500);
  } catch (error) {
    onlineUpdatePreparing.value = false;
    Message.error(error instanceof Error ? error.message : "更新包下载未能开始");
  }
}

async function pollOnlineUpdatePreparation() {
  const raw = localStorage.getItem(ONLINE_UPDATE_TASK_KEY);
  if (!raw) {
    onlineUpdatePreparing.value = false;
    return;
  }
  let pending: { packageId: string; version: string; startedAt?: string };
  try {
    pending = JSON.parse(raw);
  } catch {
    localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
    window.dispatchEvent(new CustomEvent("partyops:update-task-changed"));
    onlineUpdatePreparing.value = false;
    return;
  }
  if (Date.now() - pollStartedAt(pending.startedAt) >= PREPARE_POLL_DEADLINE_MS) {
    stopOnlineUpdateMonitor("下载校验超过 30 分钟，状态已停止跟踪；当前版本未受影响，可重试或打开诊断日志。");
    return;
  }
  try {
    updatePackages.value = await api.get<typeof updatePackages.value>("/admin/updates");
    const prepared = updatePackages.value.find((item) => item.id === pending.packageId);
    onlineUpdatePollFailures = 0;
    onlineUpdateMissingPolls = prepared ? 0 : onlineUpdateMissingPolls + 1;
    if (prepared?.status === "validated") {
      localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
      window.dispatchEvent(new CustomEvent("partyops:update-task-changed"));
      onlineUpdatePreparing.value = false;
      Message.success(`${pending.version} 已下载并通过双重校验，请确认后开始升级`);
      return;
    }
    if (prepared?.status === "failed") {
      localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
      window.dispatchEvent(new CustomEvent("partyops:update-task-changed"));
      onlineUpdatePreparing.value = false;
      const state = prepared.manifest as { download_message?: string };
      Message.warning(state.download_message || "安全下载未完成；当前版本未受影响，可稍后重试");
      return;
    }
  } catch {
    onlineUpdatePollFailures += 1;
  }
  if (onlineUpdatePollFailures >= MAX_POLL_FAILURES) {
    stopOnlineUpdateMonitor("连续 8 次无法读取下载状态，已停止自动等待；任务不会重复执行，可稍后重试或查看日志。");
    return;
  }
  if (onlineUpdateMissingPolls >= MAX_MISSING_POLLS) {
    stopOnlineUpdateMonitor("服务器未找到对应下载任务，已停止自动等待；请重新检查更新或查看日志。");
    return;
  }
  onlineUpdatePreparing.value = true;
  scheduleOnlineUpdatePoll(Math.min(15_000, 1500 * 2 ** Math.min(onlineUpdatePollFailures, 3)));
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
    JSON.stringify({ packageId, version, includeHost, startedAt: beijingNowIso() }),
  );
  updatePollFailures = 0;
  updateMissingPolls = 0;
  updatePolling.value = true;
  scheduleUpdatePoll(1500);
}

function stopUpdateMonitor(message: string) {
  window.clearTimeout(updatePollTimer);
  localStorage.removeItem(UPDATE_TASK_KEY);
  updatePolling.value = false;
  updatePollFailures = 0;
  updateMissingPolls = 0;
  Message.warning(message);
}

async function pollUpdateProgress() {
  const raw = localStorage.getItem(UPDATE_TASK_KEY);
  if (!raw) {
    updatePolling.value = false;
    return;
  }
  let pending: { packageId: string; version: string; includeHost: boolean; startedAt?: string };
  try {
    pending = JSON.parse(raw);
  } catch {
    localStorage.removeItem(UPDATE_TASK_KEY);
    updatePolling.value = false;
    return;
  }
  if (Date.now() - pollStartedAt(pending.startedAt) >= APPLY_POLL_DEADLINE_MS) {
    stopUpdateMonitor("升级状态已等待 60 分钟，系统停止自动轮询以免无限等待；请打开诊断日志确认结果后再操作。");
    return;
  }
  try {
    updateRuns.value = await api.get<typeof updateRuns.value>("/admin/update-runs");
    const relevant = updateRuns.value.filter((run) => run.package_id === pending.packageId);
    updatePollFailures = 0;
    updateMissingPolls = relevant.length ? 0 : updateMissingPolls + 1;
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
    // 主机安装和重启期间短暂无法访问是正常现象；连续失败才停止自动轮询。
    updatePollFailures += 1;
  }
  if (updatePollFailures >= MAX_POLL_FAILURES) {
    stopUpdateMonitor("连续 8 次无法读取升级状态，结果暂时未知；系统不会自动重复安装，请查看日志后再重试。");
    return;
  }
  if (updateMissingPolls >= MAX_MISSING_POLLS) {
    stopUpdateMonitor("服务器未找到对应升级任务，已停止自动等待；请查看更新记录和诊断日志。");
    return;
  }
  updatePolling.value = true;
  scheduleUpdatePoll(Math.min(30_000, 3000 * 2 ** Math.min(updatePollFailures, 3)));
}

function updateTargetName(deviceId: string | null): string {
  if (!deviceId) return "主机";
  return updateDevices.value.find((device) => device.id === deviceId)?.name || "已移除设备";
}

function requestUpdate(item: typeof updatePackages.value[number]) {
  const enabledDevices = updateDevices.value.filter((device) => device.active).length;
  Modal.confirm({
    title: `确认升级到 ${item.version}`,
    content: `系统将先建立一致性备份，再升级本机${enabledDevices ? `并为 ${enabledDevices} 台协同电脑排队` : ""}。期间页面可能短暂断开；失败会自动恢复上一版本和数据。是否现在开始？`,
    okText: "自动备份并开始升级",
    cancelText: "暂不升级",
    maskClosable: false,
    onOk: () => applyUpdate(item),
  });
}

function updateDownloadState(item: typeof updatePackages.value[number]) {
  return item.manifest.online_download || item.manifest;
}

function updateDownloadPercent(item: typeof updatePackages.value[number]): number {
  const state = updateDownloadState(item);
  const total = Number(state.download_total || 0);
  return total > 0 ? Math.min(100, Math.round(Number(state.download_received || 0) * 100 / total)) : 0;
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
    const current = aiPolicies.value.find((item) => item.id === editingPolicyId.value);
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

function editAIPolicy(policy: AIPolicy) {
  editingPolicyId.value = policy.id;
  resetPolicyForm(policy);
}

function newAIPolicy() {
  editingPolicyId.value = "";
  resetPolicyForm();
}

async function toggleAIPolicy(policy: AIPolicy) {
  try {
    await api.patch(
      `/ai/policies/${policy.id}`,
      {
        name: policy.name,
        allowed_root_ids: policy.allowed_root_ids,
        allowed_task_categories: policy.allowed_task_categories,
        allowed_file_types: policy.allowed_file_types,
        capabilities: policy.capabilities,
        allow_restricted: false,
        active: !policy.active,
      },
      { "If-Match": String(policy.version) },
    );
    Message.success(policy.active ? "AI 白名单已停用，历史草稿和审计保留" : "AI 白名单已恢复启用");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "AI 白名单状态更新失败");
  }
}

async function uninstallModelPack(pack: AIModelPack) {
  if (pack.active_capabilities.length) {
    Message.warning("请先停用这个模型包的全部能力");
    return;
  }
  try {
    const result = await api.delete<{ cleanup_pending: boolean }>(
      `/admin/ai/model-packs/${pack.id}`,
    );
    Message.success(
      result.cleanup_pending
        ? "模型包已卸载；被系统占用的残留文件将在下次启动时清理"
        : "模型包已从本机安全卸载",
    );
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "模型包卸载失败");
  }
}

async function openUserDelete(user: User) {
  try {
    deletingUser.value = user;
    deletionImpact.value = await api.get<UserDeletionImpact>(`/admin/users/${user.id}/deletion-impact`);
    transferToUserId.value = users.value.find((item) => item.active && item.id !== user.id)?.id || "";
    userDeleteVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "无法读取删除影响");
  }
}

async function archiveUser() {
  if (!deletingUser.value || !deletionImpact.value) return;
  if (deletionImpact.value.requires_transfer && !transferToUserId.value) {
    Message.warning("请先选择责任接收人");
    return;
  }
  try {
    const query = transferToUserId.value ? `?transfer_to=${encodeURIComponent(transferToUserId.value)}` : "";
    await api.delete(`/admin/users/${deletingUser.value.id}${query}`);
    userDeleteVisible.value = false;
    Message.success("用户已归档，责任已移交，历史记录保留");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "用户归档失败");
  }
}

async function restoreUser(user: User) {
  try {
    await api.post(`/admin/users/${user.id}/restore`);
    Message.success("用户已恢复；设备授权需由管理员按需重新授予");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "用户恢复失败");
  }
}

async function saveNetwork() {
  networkActionLoading.value = true;
  try {
    await api.post("/system/network/validate", networkForm);
    const result = await api.patch<{ restart_required: boolean; device_notifications?: number }>("/system/network", networkForm);
    Message.success(result.restart_required ? `网络事务已准备，并通知 ${result.device_notifications || 0} 台协同电脑；重启服务后请完成健康检查` : "网络配置未变化");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "网络配置更新失败");
  } finally {
    networkActionLoading.value = false;
  }
}

async function confirmNetwork() {
  const transactionId = network.value?.pending?.transaction_id;
  if (!transactionId) return;
  networkActionLoading.value = true;
  try {
    await api.post(`/system/network/transactions/${transactionId}/confirm`);
    Message.success("回环、公布地址、TLS 证书和健康端点均已通过，网络事务已提交");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "网络健康检查失败");
  } finally {
    networkActionLoading.value = false;
  }
}

async function rollbackNetwork() {
  const transactionId = network.value?.pending?.transaction_id;
  if (!transactionId) return;
  networkActionLoading.value = true;
  try {
    await api.post(`/system/network/transactions/${transactionId}/rollback`);
    Message.success("旧网络配置和证书已恢复，请受控重启服务完成回滚");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "网络配置回滚失败");
  } finally {
    networkActionLoading.value = false;
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

async function loadBackups() {
  try {
    backups.value = await api.get<Backup[]>(`/backups?lifecycle=${backupLifecycle.value}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "备份记录读取失败");
  }
}

async function openBackupDelete(backup: Backup) {
  try {
    backupDeleteTarget.value = backup;
    backupDeletionImpact.value = await api.get(
      `/admin/backups/${backup.id}/deletion-impact`,
    );
    backupDeleteReason.value = "";
    backupDeleteVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "无法读取备份移除影响");
  }
}

async function deleteBackup() {
  if (!backupDeleteTarget.value || backupDeleteReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的移除原因");
    return;
  }
  try {
    await api.deleteBody(
      `/admin/backups/${backupDeleteTarget.value.id}`,
      { reason: backupDeleteReason.value.trim() },
      { "If-Match": String(backupDeleteTarget.value.version) },
    );
    backupDeleteVisible.value = false;
    Message.success("备份已移入回收站，可在保留期内恢复");
    await loadBackups();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "备份移除失败");
  }
}

async function restoreDeletedBackup(backup: Backup) {
  try {
    await api.post(
      `/admin/backups/${backup.id}/restore`,
      { reason: "管理员复核后恢复备份文件" },
      { "If-Match": String(backup.version) },
    );
    Message.success("备份已恢复到可用列表");
    await loadBackups();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "备份恢复失败");
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
    if (localStorage.getItem(ONLINE_UPDATE_TASK_KEY)) {
      onlineUpdatePreparing.value = true;
      scheduleOnlineUpdatePoll(500);
    }
    const lastOnlineCheck = Number(localStorage.getItem(ONLINE_UPDATE_LAST_CHECK_KEY) || 0);
    if (!lastOnlineCheck || Date.now() - lastOnlineCheck >= 24 * 60 * 60 * 1000) {
      void checkOnlineUpdate(false);
    }
  }
});
onBeforeUnmount(() => {
  window.clearTimeout(updatePollTimer);
  window.clearTimeout(onlineUpdatePollTimer);
});
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
            <div><span>运行模式</span><strong>{{ runtimeModeLabel }}</strong></div>
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
        <section class="role-reconfigure-panel">
          <div>
            <strong>需要把这台电脑改为其他角色？</strong>
            <p>可在这台电脑上重新选择个人模式、主机模式或协同机模式。向导会先保存回滚快照，验证新角色可用后才提交切换。</p>
          </div>
          <a-button type="outline" status="warning" @click="openRoleConfigurationWizard">
            重新配置运行角色
          </a-button>
        </section>
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

      <a-tab-pane v-if="session.user?.role === 'admin'" key="network" title="网络与协同">
        <div class="tab-toolbar">
          <p>自动探测、监听地址和协同机实际访问地址分开管理；变更前先校验，失败会恢复旧配置。</p>
          <a-tag :color="network?.tls_enabled ? 'green' : 'orange'">{{ network?.tls_enabled ? "HTTPS 已启用" : "仅本机可使用 HTTP" }}</a-tag>
        </div>
        <a-alert v-if="network?.pending && network.pending.state !== 'active' && network.pending.state !== 'rolled_back'" type="warning" class="update-note">
          网络事务 {{ network.pending.transaction_id }} 已保存回滚快照，并通知 {{ network.pending.device_notifications || 0 }} 台协同电脑。
          当前状态：{{ zhLabel(network.pending.state || "pending") }}。重启服务后执行健康检查，失败可恢复旧配置和证书。
          <a-space>
            <a-button size="mini" :loading="networkActionLoading" @click="confirmNetwork">验证新地址并提交</a-button>
            <a-popconfirm content="确认恢复旧地址和旧主机证书？恢复后需要受控重启服务。" @ok="rollbackNetwork">
              <a-button size="mini" status="danger" :loading="networkActionLoading">回滚旧配置</a-button>
            </a-popconfirm>
          </a-space>
        </a-alert>
        <section class="network-address-ledger">
          <article><span>本机浏览地址</span><strong>{{ network?.local_browser_url || "待检测" }}</strong><small>只供主机自己打开，不会下发协同机</small></article>
          <article><span>实际监听</span><strong>{{ network?.bind_host || "待检测" }}:{{ network?.port || 18765 }}</strong><small>可使用通配监听，但必须配套明确私网公布地址</small></article>
          <article><span>协同公布地址</span><strong>{{ network?.service_url || "待配置" }}</strong><small>127.0.0.1、localhost、0.0.0.0 永不允许下发</small></article>
        </section>
        <a-form :model="networkForm" layout="vertical" class="network-form">
          <a-form-item label="自动探测地址">
            <a-radio-group v-model="networkForm.advertise_host">
              <a-radio v-for="address in network?.automatic_addresses || []" :key="address" :value="address">{{ address }}</a-radio>
            </a-radio-group>
            <p class="muted">没有合适地址时可在下方手工填写固定私网 IP 或主机名。</p>
          </a-form-item>
          <a-form-item label="监听地址"><a-input v-model="networkForm.bind_host" placeholder="例如 0.0.0.0 或 192.168.1.10" /></a-form-item>
          <a-form-item label="对外公布地址"><a-input v-model="networkForm.advertise_host" placeholder="协同机实际能够访问的地址" /></a-form-item>
          <a-form-item label="业务端口"><a-input-number v-model="networkForm.port" :min="1024" :max="65535" /></a-form-item>
          <a-form-item label="旧地址迁移宽限（小时）"><a-input-number v-model="networkForm.migration_grace_hours" :min="1" :max="168" /></a-form-item>
          <a-button type="primary" :loading="networkActionLoading" @click="saveNetwork">验证并创建网络事务</a-button>
        </a-form>
        <a-table :data="updateDevices.filter((item) => item.active)" :pagination="false" class="network-device-table">
          <template #columns>
            <a-table-column title="协同电脑" data-index="name" />
            <a-table-column title="系统 / 架构"><template #cell="{ record }">{{ record.platform }} / {{ record.architecture }}</template></a-table-column>
            <a-table-column title="协议"><template #cell="{ record }">v{{ record.protocol_version || 1 }}</template></a-table-column>
            <a-table-column title="凭据"><template #cell="{ record }">{{ zhLabel(record.credential_state || "active") }}</template></a-table-column>
            <a-table-column title="最后心跳"><template #cell="{ record }">{{ formatServerTime(record.last_seen_at, "YYYY-MM-DD HH:mm", "从未连接") }}</template></a-table-column>
            <a-table-column title="状态"><template #cell="{ record }"><a-tag>{{ zhLabel(record.status) }}</a-tag></template></a-table-column>
          </template>
        </a-table>
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
          <p>在系统内完成检查、下载、校验、安装和失败回滚；无需重新到官网下载安装包。</p>
          <a-space>
            <a-button :loading="onlineUpdateChecking" @click="checkOnlineUpdate()"><template #icon><IconRefresh /></template>检查官方更新</a-button>
            <a-button aria-label="导入 PartyOps 更新包" @click="updateInput?.click()"><template #icon><IconUpload /></template>导入 .partyops-update</a-button>
            <input ref="updateInput" type="file" accept=".partyops-update" hidden @change="uploadUpdate(($event.target as HTMLInputElement).files?.[0] || null)" />
          </a-space>
        </div>
        <section v-if="onlineUpdate" class="online-update-card" :class="{ available: onlineUpdate.available }">
          <div>
            <small>当前 {{ onlineUpdate.current_version }} · 官方目录签名已验证</small>
            <strong>{{ onlineUpdate.available ? `可更新到 ${onlineUpdate.version}` : onlineUpdate.target_available === false ? "当前系统暂缓提供此版本" : "当前已是最新版本" }}</strong>
            <p>{{ onlineUpdate.availability_message || onlineUpdate.title }}<template v-if="onlineUpdate.published_at"> · {{ onlineUpdate.published_at }}</template></p>
            <ul v-if="onlineUpdate.release_notes?.length" class="compact-notes">
              <li v-for="note in onlineUpdate.release_notes?.slice(0, 5) || []" :key="note">{{ note }}</li>
            </ul>
          </div>
          <a-button
            v-if="onlineUpdate.available"
            type="primary"
            :loading="onlineUpdatePreparing"
            @click="prepareOnlineUpdate"
          >
            <template #icon><IconCloudDownload /></template>安全下载更新
          </a-button>
        </section>
        <a-alert type="info" class="update-note">
          系统会先验证官方目录签名，再验证整个更新包和其中每个安装程序的 Ed25519 签名、大小与 SHA-256。
          点击“开始升级”后自动备份；安装或健康检查失败会恢复上一版本和数据。只有旧版缺少更新助手时才需手工安装一次桥接包。
        </a-alert>
        <a-alert v-if="onlineUpdatePreparing" type="info" class="update-note">
          更新包正在后台安全下载。可以继续处理其他工作，离开本页不会中断；下载完成后仍需您确认才会安装。
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
                <template v-if="updateDownloadState(record).download_total">
                  <a-progress :percent="updateDownloadPercent(record) / 100" size="small" />
                  <small class="update-content">{{ updateDownloadState(record).download_message }}</small>
                </template>
              </template>
            </a-table-column>
            <a-table-column title="签名"><template #cell="{ record }">{{ record.signature_valid ? "已验证" : "未通过，不能安装" }}</template></a-table-column>
            <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.status) }}</template></a-table-column>
            <a-table-column title="校验值"><template #cell="{ record }"><code>{{ record.sha256.slice(0, 16) }}…</code></template></a-table-column>
            <a-table-column title="操作">
              <template #cell="{ record }">
                <a-button
                  size="mini"
                  type="primary"
                  :disabled="!record.signature_valid || record.status !== 'validated' || updatePolling"
                  @click="requestUpdate(record)"
                >{{ record.status === "validated" ? "开始升级" : "等待校验完成" }}</a-button>
              </template>
            </a-table-column>
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
              <a-alert
                v-if="aiForm.base_url.trim().toLowerCase().startsWith('http://')"
                :type="aiForm.trusted_intranet ? 'warning' : 'error'"
                show-icon
              >
                HTTP 接口仅限本机或单位内网地址；请确认网络边界可信并开启“单位可信内网”。公网模型仍必须使用 HTTPS。
              </a-alert>
              <a-form-item label="启用 AI"><a-switch v-model="aiForm.enabled" /></a-form-item>
              <a-space>
                <a-button type="primary" @click="saveAISettings">加密保存</a-button>
                <a-button :disabled="!aiProvider?.base_url" @click="testAISettings">仅测试连接</a-button>
              </a-space>
              <p class="ai-status">最近状态：{{ zhLabel(aiProvider?.last_status, "未配置") }}<template v-if="aiProvider?.last_test_at"> · {{ formatServerTime(aiProvider.last_test_at, "YYYY-MM-DD HH:mm") }}</template></p>
            </a-form>
          </section>
          <section>
            <div class="policy-heading"><div><h3>AI 只读白名单</h3><p>共 {{ aiPolicies.length }} 条；每条均可单独编辑、停用和恢复。</p></div><a-button size="small" @click="newAIPolicy"><template #icon><IconPlus /></template>新建策略</a-button></div>
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
              <a-form-item v-if="editingPolicyId" label="策略状态"><a-switch v-model="policyForm.active" checked-text="启用" unchecked-text="停用" /><span class="inline-note">停用不会删除历史草稿或审计记录。</span></a-form-item>
              <a-button type="primary" class="policy-save" @click="saveAIPolicy">{{ editingPolicyId ? "保存白名单" : "创建白名单" }}</a-button>
            </a-form>
            <div class="policy-list">
              <article v-for="policy in aiPolicies" :key="policy.id" :class="{ inactive: !policy.active, selected: editingPolicyId === policy.id }">
                <div><strong>{{ policy.name }}</strong><small>{{ policy.active ? "启用" : "已停用" }} · {{ policy.capabilities.length }} 项能力 · {{ policy.allowed_root_ids.length }} 个目录</small></div>
                <a-space><a-button size="mini" type="text" @click="editAIPolicy(policy)">编辑</a-button><a-popconfirm :content="policy.active ? '停用后不再授权新的 AI 请求，历史草稿和审计继续保留。确认停用？' : '确认恢复这条 AI 白名单？'" @ok="toggleAIPolicy(policy)"><a-button size="mini" type="text" :status="policy.active ? 'danger' : 'normal'">{{ policy.active ? "停用" : "恢复" }}</a-button></a-popconfirm></a-space>
              </article>
              <p v-if="!aiPolicies.length" class="muted">尚未建立 AI 白名单；默认拒绝读取任何业务目录。</p>
            </div>
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
            <div class="hardware-detector">
              <div class="detector-heading">
                <div><strong>先检测这台主机适合什么模型</strong><p>仅读取本机处理器、内存、显存和模型目录空间，不上传任何硬件或业务信息。</p></div>
                <a-space><a-button :loading="hardwareChecking" @click="detectLocalHardware"><template #icon><IconRefresh /></template>{{ hardwareProfile ? "重新检测" : "开始检测" }}</a-button><a-button v-if="hardwareProfile" :loading="benchmarkRunning" @click="runHardwareBenchmark">可选性能测试</a-button></a-space>
              </div>
              <template v-if="hardwareProfile">
                <div class="hardware-summary">
                  <div><span>处理器</span><strong>{{ hardwareProfile.cpu_name }}</strong><small>{{ hardwareProfile.cpu_cores }} 核 · {{ hardwareProfile.architecture }}</small></div>
                  <div><span>内存余量</span><strong>{{ memoryText(hardwareProfile.available_memory_mb) }}</strong><small>为系统保留 {{ memoryText(hardwareProfile.reserved_memory_mb) }}</small></div>
                  <div><span>模型空间</span><strong>{{ memoryText(hardwareProfile.model_disk_free_mb) }}</strong><small>含 25% 安全余量后再推荐</small></div>
                  <div><span>加速后端</span><strong>{{ hardwareProfile.gpu_backends.join('、') || 'CPU' }}</strong><small>{{ hardwareProfile.gpu_memory_mb ? `显存 ${memoryText(hardwareProfile.gpu_memory_mb)}` : '未要求必须有独立显卡' }}</small></div>
                </div>
                <a-alert v-if="hardwareBenchmark" :type="hardwareBenchmark.available ? 'success' : 'info'">{{ hardwareBenchmark.message }}<template v-if="hardwareBenchmark.available"> · 本机分值 {{ hardwareBenchmark.score }}</template></a-alert>
                <details class="model-recommendations">
                  <summary>查看从基础到旗舰的 {{ modelRecommendations.length }} 个模型建议</summary>
                  <div class="model-recommendation-list">
                    <article v-for="model in modelRecommendations" :key="model.id">
                      <header><div><span>{{ model.tier }} · {{ model.kind === 'embedding' ? '语义检索' : model.kind === 'intent_router' ? '意图助手' : '本地草稿' }}</span><h4>{{ model.name }}</h4></div><a-tag :color="recommendationColor(model.status)">{{ model.status }}</a-tag></header>
                      <p>{{ model.summary }}</p>
                      <small>{{ model.reason }}</small>
                      <dl><div><dt>最低内存</dt><dd>{{ memoryText(model.min_memory_mb) }}</dd></div><div><dt>建议内存</dt><dd>{{ memoryText(model.recommended_memory_mb) }}</dd></div><div><dt>建议显存</dt><dd>{{ model.recommended_vram_mb ? memoryText(model.recommended_vram_mb) : '无需独显' }}</dd></div><div><dt>模型空间</dt><dd>{{ memoryText(model.disk_mb) }}</dd></div><div><dt>推荐量化</dt><dd>{{ model.quantization }}</dd></div><div><dt>建议配置</dt><dd>{{ model.effective_threads }} 线程 · {{ model.effective_context_tokens }} 上下文</dd></div></dl>
                      <footer><a-button size="mini" type="text" :href="model.hosted_url || (model.delivery === 'partyops_pack' ? 'https://www.partyops.cn/models' : model.official_url)" target="_blank" rel="noopener noreferrer">{{ model.delivery === 'partyops_pack' && model.hosted_url ? '下载官网模型包' : model.delivery === 'partyops_pack' ? '查看官网导入说明' : '打开官方模型页' }}</a-button><span>{{ model.delivery === 'official' ? '大模型不由官网转存，通过本机 OpenAI 兼容服务接入' : model.hosted_url ? '签名模型包可直接导入' : '未冻结签名包前只使用官方来源' }}</span></footer>
                    </article>
                  </div>
                </details>
              </template>
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
                <a-table-column title="组件"><template #cell="{ record }">{{ record.capabilities.map((item: string) => item === 'embedding' ? '中文向量' : item === 'intent_router' ? '受控意图' : '本地 LLM').join('、') }}</template></a-table-column>
                <a-table-column title="架构" data-index="architecture" :width="100" />
                <a-table-column title="签名" :width="100"><template #cell="{ record }">{{ record.signature_valid ? "已验证" : "开发包" }}</template></a-table-column>
                <a-table-column title="资源" :width="120"><template #cell="{ record }">{{ record.estimated_memory_mb ? `${(record.estimated_memory_mb / 1024).toFixed(1)}GB` : '按运行时判断' }}</template></a-table-column>
                <a-table-column title="操作" :width="360"><template #cell="{ record }"><a-space wrap><a-button v-if="record.capabilities.includes('embedding')" size="mini" :type="record.active_capabilities.includes('embedding') ? 'outline' : 'primary'" @click="record.active_capabilities.includes('embedding') ? deactivateModelCapability('embedding') : activateModelPack(record, 'embedding')">{{ record.active_capabilities.includes('embedding') ? '停用向量' : '启用向量' }}</a-button><a-button v-if="record.capabilities.includes('llm')" size="mini" :type="record.active_capabilities.includes('llm') ? 'outline' : 'primary'" @click="record.active_capabilities.includes('llm') ? deactivateModelCapability('llm') : activateModelPack(record, 'llm')">{{ record.active_capabilities.includes('llm') ? '停用 LLM' : '启用 LLM' }}</a-button><a-button v-if="record.capabilities.includes('intent_router')" size="mini" :type="record.active_capabilities.includes('intent_router') ? 'outline' : 'primary'" @click="record.active_capabilities.includes('intent_router') ? deactivateModelCapability('intent_router') : activateModelPack(record, 'intent_router')">{{ record.active_capabilities.includes('intent_router') ? '停用意图' : '启用意图' }}</a-button><a-popconfirm v-if="!record.active_capabilities.length" content="卸载只删除本机模型文件，不影响业务数据和历史审计。确认继续？" @ok="uninstallModelPack(record)"><a-button size="mini" type="text" status="danger">卸载</a-button></a-popconfirm></a-space></template></a-table-column>
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
            <a-table-column title="状态"><template #cell="{ record }">{{ record.archived_at ? "已归档" : record.active ? "启用" : "停用" }}</template></a-table-column>
            <a-table-column title="操作" :width="260"><template #cell="{ record }"><a-space><a-button v-if="!record.archived_at" size="mini" type="text" @click="openUserEdit(record)">编辑角色与权限</a-button><a-button v-if="record.archived_at" size="mini" type="text" @click="restoreUser(record)">恢复</a-button><a-button v-else-if="record.id !== session.user?.id" size="mini" type="text" status="danger" @click="openUserDelete(record)">删除/归档</a-button></a-space></template></a-table-column>
          </template>
        </a-table>
      </a-tab-pane>

      <a-tab-pane v-if="session.user?.role === 'admin'" key="backup" title="备份与灾备">
        <div class="tab-toolbar">
          <p>数据库、附件与配置打包校验；恢复前会自动再备份当前数据。</p>
          <a-space>
            <a-radio-group v-model="backupLifecycle" type="button" @change="loadBackups"><a-radio value="active">可用备份</a-radio><a-radio value="deleted">回收站</a-radio></a-radio-group>
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
                  <a-button v-if="!record.deleted_at" size="mini" :href="downloadUrl(`/backups/${record.id}/download`)" target="_blank"><template #icon><IconDownload /></template>下载</a-button>
                  <a-button v-if="!record.deleted_at" size="mini" @click="verifyBackup(record)">校验</a-button>
                  <a-popconfirm v-if="!record.deleted_at" content="恢复会替换当前数据；系统会先自动备份现状。确认继续？" @ok="restoreBackup(record)">
                    <a-button size="mini" status="danger">恢复数据</a-button>
                  </a-popconfirm>
                  <a-button v-if="!record.deleted_at" size="mini" type="text" status="danger" @click="openBackupDelete(record)">移入回收站</a-button>
                  <a-button v-else size="mini" type="text" @click="restoreDeletedBackup(record)">恢复备份</a-button>
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

    <a-modal v-model:visible="backupDeleteVisible" title="将备份移入回收站" ok-text="确认移除" @ok="deleteBackup">
      <a-alert type="warning">不会立即删除文件，{{ backupDeletionImpact?.recoverable_days || 30 }} 天内可恢复。系统禁止移除最后一个可用备份。</a-alert>
      <p v-if="backupDeletionImpact" class="impact-summary">文件大小 {{ (backupDeletionImpact.size_bytes / 1024 / 1024).toFixed(2) }} MB；移除后仍有 {{ backupDeletionImpact.remaining_completed_backups }} 个可用恢复点。</p>
      <a-form-item label="移除原因" required><a-textarea v-model="backupDeleteReason" /></a-form-item>
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

    <a-modal v-model:visible="userDeleteVisible" title="归档用户并移交责任" @ok="archiveUser">
      <a-alert type="warning">不会物理删除审计、会议或业务记录；会撤销登录会话和个人设备授权。</a-alert>
      <div v-if="deletingUser && deletionImpact" class="deletion-impact">
        <p><strong>{{ deletingUser.display_name }}</strong> 当前责任：事项负责人 {{ deletionImpact.counts.owned_tasks || 0 }}、审核 {{ deletionImpact.counts.review_tasks || 0 }}、步骤 {{ deletionImpact.counts.assigned_steps || 0 }}、参与记录 {{ deletionImpact.counts.participations || 0 }}。</p>
        <a-form-item v-if="deletionImpact.requires_transfer" label="责任接收人">
          <a-select v-model="transferToUserId">
            <a-option v-for="item in users.filter((candidate) => candidate.active && candidate.id !== deletingUser?.id)" :key="item.id" :value="item.id">{{ item.display_name }}</a-option>
          </a-select>
        </a-form-item>
      </div>
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

.online-update-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin: 0 0 16px;
  padding: 20px;
  border: 1px solid var(--line);
  background: rgba(251, 248, 241, 0.78);
}

.online-update-card.available {
  border-left: 4px solid var(--cinnabar);
}

.online-update-card small,
.online-update-card strong {
  display: block;
}

.online-update-card strong {
  margin: 6px 0;
  font-size: 18px;
}

.online-update-card p {
  margin: 0;
  color: var(--muted);
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

.role-reconfigure-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-top: 18px;
  padding: 18px 20px;
  border: 1px solid rgba(180, 35, 24, 0.26);
  border-left: 4px solid var(--cinnabar);
  background: rgba(251, 248, 241, 0.78);
}

.role-reconfigure-panel strong,
.role-reconfigure-panel p {
  display: block;
  margin: 0;
}

.role-reconfigure-panel p {
  margin-top: 5px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 720px) {
  .role-reconfigure-panel {
    align-items: stretch;
    flex-direction: column;
  }
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

.hardware-detector {
  margin: 18px 0;
  padding: 18px;
  background: rgba(238, 230, 217, 0.48);
  border: 1px solid var(--line);
  border-left: 3px solid var(--cinnabar);
}

.detector-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.detector-heading p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.7; }
.hardware-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; margin: 16px 0; background: var(--line); border: 1px solid var(--line); }
.hardware-summary > div { min-width: 0; padding: 12px; background: #f7f1e8; }
.hardware-summary span, .hardware-summary strong, .hardware-summary small { display: block; }
.hardware-summary span, .hardware-summary small { color: var(--muted); font-size: 10px; }
.hardware-summary strong { margin: 6px 0 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.model-recommendations { margin-top: 14px; }
.model-recommendations > summary { padding: 12px 0 4px; color: var(--cinnabar); font-size: 12px; cursor: pointer; }
.model-recommendation-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 10px; }
.model-recommendation-list article { padding: 14px; background: rgba(251, 248, 241, 0.82); border: 1px solid var(--line-light); }
.model-recommendation-list header, .model-recommendation-list footer { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.model-recommendation-list h4 { margin: 4px 0 0; font-size: 14px; }
.model-recommendation-list header span, .model-recommendation-list article > small, .model-recommendation-list footer span { color: var(--muted); font-size: 10px; }
.model-recommendation-list article > p { min-height: 40px; margin: 10px 0 5px; color: var(--charcoal); font-size: 11px; line-height: 1.7; }
.model-recommendation-list dl { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 12px; margin: 12px 0; padding: 9px 0; border-top: 1px solid var(--line-light); border-bottom: 1px solid var(--line-light); }
.model-recommendation-list dl div { display: flex; justify-content: space-between; gap: 8px; font-size: 10px; }
.model-recommendation-list dt { color: var(--muted); }
.model-recommendation-list dd { margin: 0; }
.model-recommendation-list footer { align-items: center; }

@media (max-width: 900px) {
  .detector-heading { flex-direction: column; }
  .hardware-summary, .model-recommendation-list { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 600px) {
  .hardware-summary, .model-recommendation-list { grid-template-columns: 1fr; }
  .model-recommendation-list article > p { min-height: 0; }
}

.ai-status {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 10px;
}

.policy-save {
  margin-top: 16px;
}

.policy-heading,
.policy-list article {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.policy-heading h3 { margin-bottom: 4px; }
.policy-heading p { margin: 0; color: var(--muted); font-size: 10px; }
.policy-list { display: grid; gap: 8px; margin-top: 18px; }
.policy-list article { align-items: center; padding: 12px; background: #fffaf0; border: 1px solid var(--line-light); }
.policy-list article.selected { border-color: rgba(180, 35, 24, 0.48); box-shadow: inset 3px 0 var(--cinnabar); }
.policy-list article.inactive { opacity: 0.64; }
.policy-list strong,
.policy-list small { display: block; }
.policy-list small { margin-top: 4px; color: var(--muted); font-size: 10px; }

.network-address-ledger {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 16px 0;
  background: var(--line);
  border: 1px solid var(--line);
}

.network-address-ledger article { min-width: 0; padding: 16px; background: rgba(255, 250, 240, 0.94); }
.network-address-ledger span, .network-address-ledger strong, .network-address-ledger small { display: block; }
.network-address-ledger span, .network-address-ledger small { color: var(--muted); font-size: 10px; }
.network-address-ledger strong { margin: 6px 0; overflow-wrap: anywhere; color: var(--charcoal); }
.network-device-table { margin-top: 20px; }

@media (max-width: 760px) {
  .network-address-ledger { grid-template-columns: 1fr; }
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
