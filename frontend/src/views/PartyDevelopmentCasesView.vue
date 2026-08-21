<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconCalendar, IconDownload, IconPlus, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, saveBlobDownload } from "../api";
import { formatServerTime } from "../utils/datetime";

interface Milestone { id: string; milestone_type: string; actual_at: string | null; legal_earliest_at: string | null; legal_deadline_at: string | null; planned_at: string | null; adjusted_at: string | null; legal_basis: string; plan_kind: string; reminder_days: number[]; version: number; }
interface DevelopmentCase { id: string; party_committee: string; party_branch: string; name: string; gender: string; ethnicity: string; birth_date: string | null; education: string; application_at: string; activist_at: string | null; training_contacts: string[]; introducers: string[]; development_object_at: string | null; probationary_at: string | null; converted_at: string | null; stage: string; status: string; rule_version: string; version: number; milestones: Milestone[]; }
interface Statistics { total: number; stage_counts: Record<string, number>; upcoming_60_days: number; overdue: number; }

const cases = ref<DevelopmentCase[]>([]);
const statistics = ref<Statistics>({ total: 0, stage_counts: {}, upcoming_60_days: 0, overdue: 0 });
const selectedId = ref("");
const createVisible = ref(false);
const creating = ref(false);
const selected = computed(() => cases.value.find((item) => item.id === selectedId.value) || null);
const form = reactive({ party_committee: "", party_branch: "", name: "", gender: "", ethnicity: "", birth_date: "", education: "", application_date: "", activist_date: "", training_contacts_text: "", introducers_text: "", development_object_date: "", probationary_date: "", converted_date: "" });

const milestoneLabels: Record<string, string> = {
  application: "提交入党申请书", conversation_window: "谈话建议窗口", conversation_deadline: "谈话截止",
  activist_date: "确定入党积极分子", first_half_year_assessment: "首次半年考察", development_object_earliest: "列为发展对象最早日期",
  development_object_publicity: "发展对象公示", development_object_date: "确定发展对象", political_review: "政治审查",
  training: "集中培训", pre_review_approved: "上级党委预审", branch_acceptance_deadline: "支部大会讨论截止",
  branch_acceptance: "接收预备党员", committee_approval: "党委审批期限", oath_deadline: "入党宣誓",
  probation_end: "预备期满", transition_application: "提交转正申请", transition_branch_meeting: "转正支部大会",
  transition_approval_deadline: "转正审批截止", archive: "材料归档",
};

function splitPeople(value: string) { return [...new Set(value.split(/[、,，\s]+/).map((item) => item.trim()).filter(Boolean))]; }
function cleanPayload() {
  return {
    party_committee: form.party_committee, party_branch: form.party_branch, name: form.name, gender: form.gender,
    ethnicity: form.ethnicity, birth_date: form.birth_date || null, education: form.education,
    application_date: form.application_date, activist_date: form.activist_date || null,
    training_contacts: splitPeople(form.training_contacts_text), introducers: splitPeople(form.introducers_text),
    development_object_date: form.development_object_date || null, probationary_date: form.probationary_date || null,
    converted_date: form.converted_date || null,
  };
}

async function load() {
  try {
    [cases.value, statistics.value] = await Promise.all([api.get<DevelopmentCase[]>("/party-development/cases"), api.get<Statistics>("/party-development/statistics")]);
    if (!selectedId.value || !cases.value.some((item) => item.id === selectedId.value)) selectedId.value = cases.value[0]?.id || "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "发展档案读取失败");
  }
}

async function createCase() {
  if (!form.party_committee.trim() || !form.party_branch.trim() || !form.name.trim() || !form.application_date) {
    Message.warning("请填写党委、党支部、姓名和申请书日期");
    return;
  }
  creating.value = true;
  try {
    const created = await api.post<DevelopmentCase>("/party-development/cases", cleanPayload());
    await api.post(`/party-development/cases/${created.id}/generate-milestones`);
    selectedId.value = created.id;
    createVisible.value = false;
    Message.success("发展档案和参考节点已生成；组织决定仍需人工补录");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "发展档案创建失败");
  } finally {
    creating.value = false;
  }
}

async function regenerate() {
  if (!selected.value) return;
  try {
    await api.post(`/party-development/cases/${selected.value.id}/generate-milestones`);
    Message.success("已按当前规则重新计算法定边界和参考计划，实际日期未被覆盖");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "节点生成失败");
  }
}

async function exportCases(format: "docx" | "xlsx") {
  try {
    const blob = await api.get<Blob>(`/party-development/cases/export.${format}`);
    saveBlobDownload(blob, `党员发展情况统计表.${format}`);
    Message.success(`${format.toUpperCase()} 汇总表已导出`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "发展档案导出失败");
  }
}

function targetDate(item: Milestone) { return item.adjusted_at || item.legal_deadline_at || item.legal_earliest_at || item.planned_at || item.actual_at; }
function milestoneState(item: Milestone) {
  if (item.actual_at) return "completed";
  const target = targetDate(item);
  if (target && new Date(target).getTime() < Date.now()) return "overdue";
  if (target && new Date(target).getTime() - Date.now() <= 60 * 86400000) return "upcoming";
  return "planned";
}

onMounted(load);
</script>

<template>
  <div class="page case-page">
    <header class="page-header"><div><p class="page-kicker">工作 · 全周期发展档案</p><h1 class="page-title">党员发展档案与提醒</h1><p class="page-description">实际发生日期、法定边界和参考计划分栏保存；系统只辅助计算，不替代组织研究和审批。</p></div><a-space><a-button @click="exportCases('docx')"><template #icon><IconDownload /></template>导出 Word</a-button><a-button @click="exportCases('xlsx')"><template #icon><IconDownload /></template>导出 Excel</a-button><a-button type="primary" @click="createVisible = true"><template #icon><IconPlus /></template>新增发展档案</a-button></a-space></header>
    <section class="case-stats"><article><span>在办人员</span><strong>{{ statistics.total }}</strong></article><article><span>60 天内节点</span><strong>{{ statistics.upcoming_60_days }}</strong></article><article :class="{ danger: statistics.overdue }"><span>逾期待核查</span><strong>{{ statistics.overdue }}</strong></article><article><span>规则版本</span><strong class="rule-version">{{ selected?.rule_version || "—" }}</strong></article></section>
    <section class="case-workspace">
      <aside><button v-for="item in cases" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="selectedId = item.id"><span>{{ item.party_branch }}</span><strong>{{ item.name }}</strong><small>{{ item.stage }} · {{ formatServerTime(item.application_at, "YYYY-MM-DD") }}</small></button><div v-if="!cases.length" class="empty-state">尚无发展档案。</div></aside>
      <main v-if="selected">
        <header class="person-header"><div><span>{{ selected.party_committee }} / {{ selected.party_branch }}</span><h2>{{ selected.name }}</h2><p>{{ selected.gender || "性别未填" }} · {{ selected.ethnicity || "民族未填" }} · {{ selected.education || "文化程度未填" }}</p></div><a-button @click="regenerate"><template #icon><IconRefresh /></template>一键生成后续节点</a-button></header>
        <a-alert type="info">计算日期只写入“法定最早/截止/参考计划”，不会伪装成已经发生的实际日期；人工调整保留审计。</a-alert>
        <section class="milestone-grid"><article v-for="item in selected.milestones" :key="item.id" :class="`state-${milestoneState(item)}`"><i /><div><span>{{ item.plan_kind === "legal" ? "法规边界" : "参考计划" }}</span><h3>{{ milestoneLabels[item.milestone_type] || item.milestone_type }}</h3><strong><IconCalendar /> {{ formatServerTime(targetDate(item), "YYYY-MM-DD", "等待组织确认") }}</strong><p>{{ item.legal_basis }}</p><small v-if="item.actual_at">实际完成：{{ formatServerTime(item.actual_at, "YYYY-MM-DD") }}</small><small v-else>提醒：提前 {{ item.reminder_days.join("、") }} 天及逾期</small></div></article></section>
      </main>
      <main v-else class="empty-state">选择人员查看节点。</main>
    </section>
    <a-modal v-model:visible="createVisible" title="新增党员发展档案" :ok-loading="creating" @ok="createCase"><a-form :model="form" layout="vertical"><div class="form-grid"><a-form-item label="所属党委" required><a-input v-model="form.party_committee" /></a-form-item><a-form-item label="所属党支部" required><a-input v-model="form.party_branch" /></a-form-item><a-form-item label="姓名" required><a-input v-model="form.name" /></a-form-item><a-form-item label="性别"><a-input v-model="form.gender" /></a-form-item><a-form-item label="民族"><a-input v-model="form.ethnicity" /></a-form-item><a-form-item label="出生年月日"><a-date-picker v-model="form.birth_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="文化程度"><a-input v-model="form.education" /></a-form-item><a-form-item label="提交入党申请时间" required><a-date-picker v-model="form.application_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="列为积极分子时间"><a-date-picker v-model="form.activist_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="列为发展对象时间"><a-date-picker v-model="form.development_object_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="列为预备党员时间"><a-date-picker v-model="form.probationary_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="预备党员转正时间"><a-date-picker v-model="form.converted_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="培养联系人"><a-input v-model="form.training_contacts_text" placeholder="多人用顿号分隔" /></a-form-item><a-form-item label="入党介绍人"><a-input v-model="form.introducers_text" placeholder="多人用顿号分隔" /></a-form-item></div></a-form></a-modal>
  </div>
</template>

<style scoped>
.case-page{max-width:1540px}.case-stats{display:grid;grid-template-columns:repeat(4,1fr);margin:18px 0;border:1px solid var(--line);background:var(--line);gap:1px}.case-stats article{padding:20px 24px;background:#fffaf0}.case-stats span{display:block;color:var(--muted);font-size:11px}.case-stats strong{display:block;margin-top:7px;color:#4d382c;font-family:Georgia,"Noto Serif SC",serif;font-size:30px}.case-stats article.danger strong{color:#b42318}.case-stats .rule-version{font-size:20px}.case-workspace{display:grid;grid-template-columns:260px 1fr;min-height:690px;border:1px solid var(--line);background:#fffaf0}.case-workspace>aside{border-right:1px solid var(--line);background:#f8f0e2}.case-workspace>aside button{display:grid;width:100%;gap:5px;padding:17px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.case-workspace>aside button.active{background:#fffdf8;box-shadow:inset 4px 0 #9b2b24}.case-workspace>aside span{color:#9b2b24;font-size:11px}.case-workspace>aside strong{color:#4d382c;font-size:17px}.case-workspace>aside small{color:var(--muted)}.case-workspace>main{padding:26px}.person-header{display:flex;justify-content:space-between;align-items:start;margin-bottom:18px}.person-header span{color:#9b2b24}.person-header h2{margin:6px 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif;font-size:28px}.person-header p{color:var(--muted)}.milestone-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:20px}.milestone-grid article{display:grid;grid-template-columns:7px 1fr;min-height:170px;border:1px solid #dfd1bd;background:#fffdf8}.milestone-grid article>i{background:#b78b4a}.milestone-grid article.state-completed>i{background:#4c7a5d}.milestone-grid article.state-overdue>i{background:#b42318}.milestone-grid article.state-upcoming>i{background:#da7b20}.milestone-grid article>div{padding:16px}.milestone-grid span{color:#9b2b24;font-size:10px}.milestone-grid h3{margin:6px 0;color:#4d382c}.milestone-grid strong{display:flex;gap:6px;align-items:center;color:#8c5f35}.milestone-grid p{color:#796656;font-size:12px;line-height:1.6}.milestone-grid small{color:var(--muted)}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}@media(max-width:1000px){.case-workspace{grid-template-columns:1fr}.case-workspace>aside{max-height:230px;overflow:auto}.milestone-grid{grid-template-columns:1fr}}@media(max-width:720px){.case-stats,.form-grid{grid-template-columns:1fr}}
</style>
