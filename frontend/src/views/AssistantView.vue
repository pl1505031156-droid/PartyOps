<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconCopy, IconFile, IconRefresh, IconRobot } from "@arco-design/web-vue/es/icon";
import { Message, Modal } from "@arco-design/web-vue";
import { ApiError, api } from "../api";
import PageHelp from "../components/PageHelp.vue";
import type {
  AIDraft,
  AIOrchestration,
  AIOrchestrationCapabilities,
  AIOrchestrationStep,
  AIRecommendation,
  Task,
  WorkspaceFile,
} from "../types";
import { formatServerTime } from "../utils/datetime";

const drafts = ref<AIDraft[]>([]);
const tasks = ref<Task[]>([]);
const fileResults = ref<WorkspaceFile[]>([]);
const recommendations = ref<AIRecommendation[]>([]);
const selectedDraftId = ref("");
const fileKeyword = ref("");
const loading = ref(false);
const querying = ref(false);
const orchestrationBusy = ref(false);
const orchestrationGoal = ref("");
const orchestration = ref<AIOrchestration | null>(null);
const orchestrationCapabilities = ref<AIOrchestrationCapabilities | null>(null);
const form = reactive({
  capability: "summarize",
  instruction: "",
  task_ids: [] as string[],
  file_ids: [] as string[],
});
const capabilityOptions = [
  { value: "search", label: "资料检索", hint: "根据本地索引查找相关工作" },
  { value: "summarize", label: "内容摘要", hint: "整理所选任务和文件的要点" },
  { value: "classify", label: "材料分类", hint: "建议文件类别和归档标签" },
  { value: "draft_report", label: "周报草拟", hint: "生成本周完成或下周计划草稿" },
  { value: "suggest_breakdown", label: "任务拆解", hint: "建议办理步骤与材料清单" },
  { value: "check_materials", label: "材料检查", hint: "检查可能的材料缺项" },
];

const selectedDraft = computed(() => drafts.value.find((item) => item.id === selectedDraftId.value) || drafts.value[0]);

async function load() {
  loading.value = true;
  try {
    const [draftList, taskList, recommendationList] = await Promise.all([
      api.get<AIDraft[]>("/ai/drafts"),
      api.get<{ items: Task[] }>("/tasks?page_size=100"),
      api.get<AIRecommendation[]>("/ai/recommendations?limit=5").catch(() => []),
    ]);
    drafts.value = draftList;
    tasks.value = taskList.items.filter((item) => item.sensitivity === "normal");
    recommendations.value = recommendationList;
    selectedDraftId.value = selectedDraftId.value || drafts.value[0]?.id || "";
    orchestrationCapabilities.value = await api
      .get<AIOrchestrationCapabilities>("/ai/capabilities")
      .catch(() => null);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "AI 工作区加载失败");
  } finally {
    loading.value = false;
  }
}

const toolLabels: Record<string, string> = {
  "work.search": "查询工作台",
  "navigation.find": "定位功能入口",
  "business_meeting.create_draft": "创建会议草稿",
  "business_meeting.prepare_workflow": "生成会议筹备流程",
  "ledger.inspect": "检查台账字段",
  "ledger.commit": "提交台账导入",
  "party_development.timeline": "生成发展党员时间轴",
  "notifications.recalculate": "重算提醒",
  "files.explain_open": "诊断文件打开",
  "fleet.diagnose": "诊断协同设备",
  "official_format.review_diagnostics": "复核公文排版诊断",
  "fleet.rebind": "重新绑定设备",
  "user.archive": "归档用户并移交责任",
  "user.delete": "进入用户归档流程",
  "settings.network_change": "修改协同公布地址",
};

function toolLabel(step: AIOrchestrationStep) {
  return toolLabels[step.tool_name] || "受控业务步骤";
}

function handoffFor(step: AIOrchestrationStep): { route: string; label: string } | null {
  const handoff = step.result_summary?.handoff;
  if (!handoff || typeof handoff !== "object") return null;
  const value = handoff as Record<string, unknown>;
  return typeof value.route === "string" && typeof value.label === "string"
    ? { route: value.route, label: value.label }
    : null;
}

function openBusinessHandoff(step: AIOrchestrationStep) {
  const handoff = handoffFor(step);
  if (handoff) window.location.assign(handoff.route);
}

async function createOrchestration() {
  if (!orchestrationGoal.value.trim()) {
    Message.warning("请先描述希望系统统筹完成的工作");
    return;
  }
  orchestrationBusy.value = true;
  try {
    orchestration.value = await api.post<AIOrchestration>("/ai/orchestrations", {
      goal: orchestrationGoal.value,
      context_scope: { task_ids: form.task_ids, file_ids: form.file_ids },
    });
    Message.success("已生成受控计划；系统尚未修改任何业务数据");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "生成编排计划失败");
  } finally {
    orchestrationBusy.value = false;
  }
}

async function approveStep(step: AIOrchestrationStep) {
  if (!orchestration.value) return;
  const current = orchestration.value;
  Modal.confirm({
    title: `确认执行：${toolLabel(step)}`,
    content: `风险等级：${step.risk_level}。只确认当前展示的参数范围；计划变化后本次确认自动失效。`,
    okText: "确认当前步骤",
    cancelText: "取消",
    async onOk() {
      orchestrationBusy.value = true;
      try {
        orchestration.value = await api.post<AIOrchestration>(
          `/ai/orchestrations/${current.id}/steps/${step.id}/approve`,
          { approved: true, scope_sha256: step.scope_sha256 },
          { "If-Match": String(current.version) },
        );
      } catch (error) {
        Message.error(error instanceof Error ? error.message : "步骤确认失败");
        throw error;
      } finally {
        orchestrationBusy.value = false;
      }
    },
  });
}

async function executeOrchestration() {
  if (!orchestration.value) return;
  orchestrationBusy.value = true;
  try {
    orchestration.value = await api.post<AIOrchestration>(
      `/ai/orchestrations/${orchestration.value.id}/execute`,
      undefined,
      { "If-Match": String(orchestration.value.version) },
    );
    if (orchestration.value.state === "awaiting_business_action") {
      Message.info("确认门禁已通过；请进入对应业务页面核对并完成写入");
    } else {
      Message.success("只读步骤已完成，结果已写入审计");
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "执行编排计划失败");
  } finally {
    orchestrationBusy.value = false;
  }
}

async function handleRecommendation(item: AIRecommendation, action: "accept" | "dismiss") {
  try {
    await api.post(`/ai/recommendations/${item.id}/${action}`, undefined, {
      "If-Match": String(item.version),
    });
    recommendations.value = recommendations.value.filter((current) => current.id !== item.id);
    if (action === "accept" && item.route) window.location.assign(item.route);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "智能建议状态更新失败");
  }
}

async function searchFiles() {
  if (!fileKeyword.value.trim()) {
    fileResults.value = [];
    return;
  }
  try {
    fileResults.value = await api.get<WorkspaceFile[]>(
      `/workspace/search?keyword=${encodeURIComponent(fileKeyword.value)}&limit=30`,
    );
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文件检索失败");
  }
}

function toggleFile(file: WorkspaceFile) {
  const current = new Set(form.file_ids);
  if (current.has(file.id)) current.delete(file.id);
  else current.add(file.id);
  form.file_ids = [...current];
}

async function sendQuery(confirmExternal = false) {
  if (!form.instruction.trim()) {
    Message.warning("请先说明希望 AI 完成什么");
    return;
  }
  querying.value = true;
  try {
    const draft = await api.post<AIDraft>("/ai/query", {
      ...form,
      confirm_external: confirmExternal,
    });
    Message.success("AI 已生成只读草稿，尚未修改任何任务或文件");
    await load();
    selectedDraftId.value = draft.id;
  } catch (error) {
    if (error instanceof ApiError && error.code === "AI_EXTERNAL_CONFIRM_REQUIRED") {
      const sources = Array.isArray(error.problem.sources) ? error.problem.sources : [];
      Modal.confirm({
        title: "确认最小资料范围",
        content: `当前模型接口不属于已信任内网。本次将发送 ${sources.length} 个已授权资料片段，不发送目录、数据库或密钥。是否继续？`,
        okText: "确认发送",
        cancelText: "取消",
        onOk: () => sendQuery(true),
      });
    } else {
      Message.error(error instanceof Error ? error.message : "AI 请求失败");
    }
  } finally {
    querying.value = false;
  }
}

async function copyDraft() {
  if (!selectedDraft.value) return;
  await navigator.clipboard.writeText(selectedDraft.value.content);
  Message.success("草稿已复制；粘贴到正式记录前请人工核对");
}

async function discardDraft() {
  if (!selectedDraft.value) return;
  await api.post(
    `/ai/drafts/${selectedDraft.value.id}/discard`,
    undefined,
    { "If-Match": String(selectedDraft.value.version) },
  );
  Message.success("草稿已废弃");
  selectedDraftId.value = "";
  await load();
}

onMounted(load);
</script>

<template>
  <div class="page assistant-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">受控 AI 工作助手</p>
        <h1 class="page-title">AI 工作助手</h1>
        <p class="page-description">只读取管理员授权的最小资料片段，只生成草稿；敏感事项、目录路径和密钥永不进入模型。</p>
      </div>
      <a-space>
        <PageHelp
          title="AI 工作助手怎么用"
          :tips="['AI 默认关闭，且只能读取管理员明确授权的最小片段。', '所有结果先进入草稿或审批队列。', '敏感事项、密钥和目录路径不会发送给模型。']"
          help-query="AI 助手"
        />
        <a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新草稿</a-button>
      </a-space>
    </header>

    <a-alert type="warning" class="security-banner">
      AI 默认关闭。启用和调整资料范围只能由管理员在“系统设置 → AI 与权限”中完成。
    </a-alert>

    <section class="orchestration-panel">
      <div class="orchestration-heading">
        <div>
          <p class="page-kicker">全系统智能编排器 · {{ orchestrationCapabilities?.release || "安全降级可用" }}</p>
          <h2>先看计划，再决定执行哪些步骤</h2>
          <p>DeepSeek 负责规划，Needle 2 负责安全路由，BGE 负责中文检索；模型不能直接访问数据库、文件系统或网络。</p>
        </div>
        <a-tag>{{ orchestrationCapabilities?.planner?.role || "规则编排引擎" }}</a-tag>
      </div>
      <div class="orchestration-input">
        <a-textarea
          v-model="orchestrationGoal"
          :auto-size="{ minRows: 2, maxRows: 5 }"
          placeholder="例如：识别党委会议程，建立会议草稿和六步筹备流程，并给负责人生成提醒预览。"
        />
        <a-button type="primary" :loading="orchestrationBusy" @click="createOrchestration">生成跨模块计划</a-button>
      </div>
      <div v-if="orchestration" class="orchestration-result">
        <header>
          <div><strong>{{ orchestration.goal_summary }}</strong><small>{{ orchestration.model_id }} · {{ orchestration.state }}</small></div>
          <a-button
            type="primary"
            :disabled="orchestration.state !== 'awaiting_confirmation'"
            :loading="orchestrationBusy"
            @click="executeOrchestration"
          >执行已确认步骤</a-button>
        </header>
        <ol>
          <li v-for="step in orchestration.steps" :key="step.id">
            <span class="step-order">{{ step.step_order }}</span>
            <div><strong>{{ toolLabel(step) }}</strong><p>{{ step.reason }}</p><small>置信度 {{ Math.round(step.confidence * 100) }}% · 风险 {{ step.risk_level }} · {{ step.status }}</small></div>
            <a-button v-if="step.requires_confirmation && step.status === 'pending'" size="small" @click="approveStep(step)">查看并确认</a-button>
            <a-button v-else-if="handoffFor(step)" size="small" type="primary" @click="openBusinessHandoff(step)">{{ handoffFor(step)?.label }}</a-button>
            <a-tag v-else :color="step.status === 'completed' ? 'green' : 'gray'">{{ step.status }}</a-tag>
          </li>
        </ol>
      </div>
    </section>

    <div class="assistant-layout">
      <section class="prompt-panel">
        <div class="assistant-mark"><IconRobot /><div><strong>只读助手</strong><span>不会自动修改、发布或整理文件</span></div></div>
        <div v-if="recommendations.length" class="recommendation-list">
          <label>可解释建议</label>
          <article v-for="item in recommendations.slice(0, 3)" :key="item.id">
            <div><strong>{{ item.title }}</strong><small>{{ item.reason }}</small></div>
            <a-space><a-button size="mini" type="text" @click="handleRecommendation(item, 'accept')">查看</a-button><a-button size="mini" type="text" @click="handleRecommendation(item, 'dismiss')">忽略</a-button></a-space>
          </article>
        </div>
        <a-form :model="form" layout="vertical">
          <a-form-item label="希望 AI 协助">
            <div class="capability-grid">
              <button
                v-for="option in capabilityOptions"
                :key="option.value"
                type="button"
                :class="{ active: form.capability === option.value }"
                @click="form.capability = option.value"
              >
                <b>{{ option.label }}</b><small>{{ option.hint }}</small>
              </button>
            </div>
          </a-form-item>
          <a-form-item label="具体要求">
            <a-textarea
              v-model="form.instruction"
              :auto-size="{ minRows: 4, maxRows: 10 }"
              placeholder="例如：根据所选事项和文件，草拟一份本周工作完成情况，按成效、问题、下步安排组织。"
            />
          </a-form-item>
          <a-form-item label="允许读取的任务（可选）">
            <a-select v-model="form.task_ids" multiple allow-search allow-clear placeholder="只显示非敏感任务">
              <a-option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</a-option>
            </a-select>
          </a-form-item>
          <a-form-item label="允许读取的原始文件（可选）">
            <a-input-search v-model="fileKeyword" placeholder="先搜索已授权目录中的文件" @search="searchFiles" />
            <div v-if="fileResults.length" class="file-results">
              <button
                v-for="file in fileResults"
                :key="file.id"
                type="button"
                :class="{ active: form.file_ids.includes(file.id) }"
                @click="toggleFile(file)"
              >
                <IconFile /><span>{{ file.name }}</span><small>{{ file.extension || "文件" }}</small>
              </button>
            </div>
            <p v-if="form.file_ids.length" class="selection-count">已选择 {{ form.file_ids.length }} 个文件片段</p>
          </a-form-item>
          <a-button type="primary" long :loading="querying" @click="sendQuery(false)">生成只读草稿</a-button>
        </a-form>
      </section>

      <section class="draft-panel">
        <div class="draft-toolbar">
          <div><span>引用式草稿</span><h2>AI 草稿</h2></div>
          <a-space v-if="selectedDraft">
            <a-button size="small" @click="copyDraft"><template #icon><IconCopy /></template>复制</a-button>
            <a-button size="small" status="danger" @click="discardDraft">废弃</a-button>
          </a-space>
        </div>
        <div v-if="selectedDraft" class="draft-content">
          <div class="draft-meta">
            <span>{{ selectedDraft.capability }}</span>
            <time>{{ formatServerTime(selectedDraft.created_at, "YYYY-MM-DD HH:mm") }}</time>
            <span>{{ selectedDraft.sources.length }} 个资料来源</span>
          </div>
          <h3>{{ selectedDraft.title }}</h3>
          <pre>{{ selectedDraft.content }}</pre>
          <div class="source-list">
            <blockquote v-for="source in selectedDraft.sources" :key="source.id">
              <b>{{ source.type === "task" ? "任务" : "文件" }} · {{ source.name }}</b>
              <p>{{ source.citation || "旧草稿未保存原文引用片段" }}</p>
            </blockquote>
          </div>
          <a-alert type="info">这是草稿。复制或引用到正式任务、报告前，必须由经办人核对事实和表述。</a-alert>
        </div>
        <div v-else class="draft-empty">
          <IconRobot />
          <p>选择明确的资料范围并说明要求，AI 生成的结果会在这里显示。</p>
        </div>
        <div v-if="drafts.length > 1" class="draft-history">
          <label>最近草稿</label>
          <button
            v-for="draft in drafts.slice(0, 8)"
            :key="draft.id"
            type="button"
            :class="{ active: selectedDraft?.id === draft.id }"
            @click="selectedDraftId = draft.id"
          >
            <span>{{ draft.title }}</span><small>{{ formatServerTime(draft.created_at, "MM-DD HH:mm") }}</small>
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page-kicker {
  margin: 0 0 8px;
  color: var(--cinnabar);
  font: 11px Georgia, serif;
  letter-spacing: 0.18em;
}

.security-banner {
  margin-bottom: 16px;
}

.orchestration-panel {
  margin-bottom: 16px;
  padding: 22px 24px;
  background: linear-gradient(135deg, rgba(251, 248, 241, 0.96), rgba(247, 238, 225, 0.78));
  border: 1px solid var(--line);
  border-top: 3px solid var(--cinnabar);
}

.orchestration-heading,
.orchestration-result > header,
.orchestration-input,
.orchestration-result li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.orchestration-heading h2,
.orchestration-heading p { margin: 0; }
.orchestration-heading > div > p:last-child { margin-top: 6px; color: var(--muted); }
.orchestration-input { margin-top: 18px; align-items: stretch; }
.orchestration-input .arco-textarea-wrapper { flex: 1; }
.orchestration-result { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
.orchestration-result header small,
.orchestration-result li small { display: block; margin-top: 4px; color: var(--muted); }
.orchestration-result ol { display: grid; gap: 8px; margin: 14px 0 0; padding: 0; list-style: none; }
.orchestration-result li { padding: 12px; background: rgba(255,255,255,.45); border: 1px solid var(--line-light); }
.orchestration-result li > div { flex: 1; }
.orchestration-result li p { margin: 4px 0 0; color: var(--muted); }
.step-order { display: grid; width: 28px; height: 28px; place-items: center; color: white; background: var(--cinnabar); border-radius: 50%; }

.assistant-layout {
  display: grid;
  min-height: 650px;
  grid-template-columns: minmax(480px, 0.9fr) minmax(480px, 1.1fr);
  gap: 16px;
}

.prompt-panel,
.draft-panel {
  padding: 24px;
  background: rgba(251, 248, 241, 0.7);
  border: 1px solid var(--line);
}

.assistant-mark {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.assistant-mark > svg {
  color: var(--cinnabar);
  font-size: 28px;
}

.assistant-mark strong,
.assistant-mark span {
  display: block;
}

.recommendation-list {
  margin: -6px 0 20px;
  border-top: 1px solid var(--line-light);
}

.recommendation-list > label {
  display: block;
  padding: 10px 0 6px;
  color: var(--cinnabar);
  font-size: 10px;
  letter-spacing: 0.12em;
}

.recommendation-list article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0;
  border-top: 1px solid var(--line-light);
}

.recommendation-list strong,
.recommendation-list small {
  display: block;
}

.recommendation-list small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.assistant-mark span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.capability-grid {
  display: grid;
  width: 100%;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

.capability-grid button {
  min-height: 70px;
  padding: 10px;
  text-align: left;
  background: transparent;
  border: 1px solid var(--line);
  cursor: pointer;
}

.capability-grid button.active {
  color: var(--cinnabar);
  background: rgba(180, 35, 24, 0.05);
  border-color: rgba(180, 35, 24, 0.42);
}

.capability-grid b,
.capability-grid small {
  display: block;
}

.capability-grid b {
  font-size: 12px;
}

.capability-grid small {
  margin-top: 5px;
  color: var(--muted);
  font-size: 9px;
  line-height: 1.5;
}

.file-results {
  width: 100%;
  max-height: 150px;
  margin-top: 6px;
  overflow: auto;
  border: 1px solid var(--line);
}

.file-results button {
  display: grid;
  width: 100%;
  align-items: center;
  grid-template-columns: 20px minmax(0, 1fr) 50px;
  gap: 6px;
  padding: 7px 10px;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--line-light);
  cursor: pointer;
}

.file-results button.active {
  color: var(--cinnabar);
  background: rgba(180, 35, 24, 0.06);
}

.file-results span {
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-results small,
.selection-count {
  color: var(--muted);
  font-size: 9px;
}

.draft-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 17px;
  border-bottom: 2px solid var(--charcoal);
}

.draft-toolbar span {
  color: var(--cinnabar);
  font: 10px Georgia, serif;
  letter-spacing: 0.16em;
}

.draft-toolbar h2 {
  margin: 5px 0 0;
  font-size: 20px;
}

.draft-content {
  padding-top: 18px;
}

.draft-meta {
  display: flex;
  gap: 14px;
  color: var(--muted);
  font-size: 10px;
}

.draft-content h3 {
  margin: 18px 0 10px;
  font-size: 17px;
}

.draft-content pre {
  max-height: 360px;
  overflow: auto;
  margin: 0 0 18px;
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.9;
}

.source-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-bottom: 16px;
}

.source-list blockquote {
  flex: 1 1 260px;
  margin: 0;
  padding: 8px 10px;
  color: var(--cinnabar);
  font-size: 9px;
  background: rgba(180, 35, 24, 0.06);
  border-left: 2px solid var(--cinnabar);
}

.source-list p {
  margin: 4px 0 0;
  color: var(--muted);
  line-height: 1.6;
}

.draft-empty {
  display: flex;
  min-height: 430px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  text-align: center;
}

.draft-empty svg {
  color: #aaa096;
  font-size: 44px;
}

.draft-empty p {
  max-width: 330px;
  line-height: 1.8;
}

.draft-history {
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.draft-history label {
  display: block;
  margin-bottom: 5px;
  color: var(--muted);
  font-size: 10px;
}

.draft-history button {
  display: flex;
  width: 100%;
  justify-content: space-between;
  padding: 7px 4px;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--line-light);
  cursor: pointer;
}

.draft-history button.active {
  color: var(--cinnabar);
}

.draft-history span,
.draft-history small {
  font-size: 10px;
}

.draft-history small {
  color: var(--muted);
}
</style>
