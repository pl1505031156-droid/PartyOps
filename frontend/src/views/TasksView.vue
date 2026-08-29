<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { IconDownload, IconFilter, IconSearch } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import PageHelp from "../components/PageHelp.vue";
import type { SavedView, Task, TaskStatus, User } from "../types";
import TaskStatusTag from "../components/TaskStatusTag.vue";
import { beijingNow, formatServerTime, localInputToUtc, serverTime } from "../utils/datetime";

const loading = ref(false);
const tasks = ref<Task[]>([]);
const users = ref<User[]>([]);
const keyword = ref("");
const status = ref<string>("");
const owner = ref("");
const year = ref<number | undefined>();
const category = ref("");
const fileName = ref("");
const smart = ref("");
const savedViews = ref<SavedView[]>([]);
const selectedTaskIds = ref<string[]>([]);
const batchVisible = ref(false);
const saveViewVisible = ref(false);
const savedViewName = ref("");
const batchForm = reactive({
  status: "",
  owner_id: "",
  internal_due_at: "",
  planned_start_at: "",
  planned_end_at: "",
  tags: "",
  note: "",
});
const viewMode = ref<"table" | "board" | "calendar" | "timeline">("table");
const calendarAnchor = ref(beijingNow().startOf("month"));
const statusOptions = [
  ["", "全部状态"],
  ["pending_receipt", "待接收"],
  ["pending_breakdown", "待拆解"],
  ["in_progress", "办理中"],
  ["waiting_feedback", "等待反馈"],
  ["pending_review", "待审核"],
  ["returned", "退回修改"],
  ["completed", "已完成"],
  ["archived", "已归档"],
];
const userNames = computed(() => Object.fromEntries(users.value.map((item) => [item.id, item.display_name])));
const operationalStatuses = computed(() =>
  statusOptions.slice(1).map(([value, label]) => ({
    value,
    label,
    tasks: tasks.value.filter((task) => task.status === value),
  })),
);
const timelineTasks = computed(() =>
  [...tasks.value].sort((left, right) => {
    const leftTime = left.planned_start_at || left.internal_due_at || left.formal_due_at || left.updated_at;
    const rightTime = right.planned_start_at || right.internal_due_at || right.formal_due_at || right.updated_at;
    return serverTime(leftTime).valueOf() - serverTime(rightTime).valueOf();
  }),
);
const calendarTaskMap = computed(() => {
  const result: Record<string, Task[]> = {};
  for (const task of tasks.value) {
    const date = task.planned_start_at || task.internal_due_at || task.formal_due_at;
    if (!date) continue;
    const key = serverTime(date).format("YYYY-MM-DD");
    (result[key] ||= []).push(task);
  }
  return result;
});
const calendarCells = computed(() => {
  const start = calendarAnchor.value.startOf("month").startOf("week");
  return Array.from({ length: 42 }, (_, index) => {
    const date = start.add(index, "day");
    return {
      key: date.format("YYYY-MM-DD"),
      date,
      currentMonth: date.month() === calendarAnchor.value.month(),
      tasks: calendarTaskMap.value[date.format("YYYY-MM-DD")] || [],
    };
  });
});

function shiftCalendar(months: number) {
  calendarAnchor.value = calendarAnchor.value.add(months, "month");
}

async function load() {
  loading.value = true;
  try {
    const params = new URLSearchParams();
    if (status.value) params.set("status", status.value);
    if (owner.value) params.set("owner_id", owner.value);
    if (keyword.value) params.set("q", keyword.value);
    if (year.value) params.set("year", String(year.value));
    if (category.value) params.set("category", category.value);
    if (fileName.value) params.set("file_name", fileName.value);
    if (smart.value) params.set("smart", smart.value);
    params.set("page_size", "100");
    const result = await api.get<{ items: Task[] }>(`/search?${params}`);
    tasks.value = result.items;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "事项加载失败");
  } finally {
    loading.value = false;
  }
}

function filterSnapshot() {
  return {
    q: keyword.value,
    status: status.value,
    owner_id: owner.value,
    year: year.value || null,
    category: category.value,
    file_name: fileName.value,
    smart: smart.value,
  };
}

async function loadSavedViews() {
  savedViews.value = await api.get<SavedView[]>("/saved-views");
}

async function createSavedView() {
  if (!savedViewName.value.trim()) {
    Message.warning("请填写智能文件夹名称");
    return;
  }
  await api.post("/saved-views", {
    name: savedViewName.value.trim(),
    view_type: "tasks",
    filters: filterSnapshot(),
    columns: ["title", "status", "owner", "internal_due_at", "formal_due_at"],
    pinned: true,
  });
  savedViewName.value = "";
  saveViewVisible.value = false;
  Message.success("已保存为智能文件夹");
  await loadSavedViews();
}

async function deleteSavedView(view: SavedView) {
  await api.delete(`/saved-views/${view.id}`, { "If-Match": String(view.version) });
  Message.success("智能文件夹已删除");
  await loadSavedViews();
}

function applySavedView(view: SavedView) {
  const filters = view.filters || {};
  keyword.value = String(filters.q || "");
  status.value = String(filters.status || "");
  owner.value = String(filters.owner_id || "");
  year.value = typeof filters.year === "number" ? filters.year : undefined;
  category.value = String(filters.category || "");
  fileName.value = String(filters.file_name || "");
  smart.value = String(filters.smart || "");
  load();
}

function applyBuiltInSmart(value: string) {
  smart.value = smart.value === value ? "" : value;
  load();
}

async function applyBatch() {
  if (!selectedTaskIds.value.length) {
    Message.warning("请先勾选事项");
    return;
  }
  const payload: Record<string, unknown> = {
    task_ids: selectedTaskIds.value,
    note: batchForm.note || "批量处理",
  };
  if (batchForm.status) payload.status = batchForm.status;
  if (batchForm.owner_id) payload.owner_id = batchForm.owner_id;
  if (batchForm.internal_due_at) payload.internal_due_at = localInputToUtc(batchForm.internal_due_at);
  if (batchForm.planned_start_at) payload.planned_start_at = localInputToUtc(`${batchForm.planned_start_at}T00:00:00`);
  if (batchForm.planned_end_at) payload.planned_end_at = localInputToUtc(`${batchForm.planned_end_at}T23:59:59`);
  if (batchForm.tags.trim()) payload.tags = batchForm.tags.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
  try {
    const result = await api.post<{ count: number }>("/tasks/batch", payload);
    Message.success(`已完成 ${result.count} 项批量处理`);
    batchVisible.value = false;
    selectedTaskIds.value = [];
    Object.assign(batchForm, { status: "", owner_id: "", internal_due_at: "", planned_start_at: "", planned_end_at: "", tags: "", note: "" });
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "批量处理失败");
  }
}

onMounted(async () => {
  try {
    users.value = await api.get<User[]>("/users");
  } catch {
    users.value = [await api.get<User>("/auth/me")];
  }
  await loadSavedViews();
  await load();
  window.addEventListener("partyops:refresh", load);
});
onBeforeUnmount(() => window.removeEventListener("partyops:refresh", load));
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">事项与清单</h1>
        <p class="page-description">所有工作沿同一条责任链办理，避免重复建表和多版本散落。</p>
      </div>
      <a-space>
        <PageHelp
          title="事项与清单怎么用"
          :tips="['快速事项只填必要字段，复杂工作再展开步骤和材料。', '所有状态变化都会进入活动时间线。', '完成后自动归入周期汇总，归档前必须检查材料完整性。']"
          help-query="事项清单"
        />
        <a-button :disabled="!selectedTaskIds.length" @click="batchVisible = true">
          批量处理{{ selectedTaskIds.length ? `（${selectedTaskIds.length}）` : "" }}
        </a-button>
        <a-button @click="saveViewVisible = true"><template #icon><IconFilter /></template>保存智能文件夹</a-button>
        <a-radio-group v-model="viewMode" type="button" size="small">
          <a-radio value="table">表格</a-radio>
          <a-radio value="board">看板</a-radio>
          <a-radio value="calendar">日历</a-radio>
          <a-radio value="timeline">时间轴</a-radio>
        </a-radio-group>
        <a-button aria-label="导出周工作清单" :href="downloadUrl('/exports/tasks.docx?kind=周工作清单')" target="_blank">
          <template #icon><IconDownload /></template>
          导出周清单
        </a-button>
        <a-button aria-label="导出任务台账" :href="downloadUrl('/exports/tasks.xlsx?kind=任务台账')" target="_blank">
          <template #icon><IconDownload /></template>
          导出台账
        </a-button>
        <a-dropdown>
          <a-button>更多直接产出</a-button>
          <template #content>
            <a-doption><a :href="downloadUrl('/exports/tasks.xlsx?kind=催报清单')" target="_blank">催报清单</a></a-doption>
            <a-doption><a :href="downloadUrl('/exports/tasks.xlsx?kind=材料目录')" target="_blank">材料目录</a></a-doption>
            <a-doption><a :href="downloadUrl('/exports/tasks.xlsx?kind=交接清单')" target="_blank">交接清单</a></a-doption>
          </template>
        </a-dropdown>
      </a-space>
    </header>

    <div class="filter-bar">
      <a-input-search v-model="keyword" placeholder="搜索事项名称、来源或关键词" @search="load">
        <template #prefix><IconSearch /></template>
      </a-input-search>
      <a-select v-model="status" @change="load">
        <a-option v-for="[value, label] in statusOptions" :key="value" :value="value">{{ label }}</a-option>
      </a-select>
      <a-select v-model="owner" placeholder="全部责任人" allow-clear @change="load">
        <a-option value="">全部责任人</a-option>
        <a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option>
      </a-select>
      <a-input-number v-model="year" placeholder="年度" :min="2000" :max="2200" allow-clear @change="load" />
      <a-input v-model="category" placeholder="工作类别" allow-clear @press-enter="load" />
      <a-input-search v-model="fileName" placeholder="按文件名查找终稿" @search="load" />
    </div>

    <div class="smart-folders">
      <span>智能文件夹</span>
      <button :class="{ active: smart === 'this_week_completed' }" @click="applyBuiltInSmart('this_week_completed')">本周完成</button>
      <button :class="{ active: smart === 'unarchived' }" @click="applyBuiltInSmart('unarchived')">未归档</button>
      <button :class="{ active: smart === 'finals' }" @click="applyBuiltInSmart('finals')">已有最终稿</button>
      <button :class="{ active: smart === 'annual_focus' }" @click="applyBuiltInSmart('annual_focus')">年度重点</button>
      <a-dropdown v-if="savedViews.length">
        <a-button size="mini">我的保存视图（{{ savedViews.length }}）</a-button>
        <template #content>
          <a-doption v-for="view in savedViews" :key="view.id">
            <div class="saved-view-option"><button @click="applySavedView(view)">{{ view.name }}</button><a-button type="text" size="mini" status="danger" @click.stop="deleteSavedView(view)">删除</a-button></div>
          </a-doption>
        </template>
      </a-dropdown>
    </div>

    <a-table
      v-if="viewMode === 'table'"
      :data="tasks"
      :loading="loading"
      row-key="id"
      v-model:selected-keys="selectedTaskIds"
      :row-selection="{ type: 'checkbox', showCheckedAll: true, onlyCurrent: false }"
      :pagination="{ pageSize: 20, showTotal: true }"
      class="tasks-table"
    >
      <template #columns>
        <a-table-column title="事项" :width="360">
          <template #cell="{ record }">
            <RouterLink :to="`/tasks/${record.id}`" class="task-link">
              <strong>{{ record.title }}</strong>
              <span>{{ record.category || "未分类" }} · {{ record.source || "未填写来源" }}</span>
            </RouterLink>
          </template>
        </a-table-column>
        <a-table-column title="状态" :width="116">
          <template #cell="{ record }"><TaskStatusTag :status="record.status as TaskStatus" /></template>
        </a-table-column>
        <a-table-column title="主办人" :width="120">
          <template #cell="{ record }">{{ userNames[record.owner_id] || "未知" }}</template>
        </a-table-column>
        <a-table-column title="内部节点" :width="160">
          <template #cell="{ record }">{{ formatServerTime(record.internal_due_at, "MM-DD HH:mm") }}</template>
        </a-table-column>
        <a-table-column title="正式截止" :width="160">
          <template #cell="{ record }">{{ formatServerTime(record.formal_due_at, "MM-DD HH:mm") }}</template>
        </a-table-column>
        <a-table-column title="材料" :width="110">
          <template #cell="{ record }">
            <span :class="record.missing_required_materials ? 'text-danger' : 'text-success'">
              {{ record.missing_required_materials ? `缺 ${record.missing_required_materials} 项` : "正常" }}
            </span>
          </template>
        </a-table-column>
        <a-table-column title="更新" :width="150">
          <template #cell="{ record }">{{ formatServerTime(record.updated_at, "MM-DD HH:mm") }}</template>
        </a-table-column>
      </template>
    </a-table>

    <section v-else-if="viewMode === 'board'" class="board-view" aria-label="事项看板">
      <article v-for="column in operationalStatuses" :key="column.value" class="board-column">
        <header>
          <strong>{{ column.label }}</strong>
          <span>{{ column.tasks.length }}</span>
        </header>
        <div class="board-cards">
          <RouterLink
            v-for="task in column.tasks"
            :key="task.id"
            :to="`/tasks/${task.id}`"
            class="board-card"
          >
            <strong>{{ task.title }}</strong>
            <span>{{ userNames[task.owner_id] || "未知" }} · {{ task.work_area || task.category || "未分类" }}</span>
            <time>{{ formatServerTime(task.internal_due_at, "MM-DD HH:mm", "无内部节点") }}</time>
          </RouterLink>
          <p v-if="!column.tasks.length" class="empty-column">暂无事项</p>
        </div>
      </article>
    </section>

    <section v-else-if="viewMode === 'calendar'" class="calendar-view" aria-label="事项日历">
      <header class="calendar-toolbar">
        <a-button size="small" @click="shiftCalendar(-1)">上个月</a-button>
        <h2>{{ calendarAnchor.format("YYYY 年 M 月") }}</h2>
        <a-space>
          <a-button size="small" @click="calendarAnchor = beijingNow().startOf('month')">本月</a-button>
          <a-button size="small" @click="shiftCalendar(1)">下个月</a-button>
        </a-space>
      </header>
      <div class="calendar-weekdays">
        <span v-for="label in ['日', '一', '二', '三', '四', '五', '六']" :key="label">周{{ label }}</span>
      </div>
      <div class="calendar-grid">
        <article
          v-for="cell in calendarCells"
          :key="cell.key"
          :class="{ muted: !cell.currentMonth, today: cell.key === beijingNow().format('YYYY-MM-DD') }"
        >
          <time>{{ cell.date.date() }}</time>
          <RouterLink
            v-for="task in cell.tasks.slice(0, 3)"
            :key="task.id"
            :to="`/tasks/${task.id}`"
            :title="task.title"
          >
            {{ task.title }}
          </RouterLink>
          <span v-if="cell.tasks.length > 3" class="more-count">另有 {{ cell.tasks.length - 3 }} 项</span>
        </article>
      </div>
    </section>

    <section v-else class="task-timeline" aria-label="事项时间轴">
      <article v-for="task in timelineTasks" :key="task.id">
        <div class="timeline-date">
          <strong>{{ formatServerTime(task.planned_start_at || task.internal_due_at || task.formal_due_at || task.updated_at, "MM-DD") }}</strong>
          <span>{{ formatServerTime(task.planned_start_at || task.internal_due_at || task.formal_due_at || task.updated_at, "HH:mm") }}</span>
        </div>
        <div class="timeline-line"><i /></div>
        <RouterLink :to="`/tasks/${task.id}`" class="timeline-task">
          <div>
            <TaskStatusTag :status="task.status as TaskStatus" />
            <span>{{ task.work_area || task.category || "未分类" }}</span>
          </div>
          <strong>{{ task.title }}</strong>
          <p>{{ userNames[task.owner_id] || "未知" }} · {{ task.reporting_scope || "未设置汇报口径" }}</p>
        </RouterLink>
      </article>
      <a-empty v-if="!timelineTasks.length" description="当前筛选条件下暂无事项" />
    </section>

    <a-modal v-model:visible="batchVisible" title="批量处理事项" width="640px" @ok="applyBatch">
      <a-alert type="warning">只会修改明确填写的字段；批量状态变化仍逐项校验合法状态机，不会静默跳过失败事项。</a-alert>
      <a-form :model="batchForm" layout="vertical" class="batch-form">
        <div class="form-grid">
          <a-form-item label="责任人"><a-select v-model="batchForm.owner_id" allow-clear><a-option v-for="item in users" :key="item.id" :value="item.id">{{ item.display_name }}</a-option></a-select></a-form-item>
          <a-form-item label="目标状态"><a-select v-model="batchForm.status" allow-clear><a-option v-for="[value, label] in statusOptions.slice(1)" :key="value" :value="value">{{ label }}</a-option></a-select></a-form-item>
          <a-form-item label="计划开始"><a-date-picker v-model="batchForm.planned_start_at" value-format="YYYY-MM-DD" style="width:100%" /></a-form-item>
          <a-form-item label="计划结束"><a-date-picker v-model="batchForm.planned_end_at" value-format="YYYY-MM-DD" style="width:100%" /></a-form-item>
          <a-form-item label="内部节点"><a-date-picker v-model="batchForm.internal_due_at" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" /></a-form-item>
          <a-form-item label="标签"><a-input v-model="batchForm.tags" placeholder="多个标签用逗号分隔" /></a-form-item>
        </div>
        <a-form-item label="批量操作说明"><a-textarea v-model="batchForm.note" :auto-size="{ minRows: 2, maxRows: 4 }" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="saveViewVisible" title="保存为智能文件夹" @ok="createSavedView">
      <a-input v-model="savedViewName" placeholder="例如：本季度待审核重点事项" />
      <p class="modal-hint">保存当前关键词、状态、责任人、年度、类别、文件名和智能条件；以后打开时自动重新计算，不复制任务。</p>
    </a-modal>
  </div>
</template>

<style scoped>
.filter-bar {
  display: grid;
  margin-bottom: 18px;
  grid-template-columns: minmax(260px, 1fr) 150px 150px 120px 150px minmax(200px, 0.8fr);
  gap: 12px;
}

.task-link {
  display: block;
}

.task-link strong,
.task-link span {
  display: block;
}

.task-link strong {
  margin-bottom: 5px;
  font-size: 14px;
}

.task-link span {
  color: var(--muted);
  font-size: 11px;
}

.tasks-table {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow-x: auto;
  border-top: 1px solid var(--line);
}

@media (max-width: 1380px) {
  .filter-bar {
    grid-template-columns:
      minmax(210px, 1.8fr)
      minmax(112px, 0.9fr)
      minmax(112px, 0.9fr)
      minmax(88px, 0.7fr)
      minmax(112px, 0.9fr)
      minmax(160px, 1.25fr);
    gap: 8px;
  }
}

@media (max-width: 1120px) {
  .filter-bar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.board-view {
  display: grid;
  grid-auto-columns: minmax(230px, 1fr);
  grid-auto-flow: column;
  gap: 12px;
  padding-bottom: 12px;
  overflow-x: auto;
}

.board-column {
  min-height: 460px;
  padding: 12px;
  background: color-mix(in srgb, var(--paper) 90%, var(--ink) 10%);
  border: 1px solid var(--line);
}

.board-column > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 2px solid var(--red);
}

.board-column > header span {
  min-width: 24px;
  padding: 2px 7px;
  text-align: center;
  background: var(--paper);
  border: 1px solid var(--line);
}

.board-cards {
  display: grid;
  gap: 9px;
  margin-top: 10px;
}

.board-card {
  display: grid;
  gap: 7px;
  padding: 12px;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line);
  box-shadow: 0 3px 10px rgb(62 45 29 / 5%);
}

.board-card span,
.board-card time,
.empty-column {
  color: var(--muted);
  font-size: 11px;
}

.empty-column {
  padding: 24px 0;
  text-align: center;
}

.calendar-view {
  border: 1px solid var(--line);
  background: var(--surface);
}

.calendar-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 58px;
  padding: 0 14px;
  border-bottom: 1px solid var(--line);
}

.calendar-toolbar h2 {
  margin: 0;
  font-family: var(--serif);
  font-size: 20px;
}

.calendar-weekdays,
.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
}

.calendar-weekdays span {
  padding: 9px;
  color: var(--muted);
  text-align: center;
  background: var(--paper);
  border-right: 1px solid var(--line);
}

.calendar-grid article {
  min-height: 116px;
  padding: 8px;
  border-top: 1px solid var(--line);
  border-right: 1px solid var(--line);
}

.calendar-grid article > time {
  display: grid;
  width: 26px;
  height: 26px;
  margin-bottom: 5px;
  place-items: center;
  font-weight: 600;
}

.calendar-grid article.today > time {
  color: white;
  background: var(--red);
  border-radius: 50%;
}

.calendar-grid article.muted {
  color: var(--muted);
  background: color-mix(in srgb, var(--paper) 72%, transparent);
}

.calendar-grid article a,
.more-count {
  display: block;
  margin-top: 4px;
  padding: 3px 5px;
  overflow: hidden;
  color: var(--ink);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: color-mix(in srgb, var(--red) 8%, var(--paper));
  border-left: 2px solid var(--red);
}

.more-count {
  color: var(--muted);
  background: transparent;
  border-left-color: var(--line);
}

.task-timeline {
  max-width: 920px;
}

.task-timeline > article {
  display: grid;
  grid-template-columns: 72px 28px minmax(0, 1fr);
  min-height: 108px;
}

.timeline-date {
  padding-top: 12px;
  text-align: right;
}

.timeline-date strong,
.timeline-date span {
  display: block;
}

.timeline-date strong {
  font-size: 18px;
}

.timeline-date span {
  color: var(--muted);
  font-size: 11px;
}

.timeline-line {
  position: relative;
}

.timeline-line::after {
  position: absolute;
  top: 22px;
  bottom: -22px;
  left: 14px;
  width: 1px;
  content: "";
  background: var(--line);
}

.timeline-line i {
  position: absolute;
  z-index: 1;
  top: 18px;
  left: 9px;
  width: 11px;
  height: 11px;
  background: var(--red);
  border: 3px solid var(--paper);
  border-radius: 50%;
}

.timeline-task {
  display: grid;
  gap: 7px;
  margin: 4px 0 14px;
  padding: 14px 16px;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line);
}

.timeline-task > div {
  display: flex;
  align-items: center;
  gap: 9px;
}

.timeline-task > div span,
.timeline-task p {
  margin: 0;
  color: var(--muted);
  font-size: 11px;
}

.smart-folders {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: -6px 0 16px;
  padding: 10px 12px;
  background: rgba(251, 248, 241, 0.72);
  border: 1px solid var(--line);
}

.smart-folders > span {
  margin-right: 4px;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.12em;
}

.smart-folders > button,
.saved-view-option > button {
  padding: 5px 9px;
  color: var(--muted);
  background: transparent;
  border: 1px solid var(--line-light);
  cursor: pointer;
}

.smart-folders > button.active {
  color: white;
  background: var(--red);
  border-color: var(--red);
}

.saved-view-option {
  display: flex;
  min-width: 210px;
  align-items: center;
  justify-content: space-between;
}

.saved-view-option > button {
  flex: 1;
  color: var(--ink);
  text-align: left;
  border: 0;
}

.batch-form {
  margin-top: 16px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 14px;
}

.modal-hint {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .filter-bar {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .calendar-grid article {
    min-height: 96px;
  }
}
</style>
