<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
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

interface FormatIssue {
  code: string;
  severity: "error" | "warning";
  title: string;
  detail: string;
  clause: string;
}

interface FormatReport {
  compliant: boolean;
  paragraph_count: number;
  table_count: number;
  changed_count: number;
  issues: FormatIssue[];
}

interface TicketResponse {
  ticket: string;
  expires_at: string;
  local_base_url: string;
}

interface SessionResponse {
  session_id: string;
  session_token: string;
  expires_in_seconds: number;
}

class LocalFormatError extends Error {
  code: string;

  constructor(code: string, detail: string) {
    super(detail);
    this.code = code;
  }
}

const fileInput = ref<HTMLInputElement | null>(null);
const selectedFile = ref<File | null>(null);
const busy = ref(false);
const stage = ref<"select" | "diagnosed" | "formatted">("select");
const report = ref<FormatReport | null>(null);
const converted = ref(false);
const errorCode = ref("");
const errorDetail = ref("");
const localBaseUrl = ref("");
const localSessionId = ref("");
const localSessionToken = ref("");
const documentId = ref("");

const currentStep = computed(() => stage.value === "select" ? 1 : stage.value === "diagnosed" ? 2 : 4);
const blockingIssues = computed(() => report.value?.issues.filter((item) => item.severity === "error").length || 0);
const safeOutputName = computed(() => {
  const original = selectedFile.value?.name || "公文.docx";
  const stem = original.replace(/\.[^.]+$/, "").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").slice(0, 80) || "公文";
  return `${stem}-公文规范版.docx`;
});

function chooseFile() {
  fileInput.value?.click();
}

async function parseLocalResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  let payload: Record<string, unknown> = {};
  try {
    payload = await response.json() as Record<string, unknown>;
  } catch {
    payload = { code: "LOCAL_HELPER_RESPONSE_INVALID", detail: `本机服务返回异常（${response.status}）` };
  }
  throw new LocalFormatError(
    String(payload.code || "LOCAL_FORMAT_FAILED"),
    String(payload.detail || payload.title || "本机排版未完成"),
  );
}

function localHeaders(): HeadersInit {
  return { "X-PartyOps-Local-Token": localSessionToken.value };
}

async function createLocalSession() {
  const ticket = await api.post<TicketResponse>("/official-format/local-ticket", {
    origin: window.location.origin,
  });
  localBaseUrl.value = ticket.local_base_url;
  let health: Response;
  try {
    health = await window.fetch(`${ticket.local_base_url}/health`, {
      cache: "no-store",
      credentials: "omit",
      mode: "cors",
    });
  } catch {
    throw new LocalFormatError(
      "LOCAL_HELPER_UNREACHABLE",
      "当前电脑的公文排版服务未启动。请重新打开 PartyOps；如仍失败，请查看 official-format.log。",
    );
  }
  if (!health.ok) {
    throw new LocalFormatError("LOCAL_HELPER_HEALTH_FAILED", "本机排版服务健康检查未通过，请重新打开 PartyOps。");
  }
  const response = await window.fetch(`${ticket.local_base_url}/v1/sessions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${ticket.ticket}` },
    credentials: "omit",
    mode: "cors",
  });
  const session = await parseLocalResponse<SessionResponse>(response);
  localSessionId.value = session.session_id;
  localSessionToken.value = session.session_token;
}

async function cleanupLocalSession() {
  if (!localBaseUrl.value || !localSessionId.value || !localSessionToken.value) return;
  const url = `${localBaseUrl.value}/v1/sessions/${localSessionId.value}`;
  const headers = localHeaders();
  localSessionId.value = "";
  localSessionToken.value = "";
  documentId.value = "";
  try {
    await window.fetch(url, { method: "DELETE", headers, credentials: "omit", mode: "cors", keepalive: true });
  } catch {
    // 服务端仍会在 15 分钟空闲期限到达后清理，不把取消失败升级为业务错误。
  }
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const next = input.files?.[0] || null;
  input.value = "";
  if (!next) return;
  await cleanupLocalSession();
  selectedFile.value = next;
  stage.value = "select";
  report.value = null;
  converted.value = false;
  errorCode.value = "";
  errorDetail.value = "";
}

function showFailure(error: unknown) {
  const current = error as { code?: string; message?: string };
  errorCode.value = current?.code || "LOCAL_FORMAT_FAILED";
  errorDetail.value = current?.message || "本机排版未完成，请重试。";
}

async function diagnose() {
  if (!selectedFile.value) {
    Message.warning("请先选择 DOC、DOCX 或 WPS 文件");
    return;
  }
  if (selectedFile.value.size > 50 * 1024 * 1024) {
    showFailure(new LocalFormatError("FILE_SIZE_LIMIT", "单个文件不得超过 50 MiB。"));
    return;
  }
  busy.value = true;
  errorCode.value = "";
  errorDetail.value = "";
  try {
    await cleanupLocalSession();
    await createLocalSession();
    const form = new FormData();
    form.append("document", selectedFile.value);
    const response = await window.fetch(
      `${localBaseUrl.value}/v1/sessions/${localSessionId.value}/diagnose`,
      { method: "POST", headers: localHeaders(), body: form, credentials: "omit", mode: "cors" },
    );
    const result = await parseLocalResponse<{ converted: boolean; document_id: string; report: FormatReport }>(response);
    converted.value = result.converted;
    documentId.value = result.document_id;
    report.value = result.report;
    stage.value = "diagnosed";
  } catch (error) {
    showFailure(error);
    await cleanupLocalSession();
  } finally {
    busy.value = false;
  }
}

async function formatDocument() {
  if (!localSessionId.value || !documentId.value) return;
  busy.value = true;
  errorCode.value = "";
  errorDetail.value = "";
  try {
    const response = await window.fetch(
      `${localBaseUrl.value}/v1/sessions/${localSessionId.value}/documents/${documentId.value}/format`,
      { method: "POST", headers: localHeaders(), credentials: "omit", mode: "cors" },
    );
    const result = await parseLocalResponse<{ document_id: string; report: FormatReport }>(response);
    report.value = result.report;
    stage.value = "formatted";
  } catch (error) {
    showFailure(error);
  } finally {
    busy.value = false;
  }
}

async function downloadResult() {
  if (!localSessionId.value || !documentId.value) return;
  busy.value = true;
  try {
    const response = await window.fetch(
      `${localBaseUrl.value}/v1/sessions/${localSessionId.value}/documents/${documentId.value}/download`,
      { headers: localHeaders(), credentials: "omit", mode: "cors" },
    );
    if (!response.ok) await parseLocalResponse<never>(response);
    const blob = await response.blob();
    saveBlobDownload(blob, safeOutputName.value);
    localSessionId.value = "";
    localSessionToken.value = "";
    documentId.value = "";
    Message.success("公文规范版已导出，本机临时副本已清理");
  } catch (error) {
    showFailure(error);
  } finally {
    busy.value = false;
  }
}

async function reset() {
  await cleanupLocalSession();
  selectedFile.value = null;
  report.value = null;
  converted.value = false;
  errorCode.value = "";
  errorDetail.value = "";
  stage.value = "select";
}

onBeforeUnmount(() => {
  void cleanupLocalSession();
});
</script>

<template>
  <div class="page official-format-page">
    <header class="page-header format-header">
      <div>
        <p class="page-kicker">资料 · 当前电脑一次性处理</p>
        <h1 class="page-title">公文规范排版</h1>
        <p class="page-description">文件不离开当前电脑，在本页完成诊断、确认、排版、复检和导出。</p>
      </div>
      <PageHelp
        title="公文规范排版怎么用"
        :tips="[
          '在本页选择 DOC、DOCX 或 WPS 文件；主机、协同机服务器和 AI 服务都不会收到文件。',
          '先核对诊断结果，再执行 GB/T 9704-2012 单一预设；不确定项必须人工复核。',
          '导出后仍须由公文责任人终审；系统不判断公文内容、政治表述和审批程序。',
        ]"
        help-query="公文规范排版"
      />
    </header>

    <a-alert class="classified-warning" type="error" show-icon>
      <template #title>安全提醒</template>
      不建议在涉密、敏感电脑上使用本功能，也不得使用 PartyOps 处理涉密文件。
    </a-alert>

    <section class="format-shell">
      <a-steps :current="currentStep" size="small" class="format-steps">
        <a-step title="选择文件" />
        <a-step title="格式诊断" />
        <a-step title="确认排版" />
        <a-step title="复检导出" />
      </a-steps>

      <div v-if="errorCode" class="format-error" role="alert">
        <IconExclamationCircle />
        <div><strong>{{ errorDetail }}</strong><span>诊断码 {{ errorCode }} · 日志位于 PartyOps 数据目录 logs/official-format.log</span></div>
      </div>

      <div v-if="stage === 'select'" class="format-grid">
        <article class="select-panel">
          <div class="panel-number">9704</div>
          <div class="local-badge"><IconSafe /> 本机回环处理 · 不上传</div>
          <h2>选择待排版公文</h2>
          <p>只接受 DOC、DOCX、WPS，单文件上限 50 MiB；原文件永不覆盖。</p>
          <input ref="fileInput" class="hidden-input" type="file" accept=".doc,.docx,.wps" @change="onFileChange">
          <button class="file-picker" type="button" @click="chooseFile">
            <IconUpload /><span><strong>{{ selectedFile?.name || '选择本机文件' }}</strong><small>{{ selectedFile ? `${(selectedFile.size / 1024 / 1024).toFixed(2)} MiB` : '文件名与内容不会发送到 PartyOps 主机' }}</small></span>
          </button>
          <a-button type="primary" size="large" :loading="busy" :disabled="!selectedFile" @click="diagnose">在本页开始诊断</a-button>
        </article>

        <div class="rules-panel">
          <header><span>唯一格式预设</span><h2>GB/T 9704-2012 公文格式</h2></header>
          <div class="rule-grid">
            <article><b>01</b><strong>页面与版心</strong><p>A4、天头、订口、版心、行数与字数按标准校准。</p></article>
            <article><b>02</b><strong>标题与正文</strong><p>识别标题、一至四级标题、正文、附件、落款、日期和版记。</p></article>
            <article><b>03</b><strong>段落与标点</strong><p>规范行距、缩进、序号和中文标点，同时保护网址、金额与法规条号。</p></article>
            <article><b>04</b><strong>页码与表格</strong><p>检查页码、表格结构、合并关系、字体、边框和对齐。</p></article>
          </div>
          <div class="format-boundary"><IconFile /><p><strong>格式边界</strong><span>DOCX 原生处理；DOC/WPS 需要本机 WPS、Office 或 LibreOffice。无法无损回写时只导出 DOCX。</span></p></div>
        </div>
      </div>

      <div v-else class="report-panel">
        <header class="report-header">
          <div><span>{{ stage === 'formatted' ? '排版后复检' : '排版前诊断' }}</span><h2>{{ selectedFile?.name }}</h2><p v-if="converted">原文件已由本机办公套件转换为 DOCX，原文件未修改。</p></div>
          <a-button @click="reset"><template #icon><IconRefresh /></template>重新选择</a-button>
        </header>
        <div class="report-summary">
          <div><span>正文段落</span><strong>{{ report?.paragraph_count || 0 }}</strong></div>
          <div><span>表格</span><strong>{{ report?.table_count || 0 }}</strong></div>
          <div><span>调整项</span><strong>{{ report?.changed_count || 0 }}</strong></div>
          <div><span>阻断问题</span><strong>{{ blockingIssues }}</strong></div>
        </div>
        <div v-if="!report?.issues.length" class="report-ok"><IconCheck />未发现阻断性版式问题，仍须由公文责任人完成最终复核。</div>
        <ul v-else class="issue-list">
          <li v-for="issue in report.issues" :key="`${issue.code}-${issue.clause}`" :class="issue.severity">
            <span>{{ issue.severity === 'error' ? '阻断' : '复核' }}</span>
            <div><strong>{{ issue.title }}</strong><p>{{ issue.detail }}</p><small>{{ issue.clause }} · {{ issue.code }}</small></div>
          </li>
        </ul>
        <footer class="report-actions">
          <div><strong>{{ stage === 'formatted' ? (report?.compliant ? '已完成机器复检' : '仍有需要人工处理的阻断项') : '确认诊断结果后再执行排版' }}</strong><span>机器校验不代替公文责任人的内容与审批终审。</span></div>
          <a-button v-if="stage === 'diagnosed'" type="primary" size="large" :loading="busy" @click="formatDocument">按国家标准一键排版</a-button>
          <a-button v-else type="primary" size="large" :loading="busy" @click="downloadResult"><template #icon><IconDownload /></template>导出公文规范版</a-button>
        </footer>
      </div>
    </section>

    <section class="privacy-ledger">
      <div><span>服务端存储</span><strong>无</strong><small>不写数据库、不生成主机档案</small></div>
      <div><span>网络边界</span><strong>127.0.0.1</strong><small>短时票据绑定用户、设备与页面来源</small></div>
      <div><span>原文件</span><strong>不覆盖</strong><small>导出增加“公文规范版”后缀</small></div>
      <div><span>临时副本</span><strong>15 分钟</strong><small>导出、取消、退出或空闲后清理</small></div>
    </section>
  </div>
</template>

<style scoped>
.official-format-page{max-width:1480px}.format-header{align-items:flex-end}.classified-warning{margin:0 0 18px;border-radius:0}.format-shell{border:1px solid var(--line);background:#fffaf0}.format-steps{padding:22px 32px;border-bottom:1px solid var(--line);background:#f7efe2}.format-grid{display:grid;grid-template-columns:410px minmax(0,1fr);min-height:560px}.select-panel{display:flex;align-items:flex-start;flex-direction:column;padding:38px;border-right:1px solid var(--line);background:linear-gradient(145deg,#f5ead8,#fffaf0 66%)}.panel-number{color:#a52b231c;font:700 86px/1 Georgia,serif}.local-badge{display:inline-flex;gap:7px;align-items:center;margin:16px 0 10px;color:#386047;font-size:12px;font-weight:700}.select-panel h2,.rules-panel h2,.report-header h2{margin:0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif}.select-panel h2{font-size:27px}.select-panel>p{margin:12px 0 18px;color:var(--muted);line-height:1.8}.hidden-input{display:none}.file-picker{display:flex;align-items:center;width:100%;gap:12px;margin-bottom:18px;padding:16px;border:1px dashed #bca88e;color:#5c4435;background:#fffdf8;text-align:left;cursor:pointer}.file-picker svg{flex:0 0 auto;color:#a52b23;font-size:22px}.file-picker span,.file-picker strong,.file-picker small{display:block;min-width:0}.file-picker strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-picker small{margin-top:3px;color:#897363;font-weight:400}.rules-panel{padding:34px}.rules-panel>header{padding-bottom:18px;border-bottom:2px solid #a52b23}.rules-panel>header span,.report-header>div>span{color:#a52b23;font-size:11px;letter-spacing:.12em}.rules-panel>header h2{margin-top:5px;font-size:24px}.rule-grid{display:grid;grid-template-columns:repeat(2,1fr);border-top:1px solid var(--line);border-left:1px solid var(--line);margin-top:24px}.rule-grid article{min-height:140px;padding:20px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);background:#fffdf8}.rule-grid b{display:block;color:#a52b23;font:700 12px Georgia,serif}.rule-grid strong{display:block;margin:9px 0;color:#4d382c}.rule-grid p{margin:0;color:var(--muted);font-size:12px;line-height:1.7}.format-boundary{display:grid;grid-template-columns:30px 1fr;gap:12px;margin-top:22px;padding:17px;border-left:3px solid #a6723f;background:#f5ead9;color:#74583f}.format-boundary svg{margin-top:4px}.format-boundary p,.format-boundary strong,.format-boundary span{display:block;margin:0}.format-boundary span{margin-top:4px;font-size:12px}.format-error{display:grid;grid-template-columns:24px 1fr;gap:12px;margin:18px 32px 0;padding:14px 16px;border-left:4px solid #a52b23;color:#74251f;background:#f7e9e4}.format-error svg{margin-top:3px}.format-error strong,.format-error span{display:block}.format-error span{margin-top:4px;font-size:12px}.report-panel{padding:32px}.report-header{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding-bottom:18px;border-bottom:2px solid #a52b23}.report-header h2{margin-top:4px;font-size:22px}.report-header p{margin:5px 0 0;color:var(--muted);font-size:12px}.report-summary{display:grid;grid-template-columns:repeat(4,1fr);margin:24px 0;border:1px solid var(--line)}.report-summary div{padding:16px;border-right:1px solid var(--line)}.report-summary div:last-child{border:0}.report-summary span,.report-summary strong{display:block}.report-summary span{color:var(--muted);font-size:11px}.report-summary strong{margin-top:4px;color:#8f2b25;font:700 24px Georgia,serif}.report-ok{display:flex;gap:10px;padding:16px;border-left:4px solid #4d7656;color:#31593b;background:#edf3e9}.issue-list{display:grid;gap:8px;margin:0;padding:0;list-style:none}.issue-list li{display:grid;grid-template-columns:58px 1fr;gap:12px;padding:14px;border:1px solid #ddcfbc;background:#fffdf8}.issue-list li>span{font-weight:700}.issue-list li.error>span{color:#a52b23}.issue-list li.warning>span{color:#9a681e}.issue-list strong,.issue-list p,.issue-list small{display:block;margin:0}.issue-list p,.issue-list small{color:#7b6859}.issue-list small{margin-top:4px;font-size:11px}.report-actions{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-top:24px;padding-top:20px;border-top:1px solid var(--line)}.report-actions strong,.report-actions span{display:block}.report-actions span{margin-top:4px;color:var(--muted);font-size:12px}.privacy-ledger{display:grid;grid-template-columns:repeat(4,1fr);margin-top:18px;border:1px solid var(--line);background:var(--line);gap:1px}.privacy-ledger div{padding:20px;background:#fffaf0}.privacy-ledger span,.privacy-ledger strong,.privacy-ledger small{display:block}.privacy-ledger span{color:var(--muted);font-size:11px}.privacy-ledger strong{margin:6px 0;color:#8f2b25;font:700 22px Georgia,"Noto Serif SC",serif}.privacy-ledger small{color:#887364}@media(max-width:980px){.format-grid{grid-template-columns:1fr}.select-panel{border-right:0;border-bottom:1px solid var(--line)}.privacy-ledger{grid-template-columns:repeat(2,1fr)}}@media(max-width:640px){.format-steps,.select-panel,.rules-panel,.report-panel{padding:22px}.rule-grid,.privacy-ledger,.report-summary{grid-template-columns:1fr}.report-summary div{border-right:0;border-bottom:1px solid var(--line)}.report-header,.report-actions{align-items:stretch;flex-direction:column}.panel-number{font-size:68px}}
</style>
