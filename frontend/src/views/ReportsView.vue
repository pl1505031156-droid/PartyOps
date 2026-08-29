<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconDownload, IconPlus, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import PageHelp from "../components/PageHelp.vue";
import type { PeriodReport, PeriodReportItem, PeriodType, ReportSection } from "../types";
import { beijingNow, formatServerTime, localInputToUtc, serverTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const reports = ref<PeriodReport[]>([]);
const reportTemplates = ref<Array<{
  id: string;
  name: string;
  period_type: PeriodType;
  description: string;
  sections: ReportSection[];
  active: boolean;
}>>([]);
const selectedId = ref("");
const loading = ref(false);
const createVisible = ref(false);
const itemVisible = ref(false);
const reportForm = reactive<{ period_type: PeriodType; anchor_at: string; title: string; template_id: string }>({
  period_type: "week",
  anchor_at: beijingNow().format("YYYY-MM-DD"),
  title: "",
  template_id: "",
});
const itemForm = reactive<{ section: ReportSection; title: string; content: string }>({
  section: "completed",
  title: "",
  content: "",
});

const selected = computed(() => reports.value.find((item) => item.id === selectedId.value) || reports.value[0]);
const allSectionDefinitions: Array<{ key: ReportSection; label: string; hint: string }> = [
  { key: "completed", label: "本期完成", hint: "根据实际完成时间自动归集" },
  { key: "next_plan", label: "下期计划", hint: "根据计划周期和截止时间自动建议" },
  { key: "carry_over", label: "延续事项", hint: "计划节点已过但仍需继续推进" },
  { key: "risk", label: "重点问题与风险", hint: "记录需要关注的堵点" },
  { key: "coordination", label: "需要协调事项", hint: "记录需要协同支持的内容" },
];
const sectionDefinitions = computed(() => {
  const snapshot = selected.value?.snapshot as { design?: { sections?: ReportSection[] } } | undefined;
  const configured = snapshot?.design?.sections || [];
  if (!configured.length) return allSectionDefinitions;
  return configured
    .map((key) => allSectionDefinitions.find((item) => item.key === key))
    .filter((item): item is (typeof allSectionDefinitions)[number] => Boolean(item));
});
const availableTemplates = computed(() =>
  reportTemplates.value.filter((item) => item.active && item.period_type === reportForm.period_type),
);
const reportTree = computed(() => {
  const groups = new Map<string, PeriodReport[]>();
  for (const report of reports.value) {
    const year = formatServerTime(report.start_at, "YYYY");
    groups.set(year, [...(groups.get(year) || []), report]);
  }
  return [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));
});

function statusLabel(value: string) {
  return zhLabel(value);
}

function itemsFor(section: ReportSection): PeriodReportItem[] {
  return selected.value?.items.filter((item) => item.section === section) || [];
}

async function load(focusId?: string) {
  loading.value = true;
  try {
    await api.post<PeriodReport[]>("/period-reports/ensure-current");
    [reports.value, reportTemplates.value] = await Promise.all([
      api.get<PeriodReport[]>("/period-reports?limit=200"),
      api.get<typeof reportTemplates.value>("/report-templates"),
    ]);
    selectedId.value = focusId || selectedId.value || reports.value[0]?.id || "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "周期报告加载失败");
  } finally {
    loading.value = false;
  }
}

async function createReport() {
  try {
    const created = await api.post<PeriodReport>("/period-reports", {
      period_type: reportForm.period_type,
      anchor_at: localInputToUtc(`${reportForm.anchor_at}T00:00:00`),
      title: reportForm.title || null,
      template_id: reportForm.template_id || null,
      auto_fill: true,
    });
    createVisible.value = false;
    reportForm.title = "";
    reportForm.template_id = "";
    Message.success("周期报告已建立，相关任务已自动归集");
    await load(created.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "创建失败");
  }
}

async function addItem() {
  if (!selected.value) return;
  try {
    await api.post(
      `/period-reports/${selected.value.id}/items`,
      {
        ...itemForm,
        source_type: "manual",
        sort_order: itemsFor(itemForm.section).length,
      },
      { "If-Match": String(selected.value.version) },
    );
    itemVisible.value = false;
    Object.assign(itemForm, { section: "completed", title: "", content: "" });
    Message.success("报告条目已添加");
    await load(selected.value.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "添加失败");
  }
}

async function saveSummary(value: string) {
  if (!selected.value || value === selected.value.summary) return;
  try {
    await api.patch(
      `/period-reports/${selected.value.id}`,
      { summary: value },
      { "If-Match": String(selected.value.version) },
    );
    Message.success("综合说明已保存");
    await load(selected.value.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "保存失败");
  }
}

async function reportAction(action: "publish" | "lock" | "reopen") {
  if (!selected.value) return;
  try {
    await api.post(
      `/period-reports/${selected.value.id}/actions`,
      { action, note: "" },
      { "If-Match": String(selected.value.version) },
    );
    Message.success(action === "publish" ? "报告已发布并保存快照" : action === "lock" ? "报告已锁定" : "报告已重新打开");
    await load(selected.value.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "操作失败");
  }
}

async function removeItem(item: PeriodReportItem) {
  if (!selected.value) return;
  try {
    await api.delete(
      `/period-reports/${selected.value.id}/items/${item.id}`,
      { "If-Match": String(item.version) },
    );
    Message.success("条目已移除，原任务或文件未删除");
    await load(selected.value.id);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "移除失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page reports-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">周期工作汇总</p>
        <h1 class="page-title">周期汇总与周计划</h1>
        <p class="page-description">任务只录一次，系统按实际完成时间和计划节点形成周、月、季度、年度汇总。</p>
      </div>
      <a-space>
        <PageHelp
          title="周期汇总怎么用"
          :tips="['完成事项会自动进入对应周、月、季度和年度汇总。', '发布时保存快照，后台不会静默改写已发布内容。', '下周计划根据计划日期和截止时间生成建议。']"
          help-query="周期汇总"
        />
        <a-button :loading="loading" @click="load()"><template #icon><IconRefresh /></template>刷新</a-button>
        <a-button type="primary" @click="createVisible = true"><template #icon><IconPlus /></template>建立周期报告</a-button>
      </a-space>
    </header>

    <div class="reports-layout">
      <aside class="period-tree">
        <div v-for="[year, items] in reportTree" :key="year" class="year-block">
          <strong>{{ year }} 年</strong>
          <button
            v-for="report in items"
            :key="report.id"
            type="button"
            :class="{ active: selected?.id === report.id }"
            @click="selectedId = report.id"
          >
            <span>{{ report.title }}</span>
            <small>{{ statusLabel(report.status) }}</small>
          </button>
        </div>
        <div v-if="!reports.length" class="tree-empty">尚未建立周期报告。</div>
      </aside>

      <main v-if="selected" class="report-paper">
        <header class="report-titlebar">
          <div>
            <span>{{ selected.period_key }} · {{ statusLabel(selected.status) }}</span>
            <h2>{{ selected.title }}</h2>
            <p>{{ formatServerTime(selected.start_at, "YYYY年MM月DD日") }} — {{ serverTime(selected.end_at).subtract(1, "day").format("MM月DD日") }}</p>
          </div>
          <a-space>
            <a-dropdown>
              <a-button><template #icon><IconDownload /></template>导出</a-button>
              <template #content>
                <a-doption><a :href="downloadUrl(`/period-reports/${selected.id}/export.docx`)" target="_blank">Word 报告</a></a-doption>
                <a-doption><a :href="downloadUrl(`/period-reports/${selected.id}/export.xlsx`)" target="_blank">Excel 清单</a></a-doption>
              </template>
            </a-dropdown>
            <a-button v-if="selected.status === 'draft'" @click="itemVisible = true">添加条目</a-button>
            <a-button v-if="selected.status === 'draft'" type="primary" @click="reportAction('publish')">发布快照</a-button>
            <a-button v-else-if="selected.status === 'published'" @click="reportAction('lock')">锁定归档</a-button>
            <a-button v-else status="warning" @click="reportAction('reopen')">重新打开</a-button>
          </a-space>
        </header>

        <section class="summary-block">
          <label>综合说明</label>
          <a-textarea
            :default-value="selected.summary"
            :disabled="selected.status !== 'draft'"
            placeholder="补充整体进展、主要成效和下一步考虑……"
            :auto-size="{ minRows: 2, maxRows: 5 }"
            @blur="saveSummary(($event.target as HTMLTextAreaElement).value)"
          />
        </section>

        <section v-for="section in sectionDefinitions" :key="section.key" class="report-section">
          <div class="report-section-title">
            <div><span>{{ section.label }}</span><small>{{ section.hint }}</small></div>
            <strong>{{ itemsFor(section.key).length }}</strong>
          </div>
          <div v-if="itemsFor(section.key).length" class="report-items">
            <article v-for="item in itemsFor(section.key)" :key="item.id">
              <span class="source-chip">{{ item.source_type === "task" ? "任务" : item.source_type === "file" ? "文件" : item.source_type === "journal" ? "日志" : "补充" }}</span>
              <div>
                <RouterLink v-if="item.source_type === 'task'" :to="`/tasks/${item.source_id}`">{{ item.title }}</RouterLink>
                <strong v-else>{{ item.title }}</strong>
                <p v-if="item.content">{{ item.content }}</p>
              </div>
              <a-button v-if="selected.status === 'draft'" type="text" size="mini" @click="removeItem(item)">移除</a-button>
            </article>
          </div>
          <p v-else class="section-empty">暂无内容。</p>
        </section>
      </main>
      <div v-else class="empty-report">建立第一份周报后，本周完成和下周计划会自动归集。</div>
    </div>

    <a-modal v-model:visible="createVisible" title="建立周期报告" @ok="createReport">
      <a-form :model="reportForm" layout="vertical">
        <a-form-item label="汇总周期">
          <a-radio-group v-model="reportForm.period_type">
            <a-radio value="week">周</a-radio><a-radio value="month">月</a-radio>
            <a-radio value="quarter">季度</a-radio><a-radio value="year">年度</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="周期内任意日期"><a-date-picker v-model="reportForm.anchor_at" value-format="YYYY-MM-DD" /></a-form-item>
        <a-form-item label="报告模板（可选）">
          <a-select v-model="reportForm.template_id" allow-clear placeholder="按所选周期显示可用模板">
            <a-option v-for="template in availableTemplates" :key="template.id" :value="template.id">{{ template.name }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="自定义标题（可留空）"><a-input v-model="reportForm.title" placeholder="系统会按周期自动命名" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="itemVisible" title="补充报告条目" @ok="addItem">
      <a-form :model="itemForm" layout="vertical">
        <a-form-item label="栏目">
          <a-select v-model="itemForm.section">
            <a-option v-for="section in sectionDefinitions" :key="section.key" :value="section.key">{{ section.label }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="事项"><a-input v-model="itemForm.title" /></a-form-item>
        <a-form-item label="说明"><a-textarea v-model="itemForm.content" :auto-size="{ minRows: 3, maxRows: 8 }" /></a-form-item>
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

.reports-layout {
  display: grid;
  min-height: 650px;
  grid-template-columns: 230px minmax(0, 1fr);
  gap: 24px;
}

.period-tree {
  padding-right: 16px;
  border-right: 1px solid var(--line);
}

.year-block {
  margin-bottom: 22px;
}

.year-block > strong {
  display: block;
  margin-bottom: 8px;
  color: var(--charcoal);
  font-family: Georgia, serif;
}

.year-block button {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px;
  text-align: left;
  background: transparent;
  border: 0;
  border-left: 2px solid transparent;
  cursor: pointer;
}

.year-block button:hover,
.year-block button.active {
  color: var(--cinnabar);
  background: rgba(180, 35, 24, 0.05);
  border-left-color: var(--cinnabar);
}

.year-block button span {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.year-block button small {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 10px;
}

.report-paper {
  padding: 28px 32px;
  background: rgba(251, 248, 241, 0.78);
  border: 1px solid var(--line);
}

.report-titlebar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 22px;
  border-bottom: 2px solid var(--charcoal);
}

.report-titlebar span {
  color: var(--cinnabar);
  font: 11px Georgia, serif;
  letter-spacing: 0.12em;
}

.report-titlebar h2 {
  margin: 8px 0 6px;
  font-family: "Noto Serif CJK SC", SimSun, serif;
  font-size: 26px;
}

.report-titlebar p,
.summary-block label,
.report-section-title small {
  color: var(--muted);
  font-size: 11px;
}

.summary-block {
  padding: 22px 0;
  border-bottom: 1px solid var(--line);
}

.summary-block label {
  display: block;
  margin-bottom: 8px;
}

.report-section {
  padding: 22px 0;
  border-bottom: 1px solid var(--line-light);
}

.report-section-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 10px;
}

.report-section-title span,
.report-section-title small {
  display: block;
}

.report-section-title span {
  font-size: 16px;
  font-weight: 600;
}

.report-section-title small {
  margin-top: 4px;
}

.report-section-title > strong {
  color: var(--muted);
  font: 22px Georgia, serif;
}

.report-items article {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) 44px;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line-light);
}

.source-chip {
  align-self: start;
  padding: 3px 5px;
  color: var(--cinnabar);
  font-size: 10px;
  text-align: center;
  background: rgba(180, 35, 24, 0.07);
}

.report-items a,
.report-items strong {
  font-size: 13px;
  font-weight: 600;
}

.report-items p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.section-empty,
.tree-empty,
.empty-report {
  color: var(--muted);
  font-size: 12px;
}

.empty-report {
  padding: 120px 40px;
  text-align: center;
  border: 1px solid var(--line);
}
</style>
