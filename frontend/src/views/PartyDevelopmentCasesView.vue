<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { IconCalendar, IconDownload, IconPlus, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, saveBlobDownload } from "../api";
import PageHelp from "../components/PageHelp.vue";
import { formatServerTime } from "../utils/datetime";

interface Milestone { id: string; milestone_type: string; actual_at: string | null; legal_earliest_at: string | null; legal_deadline_at: string | null; planned_at: string | null; adjusted_at: string | null; legal_basis: string; planning_basis?: string; plan_kind: string; reminder_days: number[]; version: number; }
interface DevelopmentCase { id: string; party_committee: string; party_branch: string; name: string; gender: string; ethnicity: string; birth_date: string | null; education: string; application_at: string; activist_at: string | null; training_contacts: string[]; introducers: string[]; development_object_at: string | null; probationary_at: string | null; converted_at: string | null; stage: string; status: string; rule_version: string; version: number; milestones: Milestone[]; }
interface Statistics { total: number; stage_counts: Record<string, number>; upcoming_60_days: number; overdue: number; }
interface ReferenceNode { key: string; title: string; reference_date: string; persisted_reference_date: string | null; adjusted_date: string | null; effective_date: string; planning_basis: string; provisional: boolean; version: number; }
interface ReferencePlan { disclaimer: string; provisional: boolean; requires_confirmation?: boolean; nodes: ReferenceNode[]; profile_snapshot: { name?: string; version?: number } | null; }

const cases = ref<DevelopmentCase[]>([]);
const statistics = ref<Statistics>({ total: 0, stage_counts: {}, upcoming_60_days: 0, overdue: 0 });
const selectedId = ref("");
const createVisible = ref(false);
const creating = ref(false);
const actualVisible = ref(false);
const actualSaving = ref(false);
const planVisible = ref(false);
const planLoading = ref(false);
const planSaving = ref(false);
const referencePlan = ref<ReferencePlan | null>(null);
const planAdjustments = reactive<Record<string, string>>({});
const selected = computed(() => cases.value.find((item) => item.id === selectedId.value) || null);
const legalMilestones = computed(() => selected.value?.milestones.filter((item) => item.plan_kind === "legal" || item.actual_at) || []);
const form = reactive({ party_committee: "", party_branch: "", name: "", gender: "", ethnicity: "", birth_date: "", education: "", application_date: "", activist_date: "", training_contacts_text: "", introducers_text: "", development_object_date: "", probationary_date: "", converted_date: "" });
const actualForm = reactive({ application_date: "", activist_date: "", development_object_date: "", probationary_date: "", converted_date: "" });

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

function dateValue(value: string | null | undefined) { return value ? String(value).slice(0, 10) : ""; }

async function loadReferencePlan(preview = false) {
  if (!selectedId.value) {
    referencePlan.value = null;
    return;
  }
  planLoading.value = true;
  try {
    const suffix = preview ? "/reference-plan/recalculate-preview" : "/reference-plan";
    const method = preview ? api.post<ReferencePlan> : api.get<ReferencePlan>;
    referencePlan.value = await method(`/party-development/cases/${selectedId.value}${suffix}`);
    Object.keys(planAdjustments).forEach((key) => delete planAdjustments[key]);
    referencePlan.value.nodes.forEach((node) => {
      planAdjustments[node.key] = dateValue(node.effective_date || node.reference_date);
    });
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "参考计划读取失败");
  } finally {
    planLoading.value = false;
  }
}

function openActualDates() {
  if (!selected.value) return;
  Object.assign(actualForm, {
    application_date: dateValue(selected.value.application_at),
    activist_date: dateValue(selected.value.activist_at),
    development_object_date: dateValue(selected.value.development_object_at),
    probationary_date: dateValue(selected.value.probationary_at),
    converted_date: dateValue(selected.value.converted_at),
  });
  actualVisible.value = true;
}

async function saveActualDates() {
  if (!selected.value || !actualForm.application_date) {
    Message.warning("入党申请书实际提交日期不能为空");
    return;
  }
  actualSaving.value = true;
  try {
    await api.patch(`/party-development/cases/${selected.value.id}`, {
      application_date: actualForm.application_date,
      activist_date: actualForm.activist_date || null,
      development_object_date: actualForm.development_object_date || null,
      probationary_date: actualForm.probationary_date || null,
      converted_date: actualForm.converted_date || null,
    }, { "If-Match": String(selected.value.version) });
    await api.post(`/party-development/cases/${selected.value.id}/generate-milestones`);
    actualVisible.value = false;
    await load();
    await loadReferencePlan(true);
    planVisible.value = true;
    Message.success("实际日期和法定边界已更新；请预览并确认后续参考计划");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "实际日期保存失败");
  } finally {
    actualSaving.value = false;
  }
}

async function previewReferencePlan() {
  if (!selected.value) return;
  try {
    await api.post(`/party-development/cases/${selected.value.id}/generate-milestones`);
    await load();
    await loadReferencePlan(true);
    planVisible.value = true;
    Message.success("法定边界已核对；参考计划只做预览，确认前不会覆盖原计划");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "参考计划预览失败");
  }
}

async function confirmReferencePlan() {
  if (!selected.value || !referencePlan.value) return;
  planSaving.value = true;
  try {
    const adjustments = Object.fromEntries(referencePlan.value.nodes.map((node) => [
      node.key,
      planAdjustments[node.key] && planAdjustments[node.key] !== dateValue(node.reference_date)
        ? planAdjustments[node.key]
        : null,
    ]));
    await api.put(`/party-development/cases/${selected.value.id}/reference-plan`, { adjustments }, { "If-Match": String(selected.value.version) });
    planVisible.value = false;
    Message.success("参考计划已确认，实际日期未被覆盖");
    await load();
    await loadReferencePlan();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "参考计划保存失败");
  } finally {
    planSaving.value = false;
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

watch(selectedId, () => loadReferencePlan());
onMounted(load);
</script>

<template>
  <div class="page case-page">
    <header class="page-header"><div><p class="page-kicker">工作 · 全周期发展档案</p><h1 class="page-title">党员发展档案与提醒</h1><p class="page-description">实际发生日期、法定边界和参考计划分栏保存；系统只辅助计算，不替代组织研究和审批。</p></div><a-space><PageHelp title="党员发展档案" :tips="['新增档案后会生成法定边界和首轮参考计划，组织结论仍须据实补录。', '实际日期、参考日期和人工调整分开保存；修改上游节点不会覆盖已发生节点。', '提醒和导出都带规则版本，党务人员应在办理前复核最新制度与本单位流程。']" help-query="党员发展档案 提醒 导出" /><a-button @click="exportCases('docx')"><template #icon><IconDownload /></template>导出 Word</a-button><a-button @click="exportCases('xlsx')"><template #icon><IconDownload /></template>导出 Excel</a-button><a-button type="primary" @click="createVisible = true"><template #icon><IconPlus /></template>新增发展档案</a-button></a-space></header>
    <section class="case-stats"><article><span>在办人员</span><strong>{{ statistics.total }}</strong></article><article><span>60 天内节点</span><strong>{{ statistics.upcoming_60_days }}</strong></article><article :class="{ danger: statistics.overdue }"><span>逾期待核查</span><strong>{{ statistics.overdue }}</strong></article><article><span>规则版本</span><strong class="rule-version">{{ selected?.rule_version || "—" }}</strong></article></section>
    <section class="case-workspace">
      <aside><button v-for="item in cases" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="selectedId = item.id"><span>{{ item.party_branch }}</span><strong>{{ item.name }}</strong><small>{{ item.stage }} · {{ formatServerTime(item.application_at, "YYYY-MM-DD") }}</small></button><div v-if="!cases.length" class="empty-state">尚无发展档案。</div></aside>
      <main v-if="selected">
        <header class="person-header"><div><span>{{ selected.party_committee }} / {{ selected.party_branch }}</span><h2>{{ selected.name }}</h2><p>{{ selected.gender || "性别未填" }} · {{ selected.ethnicity || "民族未填" }} · {{ selected.education || "文化程度未填" }}</p></div><a-space><a-button @click="openActualDates">维护实际日期</a-button><a-button @click="previewReferencePlan"><template #icon><IconRefresh /></template>预览后续计划</a-button></a-space></header>
        <a-alert type="info">计算日期只写入“法定最早/截止/参考计划”，不会伪装成已经发生的实际日期；人工调整保留审计。</a-alert>
        <section class="reference-plan-section">
          <div class="subsection-heading"><div><span>内部工作参考</span><h3>从申请书日期自动生成的后续计划</h3></div><small v-if="referencePlan?.provisional">工作日历未完整配置，公示日期待人工复核</small></div>
          <a-spin :loading="planLoading">
            <div class="reference-plan-grid"><article v-for="node in referencePlan?.nodes || []" :key="node.key"><span>参考日期</span><h4>{{ node.title }}</h4><strong><IconCalendar /> {{ dateValue(node.effective_date) }}</strong><p>{{ node.planning_basis }}</p><small v-if="node.adjusted_date">已人工调整；原参考日 {{ dateValue(node.reference_date) }}</small><small v-else-if="node.provisional">待核对节假日</small><small v-else>可在预览中调整</small></article></div>
          </a-spin>
        </section>
        <section class="legal-section"><div class="subsection-heading"><div><span>法定边界与事实记录</span><h3>组织决定和实际办理日期</h3></div><small>未录入时只显示规则边界，不推定已完成</small></div><section class="milestone-grid"><article v-for="item in legalMilestones" :key="item.id" :class="`state-${milestoneState(item)}`"><i /><div><span>{{ item.actual_at ? "实际日期" : "法规边界" }}</span><h3>{{ milestoneLabels[item.milestone_type] || item.milestone_type }}</h3><strong><IconCalendar /> {{ formatServerTime(targetDate(item), "YYYY-MM-DD", "等待组织确认") }}</strong><p>{{ item.legal_basis }}</p><small v-if="item.actual_at">实际完成：{{ formatServerTime(item.actual_at, "YYYY-MM-DD") }}</small><small v-else>提醒：提前 {{ item.reminder_days.join("、") }} 天及逾期</small></div></article><div v-if="!legalMilestones.length" class="empty-state">尚无实际日期或法定边界，请点击“预览后续计划”进行核对。</div></section></section>
      </main>
      <main v-else class="empty-state">选择人员查看节点。</main>
    </section>
    <a-modal v-model:visible="createVisible" title="新增党员发展档案" :ok-loading="creating" @ok="createCase"><a-form :model="form" layout="vertical"><div class="form-grid"><a-form-item label="所属党委" required><a-input v-model="form.party_committee" /></a-form-item><a-form-item label="所属党支部" required><a-input v-model="form.party_branch" /></a-form-item><a-form-item label="姓名" required><a-input v-model="form.name" /></a-form-item><a-form-item label="性别"><a-input v-model="form.gender" /></a-form-item><a-form-item label="民族"><a-input v-model="form.ethnicity" /></a-form-item><a-form-item label="出生年月日"><a-date-picker v-model="form.birth_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="文化程度"><a-input v-model="form.education" /></a-form-item><a-form-item label="提交入党申请时间" required><a-date-picker v-model="form.application_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="列为积极分子时间"><a-date-picker v-model="form.activist_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="列为发展对象时间"><a-date-picker v-model="form.development_object_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="列为预备党员时间"><a-date-picker v-model="form.probationary_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="预备党员转正时间"><a-date-picker v-model="form.converted_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="培养联系人"><a-input v-model="form.training_contacts_text" placeholder="多人用顿号分隔" /></a-form-item><a-form-item label="入党介绍人"><a-input v-model="form.introducers_text" placeholder="多人用顿号分隔" /></a-form-item></div></a-form></a-modal>
    <a-modal v-model:visible="actualVisible" title="维护实际发生日期" :ok-loading="actualSaving" ok-text="保存并预览后续计划" @ok="saveActualDates"><a-alert type="warning">只填写已经真实发生并有材料依据的日期。保存后系统会重算法定边界，但后续参考计划须由你预览确认。</a-alert><a-form :model="actualForm" layout="vertical" class="actual-form"><a-form-item label="入党申请书实际提交日期" required><a-date-picker v-model="actualForm.application_date" value-format="YYYY-MM-DD" /></a-form-item><a-form-item label="实际确定为入党积极分子"><a-date-picker v-model="actualForm.activist_date" value-format="YYYY-MM-DD" allow-clear /></a-form-item><a-form-item label="实际确定为发展对象"><a-date-picker v-model="actualForm.development_object_date" value-format="YYYY-MM-DD" allow-clear /></a-form-item><a-form-item label="支部大会实际接收为预备党员"><a-date-picker v-model="actualForm.probationary_date" value-format="YYYY-MM-DD" allow-clear /></a-form-item><a-form-item label="实际转正审批日期"><a-date-picker v-model="actualForm.converted_date" value-format="YYYY-MM-DD" allow-clear /></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="planVisible" title="预览并确认后续参考计划" :ok-loading="planSaving" ok-text="确认保存参考计划" width="820px" @ok="confirmReferencePlan"><a-alert :type="referencePlan?.requires_confirmation ? 'warning' : 'info'">{{ referencePlan?.requires_confirmation ? "实际日期变化会影响后续参考计划；确认前原计划保持不变。" : "可逐项调整内部参考日期；这些日期不是法定期限，也不代表组织已经作出决定。" }}</a-alert><div class="plan-edit-list"><label v-for="node in referencePlan?.nodes || []" :key="node.key"><span><b>{{ node.title }}</b><small>{{ node.planning_basis }}</small></span><a-date-picker v-model="planAdjustments[node.key]" value-format="YYYY-MM-DD" /></label></div></a-modal>
  </div>
</template>

<style scoped>
.case-page{max-width:1540px}.case-stats{display:grid;grid-template-columns:repeat(4,1fr);margin:18px 0;border:1px solid var(--line);background:var(--line);gap:1px}.case-stats article{padding:20px 24px;background:#fffaf0}.case-stats span{display:block;color:var(--muted);font-size:11px}.case-stats strong{display:block;margin-top:7px;color:#4d382c;font-family:Georgia,"Noto Serif SC",serif;font-size:30px}.case-stats article.danger strong{color:#b42318}.case-stats .rule-version{font-size:20px}.case-workspace{display:grid;grid-template-columns:260px 1fr;min-height:690px;border:1px solid var(--line);background:#fffaf0}.case-workspace>aside{border-right:1px solid var(--line);background:#f8f0e2}.case-workspace>aside button{display:grid;width:100%;gap:5px;padding:17px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.case-workspace>aside button.active{background:#fffdf8;box-shadow:inset 4px 0 #9b2b24}.case-workspace>aside span{color:#9b2b24;font-size:11px}.case-workspace>aside strong{color:#4d382c;font-size:17px}.case-workspace>aside small{color:var(--muted)}.case-workspace>main{min-width:0;padding:26px}.person-header{display:flex;justify-content:space-between;align-items:start;margin-bottom:18px}.person-header span{color:#9b2b24}.person-header h2{margin:6px 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif;font-size:28px}.person-header p{color:var(--muted)}.subsection-heading{display:flex;align-items:end;justify-content:space-between;margin:26px 0 10px;border-bottom:1px solid var(--line);padding-bottom:9px}.subsection-heading span{color:#9b2b24;font-size:10px;letter-spacing:.12em}.subsection-heading h3{margin:4px 0 0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif}.subsection-heading>small{max-width:360px;color:var(--muted);text-align:right}.reference-plan-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-top:1px solid var(--line);border-left:1px solid var(--line)}.reference-plan-grid article{min-height:150px;padding:16px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);background:rgba(255,253,248,.75)}.reference-plan-grid span{color:#9b2b24;font-size:10px}.reference-plan-grid h4{margin:6px 0;color:#4d382c}.reference-plan-grid strong{display:flex;gap:6px;align-items:center;color:#8c5f35}.reference-plan-grid p{min-height:38px;color:#796656;font-size:12px;line-height:1.6}.reference-plan-grid small{color:var(--muted)}.milestone-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:20px}.milestone-grid article{display:grid;grid-template-columns:7px 1fr;min-height:170px;border:1px solid #dfd1bd;background:#fffdf8}.milestone-grid article>i{background:#b78b4a}.milestone-grid article.state-completed>i{background:#4c7a5d}.milestone-grid article.state-overdue>i{background:#b42318}.milestone-grid article.state-upcoming>i{background:#da7b20}.milestone-grid article>div{padding:16px}.milestone-grid span{color:#9b2b24;font-size:10px}.milestone-grid h3{margin:6px 0;color:#4d382c}.milestone-grid strong{display:flex;gap:6px;align-items:center;color:#8c5f35}.milestone-grid p{color:#796656;font-size:12px;line-height:1.6}.milestone-grid small{color:var(--muted)}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.actual-form{display:grid;grid-template-columns:1fr 1fr;gap:0 14px;margin-top:18px}.plan-edit-list{display:grid;max-height:58vh;margin-top:16px;border-top:1px solid var(--line);overflow:auto}.plan-edit-list label{display:grid;grid-template-columns:minmax(0,1fr) 180px;gap:18px;align-items:center;padding:12px;border-bottom:1px solid var(--line-light)}.plan-edit-list b,.plan-edit-list small{display:block}.plan-edit-list small{margin-top:3px;color:var(--muted);font-size:11px}@media(max-width:1200px){.reference-plan-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:1000px){.case-workspace{grid-template-columns:1fr}.case-workspace>aside{max-height:230px;overflow:auto}.milestone-grid{grid-template-columns:1fr}}@media(max-width:720px){.case-stats,.form-grid,.actual-form,.reference-plan-grid{grid-template-columns:1fr}.person-header,.subsection-heading{align-items:stretch;flex-direction:column;gap:10px}.subsection-heading>small{text-align:left}.plan-edit-list label{grid-template-columns:1fr}}
</style>
