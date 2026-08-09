<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { IconCheck, IconNotification, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { NotificationItem } from "../types";
import { formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const router = useRouter();
const items = ref<NotificationItem[]>([]);
const typeFilter = ref("");
const unreadOnly = ref(false);
const loading = ref(false);
const types = [
  ["", "全部通知"], ["comment", "评论"], ["mention", "提及"], ["assignment", "指派"],
  ["review", "审核"], ["transfer", "传输"], ["root_approval", "共享目录审批"],
];
const unreadCount = computed(() => items.value.filter((item) => !item.read_at).length);

async function load() {
  loading.value = true;
  try {
    const query = new URLSearchParams({ limit: "500" });
    if (typeFilter.value) query.set("notification_type", typeFilter.value);
    if (unreadOnly.value) query.set("unread_only", "true");
    items.value = await api.get<NotificationItem[]>(`/notifications?${query}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "通知加载失败");
  } finally {
    loading.value = false;
  }
}

async function openItem(item: NotificationItem) {
  if (!item.read_at) await api.post(`/notifications/${item.id}/read`);
  if (item.entity_type === "task" && item.entity_id) await router.push(`/tasks/${item.entity_id}`);
  else if (item.entity_type === "transfer") await router.push("/fleet/inbox");
  else if (item.entity_type === "workspace_root") await router.push("/fleet/grants");
  else await load();
}

async function markAllRead() {
  await api.post("/notifications/read-all");
  await load();
  Message.success("全部通知已标为已读");
}

onMounted(load);
</script>

<template>
  <div class="page notifications-page">
    <header class="page-header">
      <div><p class="page-kicker">协同消息中心</p><h1 class="page-title">通知中心</h1><p class="page-description">评论、提及、指派、审核、文件传输和共享目录审批在这里统一查看并跳转处理。</p></div>
      <a-space><a-button @click="load"><template #icon><IconRefresh /></template>刷新</a-button><a-button type="primary" :disabled="!unreadCount" @click="markAllRead"><template #icon><IconCheck /></template>全部已读</a-button></a-space>
    </header>
    <section class="notice-toolbar">
      <a-select v-model="typeFilter" style="width: 180px" @change="load"><a-option v-for="option in types" :key="option[0]" :value="option[0]">{{ option[1] }}</a-option></a-select>
      <a-checkbox v-model="unreadOnly" @change="load">只看未读</a-checkbox><span>{{ unreadCount }} 条未读</span>
    </section>
    <a-spin :loading="loading" style="width: 100%">
      <section class="notice-list">
        <button v-for="item in items" :key="item.id" type="button" :class="{ unread: !item.read_at }" @click="openItem(item)">
          <IconNotification :size="18" /><span><b>{{ item.title }}</b><small>{{ item.body || zhLabel(item.notification_type, "协同通知") }}</small></span><em>{{ zhLabel(item.notification_type, "通知") }}</em><time>{{ formatServerTime(item.created_at, "MM-DD HH:mm") }}</time>
        </button>
        <div v-if="!items.length && !loading" class="empty-state">当前筛选下没有通知。</div>
      </section>
    </a-spin>
  </div>
</template>

<style scoped>
.notice-toolbar { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
.notice-toolbar > span { color: var(--muted); font-size: 11px; }
.notice-list { border: 1px solid var(--line); background: rgba(255,255,255,.3); }
.notice-list button { display: grid; grid-template-columns: 28px minmax(0,1fr) 110px 100px; gap: 10px; align-items: center; width: 100%; min-height: 76px; padding: 12px 16px; text-align: left; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--line-light); cursor: pointer; }
.notice-list button.unread { background: rgba(180,35,24,.055); box-shadow: inset 3px 0 0 var(--cinnabar); }
.notice-list button:hover { background: rgba(180,35,24,.08); }
.notice-list b, .notice-list small { display: block; }
.notice-list small { margin-top: 5px; color: var(--muted); }
.notice-list em { color: var(--cinnabar); font-size: 11px; font-style: normal; }
.notice-list time { color: var(--muted); font-size: 10px; text-align: right; }
@media (max-width: 720px) { .notice-list button { grid-template-columns: 24px 1fr; } .notice-list em, .notice-list time { grid-column: 2; text-align: left; } }
</style>
