<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { IconArchive, IconDownload, IconExclamation } from "@arco-design/web-vue/es/icon";
import { api, downloadUrl } from "../api";
import PageHelp from "../components/PageHelp.vue";
import type { Task } from "../types";
import TaskStatusTag from "../components/TaskStatusTag.vue";

const tasks = ref<Task[]>([]);
const selected = ref<string[]>([]);
const incomplete = computed(() => tasks.value.filter((item) => item.missing_required_materials > 0));
const complete = computed(() => tasks.value.filter((item) => item.missing_required_materials === 0));
const packageUrl = computed(() => {
  const params = new URLSearchParams();
  selected.value.forEach((id) => params.append("task_ids", id));
  return downloadUrl(`/inspection/package?${params.toString()}`);
});

onMounted(async () => {
  const result = await api.get<{ items: Task[] }>("/tasks?page_size=100");
  tasks.value = result.items;
});
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">迎检与归档</h1>
        <p class="page-description">只汇总系统已有数据，自动形成缺项清单、材料目录和归档包。</p>
      </div>
      <a-space>
        <PageHelp
          title="迎检与归档怎么用"
          :tips="['先处理材料缺项，再生成归档包。', '目录和清单直接复用事项数据，不需要重复录入。', '归档包会附带文件哈希和校验清单。']"
          help-query="迎检归档"
        />
        <a-button type="primary" :href="packageUrl" target="_blank">
          <template #icon><IconArchive /></template>
          导出选中归档包
        </a-button>
      </a-space>
    </header>
    <section class="inspection-summary">
      <div><strong>{{ tasks.length }}</strong><span>纳入汇总事项</span></div>
      <div><strong class="text-danger">{{ incomplete.length }}</strong><span>存在材料缺项</span></div>
      <div><strong class="text-success">{{ complete.length }}</strong><span>材料目录完整</span></div>
      <a-button :href="downloadUrl('/exports/tasks.xlsx?kind=材料缺项清单')" target="_blank">
        <template #icon><IconDownload /></template>导出缺项清单
      </a-button>
    </section>
    <div class="inspection-list">
      <label v-for="task in tasks" :key="task.id">
        <a-checkbox v-model="selected" :value="task.id" />
        <div class="inspection-title">
          <strong>{{ task.title }}</strong>
          <span>{{ task.source || "未填写来源" }}</span>
        </div>
        <TaskStatusTag :status="task.status" />
        <div class="material-result" :class="{ danger: task.missing_required_materials }">
          <IconExclamation v-if="task.missing_required_materials" />
          <span>{{ task.missing_required_materials ? `缺 ${task.missing_required_materials} 项必备材料` : "材料目录完整" }}</span>
        </div>
        <RouterLink :to="`/tasks/${task.id}`">核验</RouterLink>
      </label>
    </div>
  </div>
</template>

<style scoped>
.inspection-summary {
  display: grid;
  align-items: center;
  grid-template-columns: 1fr 1fr 1fr auto;
  margin-bottom: 28px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.inspection-summary > div {
  min-height: 90px;
  padding: 18px 22px;
  border-right: 1px solid var(--line-light);
}

.inspection-summary strong,
.inspection-summary span {
  display: block;
}

.inspection-summary strong {
  font-family: Georgia, serif;
  font-size: 28px;
  font-weight: 500;
}

.inspection-summary span {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
}

.inspection-summary > button,
.inspection-summary > a {
  margin: 0 20px;
}

.inspection-list {
  border-top: 1px solid var(--line);
}

.inspection-list label {
  display: grid;
  min-height: 72px;
  align-items: center;
  grid-template-columns: 34px minmax(320px, 1fr) 100px 210px 54px;
  gap: 18px;
  border-bottom: 1px solid var(--line-light);
}

.inspection-title strong,
.inspection-title span {
  display: block;
}

.inspection-title span {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
}

.material-result {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--green);
  font-size: 12px;
}

.material-result.danger {
  color: var(--danger);
}

.inspection-list a {
  color: var(--cinnabar);
  font-size: 12px;
}
</style>
