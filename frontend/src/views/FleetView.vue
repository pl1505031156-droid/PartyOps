<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { IconCloudDownload, IconDelete, IconPlus, IconRefresh, IconSafe } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import { useSessionStore } from "../stores/session";
import type {
  ArchiveRecord,
  Device,
  DeviceGrant,
  DeviceUpdateGate,
  DeviceVersionStatus,
  Task,
  Transfer,
  User,
  WorkspaceFile,
  WorkspaceRoot,
} from "../types";
import { formatServerTime } from "../utils/datetime";
import { fetchFleetSnapshot } from "../utils/fleetData";
import { zhLabel } from "../utils/labels";
import PageHelp from "../components/PageHelp.vue";

type FleetSection = "devices" | "inbox" | "transfers" | "grants";
const props = withDefaults(defineProps<{ initialSection?: FleetSection }>(), {
  initialSection: "devices",
});
const session = useSessionStore();
const activeSection = ref<FleetSection>(props.initialSection);
const devices = ref<Device[]>([]);
const grants = ref<DeviceGrant[]>([]);
const transfers = ref<Transfer[]>([]);
const versionStatuses = ref<DeviceVersionStatus[]>([]);
const roots = ref<WorkspaceRoot[]>([]);
const remoteRoots = ref<Array<{
  id: string; name: string; device_id: string; remote_key: string; approval_status: string;
  approval_note: string; enabled: boolean; file_count: number; last_scan_at: string | null; version: number;
}>>([]);
const users = ref<User[]>([]);
const localGate = ref<DeviceUpdateGate | null>(null);
const sourceFiles = ref<WorkspaceFile[]>([]);
const materialTargets = ref<Array<{ id: string; label: string }>>([]);
const archiveTargets = ref<Array<{ id: string; label: string }>>([]);
const maxDevices = ref(20);
const enrollmentVisible = ref(false);
const deleteVisible = ref(false);
const deleteTarget = ref<Device | null>(null);
const enrollmentForm = reactive({ name: "协同电脑", advertised_host: "" });
const enrollment = ref<{ code: string; host_url: string; expires_at: string; ca_fingerprint: string } | null>(null);
const enrollmentStatus = ref<{
  status: "pending" | "enrolled" | "expired";
  device_id: string | null;
  device_name: string;
  device_status: string;
  last_seen_at: string | null;
} | null>(null);
const loading = ref(false);
const transferVisible = ref(false);
const attachVisible = ref(false);
const attachTransfer = ref<Transfer | null>(null);
const approvalNotes = reactive<Record<string, string>>({});
const grantForm = reactive({
  device_id: "",
  user_id: "",
  root_id: "",
  capabilities: ["download"] as string[],
});
const transferForm = reactive({
  direction: "device_to_host" as Transfer["direction"],
  source_root_id: "",
  source_file_id: "",
  destination_device_id: "",
  destination_root_id: "",
  require_approval: false,
});
const attachForm = reactive({
  target_type: "task_material" as "task_material" | "archive",
  target_id: "",
  note: "从协同文件接收箱转入",
  stage: "draft",
  is_final: false,
});
let refreshTimer: number | undefined;
let enrollmentTimer: number | undefined;
let lastWarning = "";
const isAdmin = computed(() => session.runtimeContext?.capabilities.includes("fleet.manage") === true);

const sectionMeta = computed(() => ({
  devices: {
    kicker: "多设备安全协同",
    title: isAdmin.value ? "设备协同" : "本机协同",
    description: isAdmin.value
      ? "最多纳管 20 台 UOS 或 Windows 电脑；设备状态、版本和访问开关统一由主机管理员管理。"
      : "查看本机连接状态、自己发布的共享目录和传输记录；全局设备配置由管理员统一维护。",
  },
  inbox: {
    kicker: "跨设备文件接收",
    title: "文件接收箱",
    description: "所有发往主机的文件都进入受管接收箱，校验、授权和审计完成后再使用。",
  },
  transfers: {
    kicker: "可审计文件流转",
    title: "传输任务",
    description: "主机、协同机以及协同机之间的文件传输统一排队、审批、校验和续传。",
  },
  grants: {
    kicker: isAdmin.value ? "设备与目录双重授权" : "我的共享权限",
    title: isAdmin.value ? "设备授权与状态" : "我的目录权限",
    description: isAdmin.value
      ? "按设备、用户和目录授予最小权限；撤销后在下一个文件分块立即停止。"
      : "查看当前账号在本机可以浏览、下载和转发的共享目录；权限变更会立即生效。",
  },
}[activeSection.value]));

const receivedTransfers = computed(() =>
  transfers.value.filter((item) => item.direction === "device_to_host"),
);
const approvedRoots = computed(() => roots.value.filter((item) => item.enabled && item.approval_status === "approved"));
const ownRoots = computed(() => roots.value.filter((item) => item.permissions.manage_root));
const sourceRoots = computed(() => approvedRoots.value.filter((item) => (
  transferForm.direction === "host_to_device"
    ? item.source === "host" && item.permissions.download
    : item.source === "device" && (transferForm.direction === "device_to_device" ? item.permissions.share : item.permissions.download)
)));
const destinationRoots = computed(() => approvedRoots.value.filter((item) => (
  item.source === "device"
  && item.permissions.upload
  && (!transferForm.destination_device_id || item.device_id === transferForm.destination_device_id)
)));
const enrollmentHostOptions = computed(() => {
  const bootstrap = session.bootstrap;
  if (!bootstrap) return [];
  const values = [...bootstrap.lan_candidates];
  if (
    bootstrap.host
    && !["127.0.0.1", "::1", "localhost", "0.0.0.0", "::"].includes(bootstrap.host)
  ) values.unshift(bootstrap.host);
  return [...new Set(values)];
});
const enrollmentStep = computed(() => {
  if (!enrollment.value) return 1;
  if (enrollmentStatus.value?.status !== "enrolled") return 2;
  return enrollmentStatus.value.device_status === "online" ? 4 : 3;
});

watch(
  () => props.initialSection,
  (value) => {
    activeSection.value = value;
  },
);

async function copyEnrollmentCode() {
  if (!enrollment.value) return;
  const text = enrollment.value.code;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      if (!copied) throw new Error("copy failed");
    }
    Message.success(`完整入网码已复制（${text.length} 个字符）`);
  } catch {
    Message.error("浏览器未允许复制，请三击下方完整入网码后复制");
  }
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  try {
    if (isAdmin.value) {
      const [snapshot, workspaceRoots, pendingRoots, userItems] = await Promise.all([
        fetchFleetSnapshot((path) => api.get(path)),
        api.get<WorkspaceRoot[]>("/workspace/roots"),
        api.get<typeof remoteRoots.value>("/admin/workspace/remote-roots"),
        api.get<User[]>("/admin/users"),
      ]);
      devices.value = snapshot.devices;
      if (snapshot.config) maxDevices.value = snapshot.config.max_devices;
      if (snapshot.grants) grants.value = snapshot.grants;
      if (snapshot.transfers) transfers.value = snapshot.transfers;
      if (snapshot.versionStatuses) versionStatuses.value = snapshot.versionStatuses;
      roots.value = workspaceRoots;
      remoteRoots.value = pendingRoots;
      users.value = userItems;
      for (const item of pendingRoots) approvalNotes[item.id] = item.approval_note || "";
      const warning = snapshot.failedSections.join("、");
      if (warning && warning !== lastWarning) Message.warning(`${warning}暂未刷新，核心设备列表仍可正常使用`);
      lastWarning = warning;
    } else {
      const [ownTransfers, options, gate] = await Promise.all([
        api.get<Transfer[]>("/transfers"),
        api.get<{ current_device: Device | null; devices: Device[]; roots: WorkspaceRoot[] }>("/collaboration/options"),
        api.get<DeviceUpdateGate>("/device/update-gate"),
      ]);
      transfers.value = ownTransfers;
      devices.value = options.devices;
      roots.value = options.roots;
      localGate.value = gate;
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "设备中心加载失败");
  } finally {
    loading.value = false;
  }
}

async function approveRoot(root: typeof remoteRoots.value[number], status: "approved" | "rejected") {
  try {
    await api.patch(
      `/admin/workspace/remote-roots/${root.id}`,
      { approval_status: status, approval_note: approvalNotes[root.id] || "" },
      { "If-Match": String(root.version) },
    );
    Message.success(status === "approved" ? "共享目录已批准并启用" : "共享目录已拒绝并停用");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "共享目录审批失败");
  }
}

async function openLocalShareManager() {
  try {
    const result = await api.post<{ open_uri: string }>("/workspace/local-share-actions");
    window.location.href = result.open_uri;
    Message.success("正在打开本机共享文件夹工具");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "本机共享工具打开失败");
  }
}

async function createGrant() {
  if (!grantForm.device_id || !grantForm.root_id || !grantForm.capabilities.length) {
    Message.warning("请选择设备、共享目录和至少一项能力");
    return;
  }
  try {
    await api.post("/admin/device-grants", {
      device_id: grantForm.device_id,
      user_id: grantForm.user_id || null,
      root_id: grantForm.root_id,
      capabilities: grantForm.capabilities,
    });
    Object.assign(grantForm, { user_id: "", root_id: "", capabilities: ["download"] });
    Message.success("设备目录授权已建立");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "设备授权创建失败");
  }
}

async function toggleGrant(grant: DeviceGrant) {
  try {
    const updated = await api.patch<DeviceGrant>(
      `/admin/device-grants/${grant.id}?active=${!grant.active}`,
      {},
      { "If-Match": String(grant.version) },
    );
    Object.assign(grant, updated);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "授权状态更新失败");
    await load();
  }
}

async function loadSourceFiles(rootId: string) {
  transferForm.source_file_id = "";
  if (!rootId) {
    sourceFiles.value = [];
    return;
  }
  sourceFiles.value = (await api.get<WorkspaceFile[]>(
    `/workspace/search?root_id=${rootId}&keyword=&limit=500`,
  )).filter((item) => !item.is_directory);
}

function resetTransferSource() {
  transferForm.source_root_id = "";
  transferForm.source_file_id = "";
  transferForm.destination_device_id = "";
  transferForm.destination_root_id = "";
  sourceFiles.value = [];
}

function handleSourceRootChange(value: unknown) {
  void loadSourceFiles(String(value || ""));
}

function openTransfer() {
  const firstRoot = sourceRoots.value[0];
  transferForm.source_root_id = firstRoot?.id || "";
  transferForm.source_file_id = "";
  transferVisible.value = true;
  void loadSourceFiles(transferForm.source_root_id);
}

async function createTransfer() {
  const file = sourceFiles.value.find((item) => item.id === transferForm.source_file_id);
  if (!file) {
    Message.warning("请选择需要传输的文件");
    return;
  }
  if (transferForm.direction !== "device_to_host" && !transferForm.destination_device_id) {
    Message.warning("请选择目标协同电脑");
    return;
  }
  try {
    await api.post<Transfer>("/transfers", {
      direction: transferForm.direction,
      source_file_id: file.id,
      source_device_id: file.device_id,
      destination_device_id: transferForm.destination_device_id || null,
      destination_root_id: transferForm.destination_root_id || null,
      original_name: file.name,
      relative_path: file.relative_path,
      size_bytes: file.size_bytes,
      sha256: file.sha256 || "",
      require_approval: transferForm.require_approval,
    });
    transferVisible.value = false;
    Message.success("传输任务已创建");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "传输创建失败");
  }
}

async function freezeTransfer(item: Transfer) {
  try {
    const updated = await api.post<Transfer>(`/transfers/${item.id}/freeze`);
    Object.assign(item, updated);
    Message.success("文件已固化到受管附件库");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件固化失败");
  }
}

async function openAttach(item: Transfer) {
  attachTransfer.value = item;
  attachForm.target_id = "";
  const tasks = await api.get<{ items: Task[] }>("/tasks?page_size=100");
  materialTargets.value = tasks.items.flatMap((task) => task.materials.map((material) => ({
    id: material.id,
    label: `${task.title} · ${material.name}`,
  })));
  try {
    const yearSummary = await api.get<{ years: Array<{ year: number }> }>("/archives/years");
    const years = yearSummary.years.slice(0, 20);
    const records = await Promise.all(
      years.map((item) => api.get<ArchiveRecord[]>(`/archives/records?archive_year=${item.year}&limit=500`)),
    );
    archiveTargets.value = records.flat().map((record) => ({ id: record.id, label: `${record.archive_year} · ${record.title}` }));
  } catch {
    archiveTargets.value = [];
  }
  attachVisible.value = true;
}

async function attachReceivedFile() {
  if (!attachTransfer.value || !attachForm.target_id) {
    Message.warning("请选择目标档案或任务材料");
    return;
  }
  try {
    const updated = await api.post<Transfer>(`/transfers/${attachTransfer.value.id}/attach`, attachForm);
    Object.assign(attachTransfer.value, updated);
    attachVisible.value = false;
    Message.success(attachForm.target_type === "archive" ? "已转为档案扫描件" : "已转为任务材料");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "接收文件转换失败");
  }
}

function versionStatus(deviceId: string): DeviceVersionStatus | undefined {
  return versionStatuses.value.find((item) => item.device_id === deviceId);
}

function versionStateLabel(state: string): string {
  return {
    current: "与主机一致",
    outdated: "等待更新",
    updating: "更新中",
    unknown: "尚未上报",
    revoked: "已撤销",
    quarantined: "已隔离",
  }[state] || "待检查";
}

async function createEnrollment() {
  if (!enrollmentForm.name.trim()) {
    Message.warning("请填写便于识别的协同电脑名称");
    return;
  }
  if (!enrollmentForm.advertised_host) {
    Message.warning("请选择协同电脑能够访问的主机局域网地址");
    return;
  }
  try {
    const result = await api.post<{ id: string; code: string; host_url: string; expires_at: string; ca_fingerprint: string }>("/admin/devices/enrollments", {
      name: enrollmentForm.name.trim(),
      advertised_host: enrollmentForm.advertised_host,
    });
    enrollment.value = result;
    enrollmentStatus.value = { status: "pending", device_id: null, device_name: "", device_status: "", last_seen_at: null };
    startEnrollmentWatch(result.id);
    Message.success("入网码已生成，10 分钟内有效");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "生成入网码失败");
  }
}

async function openEnrollment() {
  enrollment.value = null;
  enrollmentStatus.value = null;
  stopEnrollmentWatch();
  try {
    const bootstrap = await session.loadBootstrap();
    const candidates = [...new Set([
      ...bootstrap.lan_candidates,
      ...(!["127.0.0.1", "::1", "localhost", "0.0.0.0", "::"].includes(bootstrap.host) ? [bootstrap.host] : []),
    ])];
    enrollmentForm.advertised_host = candidates.includes(bootstrap.host)
      ? bootstrap.host
      : candidates.length === 1 ? candidates[0] : "";
    enrollmentVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "无法读取主机网络信息");
  }
}

function stopEnrollmentWatch() {
  if (enrollmentTimer !== undefined) window.clearInterval(enrollmentTimer);
  enrollmentTimer = undefined;
}

async function checkEnrollmentStatus(enrollmentId: string) {
  try {
    const previous = enrollmentStatus.value?.status;
    const result = await api.get<typeof enrollmentStatus.value>(`/admin/devices/enrollments/${enrollmentId}/status`);
    enrollmentStatus.value = result;
    if (result?.status === "enrolled" && previous !== "enrolled") {
      Message.success(`“${result.device_name || "协同电脑"}”已完成安全入网`);
      await load();
    }
    if (result?.status === "expired") stopEnrollmentWatch();
  } catch {
    // 主机重启或短暂断线时继续轮询，入网码到期前无需用户重新操作。
  }
}

function startEnrollmentWatch(enrollmentId: string) {
  stopEnrollmentWatch();
  void checkEnrollmentStatus(enrollmentId);
  enrollmentTimer = window.setInterval(() => void checkEnrollmentStatus(enrollmentId), 2_000);
}

async function saveDevice(device: Device, field: "active" | "allow_host_access" | "allow_device_transfer" | "allow_user_shares", value: boolean) {
  try {
    const updated = await api.patch<Device>(`/admin/devices/${device.id}`, { [field]: value }, { "If-Match": String(device.version) });
    Object.assign(device, updated);
    Message.success("设备权限已更新");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "设备权限更新失败");
    await load();
  }
}

async function saveMaxDevices() {
  try {
    await api.patch(`/admin/devices/config?max_devices=${maxDevices.value}`, {});
    Message.success("设备上限已保存");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "设备上限保存失败");
  }
}

async function rotateCertificate(device: Device) {
  try {
    await api.post(`/admin/devices/${device.id}/rotate-certificate`, {});
    Message.success(`已通知“${device.name}”轮换设备证书；终端上线后自动完成。`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "证书轮换命令创建失败");
  }
}

function requestDeleteDevice(device: Device) {
  deleteTarget.value = device;
  deleteVisible.value = true;
}

async function deleteDevice() {
  const device = deleteTarget.value;
  if (!device) return;
  try {
    await api.delete(`/admin/devices/${device.id}`, {
      "If-Match": String(device.version),
    });
    Message.success(`“${device.name}”已从纳管设备中删除，历史审计仍保留`);
    deleteVisible.value = false;
    deleteTarget.value = null;
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "设备删除失败");
    await load();
  }
}

function switchValue(value: string | number | boolean): boolean {
  return value === true || value === "true" || value === 1;
}

function capabilityLabels(capabilities: string[]): string {
  return capabilities.map((item) => zhLabel(item, item)).join("、") || "无";
}

function transferDirectionLabel(item: Transfer): string {
  if (["browser", "browser_direct"].includes(item.delivery_mode)) {
    return item.source_device_id ? "协同机 → 浏览器" : "主机 → 浏览器";
  }
  if (item.delivery_mode === "current_device") {
    return item.source_device_id ? "协同机 → 本机接收" : "主机 → 本机接收";
  }
  return item.direction === "device_to_host"
    ? "终端 → 主机"
    : item.direction === "host_to_device"
      ? "主机 → 终端"
      : "终端 → 终端";
}

function transferProgress(item: Transfer): number {
  if (item.status === "completed") return 100;
  return item.total_chunks
    ? Math.round(item.completed_chunks / item.total_chunks * 100)
    : 0;
}

async function transferAction(item: Transfer, action: string) {
  try {
    const updated = await api.patch<Transfer>(`/transfers/${item.id}`, { action }, { "If-Match": String(item.version) });
    Object.assign(item, updated);
    Message.success(action === "approve" ? "传输已批准" : "传输状态已更新");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "传输操作失败");
  }
}

onMounted(() => {
  load();
  window.addEventListener("partyops:refresh", load);
  refreshTimer = window.setInterval(load, 10_000);
});
onBeforeUnmount(() => {
  window.removeEventListener("partyops:refresh", load);
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer);
  stopEnrollmentWatch();
});
</script>

<template>
  <div class="page fleet-page">
    <header class="page-header">
      <div>
        <p class="date-kicker">{{ sectionMeta.kicker }}</p>
        <h1 class="page-title">{{ sectionMeta.title }}</h1>
        <p class="page-description">{{ sectionMeta.description }}</p>
      </div>
      <a-space>
        <PageHelp
          :title="`${sectionMeta.title}怎么用`"
          :tips="['设备必须先完成安全入网和版本校验。', '文件访问同时校验登录用户、设备和目录授权。', '跨设备传输始终经过主机中转并记录审计。']"
          help-query="设备协同"
        />
        <a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新状态</a-button>
        <a-button v-if="session.runtimeContext?.capabilities.includes('workspace.local_share') && activeSection === 'devices'" type="primary" @click="openLocalShareManager"><template #icon><IconPlus /></template>共享本机文件夹</a-button>
        <a-button v-if="isAdmin && activeSection === 'devices'" type="primary" @click="openEnrollment"><template #icon><IconPlus /></template>新增协同电脑</a-button>
        <a-button v-if="activeSection === 'transfers'" type="primary" @click="openTransfer"><template #icon><IconPlus /></template>新建传输</a-button>
      </a-space>
    </header>

    <section v-if="isAdmin && activeSection === 'devices'" class="fleet-summary">
      <article><span>设备上限</span><strong>{{ maxDevices }}</strong><small>当前 {{ devices.length }} 台</small></article>
      <article><span>在线设备</span><strong>{{ devices.filter((item) => item.status === "online").length }}</strong><small>45 秒无心跳自动离线</small></article>
      <article><span>版本一致</span><strong>{{ versionStatuses.filter((item) => item.version_state === "current").length }}</strong><small>{{ versionStatuses.filter((item) => item.version_state !== "current").length }} 台待处理</small></article>
      <article><span>授权共享根</span><strong>{{ grants.filter((item) => item.root_id).length }}</strong><small>目录权限单独控制</small></article>
      <article class="safe-card"><IconSafe /><div><strong>默认拒绝</strong><small>设备和登录用户双重授权</small></div></article>
    </section>

    <section v-if="isAdmin && activeSection === 'devices'" class="panel">
      <div class="panel-heading"><div><h2>纳管设备</h2><p>可暂停、撤销和重新授权；不会远程控制桌面或执行命令。</p></div><a-space><a-input-number v-model="maxDevices" :min="1" :max="20" /><a-button @click="saveMaxDevices">保存上限</a-button></a-space></div>
      <a-table :data="devices" row-key="id" :pagination="{ pageSize: 10 }">
        <template #columns>
          <a-table-column title="设备">
            <template #cell="{ record }"><strong>{{ record.name }}</strong><small class="subline">{{ record.architecture }} · {{ record.local_username || "未上报账号" }}</small></template>
          </a-table-column>
          <a-table-column title="状态">
            <template #cell="{ record }"><span class="status-pill" :class="record.status">{{ zhLabel(record.status) }}</span><small class="subline">{{ formatServerTime(record.last_seen_at, "MM-DD HH:mm:ss", "尚无心跳") }}</small></template>
          </a-table-column>
          <a-table-column title="版本与空间">
            <template #cell="{ record }">
              <span
                class="version-pill"
                :class="versionStatus(record.id)?.version_state || 'unknown'"
              >
                {{ versionStateLabel(versionStatus(record.id)?.version_state || "unknown") }}
              </span>
              <small class="subline">
                本机 {{ record.app_version || "未上报" }} · 主机 {{ versionStatus(record.id)?.target_version || "—" }}
              </small>
              <small class="subline">{{ (record.disk_free_bytes / 1024 / 1024 / 1024).toFixed(1) }} GB 可用</small>
              <small v-if="versionStatus(record.id)?.update_message" class="subline">{{ versionStatus(record.id)?.update_message }}</small>
            </template>
          </a-table-column>
          <a-table-column title="主机访问"><template #cell="{ record }"><a-switch :model-value="record.allow_host_access" @change="(value) => saveDevice(record, 'allow_host_access', switchValue(value))" /></template></a-table-column>
          <a-table-column title="设备间传输"><template #cell="{ record }"><a-switch :model-value="record.allow_device_transfer" @change="(value) => saveDevice(record, 'allow_device_transfer', switchValue(value))" /></template></a-table-column>
          <a-table-column title="普通用户发布"><template #cell="{ record }"><a-switch :model-value="record.allow_user_shares" @change="(value) => saveDevice(record, 'allow_user_shares', switchValue(value))" /></template></a-table-column>
          <a-table-column title="设备启用"><template #cell="{ record }"><a-switch :model-value="record.active" @change="(value) => saveDevice(record, 'active', switchValue(value))" /></template></a-table-column>
          <a-table-column title="安全操作">
            <template #cell="{ record }">
              <a-space>
                <a-button size="mini" :disabled="!record.active" @click="rotateCertificate(record)">轮换证书</a-button>
                <a-button size="mini" status="danger" aria-label="删除设备" @click="requestDeleteDevice(record)">
                  <template #icon><IconDelete /></template>删除
                </a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </section>

    <section v-if="!isAdmin && activeSection === 'devices'" class="staff-collaboration-grid">
      <article class="panel"><h2>本机协同状态</h2><strong>{{ localGate?.identified ? localGate.device_name : "当前浏览器未绑定协同设备" }}</strong><p>{{ localGate?.identified ? `版本 ${localGate.current_version || '未上报'} · ${localGate.message || '连接正常'}` : "请从本机 PartyOps 桌面入口进入，系统会自动绑定设备上下文。" }}</p></article>
      <article class="panel"><h2>我发布的共享目录</h2><strong>{{ ownRoots.length }} 个</strong><p>{{ ownRoots.length ? ownRoots.map((item) => item.name).join("、") : "尚未发布本机目录，可点击上方“共享本机文件夹”。" }}</p><a-button v-if="session.runtimeContext?.capabilities.includes('workspace.local_share')" size="small" type="primary" @click="openLocalShareManager">添加、重命名或移除</a-button></article>
      <article class="panel"><h2>我的传输</h2><strong>{{ transfers.length }} 项</strong><p>{{ transfers.filter((item) => ["queued", "transferring", "awaiting_approval"].includes(item.status)).length }} 项正在等待或传输中。</p></article>
      <article class="panel"><h2>权限说明</h2><p>共享文件操作同时校验当前账号、协同设备和已批准目录。没有权限的按钮会被隐藏，授权撤销后传输会立即停止。</p></article>
    </section>

    <section v-if="activeSection === 'inbox'" class="panel">
      <div class="panel-heading">
        <div>
          <h2>主机文件接收箱</h2>
          <p>文件不会覆盖同名原件；完成 SHA-256 校验后才允许打开或关联事项。</p>
        </div>
      </div>
      <a-table :data="receivedTransfers" row-key="id" :pagination="{ pageSize: 10 }">
        <template #columns>
          <a-table-column title="接收文件" data-index="original_name" />
          <a-table-column title="来源设备">
            <template #cell="{ record }">{{ devices.find((item) => item.id === record.source_device_id)?.name || "协同电脑" }}</template>
          </a-table-column>
          <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.status) }}</template></a-table-column>
          <a-table-column title="大小"><template #cell="{ record }">{{ (record.size_bytes / 1024 / 1024).toFixed(2) }} MB</template></a-table-column>
          <a-table-column title="接收时间"><template #cell="{ record }">{{ formatServerTime(record.updated_at, "MM-DD HH:mm:ss") }}</template></a-table-column>
          <a-table-column title="处理状态"><template #cell="{ record }">{{ record.handled_at ? `${zhLabel(record.linked_entity_type, '已处理')} · ${formatServerTime(record.handled_at, 'MM-DD HH:mm')}` : "待处理" }}</template></a-table-column>
          <a-table-column title="操作" :width="250">
            <template #cell="{ record }">
              <a-space v-if="record.status === 'completed'">
                <a-button size="mini" :href="downloadUrl(`/transfers/${record.id}/content?inline=true`)" target="_blank">预览</a-button>
                <a-button size="mini" :href="downloadUrl(`/transfers/${record.id}/content`)">下载</a-button>
                <a-button size="mini" @click="freezeTransfer(record)">固化</a-button>
                <a-button size="mini" type="primary" @click="openAttach(record)">转入业务</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
      <p v-if="!receivedTransfers.length" class="muted">当前没有协同电脑发送到主机的文件。</p>
    </section>

    <section v-if="activeSection === 'transfers'" class="panel">
      <div class="panel-heading"><div><h2>{{ isAdmin ? "传输审批与队列" : "我的传输任务" }}</h2><p>终端之间始终经过主机中转；撤销授权后下一分块立即停止。</p></div></div>
      <a-table :data="transfers" row-key="id" :pagination="{ pageSize: 8 }">
        <template #columns>
          <a-table-column title="文件" data-index="original_name" />
          <a-table-column title="方向"><template #cell="{ record }">{{ transferDirectionLabel(record) }}</template></a-table-column>
          <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.status) }}</template></a-table-column>
          <a-table-column title="进度"><template #cell="{ record }">{{ transferProgress(record) }}%</template></a-table-column>
          <a-table-column title="操作"><template #cell="{ record }"><a-space v-if="isAdmin && record.status === 'awaiting_approval'"><a-button size="mini" type="primary" @click="transferAction(record, 'approve')">批准</a-button><a-button size="mini" status="danger" @click="transferAction(record, 'cancel')">拒绝</a-button></a-space><a-button v-else-if="['queued','transferring'].includes(record.status)" size="mini" @click="transferAction(record, 'pause')">暂停</a-button><a-button v-else-if="record.status === 'paused'" size="mini" @click="transferAction(record, 'resume')">继续</a-button></template></a-table-column>
        </template>
      </a-table>
      <p v-if="!transfers.length" class="muted">当前没有传输记录。</p>
    </section>

    <section v-if="isAdmin && activeSection === 'grants'" class="panel remote-root-panel">
      <div class="panel-heading"><div><h2>协同电脑共享目录审批</h2><p>只有批准并启用的共享根才能被浏览、搜索、传输或关联业务对象。</p></div></div>
      <a-table :data="remoteRoots" row-key="id" :pagination="{ pageSize: 8 }">
        <template #columns>
          <a-table-column title="共享目录"><template #cell="{ record }"><strong>{{ record.name }}</strong><small class="subline">{{ devices.find((item) => item.id === record.device_id)?.name || '协同电脑' }} · {{ record.file_count }} 个文件</small></template></a-table-column>
          <a-table-column title="状态"><template #cell="{ record }">{{ zhLabel(record.approval_status) }}</template></a-table-column>
          <a-table-column title="审批说明"><template #cell="{ record }"><a-input v-model="approvalNotes[record.id]" allow-clear placeholder="填写批准范围或拒绝原因" /></template></a-table-column>
          <a-table-column title="操作" :width="150"><template #cell="{ record }"><a-space><a-button size="mini" type="primary" @click="approveRoot(record, 'approved')">批准</a-button><a-button size="mini" status="danger" @click="approveRoot(record, 'rejected')">拒绝</a-button></a-space></template></a-table-column>
        </template>
      </a-table>
    </section>

    <section v-if="isAdmin && activeSection === 'grants'" class="panel">
      <div class="panel-heading">
        <div>
          <h2>目录授权</h2>
          <p>授权以设备、用户和文件根目录为最小组合；默认不允许浏览、下载或转发。</p>
        </div>
      </div>
      <div class="grant-form">
        <a-select v-model="grantForm.device_id" allow-search placeholder="选择设备"><a-option v-for="device in devices.filter((item) => item.active)" :key="device.id" :value="device.id">{{ device.name }}</a-option></a-select>
        <a-select v-model="grantForm.user_id" allow-clear allow-search placeholder="指定用户（留空则设备全员）"><a-option v-for="user in users.filter((item) => item.active)" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select>
        <a-select v-model="grantForm.root_id" allow-search placeholder="选择已批准目录"><a-option v-for="root in approvedRoots.filter((item) => !grantForm.device_id || item.source === 'host' || item.device_id === grantForm.device_id)" :key="root.id" :value="root.id">{{ root.name }}</a-option></a-select>
        <a-checkbox-group v-model="grantForm.capabilities"><a-checkbox value="download">下载</a-checkbox><a-checkbox value="share">分享</a-checkbox><a-checkbox value="upload">接收</a-checkbox></a-checkbox-group>
        <a-button type="primary" @click="createGrant"><template #icon><IconPlus /></template>建立授权</a-button>
      </div>
      <a-table :data="grants" row-key="id" :pagination="{ pageSize: 10 }">
        <template #columns>
          <a-table-column title="设备">
            <template #cell="{ record }">{{ devices.find((item) => item.id === record.device_id)?.name || "设备已撤销" }}</template>
          </a-table-column>
          <a-table-column title="授权用户">
            <template #cell="{ record }">{{ record.user_id ? `用户 ${record.user_id.slice(0, 8)}` : "设备上的已登录用户" }}</template>
          </a-table-column>
          <a-table-column title="目录范围">
            <template #cell="{ record }">{{ record.root_id ? `共享目录 ${record.root_id.slice(0, 8)}` : "仅设备状态" }}</template>
          </a-table-column>
          <a-table-column title="允许操作">
            <template #cell="{ record }">{{ capabilityLabels(record.capabilities) }}</template>
          </a-table-column>
          <a-table-column title="状态"><template #cell="{ record }">{{ record.active ? "有效" : "已停用" }}</template></a-table-column>
          <a-table-column title="更新时间"><template #cell="{ record }">{{ formatServerTime(record.updated_at, "MM-DD HH:mm:ss") }}</template></a-table-column>
          <a-table-column title="操作"><template #cell="{ record }"><a-button size="mini" :status="record.active ? 'danger' : 'normal'" @click="toggleGrant(record)">{{ record.active ? "停用" : "恢复" }}</a-button></template></a-table-column>
        </template>
      </a-table>
      <p v-if="!grants.length" class="muted">尚未建立设备目录授权。</p>
    </section>

    <section v-if="!isAdmin && activeSection === 'grants'" class="panel">
      <div class="panel-heading"><div><h2>我的目录权限</h2><p>以下目录已通过账号、设备和审批三重校验，可在文件中心执行授权范围内的操作。</p></div></div>
      <div class="permission-root-list"><article v-for="root in roots" :key="root.id"><b>{{ root.name }}</b><span>{{ Object.entries(root.permissions).filter(([, value]) => value).map(([key]) => zhLabel(key, key)).join('、') || '只读目录' }}</span><small>{{ root.approval_note || '管理员未附加说明' }}</small></article></div>
      <p v-if="!roots.length" class="muted">当前账号或本机尚无可用共享目录，请联系管理员审批并授权。</p>
    </section>

    <a-modal v-model:visible="enrollmentVisible" title="新增协同电脑" :width="720" :footer="false" @cancel="stopEnrollmentWatch">
      <a-alert type="info" class="enrollment-intro">
        <strong>请在主机和协同电脑都保持开机、连接同一办公局域网。</strong>
        <p>本向导会自动确认协同 Agent 是否真正入网；看到“连接完成”前请不要关闭窗口。</p>
      </a-alert>
      <a-steps :current="enrollmentStep" small class="enrollment-steps">
        <a-step title="确认主机地址" />
        <a-step title="协同机输入信息" />
        <a-step title="等待首次心跳" />
        <a-step title="连接完成" />
      </a-steps>
      <a-form v-if="!enrollment" :model="enrollmentForm" layout="vertical">
        <a-form-item field="name" label="这台协同电脑叫什么">
          <a-input v-model="enrollmentForm.name" placeholder="例如：组织委员电脑" />
          <template #extra>使用办公室或岗位名称，后续授权时更容易识别。</template>
        </a-form-item>
        <a-form-item field="advertised_host" label="协同电脑连接哪个主机地址" required>
          <a-select v-model="enrollmentForm.advertised_host" placeholder="请选择真实局域网地址">
            <a-option v-for="host in enrollmentHostOptions" :key="host" :value="host">{{ host }}</a-option>
          </a-select>
          <template #extra>不要填写 127.0.0.1；多网卡时请选择与协同电脑同一网段的地址。</template>
        </a-form-item>
        <a-alert v-if="!enrollmentHostOptions.length" type="error" class="enrollment-network-error">
          未检测到可共享的局域网地址。请先退出并重新运行“PartyOps 配置向导”，选择主机模式和真实网卡地址，再返回此处。
        </a-alert>
        <a-button type="primary" :disabled="!enrollmentForm.name.trim() || !enrollmentForm.advertised_host" @click="createEnrollment">地址已确认，生成 10 分钟入网码</a-button>
      </a-form>
      <a-alert v-if="enrollment" type="warning" class="enrollment-alert">
        <strong>现在转到协同电脑完成以下操作：</strong>
        <ol>
          <li>安装同版本 PartyOps，首次配置选择“协同机”。</li>
          <li>打开 PartyOps Agent，把下方主机地址和完整入网码粘贴进去。</li>
          <li>核对 CA 指纹后确认；本窗口会自动显示真实连接结果。</li>
        </ol>
        <p><strong>主机地址：</strong>{{ enrollment.host_url }}</p>
        <div class="enrollment-code-row">
          <strong>一次性入网码（{{ enrollment.code.length }} 个字符）：</strong>
          <code class="enrollment-code">{{ enrollment.code }}</code>
          <a-button size="small" type="primary" @click="copyEnrollmentCode">复制完整入网码</a-button>
        </div>
        <p><strong>主机 CA 指纹：</strong><code>{{ enrollment.ca_fingerprint }}</code></p>
        <p><strong>有效期：</strong>{{ formatServerTime(enrollment.expires_at, "YYYY-MM-DD HH:mm:ss") }}（北京时间）</p>
      </a-alert>
      <div v-if="enrollment" class="enrollment-result" :class="enrollmentStatus?.status || 'pending'">
        <strong v-if="enrollmentStatus?.status === 'enrolled'">{{ enrollmentStatus.device_status === 'online' ? '连接完成，可以开始协同' : '安全入网已完成，正在等待 Agent 首次心跳' }}</strong>
        <strong v-else-if="enrollmentStatus?.status === 'expired'">入网码已过期，请返回重新生成</strong>
        <strong v-else>正在等待协同电脑连接……</strong>
        <p v-if="enrollmentStatus?.status === 'enrolled'">设备：{{ enrollmentStatus.device_name }} · 状态：{{ zhLabel(enrollmentStatus.device_status) }}</p>
        <p v-else>无需手动刷新；系统每 2 秒确认一次真实入网状态。</p>
      </div>
    </a-modal>

    <a-modal
      v-model:visible="deleteVisible"
      title="删除纳管设备"
      ok-text="确认删除"
      :ok-button-props="{ status: 'danger' }"
      :mask-closable="false"
      @ok="deleteDevice"
    >
      <a-alert type="warning">
        确认删除“{{ deleteTarget?.name }}”？该设备令牌会立即失效，共享目录和授权会停用。
        历史传输与审计记录仍会保留；存在未完成传输时系统将拒绝删除。
      </a-alert>
    </a-modal>

    <a-modal v-model:visible="transferVisible" title="新建跨机器文件传输" :width="640" :mask-closable="false" @ok="createTransfer">
      <a-form :model="transferForm" layout="vertical">
        <a-form-item label="传输方向"><a-radio-group v-model="transferForm.direction" @change="resetTransferSource"><a-radio value="device_to_host">协同机 → 主机</a-radio><a-radio value="host_to_device">主机 → 协同机</a-radio><a-radio value="device_to_device">协同机 → 协同机</a-radio></a-radio-group></a-form-item>
        <a-form-item label="源共享目录"><a-select v-model="transferForm.source_root_id" allow-search @change="handleSourceRootChange"><a-option v-for="root in sourceRoots" :key="root.id" :value="root.id">{{ root.name }} · {{ root.source === 'host' ? '主机' : devices.find((item) => item.id === root.device_id)?.name || '协同机' }}</a-option></a-select></a-form-item>
        <a-form-item label="源文件"><a-select v-model="transferForm.source_file_id" allow-search placeholder="搜索并选择已索引文件"><a-option v-for="file in sourceFiles" :key="file.id" :value="file.id">{{ file.relative_path }}</a-option></a-select></a-form-item>
        <template v-if="transferForm.direction !== 'device_to_host'">
          <a-form-item label="目标协同电脑"><a-select v-model="transferForm.destination_device_id" allow-search><a-option v-for="device in devices.filter((item) => item.active)" :key="device.id" :value="device.id">{{ device.name }}</a-option></a-select></a-form-item>
          <a-form-item label="目标共享目录"><a-select v-model="transferForm.destination_root_id" allow-clear allow-search><a-option v-for="root in destinationRoots" :key="root.id" :value="root.id">{{ root.name }}</a-option></a-select></a-form-item>
        </template>
        <a-form-item label="审批"><a-switch v-model="transferForm.require_approval" /><span class="inline-note">设备间传输及高风险扩展名始终需要管理员批准。</span></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="attachVisible" title="将接收文件转入业务" :width="620" :mask-closable="false" @ok="attachReceivedFile">
      <a-form :model="attachForm" layout="vertical">
        <a-form-item label="处理方式"><a-radio-group v-model="attachForm.target_type" @change="attachForm.target_id = ''"><a-radio value="task_material">转为任务材料</a-radio><a-radio value="archive">转为档案扫描件</a-radio></a-radio-group></a-form-item>
        <a-form-item label="目标对象"><a-select v-model="attachForm.target_id" allow-search><a-option v-for="target in attachForm.target_type === 'archive' ? archiveTargets : materialTargets" :key="target.id" :value="target.id">{{ target.label }}</a-option></a-select></a-form-item>
        <template v-if="attachForm.target_type === 'task_material'"><a-form-item label="材料阶段"><a-select v-model="attachForm.stage"><a-option value="draft">初稿</a-option><a-option value="revision">修改稿</a-option><a-option value="leader_approved">领导审定稿</a-option><a-option value="submitted">实际报送稿</a-option></a-select></a-form-item><a-form-item label="最终版本"><a-switch v-model="attachForm.is_final" /></a-form-item></template>
        <a-form-item label="处理说明"><a-textarea v-model="attachForm.note" /></a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.fleet-page{max-width:1480px}.date-kicker{margin:0 0 10px;color:var(--cinnabar);font:13px Georgia,serif;letter-spacing:.08em}.fleet-summary{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;margin-bottom:28px;background:var(--line);border:1px solid var(--line)}.fleet-summary article{min-height:112px;padding:20px;background:rgba(251,248,241,.86)}.fleet-summary span,.fleet-summary strong,.fleet-summary small{display:block}.fleet-summary span{color:var(--muted);font-size:11px}.fleet-summary strong{margin:8px 0;font:30px Georgia,serif}.fleet-summary small{color:var(--muted);font-size:11px}.safe-card{display:flex;align-items:center;gap:12px;color:#f8efe4;background:var(--charcoal)!important}.safe-card svg{font-size:24px;color:#e8b1a6}.safe-card small{color:#c8beb2}.panel{margin-bottom:24px;padding:20px;background:rgba(251,248,241,.72);border:1px solid var(--line)}.panel-heading{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:16px}.panel-heading h2{margin:0;font-size:18px}.panel-heading p{margin:5px 0 0;color:var(--muted);font-size:12px}.subline{display:block;margin-top:3px;color:var(--muted);font-size:10px}.status-pill,.version-pill{display:inline-flex;padding:3px 7px;color:var(--muted);font-size:11px;background:#eee6d9;border-radius:12px}.status-pill.online,.version-pill.current{color:#21633b;background:#e4f1e7}.status-pill.revoked,.version-pill.outdated,.version-pill.failed{color:#8e2a20;background:#f3deda}.version-pill.updating{color:#7d5713;background:#f5e8c8}.muted{color:var(--muted);font-size:12px}.enrollment-intro p{margin:5px 0 0}.enrollment-steps{margin:24px 0}.enrollment-network-error{margin-bottom:16px}.enrollment-alert{margin-top:18px}.enrollment-alert ol{margin:10px 0 12px;padding-left:22px;line-height:1.8}.enrollment-alert p{margin:8px 0}.enrollment-alert code{padding:3px 6px;color:var(--charcoal);background:#eee6d9}.enrollment-code-row{display:grid;gap:8px;margin:10px 0}.enrollment-code{display:block;max-width:100%;white-space:normal;overflow-wrap:anywhere;word-break:break-all;user-select:all;line-height:1.7}.enrollment-code-row .arco-btn{justify-self:start}.enrollment-result{margin-top:14px;padding:15px 17px;border-left:3px solid #b8862e;background:#f7edcf}.enrollment-result.enrolled{border-left-color:#2e7b49;background:#e8f3ea}.enrollment-result.expired{border-left-color:var(--cinnabar);background:#f5e2de}.enrollment-result p{margin:6px 0 0;color:var(--muted)}
.fleet-page{width:100%;max-width:none}
.staff-collaboration-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.staff-collaboration-grid .panel{margin:0;min-height:150px}.staff-collaboration-grid h2{margin-top:0;font-size:16px}.staff-collaboration-grid strong{display:block;margin:16px 0 8px;color:var(--cinnabar);font:22px var(--serif)}.staff-collaboration-grid p{color:var(--muted);line-height:1.7}.remote-root-panel{margin-bottom:16px}.grant-form{display:grid;grid-template-columns:1fr 1.2fr 1.2fr 1.4fr auto;gap:10px;align-items:center;margin-bottom:16px;padding:12px;background:rgba(180,35,24,.045);border:1px solid var(--line-light)}.permission-root-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.permission-root-list article{padding:14px;border:1px solid var(--line-light);background:rgba(255,255,255,.28)}.permission-root-list b,.permission-root-list span,.permission-root-list small{display:block}.permission-root-list span{margin:7px 0;color:var(--cinnabar);font-size:11px}.permission-root-list small{color:var(--muted)}.inline-note{margin-left:10px;color:var(--muted);font-size:11px}
@media (max-width:1000px){.fleet-summary{grid-template-columns:repeat(2,1fr)}.panel-heading{align-items:flex-start;flex-direction:column;gap:12px}}
@media (max-width:900px){.staff-collaboration-grid,.permission-root-list{grid-template-columns:1fr}.grant-form{grid-template-columns:1fr}}
</style>
