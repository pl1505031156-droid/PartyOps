<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import { useSessionStore } from "../stores/session";
import type { PartyDevelopmentResult, PartyDevelopmentRuleMetadata } from "../types";

type ActualDateKey =
  | "conversation_date" | "activist_date" | "publicity_start_date" | "development_object_date"
  | "training_completed_date" | "political_review_completed_date" | "pre_review_approved_date"
  | "branch_acceptance_date" | "committee_approval_date" | "oath_date"
  | "transition_application_date" | "transition_branch_meeting_date" | "transition_approval_date";

const session = useSessionStore();
const rule = ref<PartyDevelopmentRuleMetadata | null>(null);
const result = ref<PartyDevelopmentResult | null>(null);
const calculating = ref(false);
const exporting = ref(false);
const localWarning = ref("");
const form = reactive({
  name: "",
  application_date: "",
  actual_dates: {
    conversation_date: "", activist_date: "", publicity_start_date: "", development_object_date: "",
    training_completed_date: "", political_review_completed_date: "", pre_review_approved_date: "",
    branch_acceptance_date: "", committee_approval_date: "", oath_date: "",
    transition_application_date: "", transition_branch_meeting_date: "", transition_approval_date: "",
    training_days: null as number | null,
    training_hours: null as number | null,
  },
});
let draftTimer: number | null = null;

const dateFields: Array<{ key: ActualDateKey; label: string; phase: string; hint: string }> = [
  { key: "conversation_date", label: "实际谈话日期", phase: "申请入党", hint: "收到申请书后一个月内" },
  { key: "activist_date", label: "确定积极分子日期", phase: "培养考察", hint: "由组织研究后录入" },
  { key: "publicity_start_date", label: "发展对象公示开始", phase: "发展对象", hint: "系统按工作日计算不少于五日" },
  { key: "development_object_date", label: "确定发展对象日期", phase: "发展对象", hint: "培养考察满一年并完成公示后" },
  { key: "political_review_completed_date", label: "政治审查完成日期", phase: "发展对象", hint: "结论须人工确认" },
  { key: "training_completed_date", label: "集中培训完成日期", phase: "发展对象", hint: "不少于三天或二十四学时" },
  { key: "pre_review_approved_date", label: "预审合格日期", phase: "预审接收", hint: "从此日起一个月内提交支部大会" },
  { key: "branch_acceptance_date", label: "接收预备党员支部大会日期", phase: "预备党员", hint: "预备期从该日算起" },
  { key: "committee_approval_date", label: "党委审批日期", phase: "预备党员", hint: "一般三个月，最长六个月" },
  { key: "oath_date", label: "入党宣誓日期", phase: "预备党员", hint: "批复后一般一个月内" },
  { key: "transition_application_date", label: "转正申请日期", phase: "转正", hint: "预备期满后提出" },
  { key: "transition_branch_meeting_date", label: "转正支部大会日期", phase: "转正", hint: "由组织会议决定" },
  { key: "transition_approval_date", label: "转正审批日期", phase: "转正", hint: "转正决议三个月内审批" },
];

const groupedFields = computed(() => {
  const groups = new Map<string, typeof dateFields>();
  dateFields.forEach((field) => groups.set(field.phase, [...(groups.get(field.phase) || []), field]));
  return [...groups.entries()];
});
const phaseLabels = computed(() => rule.value?.phase_labels || {});

function draftKey() {
  const user = session.user?.id || "unknown";
  const device = session.runtimeContext?.device_id || `${session.runtimeContext?.node_mode || "unknown"}:${session.runtimeContext?.platform || "local"}`;
  return `partyops.party-development.draft.v1:${user}:${device}`;
}

function cleanActualDates() {
  return Object.fromEntries(Object.entries(form.actual_dates).filter(([, value]) => value !== "" && value !== null));
}

function payload() {
  return {
    name: form.name.trim(),
    application_date: form.application_date,
    actual_dates: cleanActualDates(),
  };
}

function saveDraft() {
  try {
    localStorage.setItem(draftKey(), JSON.stringify(form));
    localWarning.value = "";
  } catch {
    localWarning.value = "当前浏览器无法保存本机草稿。系统没有将姓名或日期上传到主机，请在关闭页面前完成导出。";
  }
}

function restoreDraft() {
  try {
    const encoded = localStorage.getItem(draftKey());
    if (!encoded) return;
    const saved = JSON.parse(encoded) as Partial<typeof form>;
    if (typeof saved.name === "string") form.name = saved.name;
    if (typeof saved.application_date === "string") form.application_date = saved.application_date;
    if (saved.actual_dates && typeof saved.actual_dates === "object") Object.assign(form.actual_dates, saved.actual_dates);
  } catch {
    localStorage.removeItem(draftKey());
    localWarning.value = "本机计算草稿已损坏，已停止读取；请重新录入。";
  }
}

function clearDraft() {
  localStorage.removeItem(draftKey());
  Object.assign(form, { name: "", application_date: "" });
  Object.keys(form.actual_dates).forEach((key) => {
    (form.actual_dates as Record<string, string | number | null>)[key] = key.startsWith("training_") && !key.endsWith("date") ? null : "";
  });
  result.value = null;
  Message.success("已清除当前电脑上的计算草稿");
}

async function calculate() {
  if (!form.name.trim() || !form.application_date) {
    Message.warning("请先填写姓名和入党申请书提交日期");
    return;
  }
  calculating.value = true;
  try {
    result.value = await api.post<PartyDevelopmentResult>("/party-development/calculate", payload());
    saveDraft();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "计算失败");
  } finally {
    calculating.value = false;
  }
}

async function exportWord() {
  if (!result.value) await calculate();
  if (!result.value) return;
  exporting.value = true;
  try {
    const blob = await api.post<Blob>("/party-development/export.docx", payload());
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${form.name.replace(/[\\/:*?"<>|]/g, "_") || "党员发展"}-党员发展时间节点.docx`;
    anchor.click();
    URL.revokeObjectURL(url);
    Message.success("Word 时间节点文档已生成");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "Word 导出失败");
  } finally {
    exporting.value = false;
  }
}

function nodeDate(node: PartyDevelopmentResult["nodes"][number]) {
  if (!node.date) return "等待组织研究或人工录入";
  const range = node.end_date ? `${node.date} 至 ${node.end_date}` : node.date;
  return node.provisional ? `${range}（暂算）` : range;
}

function dateKindLabel(kind: string) {
  return ({ actual: "实际日期", deadline: "法定截止", earliest: "最早日期", window: "建议窗口", manual: "人工确认", workday_window: "工作日区间" } as Record<string, string>)[kind] || kind;
}

async function load() {
  restoreDraft();
  try {
    rule.value = await api.get<PartyDevelopmentRuleMetadata>("/party-development/rules/current");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "规则版本读取失败");
  }
}

watch(form, () => {
  if (draftTimer) window.clearTimeout(draftTimer);
  draftTimer = window.setTimeout(saveDraft, 500);
}, { deep: true });
onMounted(load);
onBeforeUnmount(() => {
  if (draftTimer) window.clearTimeout(draftTimer);
  saveDraft();
});
</script>

<template>
  <div class="page development-page">
    <header class="page-header development-header">
      <div>
        <p class="page-kicker">工作 · 制度化辅助工具</p>
        <h1 class="page-title">发展党员时间计算</h1>
        <p class="page-description">输入申请书日期即可形成首轮时间轴；实际进度随时补录，系统据此重算并提示风险。</p>
      </div>
      <a-space><a-button @click="clearDraft">清除本机草稿</a-button><a-button :disabled="!result" :loading="exporting" @click="exportWord">导出 Word</a-button><a-button type="primary" :loading="calculating" @click="calculate">开始计算</a-button></a-space>
    </header>

    <a-alert v-if="rule" class="rule-alert" type="info" show-icon>
      <template #title>{{ rule.title }} · 规则版本 {{ rule.version }}</template>
      国家规则期限不可修改；组织研究、政治审查、预审和审批结论仍须人工确认。<a :href="rule.source_url" target="_blank" rel="noopener noreferrer">查看制度来源</a>
    </a-alert>
    <a-alert v-if="localWarning" class="rule-alert" type="warning" show-icon :title="localWarning" />

    <section class="calculator-layout">
      <aside class="input-panel">
        <div class="section-heading"><span>01</span><div><h2>先填两个信息</h2><p>草稿只保存在当前电脑，不建立人员发展档案。</p></div></div>
        <a-form :model="form" layout="vertical">
          <a-form-item label="姓名" required><a-input v-model="form.name" :max-length="80" allow-clear placeholder="例如：张三" /></a-form-item>
          <a-form-item label="入党申请书提交日期" required><a-date-picker v-model="form.application_date" value-format="YYYY-MM-DD" style="width: 100%" /></a-form-item>
        </a-form>
        <div class="privacy-note"><strong>隐私边界</strong><p>姓名与草稿不进入主机数据库；点击计算或导出时，仅用于本次运算，服务端不长期保存。</p></div>
        <div class="section-heading secondary"><span>02</span><div><h2>补录真实进度</h2><p>不确定的节点留空，不能用推算代替组织结论。</p></div></div>
        <a-collapse :default-active-key="[]" accordion>
          <a-collapse-item v-for="[phase, fields] in groupedFields" :key="phase" :header="phase">
            <a-form :model="form.actual_dates" layout="vertical" size="small">
              <a-form-item v-for="field in fields" :key="field.key" :label="field.label" :extra="field.hint">
                <a-date-picker v-model="form.actual_dates[field.key]" value-format="YYYY-MM-DD" allow-clear style="width: 100%" />
              </a-form-item>
              <div v-if="phase === '发展对象'" class="training-grid">
                <a-form-item label="培训天数"><a-input-number v-model="form.actual_dates.training_days" :min="0" :max="365" /></a-form-item>
                <a-form-item label="培训学时"><a-input-number v-model="form.actual_dates.training_hours" :min="0" :max="10000" /></a-form-item>
              </div>
            </a-form>
          </a-collapse-item>
        </a-collapse>
      </aside>

      <main class="result-panel">
        <template v-if="result">
          <div class="result-summary">
            <div><span>人员</span><strong>{{ result.name }}</strong></div>
            <div><span>规则</span><strong>{{ result.rule_version }}</strong></div>
            <div><span>节点</span><strong>{{ result.nodes.length }}</strong></div>
            <div><span>风险</span><strong :class="{ risk: result.warnings.length }">{{ result.warnings.length }}</strong></div>
          </div>
          <a-alert v-if="result.provisional" type="warning" show-icon title="包含工作日暂算结果">
            相关年份尚未配置完整节假日与调休，当前按周一至周五暂算；请在工作日历完善配置后重新计算。
          </a-alert>
          <section v-if="result.warnings.length" class="warning-list">
            <h2>需要先核查的风险</h2>
            <a-alert v-for="warning in result.warnings" :key="`${warning.code}-${warning.message}`" :type="warning.level === 'high' ? 'error' : 'warning'" show-icon :title="warning.message" />
          </section>
          <section class="timeline">
            <article v-for="(node, index) in result.nodes" :key="node.key" :class="[`status-${node.status}`, { provisional: node.provisional }]">
              <div class="timeline-index">{{ String(index + 1).padStart(2, '0') }}</div>
              <div class="timeline-line"><i /></div>
              <div class="timeline-content">
                <div class="timeline-top"><span>{{ phaseLabels[node.phase] || node.phase }}</span><a-tag :color="node.status === 'overdue' ? 'red' : node.status === 'completed' ? 'green' : node.status === 'waiting_manual' ? 'gray' : 'orange'">{{ dateKindLabel(node.date_kind) }}</a-tag></div>
                <h3>{{ node.title }}</h3>
                <strong class="node-date">{{ nodeDate(node) }}</strong>
                <p>{{ node.article }} · {{ node.basis }}</p>
                <details v-if="node.materials.length"><summary>本阶段材料 {{ node.materials.length }} 项</summary><ul><li v-for="material in node.materials" :key="`${node.key}-${material.name}`"><b>{{ material.name }}</b><span>{{ material.national ? '国家规则' : `单位补充 · ${material.source}` }}</span></li></ul></details>
              </div>
            </article>
          </section>
        </template>
        <div v-else class="result-empty"><span>2026</span><h2>一条可解释、可复核的时间轴</h2><p>系统不会猜测组织决定。填写左侧姓名和申请日期后，先给出确定期限；再根据实际节点逐步更新。</p><a-button type="primary" @click="calculate">生成首轮时间轴</a-button></div>
      </main>
    </section>
  </div>
</template>

<style scoped>
.development-page { max-width: 1520px; }.development-header { align-items: flex-end; }.rule-alert { margin-bottom: 16px; }.calculator-layout { display: grid; grid-template-columns: 390px minmax(0, 1fr); gap: 20px; align-items: start; }.input-panel,.result-panel { border: 1px solid rgba(113,75,47,.17); border-radius: 22px; background: rgba(255,252,244,.94); box-shadow: 0 16px 42px rgba(78,47,27,.07); }.input-panel { position: sticky; top: 18px; padding: 22px; }.result-panel { min-height: 740px; padding: 26px; }.section-heading { display: flex; gap: 12px; margin-bottom: 18px; }.section-heading > span { display: grid; place-items: center; flex: 0 0 34px; height: 34px; border-radius: 50%; color: white; background: #9b2b24; font-family: Georgia,serif; }.section-heading h2 { margin: 0; color: #4b3528; font-family: "Noto Serif SC","Songti SC",serif; font-size: 19px; }.section-heading p { margin: 4px 0 0; color: #897262; font-size: 12px; }.section-heading.secondary { margin-top: 28px; }.privacy-note { padding: 13px 15px; border-left: 3px solid #b78b4a; background: #faf3e4; color: #715d4f; }.privacy-note p { margin: 5px 0 0; font-size: 12px; line-height: 1.7; }.training-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }.result-summary { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 18px; }.result-summary div { padding: 15px; border-radius: 14px; background: #f7f1e5; }.result-summary span { display: block; color: #907b69; font-size: 11px; }.result-summary strong { display: block; margin-top: 5px; color: #4d3729; font-size: 21px; }.result-summary strong.risk { color: #b42318; }.warning-list { display: grid; gap: 8px; margin: 22px 0; }.warning-list h2 { color: #5a3a2d; font-size: 17px; }.timeline { margin-top: 26px; }.timeline article { display: grid; grid-template-columns: 36px 22px 1fr; min-height: 150px; }.timeline-index { padding-top: 4px; color: #a18d7b; font-family: Georgia,serif; }.timeline-line { position: relative; display: flex; justify-content: center; }.timeline-line::after { position: absolute; top: 14px; bottom: -8px; width: 1px; content: ""; background: #d9cbb8; }.timeline article:last-child .timeline-line::after { display: none; }.timeline-line i { position: relative; z-index: 1; width: 11px; height: 11px; margin-top: 6px; border: 3px solid #fffaf0; border-radius: 50%; background: #b6854a; box-shadow: 0 0 0 1px #b6854a; }.status-completed .timeline-line i { background: #4c7a5d; box-shadow: 0 0 0 1px #4c7a5d; }.status-overdue .timeline-line i { background: #ae3028; box-shadow: 0 0 0 1px #ae3028; }.timeline-content { padding: 0 8px 28px 14px; border-bottom: 1px solid rgba(106,76,52,.1); }.timeline-top { display: flex; justify-content: space-between; color: #9a2d26; font-size: 12px; }.timeline-content h3 { margin: 7px 0; color: #493328; font-family: "Noto Serif SC","Songti SC",serif; font-size: 19px; }.node-date { color: #8c5f35; font-size: 15px; }.timeline-content p { color: #796656; line-height: 1.65; }.timeline details summary { color: #8f2b25; cursor: pointer; }.timeline details ul { display: grid; gap: 6px; padding-left: 20px; }.timeline details li span { display: block; color: #917c6a; font-size: 11px; }.result-empty { display: grid; place-items: center; max-width: 520px; margin: 150px auto; text-align: center; color: #806b5a; }.result-empty > span { color: rgba(143,40,34,.16); font-family: Georgia,serif; font-size: 84px; line-height: 1; }.result-empty h2 { margin: 0; color: #4d382c; font-family: "Noto Serif SC","Songti SC",serif; font-size: 26px; }.result-empty p { line-height: 1.8; }@media(max-width:1000px){.calculator-layout{grid-template-columns:1fr}.input-panel{position:static}.result-summary{grid-template-columns:repeat(2,1fr)}}
</style>
