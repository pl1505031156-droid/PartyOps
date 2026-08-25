<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconCalendar, IconCheck, IconDelete, IconPlus } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { User } from "../types";
import { formatServerTime } from "../utils/datetime";
import PageHelp from "../components/PageHelp.vue";

interface WorkflowTemplate { id: string; name: string; business_type: string; steps: Array<{ title: string; responsible_role: string }>; }
interface MeetingStep { id: string; title: string; assignee_id: string | null; due_at: string | null; done: boolean; version: number; }
interface MeetingTopic { id: string; title: string; review_result: string; amount: string; reviewed: boolean; amount_confirmed: boolean; archived_at: string | null; archive_reason: string; version: number; }
interface Meeting {
  id: string; meeting_type: string; meeting_type_label: string; organization: string; title: string;
  scheduled_at: string | null; status: string; task_id: string; version: number; archived_at: string | null; archive_reason: string;
  progress: { done: number; total: number; percent: number }; steps: MeetingStep[]; topics: MeetingTopic[]; archived_topics: MeetingTopic[];
}
interface AnnualStats { completed_meetings: number; reviewed_topics: number; confirmed_amount: string; }

const meetings = ref<Meeting[]>([]);
const templates = ref<WorkflowTemplate[]>([]);
const users = ref<User[]>([]);
const selectedId = ref("");
const createVisible = ref(false);
const topicVisible = ref(false);
const loading = ref(false);
const lifecycle = ref<"active" | "archived">("active");
const archiveVisible = ref(false);
const archiveReason = ref("");
const deletionImpact = ref<{ steps: number; topics: number; documents: number; attendees: number; actions: number } | null>(null);
const topicLifecycle = ref<"active" | "archived">("active");
const topicArchiveVisible = ref(false);
const topicArchiveReason = ref("");
const topicArchiveTarget = ref<MeetingTopic | null>(null);
const topicDeletionImpact = ref<{ annual_statistics: boolean; confirmed_amount: boolean } | null>(null);
const year = ref(new Date().getFullYear());
const stats = ref<AnnualStats>({ completed_meetings: 0, reviewed_topics: 0, confirmed_amount: "0.00" });
const form = reactive({ meeting_type: "party_committee", organization: "", title: "", scheduled_at: "", workflow_template_id: "", owner_id: "" });
const topicForm = reactive({ title: "", review_result: "", amount: "0", reviewed: false, amount_confirmed: false });
const selected = computed(() => meetings.value.find((item) => item.id === selectedId.value) || null);
const visibleTopics = computed(() => topicLifecycle.value === "active" ? selected.value?.topics || [] : selected.value?.archived_topics || []);
const userNames = computed(() => Object.fromEntries(users.value.map((item) => [item.id, item.display_name])));
const selectedTemplate = computed(() => templates.value.find((item) => item.id === form.workflow_template_id) || null);
const otherTemplates = computed(() => templates.value.filter((item) => !["branch_members", "party_member_meeting", "party_group", "party_class", "study_group"].includes(item.business_type)));
const responsibleRoles = computed(() => Array.from(new Set((selectedTemplate.value?.steps || []).map((step) => step.responsible_role).filter(Boolean))));
const assignees = reactive<Record<string, string>>({});

const meetingTypes = [
  ["party_committee", "党委会"], ["theme_party_day", "主题党日"], ["organization_life", "组织生活会"],
];

async function load() {
  loading.value = true;
  try {
    [meetings.value, templates.value, users.value, stats.value] = await Promise.all([
      api.get<Meeting[]>(`/business-meetings?scope=other&lifecycle=${lifecycle.value}`),
      api.get<WorkflowTemplate[]>("/workflow-templates"),
      api.get<User[]>("/users"),
      api.get<AnnualStats>(`/business-meetings/statistics/annual?year=${year.value}`),
    ]);
    if (!selectedId.value || !meetings.value.some((item) => item.id === selectedId.value)) selectedId.value = meetings.value[0]?.id || "";
    form.workflow_template_id ||= otherTemplates.value[0]?.id || "";
    form.owner_id ||= users.value.find((item) => item.active)?.id || "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议工作流读取失败");
  } finally {
    loading.value = false;
  }
}

async function createMeeting() {
  if (!form.organization.trim() || !form.title.trim()) {
    Message.warning("请填写组织和会议标题");
    return;
  }
  try {
    const created = await api.post<Meeting>("/business-meetings", { ...form, scheduled_at: form.scheduled_at || null, assignees });
    createVisible.value = false;
    selectedId.value = created.id;
    Object.assign(form, { title: "", scheduled_at: "" });
    for (const role of Object.keys(assignees)) delete assignees[role];
    Message.success("会议筹备流程已生成");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议创建失败");
  }
}

async function toggleStep(step: MeetingStep, done: boolean) {
  if (!selected.value) return;
  try {
    await api.patch(`/tasks/${selected.value.task_id}/steps/${step.id}`, { done }, { "If-Match": String(step.version) });
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "步骤更新失败");
  }
}

async function addTopic() {
  if (!selected.value || !topicForm.title.trim()) return;
  try {
    await api.post(`/business-meetings/${selected.value.id}/topics`, topicForm);
    topicVisible.value = false;
    Object.assign(topicForm, { title: "", review_result: "", amount: "0", reviewed: false, amount_confirmed: false });
    Message.success("议题已加入年度统计口径");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "议题保存失败");
  }
}

async function openTopicArchive(topic: MeetingTopic) {
  if (!selected.value || topic.archived_at) return;
  try {
    topicArchiveTarget.value = topic;
    topicDeletionImpact.value = await api.get(
      `/business-meetings/${selected.value.id}/topics/${topic.id}/deletion-impact`,
    );
    topicArchiveReason.value = "";
    topicArchiveVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "无法读取议题移除影响");
  }
}

async function archiveTopic() {
  if (!selected.value || !topicArchiveTarget.value || topicArchiveReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的移除原因");
    return;
  }
  try {
    await api.deleteBody(
      `/business-meetings/${selected.value.id}/topics/${topicArchiveTarget.value.id}`,
      { reason: topicArchiveReason.value.trim() },
      { "If-Match": String(topicArchiveTarget.value.version) },
    );
    topicArchiveVisible.value = false;
    Message.success("议题已移至已移除区，年度统计已同步重算");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "议题移除失败");
  }
}

async function restoreTopic(topic: MeetingTopic) {
  if (!selected.value || !topic.archived_at) return;
  try {
    await api.post(
      `/business-meetings/${selected.value.id}/topics/${topic.id}/restore`,
      { reason: "经办人复核后恢复误移除议题" },
      { "If-Match": String(topic.version) },
    );
    Message.success("议题已恢复，年度统计已同步重算");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "议题恢复失败");
  }
}

async function openArchive() {
  if (!selected.value) return;
  try {
    deletionImpact.value = await api.get(`/business-meetings/${selected.value.id}/deletion-impact`);
    archiveReason.value = "";
    archiveVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议归档影响读取失败");
  }
}

async function archiveMeeting() {
  if (!selected.value || archiveReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的归档原因");
    return;
  }
  try {
    await api.deleteBody(`/business-meetings/${selected.value.id}`, { reason: archiveReason.value }, { "If-Match": String(selected.value.version) });
    archiveVisible.value = false;
    Message.success("会议已归档，筹备步骤、议题、文档和审计均保留");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议归档失败");
  }
}

async function restoreMeeting() {
  if (!selected.value) return;
  try {
    await api.post(`/business-meetings/${selected.value.id}/restore`, { reason: "经办人核对后恢复会议办理" }, { "If-Match": String(selected.value.version) });
    Message.success("会议及其筹备事项已恢复");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议恢复失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page meetings-page">
    <header class="page-header">
      <div><p class="page-kicker">党务 · 其他会议制度化执行</p><h1 class="page-title">其他党建会议</h1><p class="page-description">党委会、主题党日、组织生活会等其他会议在此筹备；三会一课与中心组学习使用各自专属模块。</p></div>
      <a-space><PageHelp title="其他党建会议" :tips="['按模板建立筹备步骤，并为每一步指定负责人和截止时间。', '有会议创建权限的协同用户可新建、编辑和完成本人负责的步骤。', '年度统计只计已完成会议、已审议议题和已确认金额。']" help-query="业务会议 筹备 协同 年度统计" /><a-select v-model="year" :style="{ width: '120px' }" @change="load"><a-option v-for="value in [year - 1, year, year + 1]" :key="value" :value="value">{{ value }} 年</a-option></a-select><a-button type="primary" @click="createVisible = true"><template #icon><IconPlus /></template>新建会议</a-button></a-space>
    </header>

    <div class="lifecycle-toolbar"><a-radio-group v-model="lifecycle" type="button" @change="load"><a-radio value="active">在办会议</a-radio><a-radio value="archived">已归档</a-radio></a-radio-group><span>归档不删除筹备步骤、议题、材料与年度审计。</span></div>
    <section class="meeting-ledger">
      <article><span>已完成会议</span><strong>{{ stats.completed_meetings }}</strong><small>只统计状态为已完成</small></article>
      <article><span>已审议议题</span><strong>{{ stats.reviewed_topics }}</strong><small>只统计确认审议结果</small></article>
      <article class="amount"><span>确认涉及资金</span><strong>¥ {{ stats.confirmed_amount }}</strong><small>未确认金额不进入口径</small></article>
    </section>

    <section class="meeting-workspace">
      <aside class="meeting-list">
        <button v-for="item in meetings" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="selectedId = item.id">
          <span>{{ item.meeting_type_label }}</span><h2>{{ item.title }}</h2><p><IconCalendar /> {{ formatServerTime(item.scheduled_at, "YYYY-MM-DD HH:mm", "待定") }} · {{ item.organization }}</p><div><i :style="{ width: `${item.progress.percent}%` }" /><small>{{ item.progress.done }}/{{ item.progress.total }}</small></div>
        </button>
        <div v-if="!meetings.length" class="empty-state">尚无其他党建会议。新建后按所选流程生成筹备步骤。</div>
      </aside>

      <main v-if="selected" class="meeting-detail">
        <header><div><span>{{ selected.meeting_type_label }} · {{ selected.organization }}</span><h2>{{ selected.title }}</h2><small v-if="selected.archived_at">归档原因：{{ selected.archive_reason }}</small></div><a-space><a-button v-if="!selected.archived_at" status="danger" @click="openArchive"><template #icon><IconDelete /></template>归档</a-button><a-button v-else type="primary" @click="restoreMeeting">恢复办理</a-button><a-progress type="circle" size="small" :percent="selected.progress.percent / 100" /></a-space></header>
        <section class="steps-section"><div class="section-title"><h3>筹备步骤</h3><p>每一步有负责人、截止时间和完成状态。</p></div><div class="step-grid"><label v-for="(step, index) in selected.steps" :key="step.id" :class="{ done: step.done }"><b>{{ String(index + 1).padStart(2, '0') }}</b><a-checkbox :model-value="step.done" :disabled="Boolean(selected.archived_at)" @change="(value: boolean | string | number) => toggleStep(step, Boolean(value))" /><span><strong>{{ step.title }}</strong><small>{{ userNames[step.assignee_id || ''] || '待指定' }} · {{ formatServerTime(step.due_at, "MM-DD HH:mm", "时间待定") }}</small></span><IconCheck v-if="step.done" /></label></div></section>
        <section class="topics-section"><div class="section-title inline"><div><h3>会议议题</h3><p>审议结果和确认金额决定年度统计；误录议题可移除并恢复。</p></div><a-space><a-radio-group v-model="topicLifecycle" type="button" size="small"><a-radio value="active">当前</a-radio><a-radio value="archived">已移除</a-radio></a-radio-group><a-button v-if="!selected.archived_at && topicLifecycle === 'active'" size="small" @click="topicVisible = true"><template #icon><IconPlus /></template>添加议题</a-button></a-space></div><a-table :data="visibleTopics" :pagination="false"><template #columns><a-table-column title="议题" data-index="title" /><a-table-column title="审议"><template #cell="{ record }">{{ record.reviewed ? "已审议" : "待审议" }}</template></a-table-column><a-table-column title="确认金额"><template #cell="{ record }">{{ record.amount_confirmed ? `¥ ${record.amount}` : "未确认" }}</template></a-table-column><a-table-column title="结果"><template #cell="{ record }">{{ record.archived_at ? record.archive_reason : record.review_result }}</template></a-table-column><a-table-column title="操作" :width="90"><template #cell="{ record }"><a-button v-if="record.archived_at" size="mini" type="text" :disabled="Boolean(selected?.archived_at)" @click="restoreTopic(record)">恢复</a-button><a-button v-else size="mini" type="text" status="danger" :disabled="Boolean(selected?.archived_at)" @click="openTopicArchive(record)">移除</a-button></template></a-table-column></template></a-table></section>
      </main>
      <main v-else class="meeting-detail empty-state">选择一场会议查看进度。</main>
    </section>

    <a-modal v-model:visible="createVisible" title="新建其他党建会议" @ok="createMeeting">
      <a-form :model="form" layout="vertical"><a-form-item label="会议类型"><a-select v-model="form.meeting_type"><a-option v-for="item in meetingTypes" :key="item[0]" :value="item[0]">{{ item[1] }}</a-option></a-select></a-form-item><a-form-item label="所属组织"><a-input v-model="form.organization" placeholder="例如：中共XX委员会" /></a-form-item><a-form-item label="会议标题"><a-input v-model="form.title" /></a-form-item><a-form-item label="计划时间"><a-date-picker v-model="form.scheduled_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width:100%" /></a-form-item><a-form-item label="流程模板"><a-select v-model="form.workflow_template_id"><a-option v-for="item in otherTemplates" :key="item.id" :value="item.id">{{ item.name }}</a-option></a-select></a-form-item><a-form-item label="总负责人"><a-select v-model="form.owner_id"><a-option v-for="item in users.filter((user) => user.active)" :key="item.id" :value="item.id">{{ item.display_name }}</a-option></a-select></a-form-item><a-divider v-if="responsibleRoles.length" orientation="left">按角色指定步骤负责人</a-divider><a-form-item v-for="role in responsibleRoles" :key="role" :label="role"><a-select v-model="assignees[role]" allow-clear placeholder="未指定时使用总负责人"><a-option v-for="item in users.filter((user) => user.active)" :key="item.id" :value="item.id">{{ item.display_name }}</a-option></a-select></a-form-item></a-form>
    </a-modal>
    <a-modal v-model:visible="topicVisible" title="添加会议议题" @ok="addTopic"><a-form :model="topicForm" layout="vertical"><a-form-item label="议题"><a-input v-model="topicForm.title" /></a-form-item><a-form-item label="审议结果"><a-textarea v-model="topicForm.review_result" /></a-form-item><a-form-item label="涉及金额（元）"><a-input v-model="topicForm.amount" /></a-form-item><a-form-item label="统计口径"><a-space><a-checkbox v-model="topicForm.reviewed">已完成审议</a-checkbox><a-checkbox v-model="topicForm.amount_confirmed">金额已确认</a-checkbox></a-space></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="archiveVisible" title="归档会议台账" ok-text="确认归档" @ok="archiveMeeting"><a-alert type="warning">归档会把关联筹备事项从活动列表移除，但不会物理删除任何业务和审计记录。</a-alert><div v-if="deletionImpact" class="impact-grid"><span>步骤 <b>{{ deletionImpact.steps }}</b></span><span>议题 <b>{{ deletionImpact.topics }}</b></span><span>文档 <b>{{ deletionImpact.documents }}</b></span><span>参会 <b>{{ deletionImpact.attendees }}</b></span><span>落实项 <b>{{ deletionImpact.actions }}</b></span></div><a-form-item label="归档原因" required><a-textarea v-model="archiveReason" /></a-form-item></a-modal>
    <a-modal v-model:visible="topicArchiveVisible" title="移除会议议题" ok-text="确认移除" @ok="archiveTopic"><a-alert type="warning">议题不会物理删除，可从“已移除”恢复。{{ topicDeletionImpact?.annual_statistics ? "此议题当前进入年度统计，移除后统计会立即重算。" : "" }}</a-alert><a-form-item label="移除原因" required><a-textarea v-model="topicArchiveReason" /></a-form-item></a-modal>
  </div>
</template>

<style scoped>
.meetings-page{max-width:1540px}.meeting-ledger{display:grid;grid-template-columns:repeat(3,1fr);margin:18px 0;border:1px solid var(--line);background:var(--line);gap:1px}.meeting-ledger article{padding:22px 26px;background:rgba(255,252,244,.94)}.meeting-ledger span,.meeting-ledger small{display:block;color:var(--muted)}.meeting-ledger strong{display:block;margin:8px 0;color:#4d382c;font-family:Georgia,"Noto Serif SC",serif;font-size:34px}.meeting-ledger .amount strong{color:#9b2b24}.meeting-workspace{display:grid;grid-template-columns:330px minmax(0,1fr);min-height:670px;border:1px solid var(--line);background:rgba(255,252,244,.85)}.meeting-list{border-right:1px solid var(--line)}.meeting-list>button{display:block;width:100%;padding:20px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.meeting-list>button.active{background:#f5ead6;box-shadow:inset 4px 0 #9b2b24}.meeting-list span{color:#9b2b24;font-size:11px}.meeting-list h2{margin:7px 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif;font-size:17px}.meeting-list p{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:11px}.meeting-list button div{position:relative;height:5px;margin-top:13px;background:#e8ddcc}.meeting-list button i{display:block;height:100%;background:#a1683e}.meeting-list button small{position:absolute;right:0;top:-17px;color:var(--muted)}.meeting-detail{padding:28px}.meeting-detail>header{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:20px}.meeting-detail>header span{color:#9b2b24;font-size:12px}.meeting-detail>header h2{margin:7px 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif;font-size:27px}.section-title{margin:24px 0 14px}.section-title.inline{display:flex;justify-content:space-between;align-items:end}.section-title h3{margin:0;color:#4d382c}.section-title p{margin:5px 0 0;color:var(--muted);font-size:12px}.step-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.step-grid label{display:grid;grid-template-columns:34px 24px 1fr 20px;align-items:center;min-height:78px;padding:12px;border:1px solid #ded0bc;background:#fffaf0}.step-grid label.done{background:#edf3e8;border-color:#a9bea3}.step-grid label>b{color:#b39a80;font-family:Georgia,serif}.step-grid label span strong,.step-grid label span small{display:block}.step-grid label span small{margin-top:5px;color:var(--muted);font-size:11px}.step-grid label>svg{color:#4c7a5d}.topics-section{margin-top:28px}@media(max-width:1050px){.meeting-workspace{grid-template-columns:1fr}.meeting-list{border-right:0}.step-grid{grid-template-columns:1fr}}@media(max-width:720px){.meeting-ledger{grid-template-columns:1fr}}
.lifecycle-toolbar{display:flex;align-items:center;justify-content:space-between;margin:18px 0 10px;color:var(--muted);font-size:12px}.lifecycle-toolbar+.meeting-ledger{margin-top:0}.impact-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;margin:16px 0;background:var(--line);border:1px solid var(--line)}.impact-grid span{padding:12px;background:#fffaf0;color:var(--muted);font-size:11px}.impact-grid b{display:block;margin-top:4px;color:var(--charcoal);font:22px Georgia,serif}@media(max-width:720px){.impact-grid{grid-template-columns:1fr}.lifecycle-toolbar{align-items:stretch;flex-direction:column;gap:10px}}
</style>
