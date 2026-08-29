<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import {
  IconCalendar,
  IconLeft,
  IconPlus,
  IconRefresh,
  IconRight,
} from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type {
  CalendarEvent,
  CalendarEventType,
  CalendarPreference,
  User,
} from "../types";
import { beijingNow, formatServerTime, localInputToUtc, serverTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";
import { useSessionStore } from "../stores/session";
import PageHelp from "../components/PageHelp.vue";
import { orientalDateLabel } from "../utils/lunar";

type CalendarViewMode = "week" | "month" | "year";

const session = useSessionStore();
const anchor = ref(beijingNow());
const selectedDate = ref(beijingNow().format("YYYY-MM-DD"));
const mode = ref<CalendarViewMode>("week");
const events = ref<CalendarEvent[]>([]);
const preference = ref<CalendarPreference | null>(null);
const loading = ref(false);
const loadError = ref("");
const workdayVisible = ref(false);
const visibleTypes = ref<CalendarEventType[]>([]);
const users = ref<User[]>([]);
const topics = ref<Array<{ id: string; name: string }>>([]);
const selectedOwners = ref<string[]>([]);
const selectedTopics = ref<string[]>([]);
const selectedWorkAreas = ref<string[]>([]);
const knownWorkAreas = ref<string[]>([]);
const workdayForm = reactive({
  date_key: beijingNow().format("YYYY-MM-DD"),
  title: "",
  kind: "holiday",
  is_workday: false,
  note: "",
});
const todayLabel = orientalDateLabel();

const eventTypeOptions: Array<{ value: CalendarEventType; label: string }> = [
  { value: "task_due", label: "正式截止" },
  { value: "task_plan", label: "内部计划" },
  { value: "recurrence", label: "周期事项" },
  { value: "report_boundary", label: "汇总节点" },
  { value: "reminder", label: "提醒" },
  { value: "holiday", label: "节假日" },
  { value: "adjusted_workday", label: "调休工作日" },
];

function mondayOf(value: ReturnType<typeof beijingNow>) {
  return value.subtract((value.day() + 6) % 7, "day").startOf("day");
}

const range = computed(() => {
  if (mode.value === "year") {
    return {
      start: anchor.value.startOf("year"),
      end: anchor.value.add(1, "year").startOf("year"),
    };
  }
  if (mode.value === "month") {
    const monthStart = anchor.value.startOf("month");
    const gridStart = mondayOf(monthStart);
    return { start: gridStart, end: gridStart.add(42, "day") };
  }
  const start = mondayOf(anchor.value);
  return { start, end: start.add(7, "day") };
});

const weekDays = computed(() =>
  Array.from({ length: 7 }, (_, index) => range.value.start.add(index, "day")),
);
const monthDays = computed(() =>
  Array.from({ length: 42 }, (_, index) => range.value.start.add(index, "day")),
);
const yearMonths = computed(() =>
  Array.from({ length: 12 }, (_, index) => anchor.value.startOf("year").add(index, "month")),
);
const selectedEvents = computed(() =>
  events.value
    .filter((item) => serverTime(item.start_at).format("YYYY-MM-DD") === selectedDate.value)
    .sort((left, right) => left.start_at.localeCompare(right.start_at)),
);

function eventsFor(dateKey: string) {
  return events.value.filter(
    (item) => serverTime(item.start_at).format("YYYY-MM-DD") === dateKey,
  );
}

function monthEventCount(month: number) {
  return events.value.filter((item) => serverTime(item.start_at).month() === month).length;
}

function typeClass(type: CalendarEventType) {
  return `event-${type}`;
}

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    if (!preference.value) {
      preference.value = await api.get<CalendarPreference>("/calendar/preferences");
      mode.value = preference.value.default_view;
      visibleTypes.value = [...preference.value.visible_event_types];
    }
    const query = new URLSearchParams({
      start: localInputToUtc(range.value.start),
      end: localInputToUtc(range.value.end),
    });
    visibleTypes.value.forEach((value) => query.append("event_type", value));
    selectedOwners.value.forEach((value) => query.append("owner_id", value));
    selectedTopics.value.forEach((value) => query.append("topic_id", value));
    selectedWorkAreas.value.forEach((value) => query.append("work_area", value));
    events.value = await api.get<CalendarEvent[]>(`/calendar/events?${query.toString()}`);
    knownWorkAreas.value = Array.from(
      new Set([
        ...knownWorkAreas.value,
        ...events.value.map((item) => item.work_area).filter(Boolean),
      ]),
    ).sort((left, right) => left.localeCompare(right, "zh-CN"));
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "工作日历加载失败";
  } finally {
    loading.value = false;
  }
}

async function setMode(value: CalendarViewMode) {
  mode.value = value;
  if (preference.value) {
    try {
      preference.value = await api.patch<CalendarPreference>(
        "/calendar/preferences",
        { default_view: value },
        { "If-Match": String(preference.value.version) },
      );
    } catch {
      Message.warning("视图偏好暂未保存，本次查看不受影响");
    }
  }
  await load();
}

async function applyFilters() {
  if (preference.value) {
    try {
      preference.value = await api.patch<CalendarPreference>(
        "/calendar/preferences",
        { visible_event_types: visibleTypes.value },
        { "If-Match": String(preference.value.version) },
      );
    } catch {
      Message.warning("筛选偏好暂未保存");
    }
  }
  await load();
}

function move(direction: number) {
  const unit = mode.value === "year" ? "year" : mode.value === "month" ? "month" : "week";
  anchor.value = anchor.value.add(direction, unit);
  selectedDate.value = anchor.value.format("YYYY-MM-DD");
  load();
}

function goToday() {
  anchor.value = beijingNow();
  selectedDate.value = beijingNow().format("YYYY-MM-DD");
  load();
}

function selectDay(value: ReturnType<typeof beijingNow>) {
  selectedDate.value = value.format("YYYY-MM-DD");
}

async function saveWorkday() {
  if (!workdayForm.title.trim()) {
    Message.warning("请填写日期说明");
    return;
  }
  try {
    await api.post("/calendar/workdays/import", {
      items: [{ ...workdayForm, title: workdayForm.title.trim() }],
    });
    workdayVisible.value = false;
    Message.success("工作日历设置已保存");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "日期设置保存失败");
  }
}

onMounted(async () => {
  try {
    [users.value, topics.value] = await Promise.all([
      api.get<User[]>("/users"),
      api.get<Array<{ id: string; name: string }>>("/topics"),
    ]);
  } catch {
    // 筛选辅助数据失败不影响日历主体使用。
  }
  await load();
  window.addEventListener("partyops:refresh", load);
});
onBeforeUnmount(() => window.removeEventListener("partyops:refresh", load));
</script>

<template>
  <div class="page calendar-page">
    <header class="page-header">
      <div>
        <p class="date-kicker">
          {{ todayLabel.gregorian }}　{{ todayLabel.weekday }}　{{ todayLabel.lunar }}
          <b v-if="todayLabel.solarTerm">{{ todayLabel.solarTerm }}</b>
        </p>
        <h1 class="page-title">工作日历</h1>
        <p class="page-description">任务、内部计划、周期事项、报告节点和节假日只做时间投影，不生成重复数据。</p>
      </div>
      <a-space>
        <PageHelp
          title="工作日历怎么用"
          :tips="['周视图用于安排每天办理顺序。', '正式截止不能拖拽修改，避免误改上级时限。', '节假日和调休会参与内部提前节点计算。']"
          help-query="周期任务"
        />
        <a-button v-if="session.user?.role === 'admin'" @click="workdayVisible = true">
          <template #icon><IconPlus /></template>设置节假日
        </a-button>
        <a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新</a-button>
      </a-space>
    </header>

    <section class="calendar-toolbar">
      <div class="date-nav">
        <a-button size="small" @click="move(-1)"><template #icon><IconLeft /></template></a-button>
        <a-button size="small" @click="goToday">回到今天</a-button>
        <a-button size="small" @click="move(1)"><template #icon><IconRight /></template></a-button>
        <strong>{{ mode === "year" ? anchor.format("YYYY年") : anchor.format("YYYY年MM月") }}</strong>
      </div>
      <a-radio-group :model-value="mode" type="button" @change="setMode($event as CalendarViewMode)">
        <a-radio value="week">周</a-radio>
        <a-radio value="month">月</a-radio>
        <a-radio value="year">年度节点</a-radio>
      </a-radio-group>
    </section>

    <section class="filter-bar">
      <span>显示内容</span>
      <a-checkbox-group v-model="visibleTypes" @change="applyFilters">
        <a-checkbox v-for="option in eventTypeOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </a-checkbox>
      </a-checkbox-group>
      <a-select v-model="selectedOwners" multiple allow-clear allow-search placeholder="按负责人" class="calendar-filter" @change="load">
        <a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option>
      </a-select>
      <a-select v-model="selectedWorkAreas" multiple allow-clear allow-create allow-search placeholder="按工作领域" class="calendar-filter" @change="load">
        <a-option v-for="area in knownWorkAreas" :key="area" :value="area">{{ area }}</a-option>
      </a-select>
      <a-select v-model="selectedTopics" multiple allow-clear allow-search placeholder="按专题" class="calendar-filter" @change="load">
        <a-option v-for="topic in topics" :key="topic.id" :value="topic.id">{{ topic.name }}</a-option>
      </a-select>
    </section>

    <a-alert v-if="loadError" type="error" title="工作日历暂时无法加载" :content="loadError" show-icon>
      <template #action><a-button size="small" @click="load">重新加载</a-button></template>
    </a-alert>

    <a-spin :loading="loading" class="calendar-spin">
      <section v-if="mode === 'week'" class="week-layout">
        <div class="week-grid">
          <button
            v-for="date in weekDays"
            :key="date.format('YYYY-MM-DD')"
            type="button"
            class="week-day"
            :class="{ selected: selectedDate === date.format('YYYY-MM-DD'), today: date.isSame(beijingNow(), 'day') }"
            @click="selectDay(date)"
          >
            <header><span>{{ date.format("ddd") }}</span><strong>{{ date.format("DD") }}</strong></header>
            <div
              v-for="event in eventsFor(date.format('YYYY-MM-DD')).slice(0, 6)"
              :key="event.id"
              class="calendar-event"
              :class="typeClass(event.event_type)"
            >
              <time>{{ event.all_day ? "全天" : formatServerTime(event.start_at, "HH:mm") }}</time>
              <span>{{ event.title }}</span>
            </div>
            <small v-if="eventsFor(date.format('YYYY-MM-DD')).length > 6">
              另有 {{ eventsFor(date.format("YYYY-MM-DD")).length - 6 }} 项
            </small>
          </button>
        </div>
        <aside class="agenda">
          <div class="agenda-heading">
            <IconCalendar /><div><span>当日日程</span><h2>{{ serverTime(`${selectedDate}T00:00:00+08:00`).format("MM月DD日 dddd") }}</h2></div>
          </div>
          <RouterLink v-for="event in selectedEvents" :key="event.id" :to="event.route || '/calendar'" class="agenda-item">
            <i :class="typeClass(event.event_type)"></i>
            <div><strong>{{ event.title }}</strong><small>{{ zhLabel(event.event_type) }} · {{ event.all_day ? "全天" : formatServerTime(event.start_at, "HH:mm") }}</small></div>
          </RouterLink>
          <p v-if="!selectedEvents.length" class="empty-agenda">当天没有安排。</p>
        </aside>
      </section>

      <section v-else-if="mode === 'month'" class="month-grid">
        <div v-for="label in ['一','二','三','四','五','六','日']" :key="label" class="weekday-label">周{{ label }}</div>
        <button
          v-for="date in monthDays"
          :key="date.format('YYYY-MM-DD')"
          type="button"
          class="month-day"
          :class="{ outside: date.month() !== anchor.month(), selected: selectedDate === date.format('YYYY-MM-DD'), today: date.isSame(beijingNow(), 'day') }"
          @click="selectDay(date)"
        >
          <strong>{{ date.date() }}</strong>
          <span v-for="event in eventsFor(date.format('YYYY-MM-DD')).slice(0, 3)" :key="event.id" :class="typeClass(event.event_type)">
            {{ event.title }}
          </span>
          <small v-if="eventsFor(date.format('YYYY-MM-DD')).length > 3">+{{ eventsFor(date.format("YYYY-MM-DD")).length - 3 }}</small>
        </button>
      </section>

      <section v-else class="year-grid">
        <button v-for="(month, index) in yearMonths" :key="month.format('YYYY-MM')" type="button" @click="anchor = month; setMode('month')">
          <span>{{ index + 1 }}月</span><strong>{{ monthEventCount(index) }}</strong><small>个工作节点</small>
        </button>
      </section>
    </a-spin>

    <a-modal v-model:visible="workdayVisible" title="设置节假日或调休工作日" width="560px" @ok="saveWorkday">
      <a-form :model="workdayForm" layout="vertical">
        <a-form-item label="日期"><a-date-picker v-model="workdayForm.date_key" value-format="YYYY-MM-DD" style="width:100%" /></a-form-item>
        <a-form-item label="类型">
          <a-radio-group v-model="workdayForm.kind">
            <a-radio value="holiday" @change="workdayForm.is_workday = false">节假日</a-radio>
            <a-radio value="adjusted_workday" @change="workdayForm.is_workday = true">调休工作日</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="名称" required><a-input v-model="workdayForm.title" placeholder="例如：国庆节、周六调休上班" /></a-form-item>
        <a-form-item label="备注"><a-textarea v-model="workdayForm.note" :max-length="500" show-word-limit /></a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.calendar-page { width: 100%; max-width: none; }
.date-kicker { margin: 0 0 9px; color: var(--cinnabar); font: 11px Georgia,serif; letter-spacing: .08em; }
.date-kicker b { margin-left: 8px; padding: 2px 6px; font: 500 10px var(--serif); border: 1px solid rgba(180,35,24,.5); }
.calendar-toolbar,.filter-bar { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 0; border-top: 1px solid var(--line); }
.date-nav { display: flex; align-items: center; gap: 8px; }
.date-nav strong { margin-left: 8px; font-family: "Noto Serif CJK SC",SimSun,serif; font-size: 18px; }
.filter-bar { flex-wrap: wrap; justify-content: flex-start; color: var(--muted); font-size: 12px; border-bottom: 1px solid var(--line); }
.calendar-filter { width: 168px; }
.calendar-spin { display: block; min-height: 480px; margin-top: 18px; }
.week-layout { display: grid; grid-template-columns: minmax(0,1fr) 300px; gap: 18px; }
.week-grid { display: grid; grid-template-columns: repeat(7,minmax(88px,1fr)); border: 1px solid var(--line); background: var(--line); gap: 1px; overflow: hidden; }
.week-day { min-height: 520px; padding: 0 10px 12px; text-align: left; background: rgba(251,248,241,.9); border: 0; cursor: pointer; }
.week-day:hover,.week-day.selected { background: #fffaf1; }
.week-day.selected { box-shadow: inset 0 3px var(--cinnabar); }
.week-day header { display: flex; align-items: baseline; justify-content: space-between; padding: 15px 2px 12px; border-bottom: 1px solid var(--line-light); }
.week-day header span { color: var(--muted); font-size: 11px; }
.week-day header strong { font: 24px Georgia,serif; }
.week-day.today header strong { color: var(--cinnabar); }
.calendar-event { margin-top: 8px; padding: 8px; border-left: 3px solid #867e74; background: rgba(83,78,72,.06); }
.calendar-event time,.calendar-event span { display: block; }
.calendar-event time { color: var(--muted); font-size: 9px; }
.calendar-event span { margin-top: 3px; overflow: hidden; font-size: 11px; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.event-task_due { border-color: var(--danger)!important; }
.event-task_plan { border-color: #3f6d8d!important; }
.event-recurrence { border-color: #8b5f9d!important; }
.event-report_boundary { border-color: var(--cinnabar)!important; }
.event-reminder { border-color: var(--amber)!important; }
.event-holiday { border-color: #2f7d4c!important; }
.event-adjusted_workday { border-color: #517f63!important; }
.agenda { padding: 20px; background: rgba(251,248,241,.76); border: 1px solid var(--line); }
.agenda-heading { display: flex; align-items: center; gap: 10px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.agenda-heading svg { color: var(--cinnabar); }
.agenda-heading span { color: var(--muted); font-size: 10px; }
.agenda-heading h2 { margin: 3px 0 0; font-size: 17px; }
.agenda-item { display: grid; grid-template-columns: 3px 1fr; gap: 10px; padding: 13px 0; border-bottom: 1px solid var(--line-light); }
.agenda-item i { border-left: 3px solid #867e74; }
.agenda-item strong,.agenda-item small { display: block; }
.agenda-item small { margin-top: 4px; color: var(--muted); font-size: 10px; }
.empty-agenda { padding: 36px 0; color: var(--muted); text-align: center; font-size: 12px; }
.month-grid { display: grid; grid-template-columns: repeat(7,minmax(110px,1fr)); border: 1px solid var(--line); background: var(--line); gap: 1px; overflow-x: auto; }
.weekday-label { padding: 9px; color: var(--muted); text-align: center; background: var(--ivory-deep); font-size: 11px; }
.month-day { min-height: 116px; padding: 8px; text-align: left; background: rgba(251,248,241,.94); border: 0; cursor: pointer; }
.month-day.outside { opacity: .46; }
.month-day.selected { box-shadow: inset 0 0 0 2px var(--cinnabar); }
.month-day.today > strong { display: inline-grid; width: 24px; height: 24px; place-items: center; color: white; background: var(--cinnabar); border-radius: 50%; }
.month-day > span { display: block; margin-top: 5px; padding-left: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; border-left: 3px solid #867e74; font-size: 10px; }
.month-day > small { color: var(--cinnabar); font-size: 9px; }
.year-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); }
.year-grid button { min-height: 150px; padding: 24px; text-align: left; background: rgba(251,248,241,.92); border: 0; cursor: pointer; }
.year-grid button:hover { background: rgba(180,35,24,.06); }
.year-grid span,.year-grid strong,.year-grid small { display: block; }
.year-grid strong { margin: 10px 0 4px; color: var(--cinnabar); font: 34px Georgia,serif; }
.year-grid small { color: var(--muted); }
@media (max-width: 1160px) {
  .week-layout { grid-template-columns: 1fr; }
  .week-day { min-height: 430px; }
  .year-grid { grid-template-columns: repeat(3,1fr); }
}
</style>
