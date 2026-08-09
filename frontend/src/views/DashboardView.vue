<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { IconArrowRight, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { DashboardBucket, DashboardData, Task } from "../types";
import TaskStatusTag from "../components/TaskStatusTag.vue";
import { dayjs, formatServerTime } from "../utils/datetime";

const buckets = ref<DashboardBucket[]>([]);
const dashboard = ref<DashboardData | null>(null);
const loading = ref(false);
const selected = ref("today");

const current = computed(() => buckets.value.find((item) => item.key === selected.value) || buckets.value[0]);

async function load() {
  loading.value = true;
  try {
    const data = await api.get<DashboardData>("/dashboard");
    dashboard.value = data;
    buckets.value = data.buckets;
    if (!buckets.value.some((item) => item.key === selected.value)) selected.value = buckets.value[0]?.key || "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "首页加载失败");
  } finally {
    loading.value = false;
  }
}

function dueLabel(task: Task) {
  const due = task.internal_due_at || task.formal_due_at;
  return formatServerTime(due, "MM月DD日 HH:mm", "未设时限");
}

onMounted(() => {
  load();
  window.addEventListener("partyops:refresh", load);
});
onBeforeUnmount(() => window.removeEventListener("partyops:refresh", load));
</script>

<template>
  <div class="page dashboard-page">
    <header class="page-header">
      <div>
        <p class="date-kicker">{{ dayjs().format("YYYY年MM月DD日 dddd") }}</p>
        <h1 class="page-title">一周工作，一处看清</h1>
        <p class="page-description">完成情况、下周安排、截止风险与材料档案自动关联，不再重复整理。</p>
      </div>
      <a-space>
        <a-badge :count="dashboard?.unread_notifications || 0">
          <RouterLink class="notice-link" to="/journal?tab=notifications">工作提醒</RouterLink>
        </a-badge>
        <a-button :loading="loading" @click="load">
          <template #icon><IconRefresh /></template>
          刷新
        </a-button>
      </a-space>
    </header>

    <section class="weekly-board">
      <article class="weekly-column completed">
        <div class="weekly-heading">
          <div><span>本周归集</span><h2>本周完成</h2></div>
          <strong>{{ dashboard?.this_week_completed.length || 0 }}</strong>
        </div>
        <div v-if="dashboard?.this_week_completed.length" class="weekly-items">
          <RouterLink v-for="task in dashboard.this_week_completed.slice(0, 5)" :key="task.id" :to="`/tasks/${task.id}`">
            <span class="check-mark">✓</span>
            <div><b>{{ task.title }}</b><small>{{ task.work_area || task.category || "日常工作" }}</small></div>
          </RouterLink>
        </div>
        <p v-else class="weekly-empty">本周尚无已完成事项，完成后会自动归集到这里。</p>
      </article>
      <article class="weekly-column planned">
        <div class="weekly-heading">
          <div><span>下周安排</span><h2>下周计划</h2></div>
          <strong>{{ dashboard?.next_week_planned.length || 0 }}</strong>
        </div>
        <div v-if="dashboard?.next_week_planned.length" class="weekly-items">
          <RouterLink v-for="task in dashboard.next_week_planned.slice(0, 5)" :key="task.id" :to="`/tasks/${task.id}`">
            <span class="date-mark">{{ formatServerTime(task.planned_start_at || task.internal_due_at || task.formal_due_at, "DD") }}</span>
            <div><b>{{ task.title }}</b><small>{{ dueLabel(task) }}</small></div>
          </RouterLink>
        </div>
        <p v-else class="weekly-empty">设置任务计划周或截止时间后，系统会自动形成下周计划。</p>
      </article>
      <aside class="carry-card">
        <span>需要延续</span>
        <strong>{{ dashboard?.carry_over.length || 0 }}</strong>
        <p>已过计划节点但仍未完成</p>
        <RouterLink to="/reports">整理周报 <IconArrowRight /></RouterLink>
      </aside>
    </section>

    <section class="metric-strip" aria-label="事项风险概览">
      <button
        v-for="bucket in buckets"
        :key="bucket.key"
        type="button"
        :class="{ active: selected === bucket.key }"
        @click="selected = bucket.key"
      >
        <strong>{{ bucket.count }}</strong>
        <span>{{ bucket.label }}</span>
      </button>
    </section>

    <section class="work-list">
      <div class="section-heading">
        <div>
          <span class="section-index">当前清单</span>
          <h2>{{ current?.label || "待办事项" }}</h2>
        </div>
        <RouterLink to="/tasks">查看全部事项 <IconArrowRight /></RouterLink>
      </div>
      <a-spin :loading="loading" class="full-spin">
        <div v-if="current?.items.length" class="task-rows">
          <RouterLink v-for="task in current.items" :key="task.id" :to="`/tasks/${task.id}`" class="task-row">
            <div class="priority-mark" :class="task.priority"></div>
            <div class="task-main">
              <div class="task-title-line">
                <strong>{{ task.title }}</strong>
                <TaskStatusTag :status="task.status" />
              </div>
              <p>{{ task.source || "未填写任务来源" }}</p>
            </div>
            <div class="task-meta">
              <span>{{ dueLabel(task) }}</span>
              <small v-if="task.missing_required_materials" class="text-danger">
                缺 {{ task.missing_required_materials }} 项材料
              </small>
              <small v-else>材料状态正常</small>
            </div>
            <IconArrowRight class="row-arrow" />
          </RouterLink>
        </div>
        <div v-else class="empty-state">当前没有需要处理的事项。</div>
      </a-spin>
    </section>

    <section class="reduce-load-note">
      <span>减负提示</span>
      <p>同一事项只录一次。完成办理后，周清单、任务台账和材料目录会自动复用现有数据。</p>
    </section>
  </div>
</template>

<style scoped>
.dashboard-page {
  width: 100%;
  max-width: none;
}

.date-kicker {
  margin: 0 0 10px;
  color: var(--cinnabar);
  font-family: Georgia, serif;
  font-size: 13px;
  letter-spacing: 0.08em;
}

.notice-link {
  display: inline-flex;
  height: 32px;
  align-items: center;
  padding: 0 12px;
  color: var(--cinnabar);
  background: rgba(180, 35, 24, 0.06);
  border: 1px solid rgba(180, 35, 24, 0.18);
}

.weekly-board {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 190px;
  gap: 12px;
  margin: 4px 0 28px;
}

.weekly-column,
.carry-card {
  min-height: 230px;
  padding: 20px;
  background: rgba(251, 248, 241, 0.82);
  border: 1px solid var(--line);
}

.weekly-column.completed {
  border-top: 3px solid var(--green);
}

.weekly-column.planned {
  border-top: 3px solid var(--cinnabar);
}

.weekly-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.weekly-heading span,
.carry-card > span {
  color: var(--muted);
  font-family: Georgia, serif;
  font-size: 10px;
  letter-spacing: 0.14em;
}

.weekly-heading h2 {
  margin: 4px 0 0;
  font-size: 18px;
}

.weekly-heading > strong {
  color: var(--muted);
  font-family: Georgia, serif;
  font-size: 28px;
  font-weight: 400;
}

.weekly-items a {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 9px 0;
  border-top: 1px solid var(--line-light);
}

.weekly-items a:hover b {
  color: var(--cinnabar);
}

.weekly-items b,
.weekly-items small {
  display: block;
}

.weekly-items b {
  max-width: 360px;
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weekly-items small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.check-mark,
.date-mark {
  display: inline-flex;
  width: 26px;
  height: 26px;
  flex: 0 0 26px;
  align-items: center;
  justify-content: center;
  color: var(--green);
  border: 1px solid rgba(47, 125, 76, 0.35);
  border-radius: 50%;
}

.date-mark {
  color: var(--cinnabar);
  font-family: Georgia, serif;
  border-color: rgba(180, 35, 24, 0.35);
}

.weekly-empty {
  margin: 38px 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.8;
}

.carry-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  color: #f8efe4;
  background: var(--charcoal);
  border-color: var(--charcoal);
}

.carry-card > span {
  color: #c8beb2;
}

.carry-card > strong {
  margin-top: 8px;
  font-family: Georgia, serif;
  font-size: 44px;
  font-weight: 400;
}

.carry-card p {
  color: #c8beb2;
  font-size: 11px;
  line-height: 1.6;
}

.carry-card a {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 14px;
  color: #f2b3a9;
  font-size: 12px;
}

.metric-strip {
  display: grid;
  margin: 8px 0 32px;
  grid-template-columns: repeat(8, minmax(100px, 1fr));
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.metric-strip button {
  position: relative;
  display: flex;
  min-height: 92px;
  flex-direction: column;
  justify-content: center;
  padding: 12px 15px;
  text-align: left;
  background: transparent;
  border: 0;
  border-right: 1px solid var(--line-light);
  cursor: pointer;
}

.metric-strip button:last-child {
  border-right: 0;
}

.metric-strip button::after {
  position: absolute;
  right: 14px;
  bottom: -1px;
  left: 14px;
  height: 3px;
  content: "";
  background: transparent;
}

.metric-strip button.active {
  background: rgba(180, 35, 24, 0.04);
}

.metric-strip button.active::after {
  background: var(--cinnabar);
}

.metric-strip strong {
  color: var(--charcoal);
  font-family: Georgia, serif;
  font-size: 29px;
  font-weight: 500;
}

.metric-strip span {
  margin-top: 5px;
  color: var(--muted);
  font-size: 12px;
}

.work-list {
  margin-top: 6px;
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 12px;
}

.section-heading h2 {
  margin: 5px 0 0;
  font-size: 20px;
}

.section-index {
  color: var(--cinnabar);
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.section-heading a {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--cinnabar);
  font-size: 13px;
}

.task-rows {
  border-top: 1px solid var(--line);
}

.task-row {
  display: grid;
  min-height: 78px;
  align-items: center;
  grid-template-columns: 4px minmax(0, 1fr) 180px 20px;
  gap: 18px;
  border-bottom: 1px solid var(--line-light);
  transition: 120ms ease;
}

.task-row:hover {
  padding-left: 8px;
  background: rgba(251, 248, 241, 0.72);
}

.priority-mark {
  width: 4px;
  height: 34px;
  background: #b8b1a8;
}

.priority-mark.high,
.priority-mark.urgent {
  background: var(--cinnabar);
}

.priority-mark.low {
  background: #8a9b8d;
}

.task-title-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.task-title-line strong {
  overflow: hidden;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-main p {
  overflow: hidden;
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-meta span,
.task-meta small {
  display: block;
  text-align: right;
}

.task-meta span {
  font-size: 13px;
}

.task-meta small {
  margin-top: 6px;
  color: var(--muted);
  font-size: 11px;
}

.row-arrow {
  color: #aaa198;
}

.full-spin {
  display: block;
  min-height: 180px;
}

.reduce-load-note {
  display: flex;
  align-items: center;
  gap: 22px;
  margin-top: 32px;
  padding: 16px 20px;
  background: rgba(180, 35, 24, 0.045);
  border-left: 3px solid var(--cinnabar);
}

.reduce-load-note span {
  flex: 0 0 auto;
  color: var(--cinnabar);
  font-size: 12px;
  font-weight: 600;
}

.reduce-load-note p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
</style>
