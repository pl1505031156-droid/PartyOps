<script setup lang="ts">
import { computed } from "vue";
import type { TaskStatus } from "../types";

const props = defineProps<{ status: TaskStatus }>();
const labels: Record<TaskStatus, string> = {
  pending_receipt: "待接收",
  pending_breakdown: "待拆解",
  in_progress: "办理中",
  waiting_feedback: "等待反馈",
  pending_review: "待审核",
  returned: "退回修改",
  completed: "已完成",
  archived: "已归档",
};
const tones: Record<TaskStatus, string> = {
  pending_receipt: "blue",
  pending_breakdown: "orange",
  in_progress: "red",
  waiting_feedback: "gold",
  pending_review: "purple",
  returned: "magenta",
  completed: "green",
  archived: "gray",
};
const label = computed(() => labels[props.status]);
const color = computed(() => tones[props.status]);
</script>

<template>
  <a-tag :color="color" size="small">{{ label }}</a-tag>
</template>
