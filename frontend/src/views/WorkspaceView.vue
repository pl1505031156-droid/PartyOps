<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import {
  IconDownload,
  IconDelete,
  IconFile,
  IconFolder,
  IconLink,
  IconPlus,
  IconRefresh,
  IconSafe,
  IconSearch,
} from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import PageHelp from "../components/PageHelp.vue";
import {
  MAX_STRUCTURED_PREVIEW_BYTES,
  PreviewReadError,
  isRawPreviewSupported,
  isStructuredPreviewSupported,
  previewErrorMessage,
  readResponseWithLimit,
  renderPreviewMarkdown,
  type DocumentPreviewResult,
  type PdfPreviewMetadata,
} from "../documentPreview";
import { useSessionStore } from "../stores/session";
import type {
  Task,
  Device,
  Transfer,
  User,
  WorkspaceFile,
  WorkspaceFolderOption,
  WorkspaceRoot,
  WorkspaceRootMember,
} from "../types";
import { formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const session = useSessionStore();
const roots = ref<WorkspaceRoot[]>([]);
const rootLifecycle = ref<"active" | "disabled">("active");
const files = ref<WorkspaceFile[]>([]);
const tasks = ref<Task[]>([]);
const selectedRootId = ref("");
const selectedFile = ref<WorkspaceFile | null>(null);
const checkedIds = ref<string[]>([]);
const pathStack = ref<WorkspaceFile[]>([]);
const keyword = ref("");
const loading = ref(false);
const scanning = ref(false);
const rootVisible = ref(false);
const rootDeletionVisible = ref(false);
const rootDeletionReason = ref("");
const rootDeletionImpact = ref<{
  indexed_files: number;
  business_links: number;
  sharing_members: number;
  active_transfers: number;
  original_files_changed: boolean;
} | null>(null);
const linkVisible = ref(false);
const selectionVisible = ref(false);
const folderOptions = ref<WorkspaceFolderOption[]>([]);
const selectedFolderPaths = ref<string[]>([]);
const folderKeyword = ref("");
const selectionSaving = ref(false);
const sendVisible = ref(false);
const sendDevices = ref<Device[]>([]);
const sendRoots = ref<WorkspaceRoot[]>([]);
const collaborationDevices = ref<Device[]>([]);
const sharingVisible = ref(false);
const sharingRoot = ref<WorkspaceRoot | null>(null);
const sharingMembers = ref<WorkspaceRootMember[]>([]);
const sharingUsers = ref<User[]>([]);
const sharingUserIds = ref<string[]>([]);
const sharingSaving = ref(false);
const sharingForm = reactive({ share_scope: "team" as "team" | "selected", semantic_content_enabled: false });
const downloadBusy = ref(false);
const previewVisible = ref(false);
const previewLoading = ref(false);
const previewTab = ref("reading");
const previewTitle = ref("");
const previewHtml = ref("");
const previewError = ref("");
const previewRawUrl = ref("");
const previewRawAvailable = ref(false);
const previewWarnings = ref<string[]>([]);
const previewEngine = ref("");
const previewFormat = ref("");
const previewPdf = ref<PdfPreviewMetadata | null>(null);
const sendForm = reactive({ destination_device_id: "", destination_root_id: "" });
const rootForm = reactive({
  name: "年度工作资料",
  absolute_path: "",
  selection_mode: "selected",
});
const linkTaskId = ref("");
const tagsText = ref("");
let scanMonitorTimer: number | undefined;
let previewWorker: Worker | undefined;
let previewAbortController: AbortController | undefined;
let previewTimeout: number | undefined;
let previewWorkerReject: ((reason?: unknown) => void) | undefined;
let openGrantTimer: number | undefined;

const selectedRoot = computed(() => roots.value.find((item) => item.id === selectedRootId.value));
const canShareLocalFolder = computed(() =>
  Boolean(session.runtimeContext?.capabilities.includes("workspace.local_share")),
);
const canManageHostFolder = computed(() =>
  Boolean(session.runtimeContext?.capabilities.includes("workspace.manage_host_roots")),
);
const parentId = computed(() => pathStack.value[pathStack.value.length - 1]?.id || null);
const filteredFolderOptions = computed(() => {
  const value = folderKeyword.value.trim().toLowerCase();
  if (!value) return folderOptions.value;
  return folderOptions.value.filter((item) =>
    item.name.toLowerCase().includes(value) || item.path.toLowerCase().includes(value),
  );
});

function formatSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function explainDirectoryOperations() {
  const mode = session.runtimeContext?.node_mode || "unknown";
  if (mode === "unknown") {
    Message.info("当前页面不是主机本机或已入网协同机。请在主机桌面或已入网协同机打开党建智办后操作目录。");
    return;
  }
  Message.info("当前账号或设备没有目录操作权限，请联系主机管理员开启本机共享或目录纳管能力。");
}

function statusLabel(status: string) {
  return zhLabel(status);
}

function pdfTypeLabel(pdfType: string) {
  return {
    TextBased: "文字型",
    Scanned: "扫描型",
    ImageBased: "图片型",
    Mixed: "混合型",
  }[pdfType] || "未知类型";
}

function rootStatusLabel(status: string) {
  return {
    pending: "等待扫描",
    running: "正在扫描",
    completed: "扫描完成",
    completed_with_errors: "扫描完成，部分文件仅登记属性",
    failed: "扫描未完成",
    indexed: "索引完成",
  }[status] || "未知状态";
}

async function loadRoots() {
  const path = rootLifecycle.value === "active"
    ? "/workspace/roots"
    : "/workspace/roots?lifecycle=disabled";
  roots.value = await api.get<WorkspaceRoot[]>(path);
  if (!roots.value.some((item) => item.id === selectedRootId.value)) {
    selectedRootId.value = roots.value[0]?.id || "";
  }
  const hasRunningScan = roots.value.some((item) =>
    ["pending", "running"].includes(item.scan_status),
  );
  if (hasRunningScan && scanMonitorTimer === undefined) {
    scanMonitorTimer = window.setTimeout(async () => {
      scanMonitorTimer = undefined;
      await loadRoots();
      if (selectedRootId.value) await loadFiles();
    }, 1500);
  }
}

async function loadFiles() {
  if (!selectedRootId.value || rootLifecycle.value === "disabled") {
    files.value = [];
    return;
  }
  loading.value = true;
  try {
    const query = new URLSearchParams({ root_id: selectedRootId.value });
    if (parentId.value) query.set("parent_id", parentId.value);
    files.value = await api.get<WorkspaceFile[]>(`/workspace/files?${query}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "目录加载失败");
  } finally {
    loading.value = false;
  }
}

async function load() {
  try {
    await loadRoots();
    await loadFiles();
    const taskResult = await api.get<{ items: Task[] }>("/tasks?page_size=100");
    tasks.value = taskResult.items;
    const options = await api.get<{ devices: Device[]; roots: WorkspaceRoot[] }>("/collaboration/options");
    collaborationDevices.value = options.devices;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件中心加载失败");
  }
}

async function changeRoot(rootId: string) {
  selectedRootId.value = rootId;
  pathStack.value = [];
  selectedFile.value = null;
  checkedIds.value = [];
  keyword.value = "";
  if (rootLifecycle.value === "active") await loadFiles();
}

async function changeRootLifecycle(value: string | number | boolean) {
  rootLifecycle.value = value === "disabled" ? "disabled" : "active";
  selectedRootId.value = "";
  pathStack.value = [];
  selectedFile.value = null;
  checkedIds.value = [];
  await loadRoots();
  await loadFiles();
}

async function openItem(item: WorkspaceFile) {
  if (item.is_directory) {
    pathStack.value.push(item);
    selectedFile.value = null;
    await loadFiles();
    return;
  }
  selectedFile.value = await api.get<WorkspaceFile>(`/workspace/files/${item.id}`);
  tagsText.value = selectedFile.value.tags.join("、");
}

async function goToLevel(index: number) {
  pathStack.value = index < 0 ? [] : pathStack.value.slice(0, index + 1);
  selectedFile.value = null;
  checkedIds.value = [];
  await loadFiles();
}

async function search() {
  if (!selectedRootId.value) return;
  if (!keyword.value.trim()) {
    await loadFiles();
    return;
  }
  loading.value = true;
  try {
    files.value = await api.get<WorkspaceFile[]>(
      `/workspace/search?root_id=${selectedRootId.value}&keyword=${encodeURIComponent(keyword.value)}`,
    );
    pathStack.value = [];
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件搜索失败，请稍后重试");
  } finally {
    loading.value = false;
  }
}

async function createRoot() {
  try {
    const created = await api.post<WorkspaceRoot>("/workspace/roots", rootForm);
    rootVisible.value = false;
    Object.assign(rootForm, {
      name: "年度工作资料",
      absolute_path: "",
      selection_mode: "selected",
    });
    Message.success("目录发现扫描已开始；完成后请选择需要接入系统的子文件夹");
    await loadRoots();
    await changeRoot(created.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "目录纳管失败");
  }
}

async function openRootDeletion() {
  if (!selectedRoot.value) return;
  try {
    rootDeletionImpact.value = await api.get(
      `/workspace/roots/${selectedRoot.value.id}/deletion-impact`,
    );
    rootDeletionReason.value = "";
    rootDeletionVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "目录移除影响读取失败");
  }
}

async function deleteRoot() {
  if (!selectedRoot.value) return;
  if (rootDeletionReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的移除原因");
    return;
  }
  try {
    await api.deleteBody(
      `/workspace/roots/${selectedRoot.value.id}`,
      { reason: rootDeletionReason.value.trim() },
      { "If-Match": String(selectedRoot.value.version) },
    );
    rootDeletionVisible.value = false;
    selectedRootId.value = "";
    Message.success("目录已停用，原文件未被改动，可在已停用目录中恢复");
    await loadRoots();
    await loadFiles();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "目录停用失败");
  }
}

async function restoreRoot() {
  if (!selectedRoot.value) return;
  try {
    await api.post(
      `/workspace/roots/${selectedRoot.value.id}/restore`,
      { reason: "管理员核对目录位置后恢复使用" },
      { "If-Match": String(selectedRoot.value.version) },
    );
    Message.success("目录已恢复，请执行增量扫描重新确认当前文件范围");
    selectedRootId.value = "";
    await loadRoots();
    await loadFiles();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "目录恢复失败");
  }
}

async function pollScan(jobId: string) {
  const jobs = await api.get<Array<{
    id: string;
    status: string;
    message: string;
    payload?: {
      content_indexed?: number;
      metadata_only?: number;
      pending_ocr?: number;
      content_failed?: number;
    };
  }>>("/admin/jobs?limit=20");
  const job = jobs.find((item) => item.id === jobId);
  if (!job || ["pending", "running"].includes(job.status)) {
    window.setTimeout(() => pollScan(jobId), 1500);
    return;
  }
  scanning.value = false;
  if (job.status === "completed") {
    Message.success(job.message || "文件目录和基础属性索引已完成");
    await loadRoots();
    await loadFiles();
    if (
      selectedRoot.value?.selection_mode === "selected"
      && !selectedRoot.value.included_paths.length
    ) {
      await openSelection();
    }
  } else {
    Message.error(job.message || "文件索引失败");
  }
}

async function openSelection() {
  if (!selectedRoot.value || selectedRoot.value.source === "device") return;
  try {
    folderOptions.value = await api.get<WorkspaceFolderOption[]>(
      `/workspace/roots/${selectedRoot.value.id}/folder-options`,
    );
    selectedFolderPaths.value = [...selectedRoot.value.included_paths];
    folderKeyword.value = "";
    selectionVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "接入范围读取失败");
  }
}

function folderChecked(path: string): boolean {
  return selectedFolderPaths.value.includes(path);
}

function toggleFolder(path: string, value: string | number | boolean) {
  const checked = value === true || value === "true" || value === 1;
  if (checked) {
    if (path === ".") {
      selectedFolderPaths.value = ["."];
      return;
    }
    selectedFolderPaths.value = [
      ...new Set(selectedFolderPaths.value.filter((item) => item !== ".").concat(path)),
    ];
  } else {
    selectedFolderPaths.value = selectedFolderPaths.value.filter((item) => item !== path);
  }
}

async function saveSelection() {
  if (!selectedRoot.value) return;
  if (!selectedFolderPaths.value.length) {
    Message.warning("请至少选择一个需要接入系统的文件夹");
    return;
  }
  selectionSaving.value = true;
  try {
    const job = await api.patch<{ id: string }>(
      `/workspace/roots/${selectedRoot.value.id}/selection`,
      {
        selection_mode: selectedFolderPaths.value.includes(".") ? "all" : "selected",
        included_paths: selectedFolderPaths.value,
      },
      { "If-Match": String(selectedRoot.value.version) },
    );
    selectionVisible.value = false;
    scanning.value = true;
    Message.success("接入范围已保存，系统正在读取所选文件夹目录");
    await loadRoots();
    pollScan(job.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "接入范围保存失败");
    await loadRoots();
  } finally {
    selectionSaving.value = false;
  }
}

async function scanRoot() {
  if (!selectedRoot.value) return;
  scanning.value = true;
  try {
    const job = await api.post<{ id: string }>(`/workspace/roots/${selectedRoot.value.id}/scan`);
    Message.info("目录扫描已在后台开始，不会读取文件正文");
    pollScan(job.id);
  } catch (error) {
    scanning.value = false;
    Message.error(error instanceof Error ? error.message : "扫描启动失败");
  }
}

async function saveTags() {
  if (!selectedFile.value) return;
  const tags = tagsText.value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
  try {
    selectedFile.value = await api.patch<WorkspaceFile>(
      `/workspace/files/${selectedFile.value.id}/tags`,
      { tags },
      { "If-Match": String(selectedFile.value.version) },
    );
    Message.success("文件标签已保存");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "标签保存失败");
  }
}

async function linkTask() {
  if (!selectedFile.value || !linkTaskId.value) return;
  try {
    selectedFile.value = await api.post<WorkspaceFile>(
      `/workspace/files/${selectedFile.value.id}/links`,
      { entity_type: "task", entity_id: linkTaskId.value, relation: "reference" },
      { "If-Match": String(selectedFile.value.version) },
    );
    linkVisible.value = false;
    linkTaskId.value = "";
    Message.success("文件与任务已经双向关联");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "关联失败");
  }
}

async function freezeFile() {
  if (!selectedFile.value) return;
  try {
    selectedFile.value = await api.post<WorkspaceFile>(
      `/workspace/files/${selectedFile.value.id}/freeze`,
      undefined,
      { "If-Match": String(selectedFile.value.version) },
    );
    Message.success("文件已按 SHA-256 固化，纳入系统备份");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "固化失败");
  }
}

async function waitForTransferCompletion(transferId: string, signal?: AbortSignal): Promise<Transfer> {
  for (let attempt = 0; attempt < 600; attempt += 1) {
    if (signal?.aborted) throw new DOMException("预览已取消", "AbortError");
    const transfer = (await api.get<Transfer[]>("/transfers")).find((item) => item.id === transferId);
    if (!transfer) throw new Error("未找到文件传输任务");
    if (transfer.status === "completed") return transfer;
    if (["failed", "cancelled", "expired"].includes(transfer.status)) {
      throw new Error(transfer.error_message || "文件准备失败");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error("文件仍在后台准备，请到传输任务页查看进度");
}

async function freezeSelectedFile() {
  if (!selectedFile.value) return;
  if (selectedRoot.value?.source !== "device") {
    await freezeFile();
    return;
  }
  downloadBusy.value = true;
  try {
    const result = await api.post<{ transfer_id: string; status: string }>("/workspace/downloads", {
      item_ids: [selectedFile.value.id],
      bundle_mode: "single",
      delivery: "browser",
    });
    if (result.status !== "completed") {
      Message.info("正在从共享电脑拉取并校验，完成后将自动固化");
      await waitForTransferCompletion(result.transfer_id);
    }
    await api.post<Transfer>(`/transfers/${result.transfer_id}/freeze`);
    Message.success("远端文件已校验并固化到主机受管附件库");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "远端文件固化失败");
  } finally {
    downloadBusy.value = false;
  }
}

function rootSourceLabel(root: WorkspaceRoot): string {
  if (root.source === "host") return "主机文件";
  if (root.device_id === session.runtimeContext?.device_id) return "本机共享";
  const device = collaborationDevices.value.find((item) => item.id === root.device_id);
  return `${device?.name || "协同电脑"}共享`;
}

function toggleChecked(itemId: string, value: string | number | boolean) {
  const checked = value === true || value === "true" || value === 1;
  checkedIds.value = checked
    ? [...new Set([...checkedIds.value, itemId])]
    : checkedIds.value.filter((id) => id !== itemId);
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

async function waitForBrowserTransfer(
  transferId: string,
  contentUrl: string,
  target: Window | null,
  preview: boolean,
) {
  await waitForTransferCompletion(transferId);
  const url = `${contentUrl}${preview ? "?inline=true" : ""}`;
  if (target && !target.closed) target.location.href = url;
  else window.open(url, "_blank", "noopener");
  Message.success(preview ? "文件已校验并打开预览" : "文件已校验，可另存为到本机");
}

function disposePreviewRuntime() {
  previewAbortController?.abort();
  previewAbortController = undefined;
  previewWorkerReject?.(new DOMException("预览已取消", "AbortError"));
  previewWorkerReject = undefined;
  previewWorker?.terminate();
  previewWorker = undefined;
  if (previewTimeout !== undefined) window.clearTimeout(previewTimeout);
  previewTimeout = undefined;
}

function closeDocumentPreview() {
  disposePreviewRuntime();
  previewVisible.value = false;
  previewLoading.value = false;
}

async function prepareDocumentPreviewContent(file: WorkspaceFile): Promise<{
  bytesUrl: string;
  rawUrl: string;
}> {
  if (selectedRoot.value?.source !== "device") {
    return {
      bytesUrl: downloadUrl(`/workspace/files/${file.id}/download`),
      rawUrl: downloadUrl(`/workspace/files/${file.id}/preview`),
    };
  }
  const result = await api.post<{
    transfer_id: string;
    status: string;
    content_url: string;
  }>("/workspace/downloads", {
    item_ids: [file.id],
    bundle_mode: "single",
    delivery: "browser",
  });
  if (!result.content_url) throw new Error("主机未返回预览内容地址");
  if (result.status !== "completed") {
    Message.info("正在从共享电脑按需拉取并校验，完成后自动打开阅读视图");
    await waitForTransferCompletion(result.transfer_id, previewAbortController?.signal);
  }
  return {
    bytesUrl: result.content_url,
    rawUrl: `${result.content_url}?inline=true`,
  };
}

async function readPreviewSource(url: string): Promise<ArrayBuffer> {
  const response = await fetch(url, {
    credentials: "include",
    signal: previewAbortController?.signal,
  });
  if (!response.ok) {
    let message = `预览内容读取失败（${response.status}）`;
    try {
      const problem = await response.json() as { detail?: string; title?: string };
      message = problem.detail || problem.title || message;
    } catch {
      // 非 JSON 错误保持状态码提示。
    }
    throw new PreviewReadError("network", message);
  }
  return readResponseWithLimit(response);
}

function runPreviewWorker(file: WorkspaceFile, buffer: ArrayBuffer): Promise<DocumentPreviewResult> {
  const requestId = crypto.randomUUID();
  const worker = new Worker(
    new URL("../workers/documentPreview.worker.ts", import.meta.url),
    { type: "module", name: "partyops-document-preview" },
  );
  previewWorker = worker;
  return new Promise((resolve, reject) => {
    previewWorkerReject = reject;
    const cleanup = () => {
      if (previewTimeout !== undefined) window.clearTimeout(previewTimeout);
      previewTimeout = undefined;
      worker.terminate();
      if (previewWorker === worker) previewWorker = undefined;
      previewWorkerReject = undefined;
    };
    worker.onmessage = (event: MessageEvent<DocumentPreviewResult>) => {
      if (event.data.requestId !== requestId) return;
      cleanup();
      resolve(event.data);
    };
    worker.onerror = (event) => {
      cleanup();
      reject(new Error(event.message || "文档解析 Worker 异常"));
    };
    previewTimeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("文档解析超过 60 秒，已停止以保护系统资源"));
    }, 60_000);
    worker.postMessage({
      requestId,
      name: file.name,
      mimeType: file.mime_type,
      buffer,
    }, [buffer]);
  });
}

async function openDocumentPreview() {
  const file = selectedFile.value;
  if (!file || file.is_directory || !file.permissions.download) return;
  disposePreviewRuntime();
  const controller = new AbortController();
  previewAbortController = controller;
  previewVisible.value = true;
  previewLoading.value = true;
  previewTab.value = "reading";
  previewTitle.value = file.name;
  previewHtml.value = "";
  previewError.value = "";
  previewWarnings.value = [];
  previewEngine.value = "";
  previewFormat.value = "";
  previewPdf.value = null;
  previewRawUrl.value = "";
  previewRawAvailable.value = isRawPreviewSupported(file.name, file.mime_type);

  const structured = isStructuredPreviewSupported(file.name, file.size_bytes);
  if (!structured && !previewRawAvailable.value) {
    previewError.value = file.size_bytes > MAX_STRUCTURED_PREVIEW_BYTES
      ? previewErrorMessage("previewTooLarge")
      : "该格式暂不支持浏览器结构化阅读，请使用本机 WPS、Office 等默认程序打开，或下载到本机。";
    previewLoading.value = false;
    return;
  }

  try {
    const source = await prepareDocumentPreviewContent(file);
    previewRawUrl.value = source.rawUrl;
    if (!structured) {
      previewTab.value = "raw";
      previewError.value = previewErrorMessage("previewTooLarge");
      return;
    }
    const buffer = await readPreviewSource(source.bytesUrl);
    const result = await runPreviewWorker(file, buffer);
    if (!result.ok) {
      previewError.value = previewErrorMessage(result.code);
      if (previewRawAvailable.value) previewTab.value = "raw";
      return;
    }
    previewHtml.value = renderPreviewMarkdown(result.markdown);
    previewWarnings.value = result.warnings;
    previewEngine.value = `${result.engine} ${result.engineVersion}`;
    previewFormat.value = result.format;
    previewPdf.value = result.pdf || null;
    if (!result.markdown.trim() && previewRawAvailable.value) previewTab.value = "raw";
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return;
    const code = error instanceof PreviewReadError ? error.code : "unknown";
    previewError.value = previewErrorMessage(code);
    if (previewRawAvailable.value && previewRawUrl.value) previewTab.value = "raw";
  } finally {
    if (previewAbortController === controller) {
      previewLoading.value = false;
      previewAbortController = undefined;
    }
  }
}

async function createDownload(
  itemIds: string[],
  delivery: "browser" | "current_device",
  preview = false,
) {
  if (!itemIds.length || downloadBusy.value) return;
  const selected = files.value.filter((item) => itemIds.includes(item.id));
  const bundleMode = selected.length === 1
    ? (selected[0].is_directory ? "folder_zip" : "single")
    : "selection_zip";
  const target = delivery === "browser" ? window.open("about:blank", "_blank") : null;
  downloadBusy.value = true;
  try {
    const result = await api.post<{
      transfer_id: string; status: string; delivery: string; content_url: string;
    }>("/workspace/downloads", {
      item_ids: itemIds,
      bundle_mode: bundleMode,
      delivery,
    });
    if (delivery === "current_device") {
      target?.close();
      Message.success("已加入本机接收目录队列，Agent 将自动断点续传");
      return;
    }
    if (!result.content_url) throw new Error("主机未返回下载地址");
    if (result.status === "completed") {
      const url = `${result.content_url}${preview ? "?inline=true" : ""}`;
      if (target && !target.closed) target.location.href = url;
      else window.open(url, "_blank", "noopener");
    } else {
      if (target) target.document.title = "PartyOps 正在准备文件";
      Message.info("正在从共享电脑安全拉取并校验文件，请稍候");
      await waitForBrowserTransfer(result.transfer_id, result.content_url, target, preview);
    }
  } catch (error) {
    target?.close();
    Message.error(error instanceof Error ? error.message : "文件下载创建失败");
  } finally {
    downloadBusy.value = false;
  }
}

async function previewSelectedFile() {
  await openDocumentPreview();
}

async function openSharing(root: WorkspaceRoot) {
  sharingRoot.value = root;
  Object.assign(sharingForm, {
    share_scope: root.share_scope,
    semantic_content_enabled: root.semantic_content_enabled,
  });
  try {
    const [members, users] = await Promise.all([
      api.get<WorkspaceRootMember[]>(`/workspace/roots/${root.id}/members`),
      api.get<User[]>("/collaboration/users"),
    ]);
    sharingMembers.value = members;
    sharingUsers.value = users.filter((item) => item.id !== session.user?.id);
    sharingUserIds.value = members.map((item) => item.user_id);
    sharingVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "共享设置读取失败");
  }
}

async function saveSharing() {
  const root = sharingRoot.value;
  if (!root) return;
  sharingSaving.value = true;
  try {
    let updated = await api.patch<WorkspaceRoot>(
      `/workspace/roots/${root.id}/sharing`,
      sharingForm,
      { "If-Match": String(root.version) },
    );
    if (sharingForm.share_scope === "selected") {
      await api.put<WorkspaceRootMember[]>(
        `/workspace/roots/${root.id}/members`,
        {
          members: sharingUserIds.value.map((userId) => ({
            user_id: userId,
            can_browse: true,
            can_download: true,
            can_send: true,
          })),
        },
        { "If-Match": String(updated.version) },
      );
      updated = { ...updated, version: updated.version + 1 };
    }
    Object.assign(root, updated);
    sharingVisible.value = false;
    Message.success("共享范围已更新，撤销权限立即生效");
    await loadRoots();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "共享设置保存失败");
    await loadRoots();
  } finally {
    sharingSaving.value = false;
  }
}

function downloadFile() {
  if (!selectedFile.value || selectedRoot.value?.source === "device") return;
  window.open(downloadUrl(`/workspace/files/${selectedFile.value.id}/download`), "_blank", "noopener");
}

async function openSend() {
  if (!selectedFile.value || !selectedRoot.value || !selectedFile.value.permissions.send) return;
  try {
    const options = await api.get<{ devices: Device[]; roots: WorkspaceRoot[] }>("/collaboration/options");
    sendDevices.value = options.devices.filter((item) => (
      item.active
      && item.allow_device_transfer
      && item.id !== selectedRoot.value?.device_id
    ));
    sendRoots.value = options.roots.filter((item) => item.source === "device" && item.permissions.receive);
    Object.assign(sendForm, { destination_device_id: "", destination_root_id: "" });
    sendVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "协同目标加载失败");
  }
}

async function sendToDevice() {
  const file = selectedFile.value;
  if (!file || !sendForm.destination_device_id) {
    Message.warning("请选择目标协同电脑");
    return;
  }
  try {
    await api.post<Transfer>("/transfers", {
      direction: selectedRoot.value?.source === "device" ? "device_to_device" : "host_to_device",
      source_file_id: file.id,
      source_device_id: selectedRoot.value?.device_id || null,
      destination_device_id: sendForm.destination_device_id,
      destination_root_id: sendForm.destination_root_id || null,
      original_name: file.name,
      relative_path: file.relative_path,
      size_bytes: file.size_bytes,
      sha256: file.sha256 || "",
      require_approval: false,
    });
    sendVisible.value = false;
    Message.success("已创建发送到协同电脑的传输任务");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件发送失败");
  }
}

async function openWithDefaultApp() {
  if (!selectedFile.value) return;
  if (openGrantTimer !== undefined) window.clearTimeout(openGrantTimer);
  try {
    const result = await api.post<{
      grant_id: string;
      open_uri: string;
      status_url: string;
    }>(
      `/workspace/files/${selectedFile.value.id}/open-local`,
    );
    window.location.href = result.open_uri;
    Message.info("正在调用本机默认程序，系统会继续确认实际打开结果");
    let attempts = 0;
    const poll = async () => {
      attempts += 1;
      try {
        const status = await api.get<{
          status: string;
          result_code: string;
          result_detail: string;
        }>(result.status_url.replace(/^\/api\/v1/, ""));
        if (status.status === "completed") {
          Message.success("文件已交给本机默认程序打开");
          return;
        }
        if (status.status === "failed") {
          const labels: Record<string, string> = {
            DEFAULT_APP_FAILED: "系统默认程序未能打开该格式，请检查 WPS 或 Office 文件关联。",
            FILE_MISSING: "原文件已移动或删除，请刷新文件中心。",
            UNSUPPORTED_FORMAT: "本机没有可打开该格式的应用。",
            HELPER_FAILED: "本机文件助手运行失败，请打开运行诊断查看精确错误码。",
          };
          Message.error(labels[status.result_code] || status.result_detail || "本机文件打开失败");
          return;
        }
        if (status.status === "expired") {
          Message.warning("本机助手没有在五分钟内兑换授权，请重新点击打开");
          return;
        }
      } catch {
        if (attempts >= 12) return;
      }
      if (attempts < 12) openGrantTimer = window.setTimeout(poll, 1000);
    };
    openGrantTimer = window.setTimeout(poll, 800);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件打开失败");
  }
}

onMounted(load);
onBeforeUnmount(() => {
  if (scanMonitorTimer !== undefined) window.clearTimeout(scanMonitorTimer);
  if (openGrantTimer !== undefined) window.clearTimeout(openGrantTimer);
  disposePreviewRuntime();
});
</script>

<template>
  <div class="page workspace-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">综合原始文件中心</p>
        <h1 class="page-title">原始文件中心</h1>
        <p class="page-description">原文件保持原位；团队共享目录可跨机器安全浏览、结构化阅读、下载、转发与固化归档。</p>
      </div>
      <a-space>
        <PageHelp
          title="原始文件中心怎么用"
          :tips="['目录扫描只登记属性；正文只在用户点击预览时按权限临时读取。', 'Office 与 PDF 使用本地 Firecrawl 解析器生成安全阅读视图，原文件不会上传外网。', '共享电脑文件经主机分块中转和哈希校验后，可预览、下载或发送到其他协同机。']"
          help-query="原始文件中心"
        />
        <a-radio-group
          v-if="canManageHostFolder"
          :model-value="rootLifecycle"
          type="button"
          size="small"
          @change="changeRootLifecycle"
        >
          <a-radio value="active">使用中</a-radio>
          <a-radio value="disabled">已停用</a-radio>
        </a-radio-group>
        <a-dropdown trigger="click">
          <a-button type="primary" aria-label="操作目录"><template #icon><IconPlus /></template>操作目录</a-button>
          <template #content>
            <a-doption v-if="canShareLocalFolder" @click="openLocalShareManager">共享本机文件夹</a-doption>
            <a-doption v-if="canManageHostFolder" @click="rootVisible = true">纳管主机目录</a-doption>
            <a-doption v-if="!canShareLocalFolder && !canManageHostFolder" @click="explainDirectoryOperations">查看当前设备为何不能添加目录</a-doption>
          </template>
        </a-dropdown>
        <a-button v-if="selectedRoot?.enabled && selectedRoot.permissions.manage_root && selectedRoot.source === 'device'" aria-label="设置共享范围" @click="openSharing(selectedRoot)"><template #icon><IconSafe /></template>共享范围</a-button>
        <a-button v-if="selectedRoot?.enabled && selectedRoot.source !== 'device' && session.runtimeContext?.capabilities.includes('workspace.manage_host_roots')" aria-label="选择接入文件夹" @click="openSelection">
          <template #icon><IconFolder /></template>选择接入文件夹
        </a-button>
        <a-button v-if="selectedRoot?.enabled && selectedRoot.source !== 'device' && session.runtimeContext?.capabilities.includes('workspace.manage_host_roots')" aria-label="增量扫描" :loading="scanning" type="primary" @click="scanRoot">
          <template #icon><IconRefresh /></template>增量扫描
        </a-button>
        <a-button v-if="selectedRoot?.enabled && canManageHostFolder" status="danger" aria-label="停用当前目录" @click="openRootDeletion"><template #icon><IconDelete /></template>移除目录</a-button>
        <a-button v-else-if="selectedRoot && canManageHostFolder" type="primary" aria-label="恢复当前目录" @click="restoreRoot">恢复目录</a-button>
      </a-space>
    </header>

    <section v-if="roots.length" class="root-strip">
      <button
        v-for="root in roots"
        :key="root.id"
        type="button"
        :class="{ active: selectedRootId === root.id }"
        @click="changeRoot(root.id)"
      >
        <IconFolder />
        <span><b>{{ root.name }}</b><small>{{ rootSourceLabel(root) }} · {{ root.file_count }} 个文件 · {{ root.approval_status === "pending" ? "等待批准" : rootStatusLabel(root.scan_status) }}</small></span>
      </button>
    </section>

    <div v-if="selectedRoot" class="workspace-layout">
      <main class="file-browser">
        <a-alert v-if="!selectedRoot.enabled" type="info" class="selection-alert">
          该目录已停用，原文件、业务关联和审计记录没有删除。恢复后需重新扫描确认当前文件范围。
        </a-alert>
        <a-alert
          v-if="selectedRoot.selection_mode === 'selected' && !selectedRoot.included_paths.length"
          type="warning"
          class="selection-alert"
        >
          目录发现已完成，但尚未选择接入文件夹。未选择内容不会进入业务目录或提供访问。
          <a-button v-if="session.runtimeContext?.capabilities.includes('workspace.manage_host_roots')" size="mini" type="primary" @click="openSelection">现在选择</a-button>
        </a-alert>
        <div class="file-toolbar">
          <div class="breadcrumbs">
            <button type="button" @click="goToLevel(-1)">{{ selectedRoot.name }}</button>
            <template v-for="(folder, index) in pathStack" :key="folder.id">
              <span>/</span><button type="button" @click="goToLevel(index)">{{ folder.name }}</button>
            </template>
          </div>
          <a-input-search v-model="keyword" placeholder="搜索文件夹和文件名" allow-clear @search="search">
            <template #prefix><IconSearch /></template>
          </a-input-search>
        </div>
        <div class="file-columns">
          <span>名称</span><span>修改时间</span><span>大小</span><span>状态</span>
        </div>
        <div v-if="checkedIds.length" class="selection-actions">
          <b>已选择 {{ checkedIds.length }} 项</b>
          <a-space>
            <a-button size="mini" :loading="downloadBusy" @click="createDownload(checkedIds, 'browser')"><template #icon><IconDownload /></template>浏览器另存为</a-button>
            <a-button v-if="session.runtimeContext?.capabilities.includes('workspace.download.current_device')" size="mini" type="primary" :loading="downloadBusy" @click="createDownload(checkedIds, 'current_device')"><template #icon><IconDownload /></template>下载到本机接收目录</a-button>
            <a-button size="mini" type="text" @click="checkedIds = []">取消选择</a-button>
          </a-space>
        </div>
        <a-spin :loading="loading" class="file-spin">
          <button
            v-for="item in files"
            :key="item.id"
            type="button"
            class="file-row"
            :class="{ selected: selectedFile?.id === item.id, missing: item.status === 'missing' }"
            @click="openItem(item)"
          >
            <span class="file-name">
              <a-checkbox :model-value="checkedIds.includes(item.id)" aria-label="选择下载项目" @click.stop @change="(value: string | number | boolean) => toggleChecked(item.id, value)" />
              <IconFolder v-if="item.is_directory" class="folder-icon" />
              <IconFile v-else />
              <b>{{ item.name }}</b>
              <i v-if="item.sha256">已固化</i>
            </span>
            <span>{{ formatServerTime(item.modified_at, "YYYY-MM-DD HH:mm") }}</span>
            <span>{{ item.is_directory ? "—" : formatSize(item.size_bytes) }}</span>
            <span :class="`status-${item.status}`">{{ statusLabel(item.status) }}</span>
          </button>
          <div v-if="!files.length" class="empty-state">该位置暂无文件，或尚未执行扫描。</div>
        </a-spin>
      </main>

      <aside class="file-inspector">
        <template v-if="selectedFile">
          <div class="inspector-title">
            <IconFile />
            <div><h2>{{ selectedFile.name }}</h2><p>{{ selectedFile.relative_path }}</p></div>
          </div>
          <div class="inspector-actions">
            <a-button v-if="selectedRoot?.source !== 'device' && selectedFile.permissions.download" size="small" type="primary" aria-label="使用默认程序打开" @click="openWithDefaultApp">
              <template #icon><IconFile /></template>使用默认程序打开
            </a-button>
            <a-button v-if="selectedFile.permissions.download" size="small" aria-label="预览" @click="previewSelectedFile"><template #icon><IconFile /></template>预览</a-button>
            <a-button v-if="selectedFile.permissions.download" size="small" aria-label="浏览器另存为" :loading="downloadBusy" @click="createDownload([selectedFile.id], 'browser')"><template #icon><IconDownload /></template>浏览器另存为</a-button>
            <a-button v-if="selectedFile.permissions.download && session.runtimeContext?.capabilities.includes('workspace.download.current_device')" size="small" type="primary" aria-label="下载到本机" :loading="downloadBusy" @click="createDownload([selectedFile.id], 'current_device')"><template #icon><IconDownload /></template>下载到本机</a-button>
            <a-button v-if="selectedFile.permissions.send" size="small" aria-label="发送到协同机" @click="openSend"><template #icon><IconDownload /></template>发送到协同机</a-button>
            <a-button v-if="selectedFile.permissions.send" size="small" aria-label="关联任务" @click="linkVisible = true"><template #icon><IconLink /></template>关联任务</a-button>
            <a-popconfirm v-if="selectedFile.permissions.download" content="固化会复制一份去重快照到系统附件库，原文件保持不变。确认继续？" @ok="freezeSelectedFile">
              <a-button size="small" type="primary" :aria-label="selectedRoot?.source === 'device' ? '拉取并固化' : (selectedFile.sha256 ? '重新校验' : '固化归档')" :loading="downloadBusy"><template #icon><IconSafe /></template>{{ selectedRoot?.source === 'device' ? "拉取并固化" : (selectedFile.sha256 ? "重新校验" : "固化归档") }}</a-button>
            </a-popconfirm>
          </div>
          <div class="inspector-section">
            <label>文件属性</label>
            <p>{{ formatSize(selectedFile.size_bytes) }} · {{ selectedFile.extension || "无扩展名" }}</p>
            <p class="muted">修改时间：{{ formatServerTime(selectedFile.modified_at, "YYYY-MM-DD HH:mm") }}</p>
            <p class="muted">预览时才按权限读取：Office/PDF 在浏览器本地生成结构化阅读视图，不上传第三方；也可下载后用 WPS 等默认程序打开。</p>
          </div>
          <div class="inspector-section">
            <label>标签</label>
            <a-input v-model="tagsText" :disabled="!selectedFile.permissions.send" placeholder="用顿号分隔，例如：年度重点、已报送" @press-enter="saveTags" />
            <a-button v-if="selectedFile.permissions.send" size="mini" type="text" @click="saveTags">保存标签</a-button>
          </div>
          <div class="inspector-section">
            <label>关联对象</label>
            <p v-if="!selectedFile.links.length" class="muted">尚未关联任务或报告。</p>
            <RouterLink
              v-for="link in selectedFile.links.filter((item) => item.entity_type === 'task')"
              :key="link.id"
              :to="`/tasks/${link.entity_id}`"
              class="linked-item"
            >
              {{ tasks.find((item) => item.id === link.entity_id)?.title || "关联任务" }}
            </RouterLink>
            <p v-if="selectedFile.sha256" class="hash-value">SHA-256<br />{{ selectedFile.sha256 }}</p>
          </div>
        </template>
        <div v-else class="inspector-empty">
          <IconFile />
          <p>选择一个文件，查看属性、标签和任务关联。</p>
        </div>
      </aside>
    </div>
    <div v-else class="root-empty">
      <IconFolder />
      <h2>从单位现有工作文件夹开始</h2>
      <p v-if="session.runtimeContext?.capabilities.includes('workspace.local_share')">选择本机真实文件夹发布给团队，或等待同事共享目录后在此浏览和下载。</p>
      <p v-else>管理员选择主机上的资料目录，系统只读建立索引，不移动、不删除任何原始文件。</p>
      <a-button v-if="session.runtimeContext?.capabilities.includes('workspace.local_share')" type="primary" @click="openLocalShareManager">共享本机文件夹</a-button>
      <a-button v-else-if="session.runtimeContext?.capabilities.includes('workspace.manage_host_roots')" type="primary" @click="rootVisible = true">纳管第一个目录</a-button>
    </div>

    <a-modal
      v-model:visible="previewVisible"
      :title="`文件阅读 · ${previewTitle}`"
      width="min(1120px, 94vw)"
      :footer="false"
      :mask-closable="false"
      unmount-on-close
      @cancel="closeDocumentPreview"
    >
      <div class="document-preview-shell">
        <div class="document-preview-toolbar">
          <div>
            <b>{{ previewTitle }}</b>
            <span v-if="previewEngine">{{ previewEngine }} · {{ previewFormat.toUpperCase() }}</span>
          </div>
          <a-space>
            <a-button v-if="selectedFile && selectedRoot?.source !== 'device'" size="small" type="primary" @click="openWithDefaultApp">使用默认程序打开</a-button>
            <a-button v-if="previewRawAvailable && previewRawUrl" size="small" :href="previewRawUrl" target="_blank">新窗口打开原始预览</a-button>
            <a-button v-if="selectedFile" size="small" @click="createDownload([selectedFile.id], 'browser')"><template #icon><IconDownload /></template>浏览器另存为</a-button>
            <a-button
              v-if="selectedFile && session.runtimeContext?.capabilities.includes('workspace.download.current_device')"
              size="small"
              type="primary"
              @click="createDownload([selectedFile.id], 'current_device')"
            ><template #icon><IconDownload /></template>下载到本机</a-button>
          </a-space>
        </div>
        <a-alert v-if="previewError" type="warning" class="preview-alert">{{ previewError }}</a-alert>
        <a-alert v-for="warning in previewWarnings" :key="warning" type="warning" class="preview-alert">{{ warning }}</a-alert>
        <div v-if="previewPdf" class="preview-metadata">
          <span>{{ previewPdf.pageCount }} 页</span>
          <span>PDF：{{ pdfTypeLabel(previewPdf.pdfType) }}</span>
          <span>置信度 {{ Math.round(previewPdf.confidence * 100) }}%</span>
          <span v-if="previewPdf.pagesWithTables.length">表格页 {{ previewPdf.pagesWithTables.length }}</span>
          <span v-if="previewPdf.pagesWithColumns.length">分栏页 {{ previewPdf.pagesWithColumns.length }}</span>
          <span>{{ Math.round(previewPdf.processingTimeMs) }} ms</span>
        </div>
        <a-spin :loading="previewLoading" tip="正在从授权设备取回并校验文件，随后在浏览器本地解析…">
          <a-tabs v-model:active-key="previewTab" class="document-preview-tabs">
            <a-tab-pane key="reading" title="结构化阅读">
              <div v-if="previewHtml" class="document-reading" v-html="previewHtml"></div>
              <div v-else-if="!previewLoading && !previewError" class="preview-empty">文档没有可提取的文字内容，可切换原始预览。</div>
            </a-tab-pane>
            <a-tab-pane v-if="previewRawAvailable" key="raw" title="原始预览">
              <iframe
                v-if="previewRawUrl && previewTab === 'raw'"
                class="document-raw-frame"
                :src="previewRawUrl"
                :title="`${previewTitle} 原始预览`"
                sandbox="allow-same-origin allow-downloads"
              ></iframe>
              <div v-else class="preview-empty">正在准备原始文件…</div>
            </a-tab-pane>
          </a-tabs>
        </a-spin>
      </div>
    </a-modal>

    <a-modal v-model:visible="rootVisible" title="纳管主机原始目录" @ok="createRoot">
      <a-alert type="info">目录必须位于主机本地。系统先只读发现全部文件夹和文件名称，再由您选择哪些子文件夹正式接入。</a-alert>
      <a-form :model="rootForm" layout="vertical" class="root-form">
        <a-form-item label="显示名称"><a-input v-model="rootForm.name" /></a-form-item>
        <a-form-item label="主机绝对路径"><a-input v-model="rootForm.absolute_path" placeholder="/data/home/用户名/2026年工作" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="rootDeletionVisible" title="停用原始文件目录" ok-text="确认停用" @ok="deleteRoot">
      <a-alert type="warning">系统只停止索引和共享，不移动、不修改、不删除磁盘上的任何原文件；历史业务关联和审计记录继续保留。</a-alert>
      <div v-if="rootDeletionImpact" class="root-deletion-impact">
        <span>索引文件 <b>{{ rootDeletionImpact.indexed_files }}</b></span>
        <span>业务关联 <b>{{ rootDeletionImpact.business_links }}</b></span>
        <span>共享成员 <b>{{ rootDeletionImpact.sharing_members }}</b></span>
        <span>活动传输 <b>{{ rootDeletionImpact.active_transfers }}</b></span>
      </div>
      <a-form-item label="停用原因" required><a-textarea v-model="rootDeletionReason" :max-length="1000" show-word-limit /></a-form-item>
    </a-modal>

    <a-modal
      v-model:visible="selectionVisible"
      title="选择接入系统的文件夹"
      :width="720"
      :ok-loading="selectionSaving"
      ok-text="保存并接入所选范围"
      :mask-closable="false"
      @ok="saveSelection"
    >
      <a-alert type="info">
        所有文件夹和文件名称都已只读发现；只有勾选的文件夹会进入文件中心。系统不读取正文、不移动也不删除原文件。
      </a-alert>
      <a-input-search v-model="folderKeyword" class="folder-search" placeholder="搜索已发现的文件夹" allow-clear />
      <div class="folder-selection-list">
        <label
          v-for="folder in filteredFolderOptions"
          :key="folder.path"
          class="folder-selection-row"
          :style="{ paddingLeft: `${14 + Math.min(folder.depth, 10) * 22}px` }"
        >
          <a-checkbox
            :model-value="folderChecked(folder.path)"
            @change="(value: string | number | boolean) => toggleFolder(folder.path, value)"
          />
          <IconFolder />
          <span>
            <b>{{ folder.name }}</b>
            <small>{{ folder.path === "." ? "接入整个根目录" : folder.path }} · 当前层 {{ folder.direct_file_count }} 个文件</small>
          </span>
        </label>
        <div v-if="!filteredFolderOptions.length" class="empty-state">没有匹配的文件夹。</div>
      </div>
      <p class="selection-summary">
        已选择 {{ selectedFolderPaths.length }} 个范围；选择父文件夹后会自动包含其全部下级目录。
      </p>
    </a-modal>

    <a-modal v-model:visible="linkVisible" title="关联到任务" @ok="linkTask">
      <a-select v-model="linkTaskId" placeholder="选择任务" allow-search>
        <a-option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</a-option>
      </a-select>
    </a-modal>

    <a-modal v-model:visible="sendVisible" title="发送文件到协同电脑" :mask-closable="false" @ok="sendToDevice">
      <a-form :model="sendForm" layout="vertical">
        <a-form-item label="目标协同电脑"><a-select v-model="sendForm.destination_device_id" allow-search><a-option v-for="device in sendDevices" :key="device.id" :value="device.id">{{ device.name }}</a-option></a-select></a-form-item>
        <a-form-item label="目标共享目录"><a-select v-model="sendForm.destination_root_id" allow-clear allow-search><a-option v-for="root in sendRoots.filter((item) => !sendForm.destination_device_id || item.device_id === sendForm.destination_device_id)" :key="root.id" :value="root.id">{{ root.name }}</a-option></a-select></a-form-item>
        <a-alert type="info">文件经过主机受管中转并校验 SHA-256；目标设备离线时会自动排队，恢复上线后续传。</a-alert>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="sharingVisible" title="共享范围与语义索引" :ok-loading="sharingSaving" :mask-closable="false" @ok="saveSharing">
      <a-form :model="sharingForm" layout="vertical">
        <a-form-item label="共享范围">
          <a-radio-group v-model="sharingForm.share_scope">
            <a-radio value="team">团队成员（默认）</a-radio>
            <a-radio value="selected">指定人员</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item v-if="sharingForm.share_scope === 'selected'" label="可浏览、下载和转发的人员">
          <a-select v-model="sharingUserIds" multiple allow-search placeholder="选择人员">
            <a-option v-for="user in sharingUsers" :key="user.id" :value="user.id">{{ user.display_name }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="本目录正文语义索引">
          <a-switch v-model="sharingForm.semantic_content_enabled" />
          <span class="inline-note">默认关闭；开启后仅提取支持格式的正文，关闭或撤权会删除相应正文向量。</span>
        </a-form-item>
        <a-alert type="info">主机只保存目录随机标识与索引，不保存本机绝对路径；共享撤销会在传输的下一个分块生效。</a-alert>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.page-kicker {
  margin: 0 0 8px;
  color: var(--cinnabar);
  font: 11px Georgia, serif;
  letter-spacing: 0.18em;
}

.root-strip {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  padding-bottom: 14px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
}

.root-strip button {
  display: flex;
  min-width: 220px;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  text-align: left;
  background: rgba(251, 248, 241, 0.55);
  border: 1px solid var(--line);
  cursor: pointer;
}

.root-strip button.active {
  color: var(--cinnabar);
  background: var(--paper);
  border-color: rgba(180, 35, 24, 0.45);
}

.root-strip b,
.root-strip small {
  display: block;
}

.root-strip small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.workspace-layout {
  display: grid;
  min-height: 620px;
  grid-template-columns: minmax(0, 1fr) 340px;
  border: 1px solid var(--line);
}

.selection-alert {
  margin: 14px;
}

.selection-alert :deep(.arco-alert-content) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.folder-search {
  margin: 18px 0 12px;
}

.folder-selection-list {
  max-height: min(440px, calc(100vh - 330px));
  overflow: auto;
  border: 1px solid var(--line);
}

.folder-selection-row {
  display: flex;
  min-height: 48px;
  align-items: center;
  gap: 10px;
  padding-top: 7px;
  padding-right: 12px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--line-light);
  cursor: pointer;
}

.folder-selection-row:hover {
  background: rgba(180, 35, 24, 0.04);
}

.folder-selection-row > svg {
  flex: 0 0 auto;
  color: var(--cinnabar);
}

.folder-selection-row span,
.folder-selection-row b,
.folder-selection-row small {
  display: block;
  min-width: 0;
}

.folder-selection-row small {
  margin-top: 3px;
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 10px;
}

.selection-summary {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 11px;
}

.file-browser {
  min-width: 0;
  background: rgba(251, 248, 241, 0.62);
}

.file-toolbar {
  display: flex;
  height: 58px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
}

.file-toolbar .arco-input-search {
  width: 310px;
}

.breadcrumbs {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  overflow: hidden;
}

.breadcrumbs button {
  padding: 0;
  color: var(--cinnabar);
  white-space: nowrap;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.breadcrumbs span {
  color: var(--muted);
}

.file-columns,
.file-row {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 150px 90px 80px;
  align-items: center;
}

.file-columns {
  height: 34px;
  padding: 0 16px;
  color: var(--muted);
  font-size: 10px;
  background: #eee6d9;
}

.selection-actions {
  display: flex;
  min-height: 46px;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 7px 16px;
  color: var(--cinnabar);
  background: rgba(180, 35, 24, 0.055);
  border-bottom: 1px solid var(--line);
}

.inline-note {
  margin-left: 10px;
  color: var(--muted);
  font-size: 11px;
}

.file-row {
  width: 100%;
  min-height: 44px;
  padding: 0 16px;
  color: var(--charcoal);
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--line-light);
  cursor: pointer;
}

.file-row:hover,
.file-row.selected {
  background: rgba(180, 35, 24, 0.05);
}

.file-row.missing {
  opacity: 0.62;
}

.file-row > span:not(.file-name) {
  color: var(--muted);
  font-size: 11px;
}

.file-name {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
}

.file-name svg {
  flex: 0 0 auto;
  color: #8c8176;
}

.file-name .folder-icon {
  color: var(--cinnabar);
}

.file-name b {
  overflow: hidden;
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-name i {
  flex: 0 0 auto;
  padding: 2px 4px;
  color: var(--green);
  font-size: 9px;
  font-style: normal;
  border: 1px solid rgba(47, 125, 76, 0.3);
}

.status-missing,
.status-error {
  color: var(--danger) !important;
}

.status-changed {
  color: var(--amber) !important;
}

.file-spin {
  display: block;
  min-height: 500px;
}

.file-inspector {
  padding: 20px;
  overflow: hidden;
  background: rgba(238, 230, 217, 0.58);
  border-left: 1px solid var(--line);
}

.inspector-title {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.inspector-title > svg {
  margin-top: 3px;
  color: var(--cinnabar);
  font-size: 22px;
}

.inspector-title h2 {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 15px;
}

.inspector-title p {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 10px;
}

.inspector-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 16px 0;
}

.document-preview-shell {
  display: flex;
  min-height: min(680px, calc(100vh - 190px));
  flex-direction: column;
  background: rgba(253, 251, 247, 0.98);
}

.document-preview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 0 0 12px;
  border-bottom: 1px solid var(--line);
}

.document-preview-toolbar > div:first-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.document-preview-toolbar b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-preview-toolbar span {
  color: var(--muted);
  font: 10px/1.4 monospace;
}

.preview-alert {
  margin-top: 10px;
}

.preview-metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding: 10px 0 0;
}

.preview-metadata span {
  padding: 4px 7px;
  color: #665b50;
  background: #f5eee4;
  border: 1px solid #ded2c4;
  border-radius: 2px;
  font-size: 10px;
}

.document-preview-tabs {
  min-height: 560px;
}

.document-reading {
  height: min(560px, calc(100vh - 310px));
  padding: 22px clamp(18px, 4vw, 56px) 48px;
  overflow: auto;
  color: #2f2925;
  background: #fffdf9;
  border: 1px solid var(--line);
  font-size: 14px;
  line-height: 1.8;
}

.document-reading :deep(h1),
.document-reading :deep(h2),
.document-reading :deep(h3),
.document-reading :deep(h4) {
  margin: 1.4em 0 0.65em;
  color: #2e2420;
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", serif;
  line-height: 1.35;
}

.document-reading :deep(h1) {
  padding-bottom: 10px;
  border-bottom: 2px solid rgba(166, 44, 36, 0.28);
  font-size: 26px;
}

.document-reading :deep(h2) { font-size: 21px; }
.document-reading :deep(h3) { font-size: 17px; }

.document-reading :deep(table) {
  display: block;
  width: 100%;
  margin: 18px 0;
  overflow-x: auto;
  border-collapse: collapse;
}

.document-reading :deep(th),
.document-reading :deep(td) {
  min-width: 96px;
  padding: 8px 10px;
  border: 1px solid #d8cbbd;
  text-align: left;
  vertical-align: top;
}

.document-reading :deep(th) {
  background: #f4ede3;
}

.document-reading :deep(pre) {
  padding: 14px;
  overflow: auto;
  white-space: pre-wrap;
  background: #f6f1e9;
  border: 1px solid #dfd4c7;
  font: 12px/1.7 Consolas, monospace;
}

.document-reading :deep(a) { color: var(--cinnabar); }
.document-reading :deep(.preview-image-alt) { color: var(--muted); font-style: italic; }

.document-raw-frame {
  width: 100%;
  height: min(560px, calc(100vh - 310px));
  background: #f1ede7;
  border: 1px solid var(--line);
}

.preview-empty {
  display: grid;
  min-height: 320px;
  place-items: center;
  color: var(--muted);
}

.inspector-section {
  padding: 17px 0;
  border-bottom: 1px solid var(--line);
}

.inspector-section label {
  display: block;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 10px;
}

.linked-item {
  display: block;
  padding: 7px 0;
  color: var(--cinnabar);
  font-size: 11px;
}

.hash-value {
  overflow-wrap: anywhere;
  color: var(--muted);
  font: 9px/1.6 monospace;
}

.inspector-empty,
.root-empty {
  display: flex;
  min-height: 520px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  text-align: center;
}

.inspector-empty svg,
.root-empty svg {
  color: #a99f93;
  font-size: 42px;
}

.inspector-empty p,
.root-empty p {
  max-width: 480px;
  line-height: 1.8;
}

.root-form {
  margin-top: 18px;
}

.root-deletion-impact {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 16px 0;
  background: var(--line);
  border: 1px solid var(--line);
}

.root-deletion-impact span {
  padding: 12px;
  color: var(--muted);
  font-size: 11px;
  background: #fffaf0;
}

.root-deletion-impact b {
  display: block;
  margin-top: 5px;
  color: var(--charcoal);
  font: 22px Georgia, serif;
}

@media (max-width: 720px) {
  .root-deletion-impact { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
