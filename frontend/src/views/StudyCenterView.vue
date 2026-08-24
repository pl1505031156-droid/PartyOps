<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconDownload, IconPlus, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, saveBlobDownload } from "../api";
import PageHelp from "../components/PageHelp.vue";
import { useSessionStore } from "../stores/session";
import { formatServerTime, localInputToUtc, localNowInput } from "../utils/datetime";
import type { User } from "../types";

interface Topic {
  id: string;
  quarter: number;
  title: string;
  learning_materials: string[];
  research_topic: string;
  conversion_goal: string;
  sort_order: number;
  version: number;
}
interface StudyPlan {
  id: string;
  organization: string;
  year: number;
  title: string;
  group_leader_id: string | null;
  secretary_id: string | null;
  status: string;
  notes: string;
  version: number;
  created_by: string;
  topics: Topic[];
}
interface StudySession {
  id: string;
  organization: string;
  title: string;
  scheduled_at: string | null;
  status: string;
  study_plan_id: string | null;
  ledger_state: string;
  present_count: number;
  document_count: number;
  action_count: number;
  overdue_action_count: number;
  missing_items: string[];
}

const sessionStore = useSessionStore();
const isAdmin = computed(() => sessionStore.user?.role === "admin");
const year = ref(new Date().getFullYear());
const organization = ref("");
const loading = ref(false);
const plans = ref<StudyPlan[]>([]);
const sessions = ref<StudySession[]>([]);
const users = ref<User[]>([]);
const selectedPlanId = ref("");
const selectedPlan = computed(() => plans.value.find((item) => item.id === selectedPlanId.value) || null);
const canModifySelectedPlan = computed(() => Boolean(
  selectedPlan.value && (
    isAdmin.value
    || sessionStore.user?.id === selectedPlan.value.created_by
    || sessionStore.user?.id === selectedPlan.value.secretary_id
  )
));
const createPlanVisible = ref(false);
const editPlanVisible = ref(false);
const createTopicVisible = ref(false);
const editTopicVisible = ref(false);
const createSessionVisible = ref(false);
const editingTopic = ref<Topic | null>(null);

const planForm = reactive({ organization: "", year: new Date().getFullYear(), title: "", group_leader_id: "", secretary_id: "", notes: "" });
const editPlanForm = reactive({ organization: "", year: new Date().getFullYear(), title: "", group_leader_id: "", secretary_id: "", status: "draft", notes: "" });
const topicForm = reactive({ quarter: 1, title: "", learning_materials_text: "", research_topic: "", conversion_goal: "", sort_order: 0 });
const editTopicForm = reactive({ quarter: 1, title: "", learning_materials_text: "", research_topic: "", conversion_goal: "", sort_order: 0 });
const sessionForm = reactive({ organization: "", title: "", scheduled_at: localNowInput(), host_id: "", recorder_id: "", venue: "" });

function queryString() {
  const query = new URLSearchParams({ year: String(year.value) });
  if (organization.value.trim()) query.set("organization", organization.value.trim());
  return query.toString();
}

async function load() {
  loading.value = true;
  try {
    [plans.value, sessions.value, users.value] = await Promise.all([
      api.get<StudyPlan[]>(`/study-center/plans?${queryString()}`),
      api.get<StudySession[]>(`/study-center/sessions?${queryString()}`),
      api.get<User[]>("/users"),
    ]);
    if (!plans.value.some((item) => item.id === selectedPlanId.value)) selectedPlanId.value = plans.value[0]?.id || "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "中心组学习台账读取失败");
  } finally {
    loading.value = false;
  }
}

function openPlan() {
  Object.assign(planForm, { organization: organization.value, year: year.value, title: `${year.value} 年党委（党组）理论学习中心组学习计划`, group_leader_id: "", secretary_id: "", notes: "" });
  createPlanVisible.value = true;
}

async function createPlan() {
  if (!planForm.organization.trim() || !planForm.title.trim()) {
    Message.warning("请填写党组织和年度计划标题");
    return;
  }
  try {
    const created = await api.post<StudyPlan>("/study-center/plans", { ...planForm, group_leader_id: planForm.group_leader_id || null, secretary_id: planForm.secretary_id || null });
    selectedPlanId.value = created.id;
    organization.value = created.organization;
    year.value = created.year;
    createPlanVisible.value = false;
    await load();
    Message.success("年度学习计划已建立");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "年度计划创建失败");
  }
}

function openEditPlan() {
  if (!selectedPlan.value) return;
  Object.assign(editPlanForm, {
    organization: selectedPlan.value.organization,
    year: selectedPlan.value.year,
    title: selectedPlan.value.title,
    group_leader_id: selectedPlan.value.group_leader_id || "",
    secretary_id: selectedPlan.value.secretary_id || "",
    status: selectedPlan.value.status,
    notes: selectedPlan.value.notes,
  });
  editPlanVisible.value = true;
}

async function updatePlan() {
  if (!selectedPlan.value || !editPlanForm.organization.trim() || !editPlanForm.title.trim()) {
    Message.warning("请填写党组织和年度计划标题");
    return;
  }
  try {
    await api.patch(`/study-center/plans/${selectedPlan.value.id}`, {
      ...editPlanForm,
      group_leader_id: editPlanForm.group_leader_id || null,
      secretary_id: editPlanForm.secretary_id || null,
    }, { "If-Match": String(selectedPlan.value.version) });
    editPlanVisible.value = false;
    await load();
    Message.success("年度学习计划已更新");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "年度计划更新失败");
  }
}

async function createTopic() {
  if (!selectedPlan.value || !topicForm.title.trim()) {
    Message.warning("请先选择年度计划并填写专题");
    return;
  }
  try {
    await api.post(`/study-center/plans/${selectedPlan.value.id}/topics`, {
      quarter: topicForm.quarter,
      title: topicForm.title,
      learning_materials: topicForm.learning_materials_text.split(/[\n、]+/).map((item) => item.trim()).filter(Boolean),
      research_topic: topicForm.research_topic,
      conversion_goal: topicForm.conversion_goal,
      sort_order: topicForm.sort_order,
    });
    createTopicVisible.value = false;
    Object.assign(topicForm, { quarter: 1, title: "", learning_materials_text: "", research_topic: "", conversion_goal: "", sort_order: 0 });
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "学习专题保存失败");
  }
}

function openEditTopic(topic: Topic) {
  editingTopic.value = topic;
  Object.assign(editTopicForm, {
    quarter: topic.quarter,
    title: topic.title,
    learning_materials_text: topic.learning_materials.join("\n"),
    research_topic: topic.research_topic,
    conversion_goal: topic.conversion_goal,
    sort_order: topic.sort_order,
  });
  editTopicVisible.value = true;
}

async function updateTopic() {
  if (!selectedPlan.value || !editingTopic.value || !editTopicForm.title.trim()) {
    Message.warning("请填写专题名称");
    return;
  }
  try {
    await api.patch(
      `/study-center/plans/${selectedPlan.value.id}/topics/${editingTopic.value.id}`,
      {
        ...editTopicForm,
        learning_materials: editTopicForm.learning_materials_text.split(/[\n、]+/).map((item) => item.trim()).filter(Boolean),
        learning_materials_text: undefined,
      },
      { "If-Match": String(editingTopic.value.version) },
    );
    editTopicVisible.value = false;
    editingTopic.value = null;
    await load();
    Message.success("学习专题已更新");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "学习专题更新失败");
  }
}

async function deleteTopic(topic: Topic) {
  if (!selectedPlan.value) return;
  try {
    await api.delete(
      `/study-center/plans/${selectedPlan.value.id}/topics/${topic.id}`,
      { "If-Match": String(topic.version) },
    );
    await load();
    Message.success("学习专题已删除");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "学习专题删除失败");
  }
}

function openSession() {
  if (!selectedPlan.value) {
    Message.warning("请先建立并选择年度学习计划");
    return;
  }
  Object.assign(sessionForm, { organization: selectedPlan.value.organization, title: "集体学习研讨", scheduled_at: localNowInput(), host_id: selectedPlan.value.group_leader_id || "", recorder_id: selectedPlan.value.secretary_id || "", venue: "" });
  createSessionVisible.value = true;
}

async function createSession() {
  if (!selectedPlan.value || !sessionForm.title.trim()) return;
  try {
    await api.post("/study-center/sessions", {
      meeting_type: "study_group",
      ...sessionForm,
      scheduled_at: sessionForm.scheduled_at ? localInputToUtc(sessionForm.scheduled_at) : null,
      host_id: sessionForm.host_id || null,
      recorder_id: sessionForm.recorder_id || null,
      study_plan_id: selectedPlan.value.id,
    });
    createSessionVisible.value = false;
    await load();
    Message.success("学习场次已进入计划、研讨、成果转化和归档闭环");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "学习场次创建失败");
  }
}

async function exportLedger(format: "docx" | "xlsx") {
  try {
    const blob = await api.get<Blob>(`/study-center/ledger/export.${format}?${queryString()}`);
    saveBlobDownload(blob, `${year.value}年理论学习中心组台账.${format}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "台账导出失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page study-page">
    <header class="page-header"><div><p class="page-kicker">党务 · 理论学习制度化</p><h1 class="page-title">党委（党组）理论学习中心组</h1><p class="page-description">年度计划、会前自学、集体研讨、考勤发言、专题调研、成果转化与备案归档统一留痕。</p></div><a-space><PageHelp title="理论学习中心组" :tips="['按年度计划组织季度专题，再由具体学习场次完成考勤、研讨和成果转化。', '具备创建权限的协同用户可以新建场次或维护本人负责的内容，不要求回到主机操作。', 'Word、Excel 台账按当前年度和组织筛选导出，导出前应核对缺项提示。']" help-query="中心组学习 协同 台账" /><a-button @click="exportLedger('docx')"><template #icon><IconDownload /></template>Word 台账</a-button><a-button @click="exportLedger('xlsx')"><template #icon><IconDownload /></template>Excel 台账</a-button><a-button v-if="isAdmin" @click="openPlan"><template #icon><IconPlus /></template>年度计划</a-button><a-button type="primary" @click="openSession"><template #icon><IconPlus /></template>新建学习场次</a-button></a-space></header>
    <a-alert type="info">国家规则提示：集体学习研讨每季度不少于一次；本单位可以设置更高频率。系统不复制未公开内部法规，也不代替组织审核。</a-alert>
    <section class="filters"><a-input-number v-model="year" :min="2000" :max="2200" /><a-input v-model="organization" allow-clear placeholder="按党组织筛选" @press-enter="load" /><a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新</a-button></section>

    <section class="study-layout">
      <aside><button v-for="plan in plans" :key="plan.id" type="button" :class="{ active: selectedPlanId === plan.id }" @click="selectedPlanId = plan.id"><span>{{ plan.year }} 年 · {{ plan.status === "draft" ? "编制中" : plan.status }}</span><strong>{{ plan.title }}</strong><small>{{ plan.organization }} · {{ plan.topics.length }} 个专题</small></button><div v-if="!plans.length" class="empty-state">尚无年度学习计划。</div></aside>
      <section v-if="selectedPlan" class="plan-detail">
        <header><div><span>年度计划</span><h2>{{ selectedPlan.title }}</h2><p>{{ selectedPlan.organization }}</p></div><a-space><a-button v-if="canModifySelectedPlan" @click="openEditPlan">编辑计划</a-button><a-button v-if="canModifySelectedPlan" @click="createTopicVisible = true"><template #icon><IconPlus /></template>添加学习专题</a-button></a-space></header>
        <div class="quarter-grid"><article v-for="quarter in 4" :key="quarter"><header><b>第 {{ quarter }} 季度</b><span>{{ selectedPlan.topics.filter((item) => item.quarter === quarter).length }} 个专题</span></header><div v-for="topic in selectedPlan.topics.filter((item) => item.quarter === quarter)" :key="topic.id" class="topic"><div class="topic-heading"><strong>{{ topic.title }}</strong><a-space v-if="canModifySelectedPlan"><a-button size="mini" type="text" @click="openEditTopic(topic)">编辑</a-button><a-popconfirm content="删除后不会删除已经建立的学习场次，确认删除该专题？" @ok="deleteTopic(topic)"><a-button size="mini" type="text" status="danger">删除</a-button></a-popconfirm></a-space></div><p v-if="topic.research_topic">调研：{{ topic.research_topic }}</p><p v-if="topic.conversion_goal">转化：{{ topic.conversion_goal }}</p><small v-if="topic.learning_materials.length">材料：{{ topic.learning_materials.join("、") }}</small></div><p v-if="!selectedPlan.topics.some((item) => item.quarter === quarter)" class="empty-state">待安排</p></article></div>
      </section>
      <section v-else class="empty-state">选择年度计划查看专题安排。</section>
    </section>

    <section class="session-section"><header><div><p class="page-kicker">学习场次台账</p><h2>研讨、发言与成果转化</h2></div><span>共 {{ sessions.length }} 场</span></header><div class="session-table"><article v-for="item in sessions" :key="item.id"><div><span>{{ item.ledger_state }}</span><h3>{{ item.title }}</h3><p>{{ item.organization }} · {{ formatServerTime(item.scheduled_at, "YYYY-MM-DD HH:mm", "时间待补") }}</p></div><dl><div><dt>出席</dt><dd>{{ item.present_count }}</dd></div><div><dt>材料</dt><dd>{{ item.document_count }}</dd></div><div><dt>成果事项</dt><dd>{{ item.action_count }}</dd></div></dl><small>{{ item.missing_items.length ? `待补：${item.missing_items.join("、")}` : "材料记录已齐备" }}</small></article><div v-if="!sessions.length" class="empty-state">当前范围内尚无学习场次。</div></div></section>

    <a-modal v-model:visible="createPlanVisible" title="建立中心组年度学习计划" @ok="createPlan"><a-form :model="planForm" layout="vertical"><div class="form-grid"><a-form-item label="党组织" required><a-input v-model="planForm.organization" /></a-form-item><a-form-item label="年度" required><a-input-number v-model="planForm.year" :min="2000" :max="2200" /></a-form-item><a-form-item class="wide" label="计划标题" required><a-input v-model="planForm.title" /></a-form-item><a-form-item label="组长"><a-select v-model="planForm.group_leader_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item label="学习秘书"><a-select v-model="planForm.secretary_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item class="wide" label="计划说明"><a-textarea v-model="planForm.notes" /></a-form-item></div></a-form></a-modal>
    <a-modal v-model:visible="editPlanVisible" title="编辑中心组年度学习计划" @ok="updatePlan"><a-form :model="editPlanForm" layout="vertical"><div class="form-grid"><a-form-item label="党组织" required><a-input v-model="editPlanForm.organization" /></a-form-item><a-form-item label="年度" required><a-input-number v-model="editPlanForm.year" :min="2000" :max="2200" /></a-form-item><a-form-item class="wide" label="计划标题" required><a-input v-model="editPlanForm.title" /></a-form-item><a-form-item label="组长"><a-select v-model="editPlanForm.group_leader_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item label="学习秘书"><a-select v-model="editPlanForm.secretary_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item label="计划状态"><a-select v-model="editPlanForm.status"><a-option value="draft">编制中</a-option><a-option value="active">执行中</a-option><a-option value="archived">已归档</a-option></a-select></a-form-item><a-form-item class="wide" label="计划说明"><a-textarea v-model="editPlanForm.notes" /></a-form-item></div></a-form></a-modal>
    <a-modal v-model:visible="createTopicVisible" title="添加学习专题" @ok="createTopic"><a-form :model="topicForm" layout="vertical"><a-form-item label="季度"><a-select v-model="topicForm.quarter"><a-option v-for="quarter in 4" :key="quarter" :value="quarter">第 {{ quarter }} 季度</a-option></a-select></a-form-item><a-form-item label="专题名称" required><a-input v-model="topicForm.title" /></a-form-item><a-form-item label="学习材料（换行分隔）"><a-textarea v-model="topicForm.learning_materials_text" /></a-form-item><a-form-item label="专题调研"><a-textarea v-model="topicForm.research_topic" /></a-form-item><a-form-item label="成果转化目标"><a-textarea v-model="topicForm.conversion_goal" /></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="editTopicVisible" title="编辑学习专题" @ok="updateTopic"><a-form :model="editTopicForm" layout="vertical"><a-form-item label="季度"><a-select v-model="editTopicForm.quarter"><a-option v-for="quarter in 4" :key="quarter" :value="quarter">第 {{ quarter }} 季度</a-option></a-select></a-form-item><a-form-item label="专题名称" required><a-input v-model="editTopicForm.title" /></a-form-item><a-form-item label="学习材料（换行分隔）"><a-textarea v-model="editTopicForm.learning_materials_text" /></a-form-item><a-form-item label="专题调研"><a-textarea v-model="editTopicForm.research_topic" /></a-form-item><a-form-item label="成果转化目标"><a-textarea v-model="editTopicForm.conversion_goal" /></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="createSessionVisible" title="新建集体学习研讨场次" @ok="createSession"><a-form :model="sessionForm" layout="vertical"><a-form-item label="场次标题" required><a-input v-model="sessionForm.title" /></a-form-item><a-form-item label="计划时间"><a-date-picker v-model="sessionForm.scheduled_at" show-time value-format="YYYY-MM-DD HH:mm:ss" /></a-form-item><a-form-item label="地点"><a-input v-model="sessionForm.venue" /></a-form-item><a-form-item label="主持人"><a-select v-model="sessionForm.host_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item><a-form-item label="记录人/学习秘书"><a-select v-model="sessionForm.recorder_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item></a-form></a-modal>
  </div>
</template>

<style scoped>
.study-page{max-width:1540px}.filters{display:flex;gap:10px;margin:18px 0}.filters :deep(.arco-input-wrapper){max-width:320px}.study-layout{display:grid;grid-template-columns:300px 1fr;min-height:500px;border:1px solid var(--line);background:rgba(255,252,245,.94)}.study-layout>aside{border-right:1px solid var(--line);background:#f7efe2}.study-layout>aside button{display:grid;width:100%;gap:6px;padding:17px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.study-layout>aside button.active{background:#fffdf8;box-shadow:inset 4px 0 var(--cinnabar)}.study-layout>aside span,.plan-detail>header span,.session-table article>div>span{color:var(--cinnabar);font-size:11px}.study-layout>aside strong{color:#483429}.study-layout>aside small{color:var(--muted)}.plan-detail{padding:24px}.plan-detail>header,.session-section>header{display:flex;justify-content:space-between;align-items:start}.plan-detail h2,.session-section h2{margin:5px 0;color:#463328;font-family:"Noto Serif SC","Songti SC",serif}.plan-detail p{color:var(--muted)}.quarter-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:20px}.quarter-grid>article{min-height:190px;border:1px solid var(--line);background:#fffdf8}.quarter-grid>article>header{display:flex;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--line);color:#5b4436}.quarter-grid>article>header span{color:var(--muted);font-size:11px}.topic{padding:13px 14px;border-bottom:1px dashed #e1d1bd}.topic-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}.topic strong{color:#4a362a}.topic p,.topic small{display:block;margin:5px 0;color:var(--muted);font-size:11px}.session-section{margin-top:24px}.session-section>header>span{color:var(--muted)}.session-table{border:1px solid var(--line);background:#fffaf0}.session-table article{display:grid;grid-template-columns:1fr 320px 260px;align-items:center;gap:20px;padding:16px 18px;border-bottom:1px solid var(--line)}.session-table h3{margin:4px 0;color:#48352a}.session-table p,.session-table small{color:var(--muted)}.session-table dl{display:grid;grid-template-columns:repeat(3,1fr);margin:0}.session-table dl div{border-left:1px solid var(--line);padding-left:14px}.session-table dt{color:var(--muted);font-size:10px}.session-table dd{margin:4px 0;color:#543a2c;font:20px Georgia,serif}.empty-state{padding:25px;color:var(--muted);text-align:center}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.form-grid .wide{grid-column:1/-1}@media(max-width:1050px){.study-layout{grid-template-columns:1fr}.study-layout>aside{max-height:240px;overflow:auto;border-right:0;border-bottom:1px solid var(--line)}.session-table article{grid-template-columns:1fr}.session-table dl{max-width:360px}}@media(max-width:720px){.page-header,.filters{align-items:stretch;flex-direction:column}.quarter-grid,.form-grid{grid-template-columns:1fr}.form-grid .wide{grid-column:auto}}
</style>
