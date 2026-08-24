<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconCalendar, IconCheck, IconPlus } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { User } from "../types";
import { formatServerTime } from "../utils/datetime";
import PageHelp from "../components/PageHelp.vue";

interface WorkflowTemplate { id: string; name: string; business_type: string; steps: Array<{ title: string; responsible_role: string }>; }
interface MeetingStep { id: string; title: string; assignee_id: string | null; due_at: string | null; done: boolean; version: number; }
interface MeetingTopic { id: string; title: string; review_result: string; amount: string; reviewed: boolean; amount_confirmed: boolean; }
interface Meeting {
  id: string; meeting_type: string; meeting_type_label: string; organization: string; title: string;
  scheduled_at: string | null; status: string; task_id: string; version: number;
  progress: { done: number; total: number; percent: number }; steps: MeetingStep[]; topics: MeetingTopic[];
}
interface AnnualStats { completed_meetings: number; reviewed_topics: number; confirmed_amount: string; }

const meetings = ref<Meeting[]>([]);
const templates = ref<WorkflowTemplate[]>([]);
const users = ref<User[]>([]);
const selectedId = ref("");
const createVisible = ref(false);
const topicVisible = ref(false);
const loading = ref(false);
const year = ref(new Date().getFullYear());
const stats = ref<AnnualStats>({ completed_meetings: 0, reviewed_topics: 0, confirmed_amount: "0.00" });
const form = reactive({ meeting_type: "party_committee", organization: "", title: "", scheduled_at: "", workflow_template_id: "", owner_id: "" });
const topicForm = reactive({ title: "", review_result: "", amount: "0", reviewed: false, amount_confirmed: false });
const selected = computed(() => meetings.value.find((item) => item.id === selectedId.value) || null);
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
      api.get<Meeting[]>("/business-meetings?scope=other"),
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

onMounted(load);
</script>

<template>
  <div class="page meetings-page">
    <header class="page-header">
      <div><p class="page-kicker">党务 · 其他会议制度化执行</p><h1 class="page-title">其他党建会议</h1><p class="page-description">党委会、主题党日、组织生活会等其他会议在此筹备；三会一课与中心组学习使用各自专属模块。</p></div>
      <a-space><PageHelp title="其他党建会议" :tips="['按模板建立筹备步骤，并为每一步指定负责人和截止时间。', '有会议创建权限的协同用户可新建、编辑和完成本人负责的步骤。', '年度统计只计已完成会议、已审议议题和已确认金额。']" help-query="业务会议 筹备 协同 年度统计" /><a-select v-model="year" :style="{ width: '120px' }" @change="load"><a-option v-for="value in [year - 1, year, year + 1]" :key="value" :value="value">{{ value }} 年</a-option></a-select><a-button type="primary" @click="createVisible = true"><template #icon><IconPlus /></template>新建会议</a-button></a-space>
    </header>

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
        <header><div><span>{{ selected.meeting_type_label }} · {{ selected.organization }}</span><h2>{{ selected.title }}</h2></div><a-progress type="circle" size="small" :percent="selected.progress.percent / 100" /></header>
        <section class="steps-section"><div class="section-title"><h3>筹备步骤</h3><p>每一步有负责人、截止时间和完成状态。</p></div><div class="step-grid"><label v-for="(step, index) in selected.steps" :key="step.id" :class="{ done: step.done }"><b>{{ String(index + 1).padStart(2, '0') }}</b><a-checkbox :model-value="step.done" @change="(value: boolean | string | number) => toggleStep(step, Boolean(value))" /><span><strong>{{ step.title }}</strong><small>{{ userNames[step.assignee_id || ''] || '待指定' }} · {{ formatServerTime(step.due_at, "MM-DD HH:mm", "时间待定") }}</small></span><IconCheck v-if="step.done" /></label></div></section>
        <section class="topics-section"><div class="section-title inline"><div><h3>会议议题</h3><p>审议结果和确认金额决定年度统计。</p></div><a-button size="small" @click="topicVisible = true"><template #icon><IconPlus /></template>添加议题</a-button></div><a-table :data="selected.topics" :pagination="false"><template #columns><a-table-column title="议题" data-index="title" /><a-table-column title="审议"><template #cell="{ record }">{{ record.reviewed ? "已审议" : "待审议" }}</template></a-table-column><a-table-column title="确认金额"><template #cell="{ record }">{{ record.amount_confirmed ? `¥ ${record.amount}` : "未确认" }}</template></a-table-column><a-table-column title="结果" data-index="review_result" /></template></a-table></section>
      </main>
      <main v-else class="meeting-detail empty-state">选择一场会议查看进度。</main>
    </section>

    <a-modal v-model:visible="createVisible" title="新建其他党建会议" @ok="createMeeting">
      <a-form :model="form" layout="vertical"><a-form-item label="会议类型"><a-select v-model="form.meeting_type"><a-option v-for="item in meetingTypes" :key="item[0]" :value="item[0]">{{ item[1] }}</a-option></a-select></a-form-item><a-form-item label="所属组织"><a-input v-model="form.organization" placeholder="例如：中共XX委员会" /></a-form-item><a-form-item label="会议标题"><a-input v-model="form.title" /></a-form-item><a-form-item label="计划时间"><a-date-picker v-model="form.scheduled_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width:100%" /></a-form-item><a-form-item label="流程模板"><a-select v-model="form.workflow_template_id"><a-option v-for="item in otherTemplates" :key="item.id" :value="item.id">{{ item.name }}</a-option></a-select></a-form-item><a-form-item label="总负责人"><a-select v-model="form.owner_id"><a-option v-for="item in users.filter((user) => user.active)" :key="item.id" :value="item.id">{{ item.display_name }}</a-option></a-select></a-form-item><a-divider v-if="responsibleRoles.length" orientation="left">按角色指定步骤负责人</a-divider><a-form-item v-for="role in responsibleRoles" :key="role" :label="role"><a-select v-model="assignees[role]" allow-clear placeholder="未指定时使用总负责人"><a-option v-for="item in users.filter((user) => user.active)" :key="item.id" :value="item.id">{{ item.display_name }}</a-option></a-select></a-form-item></a-form>
    </a-modal>
    <a-modal v-model:visible="topicVisible" title="添加会议议题" @ok="addTopic"><a-form :model="topicForm" layout="vertical"><a-form-item label="议题"><a-input v-model="topicForm.title" /></a-form-item><a-form-item label="审议结果"><a-textarea v-model="topicForm.review_result" /></a-form-item><a-form-item label="涉及金额（元）"><a-input v-model="topicForm.amount" /></a-form-item><a-form-item label="统计口径"><a-space><a-checkbox v-model="topicForm.reviewed">已完成审议</a-checkbox><a-checkbox v-model="topicForm.amount_confirmed">金额已确认</a-checkbox></a-space></a-form-item></a-form></a-modal>
  </div>
</template>

<style scoped>
.meetings-page{max-width:1540px}.meeting-ledger{display:grid;grid-template-columns:repeat(3,1fr);margin:18px 0;border:1px solid var(--line);background:var(--line);gap:1px}.meeting-ledger article{padding:22px 26px;background:rgba(255,252,244,.94)}.meeting-ledger span,.meeting-ledger small{display:block;color:var(--muted)}.meeting-ledger strong{display:block;margin:8px 0;color:#4d382c;font-family:Georgia,"Noto Serif SC",serif;font-size:34px}.meeting-ledger .amount strong{color:#9b2b24}.meeting-workspace{display:grid;grid-template-columns:330px minmax(0,1fr);min-height:670px;border:1px solid var(--line);background:rgba(255,252,244,.85)}.meeting-list{border-right:1px solid var(--line)}.meeting-list>button{display:block;width:100%;padding:20px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.meeting-list>button.active{background:#f5ead6;box-shadow:inset 4px 0 #9b2b24}.meeting-list span{color:#9b2b24;font-size:11px}.meeting-list h2{margin:7px 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif;font-size:17px}.meeting-list p{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:11px}.meeting-list button div{position:relative;height:5px;margin-top:13px;background:#e8ddcc}.meeting-list button i{display:block;height:100%;background:#a1683e}.meeting-list button small{position:absolute;right:0;top:-17px;color:var(--muted)}.meeting-detail{padding:28px}.meeting-detail>header{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:20px}.meeting-detail>header span{color:#9b2b24;font-size:12px}.meeting-detail>header h2{margin:7px 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif;font-size:27px}.section-title{margin:24px 0 14px}.section-title.inline{display:flex;justify-content:space-between;align-items:end}.section-title h3{margin:0;color:#4d382c}.section-title p{margin:5px 0 0;color:var(--muted);font-size:12px}.step-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.step-grid label{display:grid;grid-template-columns:34px 24px 1fr 20px;align-items:center;min-height:78px;padding:12px;border:1px solid #ded0bc;background:#fffaf0}.step-grid label.done{background:#edf3e8;border-color:#a9bea3}.step-grid label>b{color:#b39a80;font-family:Georgia,serif}.step-grid label span strong,.step-grid label span small{display:block}.step-grid label span small{margin-top:5px;color:var(--muted);font-size:11px}.step-grid label>svg{color:#4c7a5d}.topics-section{margin-top:28px}@media(max-width:1050px){.meeting-workspace{grid-template-columns:1fr}.meeting-list{border-right:0}.step-grid{grid-template-columns:1fr}}@media(max-width:720px){.meeting-ledger{grid-template-columns:1fr}}
</style>
