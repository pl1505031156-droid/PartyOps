<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { IconArrowRight, IconCloudDownload, IconCommand, IconDownload, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { SavedView, Task, WorkbenchData } from "../types";
import TaskStatusTag from "../components/TaskStatusTag.vue";
import { dayjs, formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const data = ref<WorkbenchData | null>(null);
const views = ref<SavedView[]>([]);
const loading = ref(false);
const activeBucket = ref("today");

const currentBucket = computed(() => data.value?.dashboard.buckets.find((item) => item.key === activeBucket.value) || data.value?.dashboard.buckets[0]);

async function load() {
  loading.value = true;
  try {
    const [next, saved] = await Promise.all([
      api.get<WorkbenchData>("/workbench"),
      api.get<SavedView[]>("/saved-views"),
    ]);
    data.value = next;
    views.value = saved;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "工作台加载失败");
  } finally {
    loading.value = false;
  }
}

function dueLabel(task: Task) {
  const due = task.internal_due_at || task.formal_due_at;
  return formatServerTime(due, "MM月DD日 HH:mm", "未设时限");
}

async function createHandover() {
  try {
    const result = await api.post<{ id: string; filename: string }>("/handover");
    Message.success(`已生成交接包：${result.filename}`);
    window.open(`/api/v1/handover/${result.id}/download`, "_blank");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "交接包生成失败");
  }
}

function openCommandCenter() {
  window.dispatchEvent(new CustomEvent("partyops:command"));
}

onMounted(() => {
  load();
  window.addEventListener("partyops:refresh", load);
});
onBeforeUnmount(() => {
  window.removeEventListener("partyops:refresh", load);
});
</script>

<template>
  <div class="page workbench-page">
    <header class="page-header">
      <div>
        <p class="date-kicker">{{ dayjs().format("YYYY年MM月DD日 dddd") }}</p>
        <h1 class="page-title">今日工作台</h1>
        <p class="page-description">事项、文件、设备和传输放在同一处，减少重复查找与反复汇总。</p>
      </div>
      <a-space>
        <a-button @click="openCommandCenter"><template #icon><IconCommand /></template>Ctrl+K 全局查找</a-button>
        <a-button @click="createHandover"><template #icon><IconDownload /></template>生成交接包</a-button>
        <a-button type="primary" :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新</a-button>
      </a-space>
    </header>

    <section v-if="data" class="signal-grid">
      <article><span>待处理事项</span><strong>{{ data.dashboard.buckets.reduce((sum, item) => sum + item.count, 0) }}</strong><small>含逾期与审核风险</small></article>
      <article><span>待处理传输</span><strong>{{ data.pending_transfers.length }}</strong><small>统一由主机中转</small></article>
      <article><span>在线设备</span><strong>{{ data.devices.filter((item) => item.status === "online").length }}</strong><small>状态变化实时汇总</small></article>
      <article class="dark-signal"><span>保存视图</span><strong>{{ views.length }}</strong><small>可在事项页一键复用</small></article>
    </section>

    <section class="workbench-main">
      <div class="workbench-list">
        <div class="section-heading">
          <div><span class="section-index">工作队列</span><h2>{{ currentBucket?.label || "待处理事项" }}</h2></div>
          <RouterLink to="/tasks">打开完整清单 <IconArrowRight /></RouterLink>
        </div>
        <div class="bucket-tabs">
          <button v-for="bucket in data?.dashboard.buckets" :key="bucket.key" :class="{ active: activeBucket === bucket.key }" @click="activeBucket = bucket.key">
            <strong>{{ bucket.count }}</strong><span>{{ bucket.label }}</span>
          </button>
        </div>
        <div class="task-rows">
          <RouterLink v-for="task in currentBucket?.items || []" :key="task.id" :to="`/tasks/${task.id}`" class="task-row">
            <div class="priority-mark" :class="task.priority"></div>
            <div><div class="task-title-line"><strong>{{ task.title }}</strong><TaskStatusTag :status="task.status" /></div><small>{{ task.work_area || task.category || "日常工作" }}</small></div>
            <span class="due">{{ dueLabel(task) }}</span><IconArrowRight />
          </RouterLink>
          <div v-if="!currentBucket?.items.length" class="empty-state">当前队列没有事项。</div>
        </div>
      </div>

      <aside class="workbench-side">
        <section class="side-card">
          <div class="side-heading"><h3>待处理传输</h3><RouterLink to="/fleet">设备中心</RouterLink></div>
          <div v-for="item in data?.pending_transfers" :key="item.id" class="transfer-row"><IconCloudDownload /><div><strong>{{ item.name }}</strong><small>{{ zhLabel(item.status) }} · {{ item.progress }}%</small></div></div>
          <p v-if="!data?.pending_transfers.length" class="muted">暂无待处理传输。</p>
        </section>
        <section class="side-card">
          <div class="side-heading"><h3>最近文件</h3><RouterLink to="/workspace">文件中心</RouterLink></div>
          <RouterLink v-for="file in data?.recent_files.slice(0, 6)" :key="file.id" :to="`/workspace?file=${file.id}`" class="file-row"><strong>{{ file.name }}</strong><small>{{ file.relative_path }}</small></RouterLink>
          <p v-if="!data?.recent_files.length" class="muted">暂无最近文件。</p>
        </section>
      </aside>
    </section>

  </div>
</template>

<style scoped>
.workbench-page { width: 100%; max-width: none; }
.date-kicker { margin: 0 0 10px; color: var(--cinnabar); font: 13px Georgia, serif; letter-spacing: .08em; }
.signal-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin-bottom: 28px; background: var(--line); border: 1px solid var(--line); }
.signal-grid article { min-height: 122px; padding: 22px; background: rgba(251,248,241,.86); }
.signal-grid span,.signal-grid strong,.signal-grid small { display:block; }
.signal-grid span { color: var(--muted); font-size: 11px; }
.signal-grid strong { margin: 8px 0; font: 34px Georgia, serif; }
.signal-grid small { color: var(--muted); font-size: 11px; }
.dark-signal { color:#f8efe4; background:var(--charcoal)!important; }
.dark-signal span,.dark-signal small { color:#c8beb2; }
.workbench-main { display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:28px; }
.section-heading,.side-heading { display:flex; align-items:flex-end; justify-content:space-between; margin-bottom:12px; }
.section-heading h2 { margin:5px 0 0; font-size:20px; }
.section-heading a,.side-heading a { color:var(--cinnabar); font-size:12px; }
.section-index { color:var(--cinnabar); font-size:11px; letter-spacing:.18em; }
.bucket-tabs { display:grid; grid-template-columns:repeat(4, minmax(90px,1fr)); border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
.bucket-tabs button { display:flex; flex-direction:column; gap:4px; padding:12px; text-align:left; background:transparent; border:0; border-right:1px solid var(--line-light); cursor:pointer; }
.bucket-tabs button.active { color:var(--cinnabar); background:rgba(180,35,24,.05); }
.bucket-tabs strong { font:24px Georgia,serif; }
.bucket-tabs span { color:var(--muted); font-size:11px; }
.task-rows { border-top:1px solid var(--line); }
.task-row { display:grid; grid-template-columns:4px minmax(0,1fr) 150px 18px; gap:16px; align-items:center; min-height:72px; border-bottom:1px solid var(--line-light); }
.task-row:hover { padding-left:8px; background:rgba(251,248,241,.72); }
.priority-mark { width:4px; height:30px; background:#aaa198; }.priority-mark.high,.priority-mark.urgent{background:var(--cinnabar)}.priority-mark.low{background:#7e9b85}
.task-title-line { display:flex; align-items:center; gap:8px; }.task-title-line strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.task-row small,.due{color:var(--muted);font-size:11px}.due{text-align:right}
.side-card { margin-bottom:18px; padding:18px; background:rgba(251,248,241,.76); border:1px solid var(--line); }.side-heading h3{margin:0;font-size:15px}.transfer-row,.file-row{display:flex;gap:10px;padding:11px 0;border-top:1px solid var(--line-light)}.transfer-row svg{color:var(--cinnabar);flex:0 0 auto}.transfer-row strong,.transfer-row small,.file-row strong,.file-row small{display:block}.transfer-row small,.file-row small{margin-top:3px;color:var(--muted);font-size:10px}.file-row strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.muted{color:var(--muted);font-size:12px}.palette-results{margin-top:12px}.palette-results a{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line-light)}.palette-results small{color:var(--muted)}
@media (max-width: 1100px){.workbench-main{grid-template-columns:1fr}.workbench-side{display:grid;grid-template-columns:1fr 1fr;gap:18px}.signal-grid{grid-template-columns:repeat(2,1fr)}}
</style>
