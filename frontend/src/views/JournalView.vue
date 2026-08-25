<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { IconClockCircle, IconDelete, IconEdit, IconPlus, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";
import { formatServerTime, localInputToUtc, localNowInput, serverTime } from "../utils/datetime";
import { localizeEmbeddedCodes } from "../utils/labels";
import type { Task, WorkJournal } from "../types";

const entries = ref<WorkJournal[]>([]);
const tasks = ref<Task[]>([]);
const keyword = ref("");
const actorFilter = ref("");
const typeFilter = ref("");
const dateRange = ref<string[]>([]);
const loading = ref(false);
const lifecycle = ref<"active" | "archived">("active");
const visible = ref(false);
const editing = ref<WorkJournal | null>(null);
const archiveVisible = ref(false);
const archiveTarget = ref<WorkJournal | null>(null);
const archiveReason = ref("");
const deletionImpact = ref<{ revisions: number; task_link: boolean; file_link: boolean; report_link: boolean } | null>(null);
const form = reactive({
  title: "",
  content: "",
  occurred_at: localNowInput(),
  task_id: "",
  change_note: "",
});

const filteredEntries = computed(() => {
  const value = keyword.value.trim().toLowerCase();
  return entries.value.filter((item) => {
    if (value && !`${item.title}${item.content}${item.actor_name}${item.action_label}`.toLowerCase().includes(value)) return false;
    if (actorFilter.value && item.created_by !== actorFilter.value) return false;
    if (typeFilter.value && item.entry_type !== typeFilter.value) return false;
    if (dateRange.value.length === 2) {
      const event = serverTime(item.occurred_at).valueOf();
      const start = serverTime(localInputToUtc(`${dateRange.value[0]}T00:00:00`)).valueOf();
      const end = serverTime(localInputToUtc(`${dateRange.value[1]}T23:59:59`)).valueOf();
      if (event < start || event > end) return false;
    }
    return true;
  });
});
const actors = computed(() => {
  const values = new Map<string, string>();
  entries.value.forEach((entry) => values.set(entry.created_by, entry.actor_name || "系统"));
  return [...values.entries()];
});

function eventSummary(entry: WorkJournal) {
  const parts = [
    `${entry.actor_name || "系统"}于${formatServerTime(entry.occurred_at, "HH:mm")}${entry.action_label || entry.title}`,
  ];
  if (entry.from_status || entry.to_status) {
    parts.push(`${entry.from_status || "未设置"} → ${entry.to_status || "未设置"}`);
  }
  if (entry.material_stage) parts.push(`材料阶段：${entry.material_stage}`);
  return parts.join("；");
}

async function load() {
  loading.value = true;
  try {
    const [journal, taskList] = await Promise.all([
      api.get<WorkJournal[]>(`/work-journal?limit=500&lifecycle=${lifecycle.value}`),
      api.get<{ items: Task[] }>("/tasks?page_size=100"),
    ]);
    entries.value = journal;
    tasks.value = taskList.items;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "工作日志加载失败");
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editing.value = null;
  Object.assign(form, {
    title: "",
    content: "",
    occurred_at: localNowInput(),
    task_id: "",
    change_note: "",
  });
  visible.value = true;
}

function openEdit(entry: WorkJournal) {
  editing.value = entry;
  Object.assign(form, {
    title: entry.title,
    content: entry.content,
    occurred_at: serverTime(entry.occurred_at).format("YYYY-MM-DD HH:mm:ss"),
    task_id: entry.task_id || "",
    change_note: "",
  });
  visible.value = true;
}

async function saveEntry() {
  try {
    const payload = {
      title: form.title,
      content: form.content,
      occurred_at: localInputToUtc(form.occurred_at),
      task_id: form.task_id || null,
      change_note: form.change_note,
    };
    if (editing.value) {
      await api.patch(
        `/work-journal/${editing.value.id}`,
        payload,
        { "If-Match": String(editing.value.version) },
      );
      Message.success("工作日志已修订并保留版本");
    } else {
      await api.post("/work-journal", payload);
      Message.success("工作日志已记录");
    }
    visible.value = false;
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "日志保存失败");
  }
}

async function openArchive(entry: WorkJournal) {
  try {
    archiveTarget.value = entry;
    deletionImpact.value = await api.get(`/work-journal/${entry.id}/deletion-impact`);
    archiveReason.value = "";
    archiveVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "工作日志归档影响读取失败");
  }
}

async function archiveEntry() {
  const entry = archiveTarget.value;
  if (!entry || archiveReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的归档原因");
    return;
  }
  try {
    await api.deleteBody(
      `/work-journal/${entry.id}`,
      { reason: archiveReason.value.trim() },
      { "If-Match": String(entry.version) },
    );
    archiveVisible.value = false;
    Message.success("人工日志已归档，历史修订和关联记录全部保留");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "工作日志归档失败");
  }
}

async function restoreEntry(entry: WorkJournal) {
  try {
    await api.post(
      `/work-journal/${entry.id}/restore`,
      { reason: "记录人核对后恢复人工日志" },
      { "If-Match": String(entry.version) },
    );
    Message.success("人工日志已恢复");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "工作日志恢复失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page journal-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">工作办理全程留痕</p>
        <h1 class="page-title">工作日志</h1>
        <p class="page-description">人工记录与系统事件放在同一时间线；通知与待办提醒统一进入“通知中心”，避免重复查看。</p>
      </div>
      <a-space>
        <PageHelp
          title="工作日志怎么用"
          :tips="['系统事件会自动记录操作人、时间和状态变化。', '人工日志可以修订，但会保留历史版本。', '日志可关联事项、文件和周期报告并转为汇总条目。']"
          help-query="工作日志"
        />
        <a-button aria-label="刷新工作日志" :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新</a-button>
        <a-button aria-label="记录工作" type="primary" @click="openCreate"><template #icon><IconPlus /></template>记录工作</a-button>
      </a-space>
    </header>

    <section class="journal-content" aria-label="工作时间线">
        <div class="journal-toolbar">
          <a-input-search v-model="keyword" allow-clear placeholder="搜索日志标题和内容" />
          <a-select v-model="actorFilter" allow-clear placeholder="全部人员">
            <a-option v-for="[id, name] in actors" :key="id" :value="id">{{ name }}</a-option>
          </a-select>
          <a-select v-model="typeFilter" allow-clear placeholder="全部类型">
            <a-option value="manual">人工日志</a-option>
            <a-option value="system">系统事件</a-option>
          </a-select>
          <a-range-picker v-model="dateRange" value-format="YYYY-MM-DD" />
          <a-radio-group v-model="lifecycle" type="button" @change="load"><a-radio value="active">当前日志</a-radio><a-radio value="archived">已归档</a-radio></a-radio-group>
          <div class="journal-legend"><span class="manual"></span>人工日志 <span class="system"></span>系统事件</div>
        </div>
        <a-spin :loading="loading">
          <div v-if="filteredEntries.length" class="timeline">
            <article v-for="entry in filteredEntries" :key="entry.id" :class="entry.entry_type">
              <div class="timeline-time">
                <strong>{{ formatServerTime(entry.occurred_at, "MM-DD") }}</strong>
                <span>{{ formatServerTime(entry.occurred_at, "HH:mm") }}</span>
              </div>
              <div class="timeline-dot"><IconClockCircle /></div>
              <div class="timeline-card">
                <section class="journal-action">
                  <div class="timeline-heading">
                    <div>
                      <span>{{ entry.entry_type === "system" ? "系统事件" : "人工日志" }} · {{ entry.actor_name }}（{{ entry.actor_role_label }}）</span>
                      <h3>{{ entry.title }}</h3>
                    </div>
                    <a-space v-if="!entry.immutable"><a-button v-if="!entry.archived_at" type="text" size="mini" @click="openEdit(entry)"><template #icon><IconEdit /></template>修订</a-button><a-button v-if="!entry.archived_at" type="text" size="mini" status="danger" @click="openArchive(entry)"><template #icon><IconDelete /></template>归档</a-button><a-button v-else type="text" size="mini" @click="restoreEntry(entry)">恢复</a-button></a-space>
                  </div>
                </section>
                <section class="journal-detail">
                  <p class="event-summary">{{ eventSummary(entry) }}</p>
                  <p v-if="entry.content">{{ localizeEmbeddedCodes(entry.content) }}</p><small v-if="entry.archived_at" class="archive-note">归档原因：{{ entry.archive_reason }}</small>
                </section>
                <aside class="journal-meta">
                  <span class="meta-label">追溯信息</span>
                  <p v-if="entry.created_at !== entry.occurred_at" class="recorded-time">
                    系统记录时间<br>{{ formatServerTime(entry.created_at, "YYYY-MM-DD HH:mm:ss") }}（北京时间）
                  </p>
                  <RouterLink v-if="entry.task_id" :to="`/tasks/${entry.task_id}`" class="task-link">
                    关联任务 · {{ entry.task_title || entry.task_id }}
                  </RouterLink>
                  <span v-if="entry.created_at === entry.occurred_at && !entry.task_id" class="meta-empty">无额外关联信息</span>
                </aside>
              </div>
            </article>
          </div>
          <div v-else class="empty-state">尚无工作日志。新建任务、变更状态或上传材料后，系统会自动记录。</div>
        </a-spin>
    </section>

    <a-modal v-model:visible="visible" :title="editing ? '修订工作日志' : '记录工作日志'" @ok="saveEntry">
      <a-form :model="form" layout="vertical">
        <a-form-item label="日志标题"><a-input v-model="form.title" /></a-form-item>
        <a-form-item label="工作时间（北京时间）"><a-date-picker v-model="form.occurred_at" show-time value-format="YYYY-MM-DD HH:mm:ss" /></a-form-item>
        <a-form-item label="关联任务">
          <a-select v-model="form.task_id" allow-clear allow-search placeholder="可选">
            <a-option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.title }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="工作内容"><a-textarea v-model="form.content" :auto-size="{ minRows: 4, maxRows: 10 }" /></a-form-item>
        <a-form-item v-if="editing" label="修订说明"><a-textarea v-model="form.change_note" placeholder="说明本次修订内容，原版本会自动保留" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="archiveVisible" title="归档人工工作日志" ok-text="确认归档" @ok="archiveEntry"><a-alert type="warning">系统事件属于审计记录不可删除；人工日志归档后可恢复，修订历史与事项/文件/报告关联不会移除。</a-alert><div v-if="deletionImpact" class="journal-impact"><span>修订版本 <b>{{ deletionImpact.revisions }}</b></span><span>关联事项 <b>{{ deletionImpact.task_link ? 1 : 0 }}</b></span><span>关联文件 <b>{{ deletionImpact.file_link ? 1 : 0 }}</b></span><span>关联报告 <b>{{ deletionImpact.report_link ? 1 : 0 }}</b></span></div><a-form-item label="归档原因" required><a-textarea v-model="archiveReason" /></a-form-item></a-modal>
  </div>
</template>

<style scoped>
.journal-impact{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin:16px 0;background:var(--line);border:1px solid var(--line)}.journal-impact span{padding:12px;background:#fffaf0;color:var(--muted);font-size:11px}.journal-impact b{display:block;margin-top:4px;color:var(--charcoal);font:20px Georgia,serif}.archive-note{display:block;margin-top:8px;color:var(--cinnabar)}
.page-kicker {
  margin: 0 0 8px;
  color: var(--cinnabar);
  font: 11px Georgia, serif;
  letter-spacing: 0.18em;
}

.journal-toolbar,
.notice-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 22px;
  flex-wrap: wrap;
}

/* Arco Spin 默认是 inline-block，会按内容宽度收缩，导致宽屏下时间线右侧留白。 */
.journal-page :deep(.arco-spin) {
  display: block;
  width: 100%;
}

.journal-toolbar .arco-input-search {
  width: 360px;
}

.journal-toolbar .arco-select {
  width: 130px;
}

.journal-toolbar .arco-picker {
  width: 260px;
}

.journal-legend {
  color: var(--muted);
  font-size: 11px;
}

.journal-legend span {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin: 0 5px 0 16px;
  border-radius: 50%;
}

.journal-legend .manual {
  background: var(--cinnabar);
}

.journal-legend .system {
  background: #91877c;
}

.timeline {
  width: 100%;
  max-width: none;
}

.timeline article {
  display: grid;
  min-height: 124px;
  grid-template-columns: 70px 36px minmax(0, 1fr);
}

.timeline-time {
  padding-top: 12px;
  text-align: right;
}

.timeline-time strong,
.timeline-time span {
  display: block;
}

.timeline-time strong {
  font: 15px Georgia, serif;
}

.timeline-time span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.timeline-dot {
  position: relative;
  display: flex;
  justify-content: center;
  padding-top: 13px;
  color: var(--cinnabar);
}

.timeline-dot::after {
  position: absolute;
  top: 34px;
  bottom: 0;
  width: 1px;
  content: "";
  background: var(--line);
}

.system .timeline-dot {
  color: #91877c;
}

.timeline-card {
  display: grid;
  grid-template-columns: minmax(250px, 0.85fr) minmax(360px, 1.35fr) minmax(220px, 0.8fr);
  gap: 0;
  margin-bottom: 12px;
  padding: 0;
  background: rgba(251, 248, 241, 0.7);
  border: 1px solid var(--line);
}

.journal-action,
.journal-detail,
.journal-meta {
  min-width: 0;
  padding: 18px 20px;
}

.journal-detail,
.journal-meta {
  border-left: 1px solid var(--line-light);
}

.journal-action {
  box-shadow: inset 3px 0 var(--cinnabar);
}

.system .journal-action {
  box-shadow: inset 3px 0 #91877c;
}

.timeline-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.timeline-heading span {
  color: var(--muted);
  font-size: 10px;
}

.timeline-heading h3 {
  margin: 4px 0 0;
  font-size: 14px;
}

.journal-detail > p,
.journal-meta > p {
  margin: 10px 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.journal-detail .event-summary {
  margin-top: 0;
  color: var(--charcoal);
  font-weight: 600;
}

.journal-meta .recorded-time {
  margin: 8px 0 14px;
  font-size: 10px;
}

.meta-label {
  display: block;
  color: var(--muted);
  font-size: 9px;
  letter-spacing: 0.14em;
}

.meta-empty {
  display: block;
  margin-top: 12px;
  color: var(--muted);
  font-size: 10px;
}

.task-link {
  display: block;
  color: var(--cinnabar);
  font-size: 11px;
  line-height: 1.7;
}

.notice-toolbar p {
  color: var(--muted);
}

.notice-list {
  width: 100%;
  max-width: none;
  border-top: 1px solid var(--line);
}

.notice-list button {
  display: grid;
  width: 100%;
  min-height: 78px;
  align-items: center;
  grid-template-columns: 36px minmax(0, 1fr) 100px;
  gap: 12px;
  padding: 12px 16px;
  text-align: left;
  background: rgba(251, 248, 241, 0.48);
  border: 0;
  border-bottom: 1px solid var(--line-light);
  cursor: pointer;
}

.notice-list button.unread {
  background: rgba(180, 35, 24, 0.05);
}

.notice-mark {
  display: flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: 50%;
}

.unread .notice-mark {
  color: var(--cinnabar);
  border-color: rgba(180, 35, 24, 0.35);
}

.notice-list strong {
  font-size: 13px;
}

.notice-list p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 11px;
}

.notice-list time {
  color: var(--muted);
  font-size: 10px;
  text-align: right;
}

@media (max-width: 1500px) {
  .timeline-card {
    grid-template-columns: minmax(240px, 0.85fr) minmax(0, 1.4fr);
  }

  .journal-meta {
    display: flex;
    grid-column: 1 / -1;
    align-items: center;
    gap: 18px;
    padding-top: 12px;
    padding-bottom: 12px;
    border-top: 1px solid var(--line-light);
    border-left: 0;
  }

  .journal-meta .recorded-time {
    margin: 0;
  }

  .journal-meta .task-link {
    margin-left: auto;
  }
}

@media (max-width: 1180px) {
  .timeline article {
    grid-template-columns: 58px 30px minmax(0, 1fr);
  }

  .timeline-card {
    grid-template-columns: 1fr;
  }

  .journal-detail,
  .journal-meta {
    border-top: 1px solid var(--line-light);
    border-left: 0;
  }

  .journal-meta {
    grid-column: auto;
  }
}
</style>
