<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { Message, Modal } from "@arco-design/web-vue";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";
import { useSessionStore } from "../stores/session";
import type {
  AIDraft,
  Contact,
  PeriodType,
  ReportSection,
  Task,
  WorkJournal,
  WorkspaceFile,
} from "../types";
import { beijingNow, formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

interface TopicSpace {
  id: string;
  name: string;
  description: string;
  task_ids: string[];
  file_ids: string[];
  journal_ids: string[];
  contact_ids: string[];
  active: boolean;
  version: number;
}

interface AutomationRule {
  id: string;
  name: string;
  trigger: string;
  conditions: Record<string, unknown>;
  actions: Record<string, unknown>;
  enabled: boolean;
  version: number;
}

interface CalendarEntry {
  id: string;
  date_key: string;
  title: string;
  kind: string;
  is_workday: boolean;
  note: string;
  version: number;
}

interface Comparison {
  id: string;
  left_file_id: string;
  right_file_id: string;
  comparison_type: string;
  result: {
    changed?: boolean;
    lines?: string[];
    left_length?: number;
    right_length?: number;
  };
  created_at: string;
}

interface DuplicateGroup {
  id: string;
  algorithm: string;
  fingerprint: string;
  file_ids: string[];
  status: string;
  created_at: string;
}

interface ReportTemplate {
  id: string;
  name: string;
  period_type: PeriodType;
  description: string;
  sections: ReportSection[];
  active: boolean;
  version: number;
}

const props = withDefaults(defineProps<{ initialTab?: string }>(), {
  initialTab: "topics",
});
const session = useSessionStore();
const activeTab = ref(props.initialTab);
const loading = ref(false);
const tasks = ref<Task[]>([]);
const files = ref<WorkspaceFile[]>([]);
const contacts = ref<Contact[]>([]);
const journals = ref<WorkJournal[]>([]);
const topics = ref<TopicSpace[]>([]);
const rules = ref<AutomationRule[]>([]);
const calendar = ref<CalendarEntry[]>([]);
const comparisons = ref<Comparison[]>([]);
const duplicates = ref<DuplicateGroup[]>([]);
const reportTemplates = ref<ReportTemplate[]>([]);
const approvals = ref<AIDraft[]>([]);
const topicVisible = ref(false);
const topicLifecycle = ref<"active" | "archived">("active");
const topicArchiveVisible = ref(false);
const topicArchiveTarget = ref<TopicSpace | null>(null);
const topicArchiveReason = ref("");
const topicDeletionImpact = ref<{ tasks: number; files: number; journals: number; contacts: number } | null>(null);
const ruleVisible = ref(false);
const calendarVisible = ref(false);
const templateVisible = ref(false);
const editingTemplateId = ref("");
const comparisonLoading = ref(false);
const duplicateLoading = ref(false);

const topicForm = reactive({
  name: "",
  description: "",
  task_ids: [] as string[],
  file_ids: [] as string[],
  journal_ids: [] as string[],
  contact_ids: [] as string[],
});
const ruleForm = reactive({
  name: "",
  trigger: "workspace_file_indexed",
  days: 3,
  name_contains: "",
  path_contains: "",
  extensions: ".docx,.pdf,.xlsx",
  task_title: "",
  material_category: "",
  tags: "",
});
const calendarForm = reactive({
  date_key: beijingNow().format("YYYY-MM-DD"),
  title: "",
  kind: "holiday",
  is_workday: false,
  note: "",
});
const compareForm = reactive({
  left_file_id: "",
  right_file_id: "",
  comparison_type: "text",
});
const templateForm = reactive({
  name: "",
  period_type: "week" as PeriodType,
  description: "",
  sections: ["completed", "next_plan", "carry_over", "risk", "coordination"] as ReportSection[],
  active: true,
});

const sectionOptions: Array<{ value: ReportSection; label: string }> = [
  { value: "completed", label: "本期完成" },
  { value: "next_plan", label: "下期计划" },
  { value: "carry_over", label: "延续事项" },
  { value: "risk", label: "重点问题与风险" },
  { value: "coordination", label: "需要协调事项" },
];
const fileNames = computed(() => Object.fromEntries(files.value.map((item) => [item.id, item.name])));
const selectedComparison = computed(() => comparisons.value[0]);
const pageMeta = computed(() => ({
  topics: ["专题工作空间", "把同一专项的任务、文件、日志和联系人放在一处，不复制原件。"],
  automation: ["自动归档规则", "按文件名、目录和时限生成归档建议，所有业务变更仍需人工确认。"],
  documents: ["文档比较与查重", "核对领导修改内容，并发现完全重复或高度相似文件。"],
  templates: ["报告模板设计器", "自定义周报、月报和台账栏目，不需要修改程序。"],
  ai: ["AI 审批队列", "集中核对带来源引用的 AI 草稿，AI 不直接修改业务数据。"],
}[activeTab.value] || ["管理工具", "按业务领域维护系统能力。"]));

watch(
  () => props.initialTab,
  (value) => {
    activeTab.value = value;
    load();
  },
);

function splitValues(value: string): string[] {
  return value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
}

async function load() {
  loading.value = true;
  try {
    if (activeTab.value === "topics") {
      const [taskResult, fileResult, contactResult, journalResult, topicResult] =
        await Promise.all([
          api.get<{ items: Task[] }>("/tasks?page_size=100"),
          api.get<WorkspaceFile[]>("/workspace/search?limit=200"),
          api.get<Contact[]>("/contacts"),
          api.get<WorkJournal[]>("/work-journal?limit=200"),
          api.get<TopicSpace[]>(`/topics?lifecycle=${topicLifecycle.value}`),
        ]);
      tasks.value = taskResult.items;
      files.value = fileResult.filter((item) => !item.is_directory);
      contacts.value = contactResult;
      journals.value = journalResult;
      topics.value = topicResult;
    } else if (activeTab.value === "automation") {
      rules.value = await api.get<AutomationRule[]>("/automation-rules");
    } else if (activeTab.value === "calendar") {
      calendar.value = await api.get<CalendarEntry[]>(
        `/work-calendar?year=${beijingNow().year()}`,
      );
    } else if (activeTab.value === "documents") {
      const [fileResult, comparisonResult, duplicateResult] = await Promise.all([
        api.get<WorkspaceFile[]>("/workspace/search?limit=200"),
        api.get<Comparison[]>("/document-comparisons"),
        api.get<DuplicateGroup[]>("/duplicates"),
      ]);
      files.value = fileResult.filter((item) => !item.is_directory);
      comparisons.value = comparisonResult;
      duplicates.value = duplicateResult;
    } else if (activeTab.value === "templates") {
      reportTemplates.value = await api.get<ReportTemplate[]>("/report-templates");
    } else if (activeTab.value === "ai") {
      approvals.value = await api.get<AIDraft[]>("/ai/approvals");
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "当前功能加载失败");
  } finally {
    loading.value = false;
  }
}

async function createTopic() {
  if (!topicForm.name.trim()) {
    Message.warning("请填写专题名称");
    return;
  }
  const created = await api.post<TopicSpace>("/topics", {
    name: topicForm.name.trim(),
    description: topicForm.description.trim(),
  });
  await api.patch(
    `/topics/${created.id}`,
    {
      task_ids: topicForm.task_ids,
      file_ids: topicForm.file_ids,
      journal_ids: topicForm.journal_ids,
      contact_ids: topicForm.contact_ids,
    },
    { "If-Match": String(created.version) },
  );
  Object.assign(topicForm, { name: "", description: "", task_ids: [], file_ids: [], journal_ids: [], contact_ids: [] });
  topicVisible.value = false;
  Message.success("专题工作空间已建立");
  await load();
}

async function openArchiveTopic(topic: TopicSpace) {
  try {
    topicArchiveTarget.value = topic;
    topicDeletionImpact.value = await api.get(`/topics/${topic.id}/deletion-impact`);
    topicArchiveReason.value = "";
    topicArchiveVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "专题归档影响读取失败");
  }
}

async function archiveTopic() {
  const topic = topicArchiveTarget.value;
  if (!topic || topicArchiveReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的归档原因");
    return;
  }
  try {
    await api.deleteBody(
      `/topics/${topic.id}`,
      { reason: topicArchiveReason.value.trim() },
      { "If-Match": String(topic.version) },
    );
    topicArchiveVisible.value = false;
    Message.success("专题已归档，关联任务、文件、日志和联系人均未删除");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "专题归档失败");
  }
}

async function restoreTopic(topic: TopicSpace) {
  try {
    await api.post(
      `/topics/${topic.id}/restore`,
      { reason: "经办人核对关联内容后恢复专题" },
      { "If-Match": String(topic.version) },
    );
    Message.success("专题工作空间已恢复");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "专题恢复失败");
  }
}

async function createRule() {
  if (!ruleForm.name.trim()) {
    Message.warning("请填写规则名称");
    return;
  }
  const conditions = ruleForm.trigger === "workspace_file_indexed"
    ? {
        name_contains: ruleForm.name_contains.trim(),
        path_contains: ruleForm.path_contains.trim(),
        extensions: splitValues(ruleForm.extensions).map((item) => item.startsWith(".") ? item.toLowerCase() : `.${item.toLowerCase()}`),
      }
    : { days: ruleForm.days };
  const actions = ruleForm.trigger === "workspace_file_indexed"
    ? {
        type: "archive_suggestion",
        task_title: ruleForm.task_title.trim(),
        material_category: ruleForm.material_category.trim(),
        tags: splitValues(ruleForm.tags),
      }
    : { type: "notify" };
  await api.post("/automation-rules", {
    name: ruleForm.name.trim(),
    trigger: ruleForm.trigger,
    conditions,
    actions,
    enabled: true,
  });
  ruleVisible.value = false;
  Object.assign(ruleForm, { name: "", trigger: "workspace_file_indexed", days: 3, name_contains: "", path_contains: "", extensions: ".docx,.pdf,.xlsx", task_title: "", material_category: "", tags: "" });
  Message.success("自动建议规则已启用");
  await load();
}

async function toggleRule(rule: AutomationRule) {
  await api.patch(`/automation-rules/${rule.id}`, { enabled: !rule.enabled }, { "If-Match": String(rule.version) });
  await load();
}

async function deleteRule(rule: AutomationRule) {
  Modal.warning({
    title: "删除自动规则",
    content: `确认删除“${rule.name}”？历史通知和审计不会删除。`,
    hideCancel: false,
    onOk: async () => {
      await api.delete(`/automation-rules/${rule.id}`, { "If-Match": String(rule.version) });
      Message.success("规则已删除");
      await load();
    },
  });
}

async function createCalendarEntry() {
  if (!calendarForm.title.trim()) {
    Message.warning("请填写日期说明");
    return;
  }
  await api.post("/work-calendar", { ...calendarForm });
  calendarVisible.value = false;
  calendarForm.title = "";
  calendarForm.note = "";
  Message.success("工作日历已更新，周期任务将使用新规则");
  await load();
}

async function deleteCalendarEntry(entry: CalendarEntry) {
  await api.delete(`/work-calendar/${entry.id}`, { "If-Match": String(entry.version) });
  Message.success("日历记录已删除");
  await load();
}

async function compareDocuments() {
  if (!compareForm.left_file_id || !compareForm.right_file_id || compareForm.left_file_id === compareForm.right_file_id) {
    Message.warning("请选择两个不同版本的文件");
    return;
  }
  comparisonLoading.value = true;
  try {
    await api.post("/document-comparisons", compareForm);
    Message.success("版本比较已完成");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件比较失败");
  } finally {
    comparisonLoading.value = false;
  }
}

async function scanDuplicates() {
  duplicateLoading.value = true;
  try {
    duplicates.value = await api.post<DuplicateGroup[]>("/duplicates/scan");
    Message.success(`发现 ${duplicates.value.length} 组重复或近似文件`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "重复检测失败");
  } finally {
    duplicateLoading.value = false;
  }
}

function moveSection(index: number, delta: number) {
  const target = index + delta;
  if (target < 0 || target >= templateForm.sections.length) return;
  const next = [...templateForm.sections];
  [next[index], next[target]] = [next[target], next[index]];
  templateForm.sections = next;
}

function toggleTemplateSection(section: ReportSection) {
  if (templateForm.sections.includes(section)) {
    if (templateForm.sections.length === 1) {
      Message.warning("模板至少保留一个栏目");
      return;
    }
    templateForm.sections = templateForm.sections.filter((item) => item !== section);
  } else {
    templateForm.sections = [...templateForm.sections, section];
  }
}

function editTemplate(template: ReportTemplate) {
  editingTemplateId.value = template.id;
  Object.assign(templateForm, {
    name: template.name,
    period_type: template.period_type,
    description: template.description,
    sections: [...template.sections],
    active: template.active,
  });
  templateVisible.value = true;
}

async function saveTemplate() {
  if (!templateForm.name.trim()) {
    Message.warning("请填写模板名称");
    return;
  }
  if (editingTemplateId.value) {
    const current = reportTemplates.value.find((item) => item.id === editingTemplateId.value);
    if (!current) return;
    await api.patch(
      `/report-templates/${current.id}`,
      { name: templateForm.name.trim(), description: templateForm.description.trim(), sections: templateForm.sections, active: templateForm.active },
      { "If-Match": String(current.version) },
    );
  } else {
    await api.post("/report-templates", {
      name: templateForm.name.trim(),
      period_type: templateForm.period_type,
      description: templateForm.description.trim(),
      sections: templateForm.sections,
    });
  }
  templateVisible.value = false;
  editingTemplateId.value = "";
  Object.assign(templateForm, { name: "", period_type: "week", description: "", sections: ["completed", "next_plan", "carry_over", "risk", "coordination"], active: true });
  Message.success("报告模板已保存");
  await load();
}

async function approveDraft(draft: AIDraft) {
  await api.post(`/ai/drafts/${draft.id}/approve`, undefined, { "If-Match": String(draft.version) });
  Message.success("AI 草稿已审批；仍需复制到正式业务页面后人工保存");
  await load();
}

onMounted(load);
</script>

<template>
  <div class="page efficiency-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">PARTYOPS / {{ activeTab.toUpperCase() }}</p>
        <h1 class="page-title">{{ pageMeta[0] }}</h1>
        <p class="page-description">{{ pageMeta[1] }}</p>
      </div>
      <a-space>
        <PageHelp
          :title="`${pageMeta[0]}怎么用`"
          :tips="['当前页面只维护本业务域的数据。', '自动化和 AI 只生成建议，不直接修改业务数据。', '保存后可从事项、文件和报告两端查看关联。']"
          :help-query="pageMeta[0]"
        />
        <a-button :loading="loading" @click="load">刷新</a-button>
      </a-space>
    </header>

    <a-tabs v-model:active-key="activeTab" type="line" class="efficiency-tabs">
      <a-tab-pane key="topics" title="专题空间">
        <div class="section-toolbar">
          <div><h2>专题工作空间</h2><p>把同一专项的任务、文件、日志和联系人放在一个虚拟空间，不复制原件。</p></div>
          <a-space><a-radio-group v-model="topicLifecycle" type="button" @change="load"><a-radio value="active">使用中</a-radio><a-radio value="archived">已归档</a-radio></a-radio-group><a-button type="primary" @click="topicVisible = true">新建专题</a-button></a-space>
        </div>
        <div class="topic-grid">
          <article v-for="topic in topics" :key="topic.id">
            <span>TOPIC / {{ topic.id.slice(0, 6) }}</span><h3>{{ topic.name }}</h3><p>{{ topic.description || "暂无说明" }}</p>
            <div class="topic-facts"><span><b>{{ topic.task_ids.length }}</b>任务 <b>{{ topic.file_ids.length }}</b>文件 <b>{{ topic.journal_ids.length }}</b>日志 <b>{{ topic.contact_ids.length }}</b>联系人</span><a-button v-if="topic.active" size="mini" type="text" status="danger" @click="openArchiveTopic(topic)">归档</a-button><a-button v-else size="mini" type="text" @click="restoreTopic(topic)">恢复</a-button></div>
          </article>
          <div v-if="!topics.length" class="empty-panel">建立专题后，专项资料不再散落在多个文件夹和群聊中。</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="automation" title="归档规则">
        <div class="section-toolbar">
          <div><h2>自动建议规则</h2><p>只生成提醒和归档建议，不移动文件、不改任务状态。</p></div>
          <a-button type="primary" @click="ruleVisible = true">新建规则</a-button>
        </div>
        <div class="rule-list">
          <article v-for="rule in rules" :key="rule.id">
            <i :class="{ enabled: rule.enabled }"></i>
            <div><strong>{{ rule.name }}</strong><small>{{ rule.trigger }} · {{ JSON.stringify(rule.conditions) }}</small></div>
            <a-switch :model-value="rule.enabled" @change="toggleRule(rule)" />
            <a-button type="text" status="danger" size="small" @click="deleteRule(rule)">删除</a-button>
          </article>
          <div v-if="!rules.length" class="empty-panel">暂无规则。可按文件名、目录或扩展名生成材料类别与标签建议。</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="calendar" title="工作日历">
        <div class="section-toolbar">
          <div><h2>{{ beijingNow().year() }} 年工作日历</h2><p>正式时限不变；内部提前节点遇节假日会向前调整到最近工作日。</p></div>
          <a-button v-if="session.user?.role === 'admin'" type="primary" @click="calendarVisible = true">添加节假日/调休</a-button>
        </div>
        <div class="calendar-list">
          <article v-for="entry in calendar" :key="entry.id">
            <time>{{ formatServerTime(`${entry.date_key}T00:00:00+08:00`, "MM月DD日") }}</time>
            <span :class="{ workday: entry.is_workday }">{{ entry.is_workday ? "调休工作日" : "休息日" }}</span>
            <div><strong>{{ entry.title }}</strong><small>{{ entry.note }}</small></div>
            <a-button v-if="session.user?.role === 'admin'" type="text" status="danger" @click="deleteCalendarEntry(entry)">删除</a-button>
          </article>
          <div v-if="!calendar.length" class="empty-panel">未设置特殊日期，系统默认周一至周五为工作日。</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="documents" title="文档核对">
        <div class="document-grid">
          <section>
            <div class="section-toolbar compact"><div><h2>版本文字比较</h2><p>适用于已完成正文提取的 DOCX、PDF 和文本文件。</p></div></div>
            <a-form :model="compareForm" layout="vertical">
              <a-form-item label="旧版本"><a-select v-model="compareForm.left_file_id" allow-search><a-option v-for="file in files" :key="file.id" :value="file.id">{{ file.name }}</a-option></a-select></a-form-item>
              <a-form-item label="新版本"><a-select v-model="compareForm.right_file_id" allow-search><a-option v-for="file in files" :key="file.id" :value="file.id">{{ file.name }}</a-option></a-select></a-form-item>
              <a-button type="primary" long :loading="comparisonLoading" @click="compareDocuments">开始比较</a-button>
            </a-form>
          </section>
          <section class="diff-panel">
            <header><strong>最近比较结果</strong><span v-if="selectedComparison">{{ selectedComparison.result.changed ? "发现差异" : "内容一致" }}</span></header>
            <pre v-if="selectedComparison?.result.lines?.length"><code v-for="(line, index) in selectedComparison.result.lines" :key="index" :class="{ add: line.startsWith('+'), remove: line.startsWith('-') }">{{ line }}
</code></pre>
            <div v-else class="empty-panel">选择领导修改前后的两个版本，即可逐行查看新增和删除内容。</div>
          </section>
        </div>
        <div class="section-toolbar duplicate-heading">
          <div><h2>重复与近似文件</h2><p>精确重复按 SHA-256，近似文本按内容指纹；系统只提出建议，不自动删除。</p></div>
          <a-button v-if="session.user?.role === 'admin'" :loading="duplicateLoading" @click="scanDuplicates">重新扫描</a-button>
        </div>
        <div class="duplicate-list">
          <article v-for="group in duplicates" :key="group.id"><span>{{ group.algorithm === "sha256" ? "完全重复" : "内容近似" }}</span><div><strong v-for="id in group.file_ids" :key="id">{{ fileNames[id] || id }}</strong></div></article>
          <div v-if="!duplicates.length" class="empty-panel">当前没有已确认的重复组。</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="templates" title="报告设计">
        <div class="section-toolbar">
          <div><h2>报告模板设计器</h2><p>选择周期、栏目和顺序；新建报告时直接套用，无需修改程序。</p></div>
          <a-button type="primary" @click="templateVisible = true">新建模板</a-button>
        </div>
        <div class="template-grid">
          <article v-for="template in reportTemplates" :key="template.id">
            <span>{{ zhLabel(template.period_type, "周期") }} · {{ template.active ? "启用" : "已停用" }}</span><h3>{{ template.name }}</h3><p>{{ template.description || "暂无说明" }}</p>
            <ol><li v-for="section in template.sections" :key="section">{{ sectionOptions.find((item) => item.value === section)?.label }}</li></ol>
            <a-button size="small" @click="editTemplate(template)">编辑栏目</a-button>
          </article>
          <div v-if="!reportTemplates.length" class="empty-panel">建立单位自己的周报、月报、季报和年度台账模板。</div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="ai" title="AI 审批">
        <div class="section-toolbar"><div><h2>AI 草稿审批队列</h2><p>批准只表示内容已人工核对，不会自动写入任务、报告或文件。</p></div></div>
        <div class="approval-list">
          <article v-for="draft in approvals" :key="draft.id">
            <header><strong>{{ draft.title }}</strong><span>{{ formatServerTime(draft.created_at, "MM-DD HH:mm") }}</span></header>
            <pre>{{ draft.content }}</pre>
            <div class="citations"><blockquote v-for="source in draft.sources" :key="source.id"><b>{{ source.name }}</b><p>{{ source.citation || "旧草稿未保存引用片段" }}</p></blockquote></div>
            <a-button type="primary" @click="approveDraft(draft)">确认核对并批准</a-button>
          </article>
          <div v-if="!approvals.length" class="empty-panel">暂无等待审批的 AI 草稿。</div>
        </div>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:visible="topicVisible" title="建立专题工作空间" width="720px" @ok="createTopic">
      <a-form :model="topicForm" layout="vertical">
        <div class="two-columns"><a-form-item label="专题名称"><a-input v-model="topicForm.name" /></a-form-item><a-form-item label="专题说明"><a-input v-model="topicForm.description" /></a-form-item></div>
        <a-form-item label="关联任务"><a-select v-model="topicForm.task_ids" multiple allow-search><a-option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</a-option></a-select></a-form-item>
        <a-form-item label="关联文件"><a-select v-model="topicForm.file_ids" multiple allow-search><a-option v-for="file in files" :key="file.id" :value="file.id">{{ file.name }}</a-option></a-select></a-form-item>
        <div class="two-columns">
          <a-form-item label="关联日志"><a-select v-model="topicForm.journal_ids" multiple allow-search><a-option v-for="journal in journals" :key="journal.id" :value="journal.id">{{ journal.title }}</a-option></a-select></a-form-item>
          <a-form-item label="关联联系人"><a-select v-model="topicForm.contact_ids" multiple allow-search><a-option v-for="contact in contacts" :key="contact.id" :value="contact.id">{{ contact.name }} · {{ contact.organization }}</a-option></a-select></a-form-item>
        </div>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="topicArchiveVisible" title="归档专题工作空间" ok-text="确认归档" @ok="archiveTopic"><a-alert type="warning">归档只隐藏专题容器，不会删除任何原任务、文件、日志或联系人。</a-alert><div v-if="topicDeletionImpact" class="topic-impact"><span>任务 <b>{{ topicDeletionImpact.tasks }}</b></span><span>文件 <b>{{ topicDeletionImpact.files }}</b></span><span>日志 <b>{{ topicDeletionImpact.journals }}</b></span><span>联系人 <b>{{ topicDeletionImpact.contacts }}</b></span></div><a-form-item label="归档原因" required><a-textarea v-model="topicArchiveReason" /></a-form-item></a-modal>

    <a-modal v-model:visible="ruleVisible" title="新建自动建议规则" width="650px" @ok="createRule">
      <a-form :model="ruleForm" layout="vertical">
        <div class="two-columns"><a-form-item label="规则名称"><a-input v-model="ruleForm.name" /></a-form-item><a-form-item label="触发类型"><a-select v-model="ruleForm.trigger"><a-option value="workspace_file_indexed">文件符合条件</a-option><a-option value="task_due_soon">任务即将截止</a-option><a-option value="task_overdue">任务已经逾期</a-option></a-select></a-form-item></div>
        <template v-if="ruleForm.trigger === 'workspace_file_indexed'">
          <div class="two-columns"><a-form-item label="文件名包含"><a-input v-model="ruleForm.name_contains" /></a-form-item><a-form-item label="目录包含"><a-input v-model="ruleForm.path_contains" /></a-form-item></div>
          <a-form-item label="扩展名"><a-input v-model="ruleForm.extensions" /></a-form-item>
          <div class="two-columns"><a-form-item label="建议关联事项"><a-input v-model="ruleForm.task_title" /></a-form-item><a-form-item label="建议材料类别"><a-input v-model="ruleForm.material_category" /></a-form-item></div>
          <a-form-item label="建议标签"><a-input v-model="ruleForm.tags" placeholder="多个标签用逗号分隔" /></a-form-item>
        </template>
        <a-form-item v-else label="提前天数"><a-input-number v-model="ruleForm.days" :min="0" :max="365" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="calendarVisible" title="添加工作日历记录" @ok="createCalendarEntry">
      <a-form :model="calendarForm" layout="vertical">
        <a-form-item label="日期"><a-date-picker v-model="calendarForm.date_key" value-format="YYYY-MM-DD" style="width:100%" /></a-form-item>
        <a-form-item label="名称"><a-input v-model="calendarForm.title" placeholder="例如：国庆节、周六调休" /></a-form-item>
        <a-form-item label="日期性质"><a-radio-group v-model="calendarForm.is_workday"><a-radio :value="false">休息日</a-radio><a-radio :value="true">调休工作日</a-radio></a-radio-group></a-form-item>
        <a-form-item label="说明"><a-textarea v-model="calendarForm.note" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="templateVisible" :title="editingTemplateId ? '编辑报告模板' : '新建报告模板'" width="640px" @ok="saveTemplate">
      <a-form :model="templateForm" layout="vertical">
        <div class="two-columns"><a-form-item label="模板名称"><a-input v-model="templateForm.name" /></a-form-item><a-form-item label="周期"><a-select v-model="templateForm.period_type" :disabled="Boolean(editingTemplateId)"><a-option value="week">周</a-option><a-option value="month">月</a-option><a-option value="quarter">季度</a-option><a-option value="year">年度</a-option></a-select></a-form-item></div>
        <a-form-item label="用途说明"><a-input v-model="templateForm.description" /></a-form-item>
        <a-form-item v-if="editingTemplateId" label="模板状态"><a-switch v-model="templateForm.active" checked-text="启用" unchecked-text="停用" /><small class="muted">停用后不能新建报告，已有报告和历史数据保留。</small></a-form-item>
        <a-form-item label="栏目及顺序">
          <div class="section-designer">
            <button v-for="option in sectionOptions" :key="option.value" type="button" :class="{ selected: templateForm.sections.includes(option.value) }" @click="toggleTemplateSection(option.value)">{{ option.label }}</button>
            <ol><li v-for="(section, index) in templateForm.sections" :key="section"><span>{{ sectionOptions.find((item) => item.value === section)?.label }}</span><a-button-group><a-button size="mini" :disabled="index === 0" @click="moveSection(index, -1)">上移</a-button><a-button size="mini" :disabled="index === templateForm.sections.length - 1" @click="moveSection(index, 1)">下移</a-button></a-button-group></li></ol>
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.efficiency-page { width: 100%; max-width: none; }
.page-kicker { margin: 0 0 8px; color: var(--cinnabar); font: 11px Georgia, serif; letter-spacing: .18em; }
.efficiency-tabs { min-height: 680px; padding: 0 24px 28px; background: rgba(251,248,241,.68); border: 1px solid var(--line); }
.efficiency-tabs :deep(.arco-tabs-nav) { display: none; }
.efficiency-tabs :deep(.arco-tabs-content) { padding-top: 1px; }
.section-toolbar { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; margin:22px 0 18px; }
.section-toolbar.compact { margin-top:0; }.section-toolbar h2{margin:0;font-size:20px}.section-toolbar p{margin:5px 0 0;color:var(--muted);font-size:11px}
.topic-grid,.template-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.topic-grid article,.template-grid article{min-height:190px;padding:20px;background:var(--surface);border:1px solid var(--line)}.topic-grid article>span,.template-grid article>span{color:var(--cinnabar);font:10px Georgia,serif;letter-spacing:.12em}.topic-grid h3,.template-grid h3{margin:12px 0 8px}.topic-grid p,.template-grid p{min-height:44px;color:var(--muted);font-size:11px;line-height:1.7}.topic-grid article>div{display:flex;gap:7px;padding-top:12px;border-top:1px solid var(--line-light);font-size:10px}.topic-grid b{color:var(--cinnabar)}.topic-facts{align-items:center;justify-content:space-between}.topic-impact{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin:16px 0;background:var(--line);border:1px solid var(--line)}.topic-impact span{padding:12px;background:#fffaf0;color:var(--muted);font-size:11px}.topic-impact b{display:block;margin-top:4px;color:var(--charcoal);font:20px Georgia,serif}
.empty-panel{display:grid;min-height:120px;place-items:center;padding:20px;color:var(--muted);font-size:12px;text-align:center;border:1px dashed var(--line)}
.rule-list article,.calendar-list article{display:grid;align-items:center;gap:14px;padding:13px 10px;border-top:1px solid var(--line-light)}.rule-list article{grid-template-columns:10px minmax(0,1fr) 44px 48px}.rule-list i{width:8px;height:8px;background:#999;border-radius:50%}.rule-list i.enabled{background:#3c8b5b}.rule-list strong,.rule-list small,.calendar-list strong,.calendar-list small{display:block}.rule-list small,.calendar-list small{margin-top:3px;color:var(--muted);font-size:10px}.calendar-list article{grid-template-columns:80px 88px minmax(0,1fr) 50px}.calendar-list time{font:16px Georgia,serif}.calendar-list>article>span{padding:3px 7px;color:#a43b32;font-size:10px;background:#f6e4df}.calendar-list>article>span.workday{color:#2d7047;background:#dfefe5}
.document-grid{display:grid;grid-template-columns:360px minmax(0,1fr);gap:20px;margin-top:22px}.document-grid>section{padding:20px;background:var(--surface);border:1px solid var(--line)}.diff-panel header{display:flex;justify-content:space-between;padding-bottom:12px;border-bottom:1px solid var(--line)}.diff-panel header span{color:var(--cinnabar);font-size:11px}.diff-panel pre{max-height:380px;margin:12px 0 0;overflow:auto;font:11px/1.65 Consolas,monospace;white-space:pre-wrap}.diff-panel code{display:block}.diff-panel code.add{color:#287047;background:#e8f3ec}.diff-panel code.remove{color:#a62c24;background:#f7e6e3}.duplicate-heading{margin-top:28px}.duplicate-list article{display:grid;grid-template-columns:90px 1fr;gap:16px;padding:12px;border-top:1px solid var(--line-light)}.duplicate-list article>span{color:var(--cinnabar);font-size:11px}.duplicate-list strong{display:block;margin-bottom:5px;font-size:12px}
.template-grid ol{min-height:80px;padding-left:20px;color:var(--muted);font-size:11px;line-height:1.8}.approval-list article{margin-bottom:18px;padding:20px;background:var(--surface);border:1px solid var(--line)}.approval-list header{display:flex;justify-content:space-between}.approval-list header span{color:var(--muted);font-size:10px}.approval-list>article>pre{max-height:280px;padding:14px;overflow:auto;white-space:pre-wrap;background:var(--paper);border:1px solid var(--line-light)}.citations{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:12px 0}.citations blockquote{margin:0;padding:10px;border-left:3px solid var(--cinnabar);background:rgba(180,35,24,.04)}.citations p{margin:5px 0 0;color:var(--muted);font-size:10px;line-height:1.6}
.two-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.section-designer{width:100%}.section-designer>button{margin:0 6px 8px 0;padding:6px 10px;color:var(--muted);background:transparent;border:1px solid var(--line);cursor:pointer}.section-designer>button.selected{color:#fff;background:var(--cinnabar);border-color:var(--cinnabar)}.section-designer ol{margin:8px 0 0;padding:0;list-style:none;border-top:1px solid var(--line)}.section-designer li{display:flex;align-items:center;justify-content:space-between;padding:8px;border-bottom:1px solid var(--line-light)}
@media(max-width:1100px){.topic-grid,.template-grid{grid-template-columns:repeat(2,1fr)}.document-grid{grid-template-columns:1fr}.citations{grid-template-columns:1fr}}
</style>
