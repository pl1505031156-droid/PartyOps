<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  IconArrowRight,
  IconCalendar,
  IconCheckCircle,
  IconExclamationCircle,
  IconRefresh,
} from "@arco-design/web-vue/es/icon";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";
import TaskStatusTag from "../components/TaskStatusTag.vue";
import type { AIRecommendation, TodayData, TodayTask } from "../types";
import { formatServerTime } from "../utils/datetime";
import { orientalDateLabel } from "../utils/lunar";

const data = ref<TodayData | null>(null);
const loading = ref(false);
const loadError = ref("");
const recommendations = ref<AIRecommendation[]>([]);
const todayLabel = orientalDateLabel();

const alertCount = computed(() => {
  if (!data.value) return 0;
  return (
    data.value.risks.incomplete_materials
    + data.value.risks.recurrence_anomalies
    + data.value.risks.device_alerts.length
    + (data.value.risks.backup_stale ? 1 : 0)
  );
});

const primaryTasks = computed(() => {
  const seen = new Set<string>();
  return [
    ...(data.value?.today_tasks || []),
    ...(data.value?.overdue_tasks || []),
    ...(data.value?.pending_review_feedback || []),
  ].filter((task) => {
    if (seen.has(task.id)) return false;
    seen.add(task.id);
    return true;
  }).slice(0, 2);
});

const nextCalendarTask = computed(() => data.value?.next_week_plan[0] || null);

function dashboardCount(...keywords: string[]) {
  const buckets = data.value?.dashboard.buckets || [];
  return buckets
    .filter((item) => keywords.some((keyword) => `${item.key}${item.label}`.includes(keyword)))
    .reduce((sum, item) => sum + item.count, 0);
}

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    data.value = await api.get<TodayData>("/today");
    recommendations.value = await api.get<AIRecommendation[]>("/ai/recommendations?limit=3").catch(() => []);
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "今日工作台加载失败";
  } finally {
    loading.value = false;
  }
}

function dueLabel(task: TodayTask) {
  return formatServerTime(
    task.internal_due_at || task.formal_due_at || task.planned_start_at,
    "MM月DD日 HH:mm",
    "未设时间",
  );
}

onMounted(() => {
  load();
  window.addEventListener("partyops:refresh", load);
});
onBeforeUnmount(() => window.removeEventListener("partyops:refresh", load));
</script>

<template>
  <div class="page today-page">
    <header class="page-header">
      <div class="title-copy">
        <p class="date-kicker">
          <span>{{ todayLabel.gregorian }}　{{ todayLabel.weekday }}　{{ todayLabel.lunar }}</span>
          <b v-if="todayLabel.solarTerm">{{ todayLabel.solarTerm }}</b>
        </p>
        <h1 class="page-title">一周工作，一处看清</h1>
        <p class="page-description">任务、日历、材料与周期汇总自动关联，不再重复整理。</p>
      </div>
      <a-space>
        <PageHelp
          title="今日工作台怎么用"
          :tips="['先处理今天必须办、逾期和待审核事项。', '事项完成后会自动进入本周完成和周期汇总。', '工作日历、材料缺项和设备异常会在同一页提醒。']"
          help-query="每天怎么用"
        />
        <a-button :loading="loading" @click="load">
          <template #icon><IconRefresh /></template>刷新
        </a-button>
      </a-space>
    </header>

    <a-alert
      v-if="loadError"
      type="error"
      title="今日工作台暂时无法加载"
      :content="loadError"
      show-icon
    >
      <template #action><a-button size="small" @click="load">重新加载</a-button></template>
    </a-alert>

    <a-spin :loading="loading" class="full-spin">
      <template v-if="data">
        <section class="weekly-board">
          <article class="weekly-panel completed">
            <div class="weekly-heading">
              <div><span>本周归集</span><h2>本周完成</h2></div>
              <strong>{{ data.completed_this_week.length }}</strong>
            </div>
            <div class="weekly-items">
              <RouterLink
                v-for="task in data.completed_this_week.slice(0, 3)"
                :key="task.id"
                :to="task.route"
              >
                <IconCheckCircle />
                <div><b>{{ task.title }}</b><small>{{ task.work_area || "日常工作" }}</small></div>
              </RouterLink>
              <p v-if="!data.completed_this_week.length">事项完成后会自动进入这里。</p>
            </div>
          </article>

          <article class="weekly-panel planned">
            <div class="weekly-heading">
              <div><span>提前安排</span><h2>下周计划</h2></div>
              <strong>{{ data.next_week_plan.length }}</strong>
            </div>
            <div class="weekly-items">
              <RouterLink
                v-for="task in data.next_week_plan.slice(0, 3)"
                :key="task.id"
                :to="task.route"
              >
                <time>{{ formatServerTime(task.planned_start_at || task.internal_due_at, "DD", "—") }}</time>
                <div><b>{{ task.title }}</b><small>{{ dueLabel(task) }}</small></div>
              </RouterLink>
              <p v-if="!data.next_week_plan.length">设置计划日期后会自动形成下周安排。</p>
            </div>
          </article>

          <aside class="attention-panel">
            <span>需要关注</span>
            <strong>{{ alertCount + recommendations.length }}</strong>
            <p>{{ recommendations[0]?.reason || (alertCount ? "存在需要处理的材料、周期、备份或设备风险" : "当前没有异常风险") }}</p>
            <RouterLink :to="recommendations[0]?.route || (data.risks.device_alerts.length ? '/fleet/devices' : '/tasks')">
              {{ recommendations.length ? "查看建议" : "查看风险" }} <IconArrowRight />
            </RouterLink>
          </aside>
        </section>

        <section class="status-ledger" aria-label="今日工作状态">
          <RouterLink to="/tasks"><strong>{{ data.today_tasks.length }}</strong><span>今天必须办</span></RouterLink>
          <RouterLink to="/tasks"><strong>{{ dashboardCount("3日", "即将") }}</strong><span>3日内到期</span></RouterLink>
          <RouterLink to="/tasks"><strong>{{ data.overdue_tasks.length }}</strong><span>已逾期</span></RouterLink>
          <RouterLink to="/tasks"><strong>{{ data.pending_review_feedback.length }}</strong><span>等待我处理</span></RouterLink>
          <RouterLink to="/tasks"><strong>{{ dashboardCount("待审核") }}</strong><span>待审核</span></RouterLink>
          <RouterLink to="/tasks"><strong>{{ dashboardCount("反馈") }}</strong><span>等待反馈</span></RouterLink>
          <RouterLink to="/tasks"><strong>{{ data.risks.incomplete_materials }}</strong><span>材料不完整</span></RouterLink>
          <RouterLink to="/fleet/devices"><strong>{{ data.risks.device_alerts.length }}</strong><span>设备异常</span></RouterLink>
        </section>

        <section class="must-do">
          <div class="section-heading">
            <div><span>当前清单</span><h2>今天必须办理</h2></div>
            <RouterLink to="/tasks">查看全部事项 <IconArrowRight /></RouterLink>
          </div>
          <div class="task-list">
            <RouterLink v-for="task in primaryTasks" :key="task.id" :to="task.route" class="task-row">
              <i :class="task.priority"></i>
              <div>
                <div class="task-title"><strong>{{ task.title }}</strong><TaskStatusTag :status="task.status" /></div>
                <small>{{ task.work_area || "日常工作" }}</small>
              </div>
              <time>{{ dueLabel(task) }}</time>
              <IconArrowRight />
            </RouterLink>
            <div v-if="!primaryTasks.length" class="empty-state">当前没有必须立即办理的事项。</div>
          </div>
        </section>

        <section class="projection-strip">
          <RouterLink to="/calendar">
            <IconCalendar />
            <div>
              <span>工作日历</span>
              <strong>{{ nextCalendarTask?.title || "打开周视图安排办理顺序" }}</strong>
              <small v-if="nextCalendarTask">下一节点：{{ dueLabel(nextCalendarTask) }}</small>
            </div>
            <IconArrowRight />
          </RouterLink>
          <RouterLink to="/reports">
            <IconCheckCircle />
            <div>
              <span>周期汇总</span>
              <strong>已自动归集，投影正常</strong>
              <small>本周已完成 {{ data.completed_this_week.length }} 项，发布报告不会被静默改写</small>
            </div>
            <IconArrowRight />
          </RouterLink>
        </section>
      </template>
    </a-spin>
  </div>
</template>

<style scoped>
.today-page {
  width: 100%;
  max-width: none;
}

.today-page :deep(.page-header) {
  margin-bottom: 30px;
}

.today-page :deep(.page-title) {
  font-size: 36px;
  line-height: 1.25;
}

.date-kicker {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 9px;
  color: var(--cinnabar);
  font: 13px Georgia, serif;
  letter-spacing: 0.08em;
}

.date-kicker b {
  padding: 2px 6px;
  font: 500 10px var(--serif);
  letter-spacing: 0.08em;
  border: 1px solid rgba(180, 35, 24, 0.5);
}

.full-spin {
  display: block;
  min-height: 420px;
}

.weekly-board {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 232px;
  gap: 12px;
  margin-bottom: 18px;
}

.weekly-panel {
  min-height: 276px;
  padding: 20px 22px;
  background: rgba(251, 248, 241, 0.68);
  border: 1px solid var(--line);
  border-top: 3px solid var(--cinnabar);
}

.weekly-panel.completed {
  border-top-color: var(--green);
}

.weekly-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}

.weekly-heading span {
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.12em;
}

.weekly-heading h2 {
  margin: 5px 0 0;
  font-family: var(--serif);
  font-size: 20px;
  font-weight: 500;
}

.weekly-heading > strong {
  font: 29px Georgia, serif;
  font-weight: 400;
}

.weekly-items > a {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-height: 72px;
  border-bottom: 1px solid var(--line-light);
}

.weekly-items svg {
  color: var(--green);
  font-size: 21px;
}

.weekly-items time {
  display: grid;
  width: 26px;
  height: 26px;
  place-items: center;
  color: var(--cinnabar);
  font: 12px Georgia, serif;
  border: 1px solid rgba(180, 35, 24, 0.35);
  border-radius: 50%;
}

.weekly-items b,
.weekly-items small {
  display: block;
}

.weekly-items b {
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weekly-items small,
.weekly-items p {
  margin-top: 4px;
  color: var(--muted);
  font-size: 10px;
}

.attention-panel {
  display: flex;
  min-height: 276px;
  flex-direction: column;
  padding: 25px 22px;
  color: #f8efe4;
  background: var(--charcoal);
}

.attention-panel > span {
  color: #c8beb2;
  font-size: 11px;
  letter-spacing: 0.12em;
}

.attention-panel > strong {
  margin-top: 14px;
  font: 52px Georgia, serif;
  font-weight: 400;
}

.attention-panel p {
  color: #c8beb2;
  font-size: 12px;
  line-height: 1.8;
}

.attention-panel a {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  color: #e4a397;
  font-size: 12px;
}

.status-ledger {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  margin-bottom: 24px;
  background: var(--line);
  border: 1px solid var(--line);
  gap: 1px;
}

.status-ledger a {
  min-height: 86px;
  padding: 15px 14px;
  background: rgba(251, 248, 241, 0.88);
}

.status-ledger a:first-child {
  box-shadow: inset 0 -3px var(--cinnabar);
}

.status-ledger a:hover {
  background: rgba(180, 35, 24, 0.05);
}

.status-ledger strong,
.status-ledger span {
  display: block;
}

.status-ledger strong {
  font: 27px Georgia, serif;
  font-weight: 400;
}

.status-ledger span {
  margin-top: 8px;
  color: var(--muted);
  font-size: 10px;
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 10px;
}

.section-heading span {
  color: var(--cinnabar);
  font-size: 10px;
  letter-spacing: 0.14em;
}

.section-heading h2 {
  margin: 5px 0 0;
  font-family: var(--serif);
  font-size: 20px;
}

.section-heading a {
  color: var(--cinnabar);
  font-size: 12px;
}

.task-list {
  border-top: 1px solid var(--line);
}

.task-row {
  display: grid;
  grid-template-columns: 5px minmax(0, 1fr) 140px 18px;
  gap: 14px;
  align-items: center;
  min-height: 68px;
  border-bottom: 1px solid var(--line-light);
}

.task-row:hover {
  padding-left: 7px;
  background: rgba(251, 248, 241, 0.72);
}

.task-row i {
  width: 4px;
  height: 28px;
  background: #aaa198;
}

.task-row i.high,
.task-row i.urgent {
  background: var(--cinnabar);
}

.task-row i.low {
  background: var(--green);
}

.task-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-row small,
.task-row time {
  color: var(--muted);
  font-size: 11px;
}

.task-row time {
  text-align: right;
}

.projection-strip {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  margin-top: 24px;
  background: var(--line);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.projection-strip > a {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 18px;
  gap: 14px;
  align-items: center;
  min-height: 96px;
  padding: 16px 20px;
  background: rgba(251, 248, 241, 0.66);
}

.projection-strip > a > svg:first-child {
  color: var(--cinnabar);
  font-size: 25px;
}

.projection-strip span,
.projection-strip strong,
.projection-strip small {
  display: block;
}

.projection-strip span {
  color: var(--muted);
  font-size: 10px;
}

.projection-strip strong {
  margin-top: 5px;
  font-family: var(--serif);
  font-size: 16px;
}

.projection-strip small {
  margin-top: 5px;
  color: var(--muted);
  font-size: 10px;
}

@media (max-width: 1280px) {
  .weekly-board {
    grid-template-columns: 1fr 1fr 190px;
  }

  .status-ledger {
    grid-template-columns: repeat(4, 1fr);
  }
}

@media (max-height: 800px) {
  .weekly-panel,
  .attention-panel {
    min-height: 236px;
  }

  .weekly-items > a {
    min-height: 60px;
  }
}
</style>
