<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { IconRefresh, IconUserGroup } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import TaskStatusTag from "../components/TaskStatusTag.vue";
import type { Task } from "../types";
import { formatServerTime } from "../utils/datetime";

type WorkScope = "owned" | "collaborating" | "reviewing" | "step_assigned";

const scopes: Array<{ key: WorkScope; label: string; note: string }> = [
  { key: "owned", label: "我主办", note: "由我负责推进的事项" },
  { key: "collaborating", label: "我协办", note: "我作为协办人参与的事项" },
  { key: "reviewing", label: "我审核", note: "当前待我审核的事项" },
  { key: "step_assigned", label: "步骤分派给我", note: "仍有步骤需要我完成" },
];
const activeScope = ref<WorkScope>("owned");
const summary = ref<Record<WorkScope, number>>({ owned: 0, collaborating: 0, reviewing: 0, step_assigned: 0 });
const tasksByScope = ref<Record<WorkScope, Task[]>>({ owned: [], collaborating: [], reviewing: [], step_assigned: [] });
const loading = ref(false);
const activeTasks = computed(() => tasksByScope.value[activeScope.value]);

async function load() {
  loading.value = true;
  try {
    const [counts, ...results] = await Promise.all([
      api.get<Record<WorkScope, number>>("/tasks/my-work-summary"),
      ...scopes.map((scope) => api.get<{ items: Task[] }>(`/tasks?scope=${scope.key}&page_size=100`)),
    ]);
    summary.value = counts;
    scopes.forEach((scope, index) => {
      tasksByScope.value[scope.key] = results[index].items;
    });
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "我的工作加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page my-work-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">个人协同视图</p>
        <h1 class="page-title">我的工作</h1>
        <p class="page-description">把主办、协办、审核和步骤分派集中到一处，只显示当前账号真正需要处理的工作。</p>
      </div>
      <a-button @click="load"><template #icon><IconRefresh /></template>刷新</a-button>
    </header>

    <section class="scope-grid" aria-label="我的工作分类">
      <button v-for="scope in scopes" :key="scope.key" type="button" :class="{ active: activeScope === scope.key }" @click="activeScope = scope.key">
        <span><IconUserGroup /><b>{{ scope.label }}</b></span>
        <strong>{{ summary[scope.key] }}</strong>
        <small>{{ scope.note }}</small>
      </button>
    </section>

    <a-spin :loading="loading" style="width: 100%">
      <section class="work-list">
        <RouterLink v-for="task in activeTasks" :key="task.id" :to="`/tasks/${task.id}`" class="work-row">
          <div>
            <span>{{ task.category || "日常工作" }}</span>
            <b>{{ task.title }}</b>
            <small>{{ task.description || "暂无办理说明" }}</small>
          </div>
          <TaskStatusTag :status="task.status" />
          <time>{{ task.internal_due_at ? `内部截止 ${formatServerTime(task.internal_due_at, 'MM-DD HH:mm')}` : "未设内部截止" }}</time>
        </RouterLink>
        <div v-if="!activeTasks.length && !loading" class="empty-state">当前分类没有待处理事项。</div>
      </section>
    </a-spin>
  </div>
</template>

<style scoped>
.scope-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 20px; }
.scope-grid button { display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; padding: 18px; text-align: left; color: var(--ink); background: rgba(255,255,255,.4); border: 1px solid var(--line); cursor: pointer; }
.scope-grid button.active { color: var(--cinnabar); background: rgba(180,35,24,.07); border-color: rgba(180,35,24,.45); box-shadow: inset 3px 0 0 var(--cinnabar); }
.scope-grid button > span { display: flex; align-items: center; gap: 8px; }
.scope-grid button > strong { font: 28px Georgia, serif; }
.scope-grid button > small { grid-column: 1 / -1; color: var(--muted); }
.work-list { border: 1px solid var(--line); background: rgba(255,255,255,.32); }
.work-row { display: grid; grid-template-columns: minmax(0, 1fr) 100px 180px; gap: 18px; align-items: center; min-height: 86px; padding: 14px 18px; border-bottom: 1px solid var(--line-light); }
.work-row:hover { background: rgba(180,35,24,.05); }
.work-row div span, .work-row div b, .work-row div small { display: block; }
.work-row div span { color: var(--cinnabar); font-size: 10px; letter-spacing: .06em; }
.work-row div b { margin-top: 5px; font-size: 15px; }
.work-row div small, .work-row time { margin-top: 5px; color: var(--muted); font-size: 11px; }
.work-row time { text-align: right; }
@media (max-width: 900px) { .scope-grid { grid-template-columns: repeat(2, 1fr); } .work-row { grid-template-columns: 1fr; gap: 7px; } .work-row time { text-align: left; } }
</style>
