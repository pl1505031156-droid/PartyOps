<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  IconCheck,
  IconDownload,
  IconExclamationCircle,
  IconFile,
  IconRefresh,
  IconSafe,
  IconUpload,
} from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, saveBlobDownload } from "../api";
import PageHelp from "../components/PageHelp.vue";
import { beijingNow } from "../utils/datetime";

interface TicketResponse { ticket: string; expires_at: string; local_base_url: string }
interface SessionResponse { session_id: string; session_token: string; expires_in_seconds: number }
interface Capability { capability_id: string; description: string }
type FeatureId = "format" | "replace" | "redheader" | "rename" | "convert" | "pdf-to-word";
interface FeatureDefinition {
  id: FeatureId;
  display_name: string;
  notes: string;
  accepts: string[];
  capabilities: Capability[];
}
interface CapabilityResponse {
  capability_count: number;
  features: FeatureDefinition[];
  external_office_required: boolean;
}
interface ReplaceRule {
  mode: "text" | "regex" | "wildcard" | "format";
  find: string;
  replace: string;
  case_sensitive: boolean;
  font_name?: string;
  font_size?: number;
  alignment?: "" | "left" | "center" | "right" | "justify";
}
interface SavedReplacePlan { name: string; rules: ReplaceRule[] }
interface QueueFile {
  key: string;
  file: File;
  documentId: string;
  state: "ready" | "uploading" | "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  message: string;
}
interface JobOutput { id: string; document_id: string; filename: string; content_type: string; downloaded: boolean }
interface JobItem {
  document_id: string;
  filename: string;
  state: QueueFile["state"];
  progress: number;
  message: string;
  error_code: string;
  report: {
    compliant: boolean;
    paragraph_count: number;
    table_count: number;
    changed_count: number;
    issues: Array<{ code: string; severity: "error" | "warning"; title: string; detail: string; clause: string }>;
  } | null;
}
interface JobResponse {
  id: string;
  feature_id: FeatureId;
  state: "queued" | "running" | "completed" | "completed_with_errors" | "failed" | "cancelled";
  progress: number;
  message: string;
  items: JobItem[];
  outputs: JobOutput[];
}
type DirectoryHandle = {
  name: string;
  getFileHandle: (name: string, options: { create: boolean }) => Promise<{
    createWritable: () => Promise<{ write: (value: Blob) => Promise<void>; close: () => Promise<void> }>;
  }>;
};

class LocalFormatError extends Error {
  code: string;
  constructor(code: string, detail: string) { super(detail); this.code = code }
}

const fallbackFeatures: FeatureDefinition[] = [
  { id: "format", display_name: "一键排版", notes: "按排版模板处理版面、字体、页码、图片和表格。", accepts: [".docx", ".doc", ".wps", ".rtf"], capabilities: [] },
  { id: "replace", display_name: "一键替换", notes: "执行文字、正则、通配符、格式与批量规则。", accepts: [".docx", ".doc", ".wps", ".rtf"], capabilities: [] },
  { id: "redheader", display_name: "一键套红", notes: "生成版头、文号、红线和版记。", accepts: [".docx", ".doc", ".wps", ".rtf"], capabilities: [] },
  { id: "rename", display_name: "一键命名", notes: "按标题、文号、日期和自定义规则生成文件名。", accepts: [".docx", ".doc", ".wps", ".rtf"], capabilities: [] },
  { id: "convert", display_name: "一键转换", notes: "导出 DOCX、PDF、TXT、分页图片或长图。", accepts: [".docx", ".doc", ".wps", ".rtf", ".pdf"], capabilities: [] },
  { id: "pdf-to-word", display_name: "PDF 转 Word", notes: "本地重建 PDF 的文本、页面与表格。", accepts: [".pdf"], capabilities: [] },
];
const fallbackCapabilityCounts: Record<FeatureId, number> = { format: 5, replace: 5, redheader: 4, rename: 3, convert: 4, "pdf-to-word": 4 };
const REPLACE_PLANS_STORAGE_KEY = "partyops.document-formatter.replace-plans.v1";

const fileInput = ref<HTMLInputElement | null>(null);
const files = ref<QueueFile[]>([]);
const features = ref<FeatureDefinition[]>(fallbackFeatures);
const selectedFeature = ref<FeatureId>("format");
const busy = ref(false);
const dragging = ref(false);
const localBaseUrl = ref("");
const localSessionId = ref("");
const localSessionToken = ref("");
const activeJob = ref<JobResponse | null>(null);
const errorCode = ref("");
const errorDetail = ref("");
const selfTestText = ref("尚未自检");
const outputDirectory = ref<DirectoryHandle | null>(null);
const beijingClock = ref("");
const savedReplacePlans = ref<SavedReplacePlan[]>([]);
let pollTimer = 0;
let clockTimer = 0;

const options = reactive({
  compatibility_mode: "auto",
  template: "GB/T 9704-2012",
  scope: "full",
  start_paragraph: 1,
  end_paragraph: 99999,
  plan_name: "默认方案",
  rules: [{ mode: "text", find: "", replace: "", case_sensitive: false }] as ReplaceRule[],
  document_type: "down",
  copy_number: "",
  security: "",
  urgency: "",
  agency: "中共××委员会",
  document_number: "×党发〔2026〕1号",
  signatory: "",
  imprint: "中共××委员会办公室",
  parts: ["title", "document_number"] as string[],
  custom_text: "",
  separator: "-",
  rotation_words: "",
  target_format: "pdf",
  image_mode: "pages",
  page_selection: "all",
  dpi: 200,
  same_name_policy: "auto-rename",
  normalize_punctuation: true,
  reconstruct_tables: true,
});

const currentFeature = computed(() => features.value.find((item) => item.id === selectedFeature.value) || fallbackFeatures[0]);
const accept = computed(() => currentFeature.value.accepts.join(","));
const canStart = computed(() => files.value.length > 0 && !busy.value && !files.value.some((item) => !currentFeature.value.accepts.includes(fileExtension(item.file.name))));
const completedCount = computed(() => files.value.filter((item) => item.state === "completed").length);
const failedCount = computed(() => files.value.filter((item) => item.state === "failed").length);
const selectedReport = computed(() => activeJob.value?.items.find((item) => item.report)?.report || null);
const currentCapabilities = computed(() => currentFeature.value.capabilities || []);
const outputLocationLabel = computed(() => outputDirectory.value ? outputDirectory.value.name : "浏览器下载目录");

function fileExtension(name: string) {
  return name.toLowerCase().match(/\.[^.]+$/)?.[0] || "";
}
function capabilityCount(feature: FeatureDefinition) { return feature.capabilities.length || fallbackCapabilityCounts[feature.id] }
function loadSavedReplacePlans() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(REPLACE_PLANS_STORAGE_KEY) || "[]") as SavedReplacePlan[];
    savedReplacePlans.value = Array.isArray(parsed)
      ? parsed.filter((plan) => plan && typeof plan.name === "string" && Array.isArray(plan.rules)).slice(0, 30)
      : [];
  } catch { savedReplacePlans.value = [] }
}
function persistReplacePlans() {
  try { window.localStorage.setItem(REPLACE_PLANS_STORAGE_KEY, JSON.stringify(savedReplacePlans.value)) }
  catch { Message.warning("浏览器未允许保存方案，本次设置仍可继续使用") }
}
function saveReplacePlan() {
  const name = options.plan_name.trim().slice(0, 40);
  if (!name) { Message.warning("请先填写替换方案名称"); return }
  const plan = { name, rules: options.rules.map((rule) => ({ ...rule })) };
  const index = savedReplacePlans.value.findIndex((item) => item.name === name);
  if (index >= 0) savedReplacePlans.value[index] = plan;
  else {
    if (savedReplacePlans.value.length >= 30) { Message.warning("最多保存 30 套替换方案"); return }
    savedReplacePlans.value.push(plan);
  }
  persistReplacePlans(); Message.success(`已保存替换方案：${name}`);
}
function loadReplacePlan(value: unknown) {
  const name = String(value);
  const plan = savedReplacePlans.value.find((item) => item.name === name);
  if (!plan) return;
  options.plan_name = plan.name;
  options.rules = plan.rules.map((rule) => ({ ...rule }));
}
function deleteReplacePlan() {
  const index = savedReplacePlans.value.findIndex((item) => item.name === options.plan_name);
  if (index < 0) { Message.info("当前方案尚未保存"); return }
  savedReplacePlans.value.splice(index, 1); persistReplacePlans(); options.plan_name = "默认方案";
  Message.success("替换方案已删除");
}
function updateClock() { beijingClock.value = beijingNow().format("YYYY-MM-DD HH:mm") }
function chooseFiles() { fileInput.value?.click() }
function addFiles(nextFiles: File[]) {
  errorCode.value = ""; errorDetail.value = "";
  const room = Math.max(0, 50 - files.value.length);
  for (const file of nextFiles.slice(0, room)) {
    const extension = fileExtension(file.name);
    if (!currentFeature.value.accepts.includes(extension)) { Message.warning(`${currentFeature.value.display_name}不支持 ${extension || "该文件类型"}`); continue }
    if (file.size <= 0 || file.size > 50 * 1024 * 1024) { Message.warning(`${file.name} 为空或超过 50 MiB`); continue }
    files.value.push({ key: crypto.randomUUID(), file, documentId: "", state: "ready", progress: 0, message: "等待处理" });
  }
  if (nextFiles.length > room) Message.warning("单次最多处理 50 个文件");
}
function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  addFiles(Array.from(input.files || [])); input.value = "";
}
function onDrop(event: DragEvent) { dragging.value = false; addFiles(Array.from(event.dataTransfer?.files || [])) }
function removeFile(index: number) { if (!busy.value) files.value.splice(index, 1) }
function clearFiles() { if (!busy.value) { files.value = []; activeJob.value = null } }

async function parseLocalResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let payload: Record<string, unknown> = {};
  try { payload = await response.json() as Record<string, unknown> } catch { payload = { code: "LOCAL_HELPER_RESPONSE_INVALID", detail: `本机服务返回异常（${response.status}）` } }
  throw new LocalFormatError(String(payload.code || "LOCAL_FORMAT_FAILED"), String(payload.detail || payload.title || "本机处理未完成"));
}
function localHeaders(): HeadersInit { return { "X-PartyOps-Local-Token": localSessionToken.value } }
async function cleanupLocalSession() {
  window.clearTimeout(pollTimer);
  if (!localBaseUrl.value || !localSessionId.value || !localSessionToken.value) return;
  const url = `${localBaseUrl.value}/v1/sessions/${localSessionId.value}`;
  const headers = localHeaders(); localSessionId.value = ""; localSessionToken.value = "";
  try { await window.fetch(url, { method: "DELETE", headers, credentials: "omit", mode: "cors", keepalive: true }) } catch { /* 空闲期限会兜底清理。 */ }
}
async function ensureLocalSession() {
  if (localSessionId.value && localSessionToken.value) return;
  const ticket = await api.post<TicketResponse>("/official-format/local-ticket", { origin: window.location.origin });
  localBaseUrl.value = ticket.local_base_url;
  let health: Response;
  try { health = await window.fetch(`${ticket.local_base_url}/health`, { cache: "no-store", credentials: "omit", mode: "cors" }) }
  catch { throw new LocalFormatError("LOCAL_HELPER_UNREACHABLE", "当前电脑的内置公文引擎未启动，请重新打开 PartyOps。") }
  if (!health.ok) throw new LocalFormatError("LOCAL_HELPER_HEALTH_FAILED", "内置公文引擎健康检查未通过，请修复安装 PartyOps。");
  const response = await window.fetch(`${ticket.local_base_url}/v1/sessions`, { method: "POST", headers: { Authorization: `Bearer ${ticket.ticket}` }, credentials: "omit", mode: "cors" });
  const session = await parseLocalResponse<SessionResponse>(response);
  localSessionId.value = session.session_id; localSessionToken.value = session.session_token;
  const catalogResponse = await window.fetch(`${ticket.local_base_url}/v1/capabilities`, { cache: "no-store", credentials: "omit", mode: "cors" });
  if (catalogResponse.ok) {
    const catalog = await catalogResponse.json() as CapabilityResponse;
    if (catalog.features?.length === 6 && catalog.capability_count === 25) features.value = catalog.features;
  }
}
function showFailure(error: unknown) {
  const current = error as { code?: string; message?: string };
  errorCode.value = current?.code || "LOCAL_FORMAT_FAILED";
  errorDetail.value = current?.message || "本机处理未完成，请重试。";
}
async function selectOutputDirectory() {
  const picker = (window as unknown as { showDirectoryPicker?: (options: Record<string, unknown>) => Promise<DirectoryHandle> }).showDirectoryPicker;
  if (!picker) { Message.info("当前浏览器将使用系统默认下载目录"); return }
  try { outputDirectory.value = await picker({ mode: "readwrite", id: "partyops-official-format-output" }) }
  catch (error) { if ((error as { name?: string }).name !== "AbortError") Message.error("无法使用所选输出目录") }
}
function requestOptions() {
  const base = { compatibility_mode: options.compatibility_mode, same_name_policy: options.same_name_policy };
  if (selectedFeature.value === "format") return { ...base, template: options.template, scope: options.scope, start_paragraph: options.start_paragraph, end_paragraph: options.end_paragraph };
  if (selectedFeature.value === "replace") return { ...base, plan_name: options.plan_name, rules: options.rules.map((rule) => ({ ...rule })) };
  if (selectedFeature.value === "redheader") return { ...base, document_type: options.document_type, copy_number: options.copy_number, security: options.security, urgency: options.urgency, agency: options.agency, document_number: options.document_number, signatory: options.signatory, imprint: options.imprint };
  if (selectedFeature.value === "rename") return { ...base, parts: [...options.parts], custom_text: options.custom_text, separator: options.separator, rotation_words: options.rotation_words };
  if (selectedFeature.value === "convert") return { ...base, target_format: options.target_format, image_mode: options.image_mode, page_selection: options.page_selection, dpi: options.dpi };
  return { ...base, normalize_punctuation: options.normalize_punctuation, reconstruct_tables: options.reconstruct_tables };
}
async function uploadFile(item: QueueFile) {
  item.state = "uploading"; item.progress = 8; item.message = "正在传入本机引擎";
  const form = new FormData(); form.append("document", item.file);
  const response = await window.fetch(`${localBaseUrl.value}/v1/sessions/${localSessionId.value}/documents`, { method: "POST", headers: localHeaders(), body: form, credentials: "omit", mode: "cors" });
  const result = await parseLocalResponse<{ document_id: string }>(response);
  item.documentId = result.document_id; item.state = "queued"; item.progress = 12; item.message = "已进入本机队列";
}
async function startJob() {
  if (!canStart.value) { Message.warning("请添加与当前功能匹配的文件"); return }
  busy.value = true; errorCode.value = ""; errorDetail.value = ""; activeJob.value = null;
  try {
    await cleanupLocalSession();
    for (const item of files.value) { item.documentId = ""; item.state = "ready"; item.progress = 0; item.message = "等待处理" }
    await ensureLocalSession();
    for (const item of files.value) await uploadFile(item);
    const response = await window.fetch(`${localBaseUrl.value}/v1/sessions/${localSessionId.value}/jobs`, {
      method: "POST", headers: { ...localHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ feature_id: selectedFeature.value, document_ids: files.value.map((item) => item.documentId), options: requestOptions() }), credentials: "omit", mode: "cors",
    });
    activeJob.value = await parseLocalResponse<JobResponse>(response); pollJob();
  } catch (error) { showFailure(error); busy.value = false }
}
function syncQueue(job: JobResponse) {
  for (const jobItem of job.items) {
    const queueItem = files.value.find((item) => item.documentId === jobItem.document_id);
    if (queueItem) { queueItem.state = jobItem.state; queueItem.progress = jobItem.progress; queueItem.message = jobItem.message }
  }
}
async function pollJob() {
  if (!activeJob.value || !localSessionId.value) return;
  try {
    const response = await window.fetch(`${localBaseUrl.value}/v1/sessions/${localSessionId.value}/jobs/${activeJob.value.id}`, { headers: localHeaders(), cache: "no-store", credentials: "omit", mode: "cors" });
    const job = await parseLocalResponse<JobResponse>(response); activeJob.value = job; syncQueue(job);
    if (["queued", "running"].includes(job.state)) pollTimer = window.setTimeout(() => void pollJob(), 300);
    else { busy.value = false; if (job.state === "completed") Message.success(job.message); else if (job.state === "completed_with_errors") Message.warning(job.message) }
  } catch (error) { showFailure(error); busy.value = false }
}
async function cancelJob() {
  if (!activeJob.value || !busy.value) return;
  try {
    await window.fetch(`${localBaseUrl.value}/v1/sessions/${localSessionId.value}/jobs/${activeJob.value.id}/cancel`, { method: "POST", headers: localHeaders(), credentials: "omit", mode: "cors" });
    Message.info("正在安全停止任务");
  } catch (error) { showFailure(error) }
}
async function downloadOutput(output: JobOutput) {
  if (!activeJob.value) return;
  try {
    const response = await window.fetch(`${localBaseUrl.value}/v1/sessions/${localSessionId.value}/jobs/${activeJob.value.id}/outputs/${output.id}`, { headers: localHeaders(), credentials: "omit", mode: "cors" });
    if (!response.ok) await parseLocalResponse<never>(response);
    const blob = await response.blob();
    if (outputDirectory.value) {
      const handle = await outputDirectory.value.getFileHandle(output.filename, { create: true });
      const writable = await handle.createWritable(); await writable.write(blob); await writable.close();
    } else saveBlobDownload(blob, output.filename);
    output.downloaded = true; Message.success(`已保存：${output.filename}`);
  } catch (error) { showFailure(error) }
}
async function runSelfTest() {
  selfTestText.value = "正在检查内置引擎";
  try {
    await ensureLocalSession();
    const response = await window.fetch(`${localBaseUrl.value}/v1/self-test`, { cache: "no-store", credentials: "omit", mode: "cors" });
    const result = await parseLocalResponse<{ feature_count: number; capability_count: number; external_office_required: boolean }>(response);
    selfTestText.value = result.feature_count === 6 && result.capability_count === 25 && !result.external_office_required ? "6 项功能 / 25 项能力已就绪" : "能力清单不完整，请修复安装";
  } catch (error) { selfTestText.value = "内置引擎自检失败"; showFailure(error) }
}
function addReplaceRule() { if (options.rules.length < 100) options.rules.push({ mode: "text", find: "", replace: "", case_sensitive: false }) }
watch(selectedFeature, () => {
  if (busy.value) return;
  const incompatible = files.value.filter((item) => !currentFeature.value.accepts.includes(fileExtension(item.file.name))).length;
  if (incompatible) Message.info(`有 ${incompatible} 个文件不适用于当前功能，请移除后再开始`);
});
onMounted(() => { loadSavedReplacePlans(); updateClock(); clockTimer = window.setInterval(updateClock, 30_000); void runSelfTest() });
onBeforeUnmount(() => { window.clearTimeout(pollTimer); window.clearInterval(clockTimer); void cleanupLocalSession() });
</script>

<template>
  <div class="page official-format-page">
    <header class="page-header format-header">
      <div>
        <p class="page-kicker">朱批案台 · 当前电脑批量处理</p>
        <h1 class="page-title">公文规范排版</h1>
        <p class="page-description">新排版工具已完整内嵌。文件不出当前电脑，不打开 Word、WPS 或系统外窗口。</p>
      </div>
      <div class="format-header-actions">
        <button type="button" class="engine-self-test" @click="runSelfTest"><IconSafe /><span>{{ selfTestText }}</span></button>
        <PageHelp
          title="公文排版帮助"
          :tips="[
            '六类功能、25 项能力全部在 PartyOps 页面内执行，源文件默认不覆盖。',
            '文件只发送给当前电脑 127.0.0.1 的内置引擎，15 分钟无操作后自动清理临时副本。',
            '自动、Word、WPS 是输出兼容目标，不表示需要安装或启动对应软件。',
            '套红、复杂表格、扫描 PDF 和特殊版式仍应按最终打印页逐页核对。',
          ]"
        />
      </div>
    </header>

    <section class="format-workbench" aria-label="内嵌公文排版工作台">
      <nav class="feature-rail" aria-label="排版功能">
        <button v-for="feature in features" :key="feature.id" type="button" :class="{ active: selectedFeature === feature.id }" :aria-current="selectedFeature === feature.id ? 'page' : undefined" :disabled="busy" @click="selectedFeature = feature.id">
          <span>{{ feature.display_name }}</span><small>{{ capabilityCount(feature) }}</small>
        </button>
      </nav>

      <div class="format-columns">
        <aside class="format-queue-panel">
          <div class="panel-heading"><div><strong>待处理文件</strong><span>{{ files.length }}/50</span></div><button v-if="files.length" type="button" :disabled="busy" @click="clearFiles">清空</button></div>
          <input ref="fileInput" class="visually-hidden" type="file" multiple :accept="accept" @change="onFileChange" />
          <button type="button" class="format-dropzone" :class="{ dragging }" :disabled="busy" @click="chooseFiles" @dragenter.prevent="dragging = true" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
            <IconUpload /><strong>点击或拖入文件</strong><span>{{ currentFeature.accepts.join(' · ').toUpperCase() }}</span>
          </button>
          <div v-if="files.length" class="format-file-list" role="list">
            <article v-for="(item, index) in files" :key="item.key" class="format-file-row" role="listitem" :data-state="item.state">
              <IconFile class="format-file-icon" /><div><strong :title="item.file.name">{{ item.file.name }}</strong><span>{{ (item.file.size / 1024).toFixed(0) }} KiB · {{ item.message }}</span><a-progress v-if="item.progress" :percent="item.progress / 100" :show-text="false" size="small" /></div><button type="button" aria-label="移除文件" :disabled="busy" @click="removeFile(index)">×</button>
            </article>
          </div>
          <div class="output-location"><span>输出位置</span><strong :title="outputLocationLabel">{{ outputLocationLabel }}</strong><button type="button" :disabled="busy" @click="selectOutputDirectory">更改</button></div>
        </aside>

        <main class="format-main-panel">
          <div class="format-focus-heading">
            <div><span>{{ currentFeature.display_name }}</span><h2>{{ files[0]?.file.name || '先添加一份待处理公文' }}</h2><p>{{ currentFeature.notes }}</p></div>
            <span class="compatibility-badge">{{ options.compatibility_mode === 'auto' ? '自动兼容' : `${options.compatibility_mode.toUpperCase()} 兼容` }}</span>
          </div>
          <div v-if="errorDetail" class="format-error" role="alert"><IconExclamationCircle /><div><strong>{{ errorCode }}</strong><span>{{ errorDetail }}</span></div></div>
          <div v-if="activeJob" class="job-stage" :data-state="activeJob.state">
            <div class="job-progress-heading"><div><span>本次批量任务</span><strong>{{ activeJob.message }}</strong></div><b>{{ activeJob.progress }}%</b></div>
            <a-progress :percent="activeJob.progress / 100" :show-text="false" />
            <div class="job-summary"><span><IconCheck /> 已完成 {{ completedCount }}</span><span v-if="failedCount"><IconExclamationCircle /> 失败 {{ failedCount }}</span><span>全部过程仅在当前电脑</span></div>
            <div v-if="activeJob.outputs.length" class="format-results">
              <button v-for="output in activeJob.outputs" :key="output.id" type="button" @click="downloadOutput(output)"><IconDownload /><span><strong>{{ output.filename }}</strong><small>{{ output.downloaded ? '已保存，可再次下载' : '点击保存结果' }}</small></span></button>
            </div>
          </div>
          <div v-else class="diagnosis-placeholder">
            <div class="diagnosis-register"><span>01</span><strong>添加文件</strong><small>最多 50 个，只读处理源文件</small></div>
            <div class="diagnosis-register"><span>02</span><strong>核对参数</strong><small>原工具功能参数完整保留</small></div>
            <div class="diagnosis-register"><span>03</span><strong>开始处理</strong><small>进度、日志和结果留在本页</small></div>
          </div>
          <div v-if="selectedReport" class="format-report-summary"><div><span>段落</span><strong>{{ selectedReport.paragraph_count }}</strong></div><div><span>表格</span><strong>{{ selectedReport.table_count }}</strong></div><div><span>已调整</span><strong>{{ selectedReport.changed_count }}</strong></div><div><span>复核项</span><strong>{{ selectedReport.issues.length }}</strong></div></div>
          <div class="format-primary-actions"><button v-if="busy" type="button" class="format-cancel-button" @click="cancelJob">安全停止</button><button type="button" class="format-start-button" :disabled="!canStart" @click="startJob"><IconRefresh v-if="busy" class="spin" /><span>{{ busy ? '处理中…' : `开始${currentFeature.display_name.replace('一键', '')}` }}</span></button></div>
        </main>

        <aside class="format-settings-panel">
          <div class="panel-heading"><div><strong>功能参数</strong><span>{{ currentCapabilities.length }} 项能力</span></div></div>
          <div class="setting-group"><label>兼容模式</label><a-select v-model="options.compatibility_mode" :disabled="busy"><a-option value="auto">自动兼容</a-option><a-option value="word">Word 兼容</a-option><a-option value="wps">WPS 兼容</a-option></a-select><small>只决定输出兼容性，不启动外部办公软件。</small></div>

          <template v-if="selectedFeature === 'format'">
            <div class="setting-group"><label>排版模板</label><a-input v-model="options.template" :disabled="busy" /></div>
            <div class="setting-group"><label>执行范围</label><a-select v-model="options.scope" :disabled="busy"><a-option value="full">全文</a-option><a-option value="selection">段落范围</a-option><a-option value="compilation">汇编文章</a-option></a-select></div>
            <div v-if="options.scope !== 'full'" class="setting-inline"><a-input-number v-model="options.start_paragraph" :min="1" /><span>至</span><a-input-number v-model="options.end_paragraph" :min="1" /></div>
          </template>
          <template v-else-if="selectedFeature === 'replace'">
            <div class="setting-group"><label>替换方案</label><a-input v-model="options.plan_name" :disabled="busy" /></div>
            <div v-if="savedReplacePlans.length" class="setting-group"><label>已保存方案</label><a-select :model-value="options.plan_name" :disabled="busy" @change="loadReplacePlan"><a-option v-for="plan in savedReplacePlans" :key="plan.name" :value="plan.name">{{ plan.name }}</a-option></a-select></div>
            <div class="saved-plan-actions"><button type="button" :disabled="busy" @click="saveReplacePlan">保存当前方案</button><button type="button" :disabled="busy" @click="deleteReplacePlan">删除当前方案</button></div>
            <div v-for="(rule, index) in options.rules" :key="index" class="replace-rule"><a-select v-model="rule.mode" :disabled="busy"><a-option value="text">文字</a-option><a-option value="regex">正则</a-option><a-option value="wildcard">通配符</a-option><a-option value="format">格式</a-option></a-select><a-input v-model="rule.find" placeholder="查找内容或格式条件" :disabled="busy" /><template v-if="rule.mode === 'format'"><a-input v-model="rule.font_name" placeholder="目标字体，如 黑体" :disabled="busy" /><a-input-number v-model="rule.font_size" :min="5" :max="72" placeholder="字号" :disabled="busy" /><a-select v-model="rule.alignment" :disabled="busy"><a-option value="">保持对齐</a-option><a-option value="left">左对齐</a-option><a-option value="center">居中</a-option><a-option value="right">右对齐</a-option><a-option value="justify">两端对齐</a-option></a-select></template><a-input v-else v-model="rule.replace" placeholder="替换为" :disabled="busy" /><button type="button" :disabled="busy || options.rules.length === 1" @click="options.rules.splice(index, 1)">移除</button></div>
            <button type="button" class="setting-add" :disabled="busy" @click="addReplaceRule">＋ 添加规则</button>
          </template>
          <template v-else-if="selectedFeature === 'redheader'">
            <div class="setting-group"><label>公文类型</label><a-select v-model="options.document_type"><a-option value="down">下行文</a-option><a-option value="up">上行文</a-option><a-option value="letter">便函</a-option></a-select></div>
            <div class="setting-group"><label>发文机关</label><a-input v-model="options.agency" /></div><div class="setting-group"><label>发文字号</label><a-input v-model="options.document_number" /></div>
            <div class="setting-inline"><a-input v-model="options.copy_number" placeholder="份号" /><a-input v-model="options.security" placeholder="密级与期限" /></div><div class="setting-inline"><a-input v-model="options.urgency" placeholder="紧急程度" /><a-input v-model="options.signatory" placeholder="签发人" /></div>
            <div class="setting-group"><label>版记</label><a-input v-model="options.imprint" /></div>
          </template>
          <template v-else-if="selectedFeature === 'rename'">
            <div class="setting-group"><label>命名部件</label><a-checkbox-group v-model="options.parts" direction="vertical"><a-checkbox value="title">标题</a-checkbox><a-checkbox value="document_number">发文字号</a-checkbox><a-checkbox value="subtitle">副标题</a-checkbox><a-checkbox value="date">日期</a-checkbox><a-checkbox value="custom">自定义文字</a-checkbox><a-checkbox value="rotation">轮替词</a-checkbox></a-checkbox-group></div>
            <div class="setting-group"><label>自定义文字</label><a-input v-model="options.custom_text" /></div><div class="setting-inline"><a-input v-model="options.separator" placeholder="分隔符" /><a-input v-model="options.rotation_words" placeholder="轮替词，以 | 分隔" /></div>
          </template>
          <template v-else-if="selectedFeature === 'convert'">
            <div class="setting-group"><label>输出格式</label><a-select v-model="options.target_format"><a-option value="docx">DOCX</a-option><a-option value="pdf">PDF</a-option><a-option value="txt">TXT</a-option><a-option value="png">PNG</a-option><a-option value="jpg">JPG</a-option></a-select></div>
            <template v-if="['png', 'jpg'].includes(options.target_format)"><div class="setting-group"><label>图片模式</label><a-select v-model="options.image_mode"><a-option value="pages">分页图片</a-option><a-option value="long">长图</a-option></a-select></div><div class="setting-group"><label>页码范围</label><a-input v-model="options.page_selection" placeholder="all 或 1-3,5" /></div><div class="setting-group"><label>清晰度</label><a-input-number v-model="options.dpi" :min="72" :max="600" /><small>72—600 DPI</small></div></template>
          </template>
          <template v-else><div class="setting-group"><a-checkbox v-model="options.normalize_punctuation">规范中文标点</a-checkbox><a-checkbox v-model="options.reconstruct_tables">识别并重建表格</a-checkbox></div></template>
          <div class="setting-group"><label>同名文件</label><a-select v-model="options.same_name_policy" :disabled="busy"><a-option value="auto-rename">自动编号</a-option><a-option value="overwrite">覆盖本次输出</a-option><a-option value="skip">跳过</a-option></a-select></div>
          <details class="capability-list"><summary>查看 {{ capabilityCount(currentFeature) }} 项能力契约</summary><ol><li v-for="item in currentCapabilities" :key="item.capability_id"><code>{{ item.capability_id }}</code><span>{{ item.description }}</span></li></ol></details>
          <div class="estimated-result"><span>预计结果</span><strong>{{ files.length }} 个源文件副本</strong><small>源文件不覆盖 · 临时目录自动清理</small></div>
        </aside>
      </div>
    </section>
    <footer class="format-privacy-ledger"><span><IconSafe /> 文档字节、文件名、路径与结果不进入服务器、数据库或协同链路。</span><time>北京时间 {{ beijingClock }}</time></footer>
  </div>
</template>

<style scoped>
.official-format-page{max-width:none}.format-header{align-items:flex-start}.format-header-actions{display:flex;align-items:center;gap:10px}.engine-self-test{display:inline-flex;min-height:34px;align-items:center;gap:7px;padding:0 11px;border:1px solid var(--color-border-2);border-radius:2px;background:rgba(255,250,241,.84);color:var(--color-text-2);cursor:pointer}.format-workbench{overflow:hidden;border:1px solid var(--color-border-2);border-radius:2px;background:rgba(255,252,246,.92);box-shadow:0 12px 32px rgba(72,48,34,.06)}
.feature-rail{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));min-height:66px;border-bottom:1px solid var(--color-border-2)}.feature-rail button{position:relative;display:flex;align-items:center;justify-content:center;gap:9px;border:0;border-right:1px solid var(--color-border-2);background:transparent;color:var(--color-text-2);cursor:pointer;font-size:14px}.feature-rail button:last-child{border-right:0}.feature-rail button:after{position:absolute;right:14px;bottom:-1px;left:14px;height:2px;background:#b42318;content:"";opacity:0}.feature-rail button.active{color:#a51f16;font-weight:650}.feature-rail button.active:after{opacity:1}.feature-rail small{min-width:19px;padding:1px 5px;border:1px solid currentColor;border-radius:999px;font-size:10px;opacity:.66}
.format-columns{display:grid;min-height:610px;grid-template-columns:minmax(238px,.76fr) minmax(420px,1.55fr) minmax(276px,.84fr)}.format-queue-panel,.format-settings-panel{min-width:0;padding:18px;background:rgba(253,248,239,.54)}.format-queue-panel{border-right:1px solid var(--color-border-2)}.format-settings-panel{border-left:1px solid var(--color-border-2)}.format-main-panel{min-width:0;padding:22px 28px}.panel-heading{display:flex;min-height:32px;align-items:flex-start;justify-content:space-between;margin-bottom:14px}.panel-heading>div{display:flex;align-items:baseline;gap:8px}.panel-heading strong{font-family:var(--font-serif,"Songti SC",serif);font-size:16px}.panel-heading span{color:var(--color-text-3);font-size:11px}.panel-heading button,.output-location button,.replace-rule button,.setting-add{border:0;background:transparent;color:#a51f16;cursor:pointer;font-size:12px}
.format-dropzone{display:grid;width:100%;min-height:118px;place-items:center;align-content:center;gap:6px;border:1px dashed #c8b5a4;border-radius:2px;background:rgba(255,252,246,.7);color:var(--color-text-2);cursor:pointer}.format-dropzone.dragging{border-color:#b42318;background:rgba(180,35,24,.04)}.format-dropzone svg{color:#b42318;font-size:22px}.format-dropzone span{color:var(--color-text-3);font-size:10px}.format-file-list{display:grid;gap:8px;max-height:342px;margin-top:12px;overflow-y:auto}.format-file-row{display:grid;grid-template-columns:25px minmax(0,1fr) 20px;gap:8px;align-items:start;padding:10px;border:1px solid var(--color-border-2);border-radius:2px;background:rgba(255,255,255,.42)}.format-file-row[data-state=failed]{border-color:rgba(180,35,24,.38)}.format-file-row[data-state=completed]{border-color:rgba(36,125,76,.32)}.format-file-icon{margin-top:2px;color:#315eb2;font-size:19px}.format-file-row>div{min-width:0}.format-file-row strong,.format-file-row span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.format-file-row strong{font-size:12px}.format-file-row span{margin:3px 0 5px;color:var(--color-text-3);font-size:10px}.format-file-row>button{border:0;background:transparent;color:var(--color-text-3);cursor:pointer}.output-location{display:grid;margin-top:16px;padding-top:14px;grid-template-columns:1fr auto;gap:4px 8px;border-top:1px solid var(--color-border-2)}.output-location span{grid-column:1/-1;color:var(--color-text-3);font-size:10px}.output-location strong{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}
.format-focus-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding-bottom:18px;border-bottom:1px solid var(--color-border-2)}.format-focus-heading span{color:#a51f16;font-size:11px;letter-spacing:.1em}.format-focus-heading h2{overflow:hidden;max-width:560px;margin:5px 0;font-family:var(--font-serif,"Songti SC",serif);font-size:20px;text-overflow:ellipsis;white-space:nowrap}.format-focus-heading p{margin:0;color:var(--color-text-3);font-size:12px;line-height:1.65}.compatibility-badge{flex:0 0 auto;padding:5px 8px;border:1px solid var(--color-border-2);color:var(--color-text-2)!important;letter-spacing:0!important}.format-error{display:flex;gap:10px;margin-top:18px;padding:12px 14px;border:1px solid rgba(180,35,24,.26);border-left:3px solid #b42318;background:rgba(180,35,24,.035);color:#a51f16}.format-error strong,.format-error span{display:block}.format-error span{margin-top:3px;color:var(--color-text-2);font-size:12px}
.job-stage{margin-top:22px;padding:20px;border:1px solid var(--color-border-2);background:rgba(255,255,255,.35)}.job-progress-heading{display:flex;justify-content:space-between;margin-bottom:13px}.job-progress-heading span,.job-progress-heading strong{display:block}.job-progress-heading span{color:var(--color-text-3);font-size:10px}.job-progress-heading strong{margin-top:4px;font-family:var(--font-serif,"Songti SC",serif);font-size:17px}.job-progress-heading b{color:#b42318;font-family:ui-monospace,monospace;font-size:24px}.job-summary{display:flex;flex-wrap:wrap;gap:20px;margin-top:12px;color:var(--color-text-3);font-size:11px}.job-summary span{display:inline-flex;align-items:center;gap:5px}.format-results{display:grid;gap:8px;margin-top:18px}.format-results button{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--color-border-2);border-radius:2px;background:rgba(255,250,241,.86);color:var(--color-text-1);cursor:pointer;text-align:left}.format-results button>span{display:grid;gap:2px}.format-results small{color:var(--color-text-3)}
.diagnosis-placeholder{display:grid;gap:0;margin-top:24px;border-top:1px solid var(--color-border-2)}.diagnosis-register{display:grid;min-height:72px;grid-template-columns:40px 120px 1fr;align-items:center;border-bottom:1px solid var(--color-border-2)}.diagnosis-register>span{color:#b42318;font-family:ui-monospace,monospace;font-size:11px}.diagnosis-register strong{font-family:var(--font-serif,"Songti SC",serif);font-size:15px}.diagnosis-register small{color:var(--color-text-3)}.format-report-summary{display:grid;margin-top:18px;grid-template-columns:repeat(4,1fr);border:1px solid var(--color-border-2)}.format-report-summary div{display:grid;gap:4px;padding:10px 12px;border-right:1px solid var(--color-border-2)}.format-report-summary div:last-child{border-right:0}.format-report-summary span{color:var(--color-text-3);font-size:10px}.format-report-summary strong{font-size:16px}.format-primary-actions{display:flex;align-items:center;justify-content:center;gap:12px;margin-top:28px}.format-start-button,.format-cancel-button{min-width:158px;min-height:44px;border-radius:2px;cursor:pointer}.format-start-button{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid #991b13;background:#b42318;color:#fffaf1;font-family:var(--font-serif,"Songti SC",serif);font-size:15px}.format-start-button:disabled{cursor:not-allowed;opacity:.45}.format-cancel-button{border:1px solid var(--color-border-2);background:transparent;color:var(--color-text-2)}
.setting-group{display:grid;gap:7px;margin-bottom:15px}.setting-group>label{color:var(--color-text-2);font-size:12px;font-weight:650}.setting-group>small{color:var(--color-text-3);font-size:10px;line-height:1.5}.setting-inline{display:grid;margin-bottom:15px;grid-template-columns:1fr auto 1fr;gap:7px;align-items:center}.saved-plan-actions{display:flex;gap:12px;margin:-4px 0 12px}.saved-plan-actions button{border:0;background:transparent;color:#a51f16;cursor:pointer;font-size:11px}.replace-rule{display:grid;gap:6px;margin-bottom:10px;padding:10px;border:1px solid var(--color-border-2)}.replace-rule button{justify-self:end}.setting-add{margin:-2px 0 15px}.capability-list{margin-top:18px;padding-top:14px;border-top:1px solid var(--color-border-2)}.capability-list summary{color:#a51f16;cursor:pointer;font-size:11px}.capability-list ol{display:grid;gap:9px;max-height:220px;margin:12px 0 0;padding-left:17px;overflow-y:auto}.capability-list li{color:var(--color-text-3);font-size:10px;line-height:1.45}.capability-list code,.capability-list span{display:block}.capability-list code{color:var(--color-text-2)}.estimated-result{display:grid;gap:4px;margin-top:18px;padding:12px;border:1px solid var(--color-border-2);background:rgba(255,250,241,.72)}.estimated-result span,.estimated-result small{color:var(--color-text-3);font-size:10px}.estimated-result strong{font-size:13px}.format-privacy-ledger{display:flex;min-height:48px;align-items:center;justify-content:space-between;gap:20px;margin-top:12px;padding:0 14px;border:1px solid var(--color-border-2);background:rgba(255,250,241,.68);color:var(--color-text-3);font-size:11px}.format-privacy-ledger span{display:inline-flex;align-items:center;gap:7px}.spin{animation:format-spin .8s linear infinite}@keyframes format-spin{to{transform:rotate(360deg)}}
@media(max-width:1180px){.format-columns{grid-template-columns:245px minmax(0,1fr)}.format-settings-panel{grid-column:1/-1;border-top:1px solid var(--color-border-2);border-left:0}}@media(max-width:760px){.feature-rail{grid-template-columns:repeat(3,1fr)}.feature-rail button{min-height:54px;border-bottom:1px solid var(--color-border-2)}.format-columns{display:block}.format-queue-panel,.format-settings-panel{border:0;border-bottom:1px solid var(--color-border-2)}.format-main-panel{padding:18px}.format-header-actions,.format-privacy-ledger{align-items:flex-start;flex-direction:column}.format-privacy-ledger{padding:12px}}@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
