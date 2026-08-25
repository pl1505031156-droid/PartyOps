<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { IconCheck, IconDelete, IconDownload, IconPlus, IconRefresh, IconUserGroup } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, saveBlobDownload } from "../api";
import { formatServerTime, localInputToUtc, localNowInput } from "../utils/datetime";
import type { User } from "../types";
import PageHelp from "../components/PageHelp.vue";

interface Overview {
  year: number;
  current_quarter: number;
  total: number;
  completed: number;
  needs_completion: number;
  overdue_actions: number;
  quarter_guidance: Record<string, string>;
}

interface Meeting {
  id: string;
  meeting_type: string;
  meeting_type_label: string;
  organization: string;
  title: string;
  scheduled_at: string | null;
  status: string;
  archived_at: string | null;
  archive_reason: string;
  host_id: string | null;
  recorder_id: string | null;
  venue: string;
  version: number;
  ledger_state: "完整" | "需补充" | "逾期" | "待人工确认" | "不适用";
  missing_items: string[];
  present_count: number;
  document_count: number;
  action_count: number;
  overdue_action_count: number;
}

interface Attendee {
  id: string;
  display_name: string;
  role: string;
  attendance_status: string;
  voting_eligible: boolean;
  note: string;
  archived_at: string | null;
  archive_reason: string;
  version: number;
}

interface MeetingAction {
  id: string;
  title: string;
  responsible_user_id: string | null;
  due_at: string | null;
  task_id: string | null;
  status: string;
  note: string;
  archived_at: string | null;
  archive_reason: string;
  version: number;
}

const year = ref(new Date().getFullYear());
const organization = ref("");
const loading = ref(false);
const lifecycle = ref<"active" | "archived">("active");
const overview = ref<Overview | null>(null);
const meetings = ref<Meeting[]>([]);
const users = ref<User[]>([]);
const selectedId = ref("");
const attendees = ref<Attendee[]>([]);
const actions = ref<MeetingAction[]>([]);
const createVisible = ref(false);
const attendeeVisible = ref(false);
const actionVisible = ref(false);
const archiveVisible = ref(false);
const archiveReason = ref("");
const deletionImpact = ref<Record<string, number | boolean> | null>(null);
const childArchiveVisible = ref(false);
const childArchiveKind = ref<"attendees" | "actions">("attendees");
const childArchiveTarget = ref<Attendee | MeetingAction | null>(null);
const childArchiveReason = ref("");
const childDeletionImpact = ref<Record<string, boolean> | null>(null);
const selected = computed(() => meetings.value.find((item) => item.id === selectedId.value) || null);
const activeAttendees = computed(() => attendees.value.filter((item) => !item.archived_at));
const archivedAttendees = computed(() => attendees.value.filter((item) => Boolean(item.archived_at)));
const activeActions = computed(() => actions.value.filter((item) => !item.archived_at));
const archivedActions = computed(() => actions.value.filter((item) => Boolean(item.archived_at)));

const form = reactive({
  meeting_type: "party_member_meeting",
  organization: "",
  title: "",
  scheduled_at: localNowInput(),
  host_id: "",
  recorder_id: "",
  venue: "",
});
const attendeeForm = reactive({ display_name: "", role: "member", attendance_status: "expected", voting_eligible: false, note: "" });
const actionForm = reactive({ title: "", responsible_user_id: "", due_at: "", note: "", create_task: true });

const meetingTypes = [
  ["party_member_meeting", "支部党员大会"],
  ["branch_members", "支委会"],
  ["party_group", "党小组会"],
  ["party_class", "党课"],
] as const;

function queryString() {
  const query = new URLSearchParams({ year: String(year.value) });
  if (organization.value.trim()) query.set("organization", organization.value.trim());
  return query.toString();
}

async function load() {
  loading.value = true;
  try {
    [overview.value, meetings.value, users.value] = await Promise.all([
      api.get<Overview>(`/party-life/overview?${queryString()}`),
      api.get<Meeting[]>(`/party-life/meetings?${queryString()}&lifecycle=${lifecycle.value}`),
      api.get<User[]>("/users"),
    ]);
    if (!meetings.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = meetings.value[0]?.id || "";
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "三会一课台账读取失败");
  } finally {
    loading.value = false;
  }
}

async function loadMeetingDetails() {
  if (!selectedId.value) {
    attendees.value = [];
    actions.value = [];
    return;
  }
  try {
    [attendees.value, actions.value] = await Promise.all([
      api.get<Attendee[]>(`/business-meetings/${selectedId.value}/attendees?lifecycle=all`),
      api.get<MeetingAction[]>(`/business-meetings/${selectedId.value}/actions?lifecycle=all`),
    ]);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议闭环信息读取失败");
  }
}

async function openChildArchive(kind: "attendees" | "actions", item: Attendee | MeetingAction) {
  if (!selected.value || item.archived_at) return;
  try {
    childArchiveKind.value = kind;
    childArchiveTarget.value = item;
    childDeletionImpact.value = await api.get(
      `/business-meetings/${selected.value.id}/${kind}/${item.id}/deletion-impact`,
    );
    childArchiveReason.value = "";
    childArchiveVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "无法读取移除影响");
  }
}

async function archiveChild() {
  if (!selected.value || !childArchiveTarget.value || childArchiveReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的移除原因");
    return;
  }
  try {
    await api.deleteBody(
      `/business-meetings/${selected.value.id}/${childArchiveKind.value}/${childArchiveTarget.value.id}`,
      { reason: childArchiveReason.value.trim() },
      { "If-Match": String(childArchiveTarget.value.version) },
    );
    childArchiveVisible.value = false;
    Message.success(childArchiveKind.value === "attendees" ? "出席记录已移至已移除区，可恢复" : "落实项已归档，关联事项同步退出活动列表");
    await Promise.all([load(), loadMeetingDetails()]);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "记录移除失败");
  }
}

async function restoreChild(kind: "attendees" | "actions", item: Attendee | MeetingAction) {
  if (!selected.value || !item.archived_at) return;
  try {
    await api.post(
      `/business-meetings/${selected.value.id}/${kind}/${item.id}/restore`,
      { reason: "经办人复核后恢复误移除记录" },
      { "If-Match": String(item.version) },
    );
    Message.success(kind === "attendees" ? "出席记录已恢复" : "落实项及其关联事项已恢复");
    await Promise.all([load(), loadMeetingDetails()]);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "记录恢复失败");
  }
}

function openCreate() {
  Object.assign(form, {
    meeting_type: "party_member_meeting",
    organization: organization.value,
    title: "",
    scheduled_at: localNowInput(),
    host_id: "",
    recorder_id: "",
    venue: "",
  });
  createVisible.value = true;
}

async function createMeeting() {
  if (!form.organization.trim() || !form.title.trim()) {
    Message.warning("请填写党组织和会议标题");
    return;
  }
  try {
    const created = await api.post<Meeting>("/party-life/meetings", {
      ...form,
      scheduled_at: form.scheduled_at ? localInputToUtc(form.scheduled_at) : null,
      host_id: form.host_id || null,
      recorder_id: form.recorder_id || null,
    });
    selectedId.value = created.id;
    organization.value = form.organization;
    createVisible.value = false;
    Message.success("会议已建立，可继续补充出席、材料与决议落实");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议创建失败");
  }
}

async function addAttendee() {
  if (!selected.value || !attendeeForm.display_name.trim()) {
    Message.warning("请填写参会人员姓名");
    return;
  }
  try {
    // 请求层只接收本次提交的不可变快照，避免后续清空表单时改写仍在处理中的请求数据。
    await api.post(`/business-meetings/${selected.value.id}/attendees`, { ...attendeeForm });
    attendeeVisible.value = false;
    Object.assign(attendeeForm, { display_name: "", role: "member", attendance_status: "expected", voting_eligible: false, note: "" });
    await Promise.all([load(), loadMeetingDetails()]);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "出席记录保存失败");
  }
}

async function addAction() {
  if (!selected.value || !actionForm.title.trim()) {
    Message.warning("请填写决议落实事项");
    return;
  }
  try {
    await api.post(`/business-meetings/${selected.value.id}/actions`, {
      ...actionForm,
      responsible_user_id: actionForm.responsible_user_id || null,
      due_at: actionForm.due_at ? localInputToUtc(actionForm.due_at) : null,
    });
    actionVisible.value = false;
    Object.assign(actionForm, { title: "", responsible_user_id: "", due_at: "", note: "", create_task: true });
    await Promise.all([load(), loadMeetingDetails()]);
    Message.success("决议落实项已保存，并进入事项闭环");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "决议落实项保存失败");
  }
}

async function completeMeeting() {
  if (!selected.value || selected.value.archived_at) return;
  try {
    await api.patch(
      `/business-meetings/${selected.value.id}`,
      { status: "completed" },
      { "If-Match": String(selected.value.version) },
    );
    await load();
    Message.success("会议已标记完成；仍缺少的材料会继续显示为待补充");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议状态更新失败");
  }
}

async function openArchiveMeeting() {
  if (!selected.value || selected.value.archived_at) return;
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
    await api.deleteBody(
      `/business-meetings/${selected.value.id}`,
      { reason: archiveReason.value.trim() },
      { "If-Match": String(selected.value.version) },
    );
    archiveVisible.value = false;
    Message.success("会议已归档，出席、材料、决议和审计记录全部保留");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议归档失败");
  }
}

async function restoreMeeting() {
  if (!selected.value?.archived_at) return;
  try {
    await api.post(
      `/business-meetings/${selected.value.id}/restore`,
      { reason: "经办人核对后恢复会议台账" },
      { "If-Match": String(selected.value.version) },
    );
    Message.success("会议台账已恢复");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "会议恢复失败");
  }
}

async function exportLedger(format: "docx" | "xlsx") {
  try {
    const blob = await api.get<Blob>(`/party-life/ledger/export.${format}?${queryString()}`);
    saveBlobDownload(blob, `${year.value}年三会一课台账.${format}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "台账导出失败");
  }
}

watch(selectedId, loadMeetingDetails);
onMounted(load);
</script>

<template>
  <div class="page party-life-page">
    <header class="page-header">
      <div><p class="page-kicker">党务 · 组织生活闭环</p><h1 class="page-title">“三会一课”统一入口与年度台账</h1><p class="page-description">从计划、准备、召开和记录，到决议落实与归档，一次录入贯穿全流程；频次和人数只做风险提示，不替代党组织判断。</p></div>
      <a-space><PageHelp title="三会一课" :tips="['计划、出席、材料、决议落实和归档使用同一会议记录。', '有对应权限的协同用户可新建会议、补录人员和完成落实项。', '频次和材料缺口是辅助提示，最终以党组织审核和实际材料为准。']" help-query="三会一课 协同 台账" /><a-button @click="exportLedger('docx')"><template #icon><IconDownload /></template>Word 台账</a-button><a-button @click="exportLedger('xlsx')"><template #icon><IconDownload /></template>Excel 台账</a-button><a-button type="primary" @click="openCreate"><template #icon><IconPlus /></template>新建会议</a-button></a-space>
    </header>

    <section class="filters"><a-input-number v-model="year" :min="2000" :max="2200" /><a-input v-model="organization" allow-clear placeholder="按党组织筛选" @press-enter="load" /><a-radio-group v-model="lifecycle" type="button" @change="load"><a-radio value="active">在办会议</a-radio><a-radio value="archived">已归档</a-radio></a-radio-group><a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新</a-button></section>
    <section v-if="overview" class="summary-strip"><article><span>年度记录</span><strong>{{ overview.total }}</strong></article><article><span>已完成</span><strong>{{ overview.completed }}</strong></article><article><span>待补材料</span><strong>{{ overview.needs_completion }}</strong></article><article :class="{ alert: overview.overdue_actions }"><span>逾期落实项</span><strong>{{ overview.overdue_actions }}</strong></article></section>
    <section v-if="overview" class="rule-ribbon"><b>第 {{ overview.current_quarter }} 季度提示</b><span v-for="(hint, key) in overview.quarter_guidance" :key="key">{{ hint }}</span></section>

    <section class="ledger-layout">
      <aside class="meeting-list">
        <button v-for="item in meetings" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="selectedId = item.id">
          <span>{{ item.meeting_type_label }} · {{ item.ledger_state }}</span><strong>{{ item.title }}</strong><small>{{ item.organization }} · {{ formatServerTime(item.scheduled_at, "YYYY-MM-DD HH:mm", "时间待定") }}</small>
        </button>
        <div v-if="!meetings.length" class="empty-state">当前范围内尚无会议，点击“新建会议”开始。</div>
      </aside>
      <section v-if="selected" class="meeting-detail">
        <header><div><span>{{ selected.meeting_type_label }} · {{ selected.ledger_state }}</span><h2>{{ selected.title }}</h2><p>{{ selected.organization }}　{{ selected.venue || "地点待补充" }}<small v-if="selected.archived_at"> · 归档原因：{{ selected.archive_reason }}</small></p></div><a-space><a-button v-if="!selected.archived_at && selected.status !== 'completed'" @click="completeMeeting"><template #icon><IconCheck /></template>标记会议完成</a-button><a-button v-if="!selected.archived_at" status="danger" @click="openArchiveMeeting"><template #icon><IconDelete /></template>归档</a-button><a-button v-else type="primary" @click="restoreMeeting">恢复会议</a-button></a-space></header>
        <div class="completion-grid"><article><span>实际出席</span><strong>{{ selected.present_count }}</strong></article><article><span>已归集材料</span><strong>{{ selected.document_count }}</strong></article><article><span>决议落实项</span><strong>{{ selected.action_count }}</strong></article><article><span>待补内容</span><strong class="text-value">{{ selected.missing_items.join("、") || "无" }}</strong></article></div>
        <div class="detail-columns">
          <section><header><h3><IconUserGroup /> 出席与角色</h3><a-button v-if="!selected.archived_at" size="mini" @click="attendeeVisible = true">添加人员</a-button></header><ul><li v-for="item in activeAttendees" :key="item.id"><div><b>{{ item.display_name }}</b><span>{{ item.role }} · {{ item.attendance_status }}</span></div><a-space><em>{{ item.voting_eligible ? "具备表决资格" : "不参与表决" }}</em><a-button v-if="!selected.archived_at" size="mini" type="text" status="danger" @click="openChildArchive('attendees', item)">移除</a-button></a-space></li></ul><p v-if="!activeAttendees.length" class="empty-state">尚未记录出席人员。</p><details v-if="archivedAttendees.length" class="removed-records"><summary>已移除出席记录（{{ archivedAttendees.length }}）</summary><ul><li v-for="item in archivedAttendees" :key="item.id"><div><b>{{ item.display_name }}</b><span>{{ item.archive_reason }}</span></div><a-button v-if="!selected.archived_at" size="mini" type="text" @click="restoreChild('attendees', item)">恢复</a-button></li></ul></details></section>
          <section><header><h3>决议与落实</h3><a-button v-if="!selected.archived_at" size="mini" @click="actionVisible = true">新增落实项</a-button></header><ul><li v-for="item in activeActions" :key="item.id"><div><b>{{ item.title }}</b><span>{{ formatServerTime(item.due_at, "YYYY-MM-DD", "未设期限") }}</span></div><a-space><em>{{ item.status === "completed" ? "已完成" : "办理中" }}</em><a-button v-if="!selected.archived_at" size="mini" type="text" status="danger" @click="openChildArchive('actions', item)">移除</a-button></a-space></li></ul><p v-if="!activeActions.length" class="empty-state">尚无决议落实项。</p><details v-if="archivedActions.length" class="removed-records"><summary>已移除落实项（{{ archivedActions.length }}）</summary><ul><li v-for="item in archivedActions" :key="item.id"><div><b>{{ item.title }}</b><span>{{ item.archive_reason }}</span></div><a-button v-if="!selected.archived_at" size="mini" type="text" @click="restoreChild('actions', item)">恢复</a-button></li></ul></details></section>
        </div>
      </section>
      <section v-else class="empty-state">选择一场会议查看闭环详情。</section>
    </section>

    <a-modal v-model:visible="createVisible" title="新建三会一课会议" @ok="createMeeting"><a-form :model="form" layout="vertical"><div class="form-grid"><a-form-item label="会议类型" required><a-select v-model="form.meeting_type"><a-option v-for="item in meetingTypes" :key="item[0]" :value="item[0]">{{ item[1] }}</a-option></a-select></a-form-item><a-form-item label="党组织" required><a-input v-model="form.organization" /></a-form-item><a-form-item class="wide" label="会议标题" required><a-input v-model="form.title" /></a-form-item><a-form-item label="计划时间"><a-date-picker v-model="form.scheduled_at" show-time value-format="YYYY-MM-DD HH:mm:ss" /></a-form-item><a-form-item label="地点"><a-input v-model="form.venue" /></a-form-item><a-form-item label="主持人"><a-select v-model="form.host_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item label="记录人"><a-select v-model="form.recorder_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item></div></a-form></a-modal>
    <a-modal v-model:visible="attendeeVisible" title="记录出席人员" @ok="addAttendee"><a-form :model="attendeeForm" layout="vertical"><a-form-item label="姓名" required><a-input v-model="attendeeForm.display_name" /></a-form-item><a-form-item label="会议角色"><a-select v-model="attendeeForm.role"><a-option value="member">党员</a-option><a-option value="host">主持人</a-option><a-option value="recorder">记录人</a-option><a-option value="observer">列席人员</a-option></a-select></a-form-item><a-form-item label="出席状态"><a-select v-model="attendeeForm.attendance_status"><a-option value="expected">计划参加</a-option><a-option value="present">已出席</a-option><a-option value="leave">请假</a-option><a-option value="absent">缺席</a-option></a-select></a-form-item><a-checkbox v-model="attendeeForm.voting_eligible">具备本次表决资格</a-checkbox><a-form-item label="说明"><a-textarea v-model="attendeeForm.note" /></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="actionVisible" title="新增决议落实项" @ok="addAction"><a-form :model="actionForm" layout="vertical"><a-form-item label="落实事项" required><a-input v-model="actionForm.title" /></a-form-item><a-form-item label="负责人"><a-select v-model="actionForm.responsible_user_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item label="完成期限"><a-date-picker v-model="actionForm.due_at" show-time value-format="YYYY-MM-DD HH:mm:ss" /></a-form-item><a-checkbox v-model="actionForm.create_task">同步建立 PartyOps 事项并跟踪提醒</a-checkbox><a-form-item label="办理要求"><a-textarea v-model="actionForm.note" /></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="archiveVisible" title="归档会议台账" ok-text="确认归档" @ok="archiveMeeting"><a-alert type="warning">归档只会从在办列表移出；会议步骤、出席、材料、决议事项和审计不会删除。</a-alert><div v-if="deletionImpact" class="impact-grid"><span>步骤 <b>{{ deletionImpact.steps || 0 }}</b></span><span>材料 <b>{{ deletionImpact.documents || 0 }}</b></span><span>出席 <b>{{ deletionImpact.attendees || 0 }}</b></span><span>落实项 <b>{{ deletionImpact.actions || 0 }}</b></span></div><a-form-item label="归档原因" required><a-textarea v-model="archiveReason" /></a-form-item></a-modal>
    <a-modal v-model:visible="childArchiveVisible" :title="childArchiveKind === 'attendees' ? '移除出席记录' : '归档决议落实项'" ok-text="确认移除" @ok="archiveChild"><a-alert type="warning">此操作可恢复，不会物理删除历史或审计。{{ childDeletionImpact?.linked_task ? "关联事项会同步退出活动列表，恢复时一并恢复。" : "" }}</a-alert><a-form-item label="移除原因" required><a-textarea v-model="childArchiveReason" /></a-form-item></a-modal>
  </div>
</template>

<style scoped>
.party-life-page{max-width:1540px}.filters{display:flex;gap:10px;margin:18px 0}.filters :deep(.arco-input-wrapper){max-width:320px}.summary-strip{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:var(--line);gap:1px}.summary-strip article{padding:18px 22px;background:rgba(255,250,240,.94)}.summary-strip span{color:var(--muted);font-size:12px}.summary-strip strong{display:block;margin-top:6px;color:#483429;font:30px Georgia,serif}.summary-strip .alert strong{color:var(--cinnabar)}.rule-ribbon{display:flex;gap:18px;align-items:center;padding:12px 18px;border:1px solid #e4d3bc;border-top:0;background:#f8efe1;color:#725d4b;font-size:12px;overflow:auto}.rule-ribbon b{flex:none;color:var(--cinnabar)}.rule-ribbon span{flex:none}.ledger-layout{display:grid;grid-template-columns:300px 1fr;min-height:650px;margin-top:18px;border:1px solid var(--line);background:rgba(255,252,245,.94)}.meeting-list{border-right:1px solid var(--line);background:#f7efe2}.meeting-list button{display:grid;width:100%;gap:6px;padding:17px 18px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.meeting-list button.active{background:#fffdf8;box-shadow:inset 4px 0 var(--cinnabar)}.meeting-list span,.meeting-detail>header span{color:var(--cinnabar);font-size:11px}.meeting-list strong{color:#49372c}.meeting-list small{color:var(--muted)}.meeting-detail{padding:24px}.meeting-detail>header,.detail-columns>section>header{display:flex;justify-content:space-between;align-items:start;gap:16px}.meeting-detail h2{margin:6px 0;color:#3f3027;font-family:"Noto Serif SC","Songti SC",serif}.meeting-detail p{color:var(--muted)}.completion-grid{display:grid;grid-template-columns:repeat(4,1fr);margin:20px 0;background:var(--line);gap:1px}.completion-grid article{min-height:92px;padding:15px;background:#faf3e7}.completion-grid span{font-size:11px;color:var(--muted)}.completion-grid strong{display:block;margin-top:8px;color:#543c2e;font:24px Georgia,serif}.completion-grid .text-value{font:13px/1.6 sans-serif}.detail-columns{display:grid;grid-template-columns:1fr 1fr;gap:14px}.detail-columns>section{border:1px solid var(--line);background:#fffdf8}.detail-columns header{padding:14px;border-bottom:1px solid var(--line)}.detail-columns h3{display:flex;gap:7px;align-items:center;margin:0;color:#4a3529}.detail-columns ul{margin:0;padding:0;list-style:none}.detail-columns li{display:flex;justify-content:space-between;gap:15px;padding:13px 14px;border-bottom:1px solid #eee2d1}.detail-columns li div{display:grid;gap:4px}.detail-columns li span,.detail-columns li em{color:var(--muted);font-size:11px;font-style:normal}.empty-state{padding:28px;color:var(--muted);text-align:center}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.form-grid .wide{grid-column:1/-1}@media(max-width:1050px){.ledger-layout{grid-template-columns:1fr}.meeting-list{max-height:250px;overflow:auto;border-right:0;border-bottom:1px solid var(--line)}.detail-columns{grid-template-columns:1fr}}@media(max-width:720px){.summary-strip,.completion-grid,.form-grid{grid-template-columns:1fr}.page-header,.filters{align-items:stretch;flex-direction:column}.rule-ribbon{align-items:flex-start;flex-direction:column}.form-grid .wide{grid-column:auto}}
.impact-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin:16px 0;background:var(--line);border:1px solid var(--line)}.impact-grid span{padding:12px;background:#fffaf0;color:var(--muted);font-size:11px}.impact-grid b{display:block;margin-top:4px;color:var(--charcoal);font:20px Georgia,serif}@media(max-width:720px){.impact-grid{grid-template-columns:1fr 1fr}}
.removed-records{border-top:1px dashed var(--line);background:#f6efe4}.removed-records summary{padding:11px 14px;color:var(--muted);cursor:pointer;font-size:11px}.removed-records li{opacity:.78}
</style>
