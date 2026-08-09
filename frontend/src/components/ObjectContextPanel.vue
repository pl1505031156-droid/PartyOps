<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { IconLink, IconPlus, IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message, Modal } from "@arco-design/web-vue";
import { api } from "../api";
import type { ActivityEvent, ObjectLink, ObjectType } from "../types";
import { formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const props = defineProps<{ objectType: ObjectType; objectId: string }>();
const links = ref<ObjectLink[]>([]);
const activity = ref<ActivityEvent[]>([]);
const loading = ref(false);
const visible = ref(false);
const searchQuery = ref("");
const searchLoading = ref(false);
const searchResults = ref<Array<{
  type: string;
  id: string;
  title: string;
  subtitle: string;
  route: string;
}>>([]);
const selected = ref<{ type: ObjectType; id: string; title: string } | null>(null);
const linkType = ref<ObjectLink["link_type"]>("relates_to");
const note = ref("");

const supportedTypes = new Set<ObjectType>([
  "task",
  "workspace_file",
  "archive_record",
  "journal",
  "period_report",
  "knowledge",
  "contact",
  "topic",
]);
const searchTypeAliases: Record<string, ObjectType> = {
  file: "workspace_file",
  archive: "archive_record",
  report: "period_report",
};

async function load() {
  if (!props.objectId) return;
  loading.value = true;
  try {
    [links.value, activity.value] = await Promise.all([
      api.get<ObjectLink[]>(`/objects/${props.objectType}/${props.objectId}/links`),
      api.get<ActivityEvent[]>(`/objects/${props.objectType}/${props.objectId}/activity?limit=100`),
    ]);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "相关内容加载失败");
  } finally {
    loading.value = false;
  }
}

async function search() {
  if (!searchQuery.value.trim()) return;
  searchLoading.value = true;
  try {
    const result = await api.get<{ items: typeof searchResults.value }>(
      `/global-search?q=${encodeURIComponent(searchQuery.value.trim())}&limit=30`,
    );
    searchResults.value = result.items.filter((item) => {
      const type = searchTypeAliases[item.type] || item.type;
      return supportedTypes.has(type as ObjectType)
        && !(type === props.objectType && item.id === props.objectId);
    });
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "搜索相关内容失败");
  } finally {
    searchLoading.value = false;
  }
}

function choose(item: { type: string; id: string; title: string }) {
  const type = (searchTypeAliases[item.type] || item.type) as ObjectType;
  selected.value = { type, id: item.id, title: item.title };
}

async function createLink() {
  if (!selected.value) {
    Message.warning("请先选择要关联的内容");
    return;
  }
  try {
    await api.post(
      `/objects/${props.objectType}/${props.objectId}/links`,
      {
        target_type: selected.value.type,
        target_id: selected.value.id,
        link_type: linkType.value,
        note: note.value.trim(),
      },
      { "Idempotency-Key": crypto.randomUUID() },
    );
    visible.value = false;
    selected.value = null;
    searchQuery.value = "";
    searchResults.value = [];
    note.value = "";
    Message.success("相关内容已建立双向关联");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "建立关联失败");
  }
}

function removeLink(item: ObjectLink) {
  Modal.warning({
    title: "移除业务关联",
    content: `确认移除与“${item.title}”的关联吗？原业务数据不会删除。`,
    hideCancel: false,
    onOk: async () => {
      try {
        await api.delete(
          `/objects/${props.objectType}/${props.objectId}/links/${item.id}`,
          { "If-Match": String(item.version) },
        );
        Message.success("关联已移除");
        await load();
      } catch (error) {
        Message.error(error instanceof Error ? error.message : "移除关联失败");
      }
    },
  });
}

watch(() => [props.objectType, props.objectId], load);
onMounted(load);
</script>

<template>
  <a-spin :loading="loading" class="context-panel">
    <section>
      <header>
        <div><span>双向关联</span><h3>相关内容</h3></div>
        <a-space>
          <a-button size="small" @click="load"><template #icon><IconRefresh /></template></a-button>
          <a-button size="small" type="primary" @click="visible = true"><template #icon><IconPlus /></template>建立关联</a-button>
        </a-space>
      </header>
      <div v-if="links.length" class="link-list">
        <RouterLink v-for="item in links" :key="item.id" :to="item.route || '#'">
          <IconLink />
          <div><strong>{{ item.title }}</strong><small>{{ zhLabel(item.link_type) }} · {{ item.direction === "incoming" ? "引用了当前内容" : "当前内容引用" }}</small></div>
          <a-button size="mini" type="text" @click.prevent="removeLink(item)">移除</a-button>
        </RouterLink>
      </div>
      <div v-else class="empty-state">尚未关联任务、文件、档案、日志、报告或专题。</div>
    </section>

    <section>
      <header><div><span>追加式记录</span><h3>活动时间线</h3></div></header>
      <div v-if="activity.length" class="activity-list">
        <article v-for="item in activity" :key="item.id">
          <time>{{ formatServerTime(item.happened_at, "MM-DD HH:mm") }}</time>
          <i></i>
          <div><strong>{{ item.actor_name }} · {{ item.event_label }}</strong><small>{{ item.actor_role }} · 系统记录 {{ formatServerTime(item.recorded_at, "HH:mm:ss") }}</small></div>
        </article>
      </div>
      <div v-else class="empty-state">后续操作会按“谁、何时、做了什么”记录在这里。</div>
    </section>

    <a-modal v-model:visible="visible" title="建立双向业务关联" width="680px" @ok="createLink">
      <a-form :model="{ searchQuery, selected, linkType, note }" layout="vertical">
        <a-form-item label="搜索业务内容">
          <a-input-search v-model="searchQuery" :loading="searchLoading" placeholder="输入任务、文件、档案、报告或联系人名称" search-button @search="search" />
        </a-form-item>
        <div v-if="searchResults.length" class="search-results">
          <button v-for="item in searchResults" :key="`${item.type}-${item.id}`" type="button" :class="{ selected: selected?.id === item.id }" @click="choose(item)">
            <strong>{{ item.title }}</strong><small>{{ zhLabel(searchTypeAliases[item.type] || item.type, "业务内容") }} · {{ item.subtitle }}</small>
          </button>
        </div>
        <a-alert v-if="selected" type="success">已选择：{{ selected.title }}</a-alert>
        <a-form-item label="关联关系">
          <a-select v-model="linkType">
            <a-option value="relates_to">相关内容</a-option>
            <a-option value="supports">支撑材料</a-option>
            <a-option value="produced_by">产出来源</a-option>
            <a-option value="belongs_to">归属专题</a-option>
            <a-option value="mentions">提及</a-option>
            <a-option value="supersedes">替代版本</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="说明"><a-textarea v-model="note" :max-length="500" show-word-limit /></a-form-item>
      </a-form>
    </a-modal>
  </a-spin>
</template>

<style scoped>
.context-panel { display: block; }
.context-panel > section { margin-bottom: 24px; }
header { display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 12px; }
header span { color: var(--cinnabar); font-size: 10px; letter-spacing: .15em; }
header h3 { margin: 4px 0 0; font-size: 17px; }
.link-list { border-top: 1px solid var(--line); }
.link-list > a { display: grid; grid-template-columns: 22px 1fr auto; gap: 10px; align-items: center; min-height: 62px; border-bottom: 1px solid var(--line-light); }
.link-list svg { color: var(--cinnabar); }
.link-list strong,.link-list small { display: block; }
.link-list small { margin-top: 4px; color: var(--muted); font-size: 10px; }
.activity-list article { display: grid; grid-template-columns: 90px 12px 1fr; gap: 10px; min-height: 58px; }
.activity-list time { color: var(--muted); font: 11px Georgia,serif; text-align: right; }
.activity-list i { position: relative; border-left: 1px solid var(--line); }
.activity-list i::before { position: absolute; top: 2px; left: -4px; width: 7px; height: 7px; content: ""; background: var(--paper); border: 1px solid var(--cinnabar); border-radius: 50%; }
.activity-list strong,.activity-list small { display: block; }
.activity-list small { margin-top: 5px; color: var(--muted); font-size: 10px; }
.search-results { max-height: 230px; margin: -8px 0 14px; overflow: auto; border: 1px solid var(--line); }
.search-results button { display: block; width: 100%; padding: 10px 12px; text-align: left; background: transparent; border: 0; border-bottom: 1px solid var(--line-light); cursor: pointer; }
.search-results button:hover,.search-results button.selected { color: var(--cinnabar); background: rgba(180,35,24,.06); }
.search-results strong,.search-results small { display: block; }
.search-results small { margin-top: 3px; color: var(--muted); font-size: 10px; }
</style>
