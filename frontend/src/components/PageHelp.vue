<script setup lang="ts">
import { IconQuestionCircle } from "@arco-design/web-vue/es/icon";

defineProps<{
  title: string;
  tips: string[];
  helpQuery?: string;
}>();
</script>

<template>
  <span class="page-help-trigger">
    <a-popover position="br" trigger="click">
      <a-button :aria-label="`打开${title}帮助`"><template #icon><IconQuestionCircle /></template>本页帮助</a-button>
      <template #content>
        <div class="page-help">
          <strong>{{ title }}</strong>
          <ol><li v-for="tip in tips" :key="tip">{{ tip }}</li></ol>
          <RouterLink :to="{ path: '/help', query: { q: helpQuery || title } }">打开完整教程</RouterLink>
        </div>
      </template>
    </a-popover>
  </span>
</template>

<style scoped>
.page-help-trigger { display: inline-flex; }
.page-help-trigger :deep(.arco-btn) { min-height: 30px; padding-right: 10px; padding-left: 10px; color: var(--muted)!important; }
.page-help { width: min(340px, calc(100vw - 80px)); }
.page-help strong { display: block; padding-bottom: 9px; border-bottom: 1px solid var(--line); }
.page-help ol { margin: 10px 0; padding-left: 20px; color: var(--muted); font-size: 12px; line-height: 1.8; }
.page-help a { color: var(--cinnabar); font-size: 12px; }
</style>
