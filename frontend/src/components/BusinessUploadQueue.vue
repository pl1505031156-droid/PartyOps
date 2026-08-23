<script setup lang="ts">
import { IconClose, IconRefresh } from "@arco-design/web-vue/es/icon";
import type { UploadQueueItem } from "../composables/useUploadQueue";

defineProps<{ items: UploadQueueItem[] }>();
defineEmits<{ retry: [id: string]; cancel: [id: string]; clear: [] }>();

const statusLabel: Record<UploadQueueItem["status"], string> = {
  queued: "等待上传",
  uploading: "正在上传",
  succeeded: "上传完成",
  failed: "上传失败",
  cancelled: "已取消",
};
</script>

<template>
  <section v-if="items.length" class="upload-queue" aria-live="polite" aria-label="文件上传进度">
    <header>
      <div><strong>本次文件</strong><span>{{ items.filter((item) => item.status === 'succeeded').length }}/{{ items.length }} 已完成</span></div>
      <a-button size="mini" type="text" @click="$emit('clear')">清理已完成</a-button>
    </header>
    <ol>
      <li v-for="item in items" :key="item.id" :class="`is-${item.status}`">
        <div class="file-row">
          <div><strong :title="item.file.name">{{ item.file.name }}</strong><span>{{ (item.file.size / 1024 / 1024).toFixed(2) }} MB · {{ statusLabel[item.status] }}</span></div>
          <a-space>
            <a-button v-if="item.status === 'failed' || item.status === 'cancelled'" size="mini" type="text" @click="$emit('retry', item.id)"><template #icon><IconRefresh /></template>重试</a-button>
            <a-button v-if="item.status === 'queued' || item.status === 'uploading'" size="mini" type="text" @click="$emit('cancel', item.id)"><template #icon><IconClose /></template>取消</a-button>
          </a-space>
        </div>
        <a-progress :percent="item.progress / 100" :show-text="false" size="small" :status="item.status === 'failed' ? 'danger' : item.status === 'succeeded' ? 'success' : 'normal'" />
        <p v-if="item.error" class="queue-error">{{ item.error }}</p>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.upload-queue { margin-top: 14px; border: 1px solid var(--color-border-2); background: var(--color-fill-1); }
.upload-queue > header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--color-border-2); }
.upload-queue > header div { display: flex; align-items: baseline; gap: 10px; }
.upload-queue > header span { color: var(--color-text-3); font-size: 12px; }
.upload-queue ol { margin: 0; padding: 0; list-style: none; }
.upload-queue li { padding: 12px 14px; border-bottom: 1px solid var(--color-border-1); }
.upload-queue li:last-child { border-bottom: 0; }
.file-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.file-row > div:first-child { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.file-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-row span, .queue-error { color: var(--color-text-3); font-size: 12px; }
.queue-error { margin: 6px 0 0; color: rgb(var(--danger-6)); }
.is-succeeded .file-row span { color: rgb(var(--success-6)); }
@media (max-width: 640px) { .file-row { align-items: flex-start; } .file-row strong { max-width: 52vw; } }
</style>
