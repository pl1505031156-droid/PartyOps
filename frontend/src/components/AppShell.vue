<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  IconArchive,
  IconApps,
  IconBook,
  IconCalendar,
  IconCloudDownload,
  IconCommand,
  IconDown,
  IconEdit,
  IconFile,
  IconFolder,
  IconHome,
  IconHistory,
  IconImport,
  IconNotification,
  IconPlus,
  IconPoweroff,
  IconRight,
  IconSettings,
  IconRobot,
  IconUserGroup,
} from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { useSessionStore } from "../stores/session";
import QuickCreateDrawer from "./QuickCreateDrawer.vue";
import OrientalArtLayer from "./OrientalArtLayer.vue";
import { api } from "../api";
import type { NotificationItem, ReminderPreference } from "../types";
import { localizeEmbeddedCodes, zhLabel } from "../utils/labels";
import { orientalDateLabel } from "../utils/lunar";
import {
  domainForPath,
  expandedDomainsForPath,
  navigationDomains,
  type NavigationDomainKey,
} from "../navigation";
import { sceneConfigForPath, shouldShowOrientalArt } from "../theme/oriental";

const route = useRoute();
const router = useRouter();
const session = useSessionStore();
const drawerVisible = ref(false);
const commandVisible = ref(false);
const commandQuery = ref("");
const commandResults = ref<Array<{
  type: string;
  id: string;
  title: string;
  subtitle: string;
  route: string;
}>>([]);
const commandLoading = ref(false);
const connectionState = ref<"live" | "polling" | "offline">("live");
const reminderPreference = ref<ReminderPreference | null>(null);
const recentNotifications = ref<NotificationItem[]>([]);
const pendingUpdateVersion = ref("");
const unreadNotifications = computed(() => recentNotifications.value.filter((item) => !item.read_at).length);
const visibleNavigationDomains = computed(() => {
  const capabilities = new Set(session.runtimeContext?.capabilities || []);
  return navigationDomains
    .map((domain) => ({
      ...domain,
      items: domain.items.filter((item) => !item.capability || capabilities.has(item.capability)),
    }))
    .filter((domain) => domain.items.length > 0);
});
let source: EventSource | null = null;
let polling: number | null = null;
let commandTimer: number | null = null;
const NAVIGATION_STORAGE_KEY = "partyops.navigation.expanded-domains";
const ONLINE_UPDATE_TASK_KEY = "partyops.pending-online-update";
const ONLINE_UPDATE_LAST_CHECK_KEY = "partyops.online-update-last-check";
const ONLINE_UPDATE_RETRY_AFTER_KEY = "partyops.online-update-retry-after";

function refreshPendingUpdateNotice() {
  const raw = window.localStorage.getItem(ONLINE_UPDATE_TASK_KEY);
  if (!raw) {
    pendingUpdateVersion.value = "";
    return;
  }
  try {
    const pending = JSON.parse(raw) as { version?: unknown };
    pendingUpdateVersion.value = typeof pending.version === "string" ? pending.version : "";
  } catch {
    window.localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
    pendingUpdateVersion.value = "";
  }
}

function loadExpandedDomains(): NavigationDomainKey[] {
  const storedValue = typeof window === "undefined"
    ? null
    : window.localStorage.getItem(NAVIGATION_STORAGE_KEY);
  return expandedDomainsForPath(route.path, storedValue);
}

const expandedDomainKeys = ref<NavigationDomainKey[]>(loadExpandedDomains());

const iconMap = {
  home: IconHome,
  memo: IconEdit,
  task: IconFile,
  calendar: IconCalendar,
  inbox: IconImport,
  report: IconCalendar,
  journal: IconHistory,
  topic: IconBook,
  folder: IconFolder,
  archive: IconArchive,
  book: IconBook,
  compare: IconCommand,
  device: IconCloudDownload,
  transfer: IconCloudDownload,
  template: IconCalendar,
  automation: IconCommand,
  ai: IconRobot,
  settings: IconSettings,
  help: IconBook,
};

const domainIconMap = {
  today: IconHome,
  work: IconApps,
  materials: IconFolder,
  collaboration: IconUserGroup,
  management: IconSettings,
};

const quickCommands = [
  { id: "new-task", title: "快速新建事项", subtitle: "打开 30 秒建档抽屉", route: "", type: "command" },
  { id: "new-memo", title: "快速新建备忘", subtitle: "仅保存在当前电脑，不进入协同", route: "/memos", type: "command" },
  { id: "calendar", title: "打开工作日历", subtitle: "查看本周安排和年度节点", route: "/calendar", type: "command" },
  { id: "inbox", title: "解析通知或文件", subtitle: "进入快速收件箱", route: "/inbox", type: "command" },
  { id: "workspace", title: "搜索原始文件", subtitle: "进入综合文件中心", route: "/workspace", type: "command" },
  { id: "archives", title: "查询重要档案", subtitle: "人事调动、年度考核和扫描件", route: "/archives", type: "command" },
  { id: "reports", title: "建立周期报告", subtitle: "周、月、季度和年度汇总", route: "/reports", type: "command" },
  { id: "topics", title: "打开专题工作空间", subtitle: "集中查看专项任务、文件、日志和联系人", route: "/topics", type: "command" },
  { id: "help", title: "打开帮助与上手", subtitle: "查看事实检查、操作教程和防错清单", route: "/help", type: "command" },
];

const filteredCommands = computed(() => {
  const query = commandQuery.value.trim().toLowerCase();
  if (!query) return quickCommands;
  return quickCommands.filter((item) => `${item.title}${item.subtitle}`.toLowerCase().includes(query));
});

const activePath = computed(() => {
  if (route.path.startsWith("/tasks")) return "/tasks";
  return route.path;
});

const activeDomainKey = computed(() => domainForPath(route.path).key);
const orientalDate = computed(() => orientalDateLabel());
const artConfig = computed(() => sceneConfigForPath(route.path));
const showOrientalArt = computed(() => shouldShowOrientalArt(route.path));

function iconFor(key: keyof typeof iconMap) {
  return iconMap[key];
}

function domainIconFor(key: NavigationDomainKey) {
  return domainIconMap[key];
}

function isDomainExpanded(key: NavigationDomainKey) {
  return expandedDomainKeys.value.includes(key);
}

function persistExpandedDomains() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    NAVIGATION_STORAGE_KEY,
    JSON.stringify(expandedDomainKeys.value),
  );
}

function toggleDomain(key: NavigationDomainKey) {
  expandedDomainKeys.value = isDomainExpanded(key)
    ? expandedDomainKeys.value.filter((item) => item !== key)
    : [...expandedDomainKeys.value, key];
  persistExpandedDomains();
}

function isNavigationActive(path: string) {
  if (path === "/") return route.path === "/";
  if (path === "/tasks") return route.path.startsWith("/tasks");
  return activePath.value === path;
}

function timeToMinutes(value: string) {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function isQuietTime() {
  const preference = reminderPreference.value;
  if (!preference) return false;
  const start = timeToMinutes(preference.quiet_start);
  const end = timeToMinutes(preference.quiet_end);
  if (start === end) return false;
  const now = new Date();
  const current = now.getHours() * 60 + now.getMinutes();
  return start < end
    ? current >= start && current < end
    : current >= start || current < end;
}

async function loadReminderPreference() {
  try {
    reminderPreference.value = await api.get<ReminderPreference>("/reminders/preferences");
  } catch {
    // 服务端仍会保存未读提醒；偏好读取失败时不阻断主界面。
  }
}

async function loadNotifications() {
  try {
    recentNotifications.value = await api.get<NotificationItem[]>("/notifications?limit=8");
  } catch {
    // 通知面板不阻断业务页面，下一次实时事件或刷新会重试。
  }
}

async function prepareProfessionalUpdate() {
  if (session.user?.role !== "admin") return;
  const retryAfter = Number(window.localStorage.getItem(ONLINE_UPDATE_RETRY_AFTER_KEY) || 0);
  if (retryAfter && Date.now() < retryAfter) return;
  const lastCheck = Number(window.localStorage.getItem(ONLINE_UPDATE_LAST_CHECK_KEY) || 0);
  if (lastCheck && Date.now() - lastCheck < 24 * 60 * 60 * 1000) return;
  try {
    const release = await api.get<{ available: boolean; version: string }>("/admin/updates/online");
    if (!release.available) {
      window.localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
      window.localStorage.removeItem(ONLINE_UPDATE_RETRY_AFTER_KEY);
      window.localStorage.setItem(ONLINE_UPDATE_LAST_CHECK_KEY, Date.now().toString());
      pendingUpdateVersion.value = "";
      return;
    }
    let pendingVersion = "";
    const rawPending = window.localStorage.getItem(ONLINE_UPDATE_TASK_KEY);
    if (rawPending) {
      try {
        const pending = JSON.parse(rawPending) as { version?: unknown };
        pendingVersion = typeof pending.version === "string" ? pending.version : "";
      } catch {
        window.localStorage.removeItem(ONLINE_UPDATE_TASK_KEY);
      }
    }
    // 服务端 prepare 是按“版本 + SHA-256”幂等的：即使上次下载在断网或
    // 浏览器关闭时中断，也应允许它恢复；更不能让旧版本的本地标记永久
    // 阻挡下一次专业更新。
    const prepared = await api.post<{ id: string; version: string }>("/admin/updates/online/prepare");
    window.localStorage.setItem(
      ONLINE_UPDATE_TASK_KEY,
      JSON.stringify({
        packageId: prepared.id,
        version: prepared.version,
        startedAt: new Date().toISOString(),
      }),
    );
    window.localStorage.removeItem(ONLINE_UPDATE_RETRY_AFTER_KEY);
    window.localStorage.setItem(ONLINE_UPDATE_LAST_CHECK_KEY, Date.now().toString());
    pendingUpdateVersion.value = prepared.version;
    if (pendingVersion !== prepared.version) {
      Message.info(`发现 ${release.version}，已在后台下载适合本机的更新；安装前会再次请您确认`);
    }
  } catch {
    // 离线办公是正常场景：失败不阻断业务，但只短暂退避，不能静默压制 24 小时。
    window.localStorage.setItem(
      ONLINE_UPDATE_RETRY_AFTER_KEY,
      String(Date.now() + 15 * 60 * 1000),
    );
  }
}

async function openNotification(item: NotificationItem) {
  if (!item.read_at) {
    await api.post(`/notifications/${item.id}/read`);
    item.read_at = new Date().toISOString();
  }
  if (item.entity_type === "task" && item.entity_id) await router.push(`/tasks/${item.entity_id}`);
  else if (item.entity_type === "transfer") await router.push("/fleet/inbox");
  else if (item.entity_type === "workspace_root") await router.push("/fleet/grants");
  else await router.push("/notifications");
}

function connectEvents() {
  source = new EventSource("/api/v1/events/stream", { withCredentials: true });
  source.onopen = () => {
    connectionState.value = "live";
    if (polling) window.clearInterval(polling);
    polling = null;
  };
  source.onmessage = () => window.dispatchEvent(new CustomEvent("partyops:refresh"));
  [
    "task.created",
    "task.updated",
    "task.status_changed",
    "comment.created",
    "attachment.added",
    "backup.completed",
    "period_report.created",
    "period_report.updated",
    "workspace.scan_completed",
    "workspace.file_updated",
    "archive.record_created",
    "archive.record_updated",
    "archive.attachment_added",
    "work_journal.created",
    "ai.draft_created",
  ].forEach((name) => source?.addEventListener(name, () => window.dispatchEvent(new CustomEvent("partyops:refresh"))));
  source.addEventListener("notification.created", async () => {
    window.dispatchEvent(new CustomEvent("partyops:refresh"));
    await loadNotifications();
    if (
      !reminderPreference.value?.enabled
      || !reminderPreference.value.desktop_enabled
      || isQuietTime()
      || !("Notification" in window)
      || Notification.permission !== "granted"
    ) return;
    try {
      const items = await api.get<NotificationItem[]>("/notifications?unread_only=true&limit=1");
      const item = items[0];
      if (!item) return;
      const notice = new Notification(item.title, { body: item.body, tag: item.id });
      notice.onclick = () => {
        window.focus();
        if (item.entity_type === "task" && item.entity_id) router.push(`/tasks/${item.entity_id}`);
      };
    } catch {
      // 页面中的持久化提醒仍然可用，桌面通知失败不影响业务。
    }
  });
  source.onerror = () => {
    connectionState.value = navigator.onLine ? "polling" : "offline";
    if (!polling) {
      polling = window.setInterval(
        () => window.dispatchEvent(new CustomEvent("partyops:refresh")),
        10_000,
      );
    }
  };
}

async function logout() {
  await session.logout();
  Message.success("已安全退出");
  await router.push("/login");
}

function openCommandCenter() {
  commandVisible.value = true;
}

async function searchCommandCenter(query: string) {
  if (!query.trim()) {
    commandResults.value = [];
    return;
  }
  commandLoading.value = true;
  try {
    const result = await api.get<{ items: typeof commandResults.value }>(
      `/global-search?q=${encodeURIComponent(query.trim())}&limit=30`,
    );
    commandResults.value = result.items;
  } catch {
    commandResults.value = [];
  } finally {
    commandLoading.value = false;
  }
}

function chooseCommand(item: { id: string; route: string }) {
  commandVisible.value = false;
  commandQuery.value = "";
  if (item.id === "new-task") {
    drawerVisible.value = true;
    return;
  }
  if (item.id === "new-memo") {
    router.push({ path: "/memos", query: { new: String(Date.now()) } });
    return;
  }
  if (item.route) router.push(item.route);
}

function globalKeydown(event: KeyboardEvent) {
  if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "m") {
    event.preventDefault();
    router.push({ path: "/memos", query: { new: String(Date.now()) } });
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openCommandCenter();
  }
}

watch(commandQuery, (value) => {
  if (commandTimer) window.clearTimeout(commandTimer);
  commandTimer = window.setTimeout(() => searchCommandCenter(value), 180);
});

watch(
  () => route.path,
  (path) => {
    const key = domainForPath(path).key;
    if (!isDomainExpanded(key)) {
      expandedDomainKeys.value = [...expandedDomainKeys.value, key];
      persistExpandedDomains();
    }
  },
);

onMounted(async () => {
  refreshPendingUpdateNotice();
  await Promise.all([loadReminderPreference(), loadNotifications()]);
  void prepareProfessionalUpdate();
  connectEvents();
  window.addEventListener("keydown", globalKeydown);
  window.addEventListener("partyops:command", openCommandCenter);
  window.addEventListener("partyops:update-task-changed", refreshPendingUpdateNotice);
});
onBeforeUnmount(() => {
  source?.close();
  if (polling) window.clearInterval(polling);
  if (commandTimer) window.clearTimeout(commandTimer);
  window.removeEventListener("keydown", globalKeydown);
  window.removeEventListener("partyops:command", openCommandCenter);
  window.removeEventListener("partyops:update-task-changed", refreshPendingUpdateNotice);
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand-lockup" to="/">
        <span class="brand-cn"><b>党建</b>智办</span>
        <span class="brand-en">PartyOps</span>
      </RouterLink>
      <nav class="nav-list" aria-label="主导航">
        <section
          v-for="domain in visibleNavigationDomains"
          :key="domain.key"
          class="nav-group"
          :class="{ current: activeDomainKey === domain.key, expanded: isDomainExpanded(domain.key) }"
        >
          <button
            type="button"
            class="domain-toggle"
            :aria-expanded="isDomainExpanded(domain.key)"
            :aria-controls="`navigation-domain-${domain.key}`"
            @click="toggleDomain(domain.key)"
          >
            <span class="domain-identity">
              <component :is="domainIconFor(domain.key)" :size="18" />
              <strong>{{ domain.label }}</strong>
            </span>
            <span class="domain-meta">
              <small>{{ domain.items.length }} 项</small>
              <IconDown v-if="isDomainExpanded(domain.key)" :size="13" />
              <IconRight v-else :size="13" />
            </span>
          </button>
          <div
            v-if="isDomainExpanded(domain.key)"
            :id="`navigation-domain-${domain.key}`"
            class="domain-items"
          >
            <RouterLink
              v-for="item in domain.items"
              :key="item.path"
              :to="item.path"
              class="nav-item"
              :class="{ active: isNavigationActive(item.path) }"
            >
              <component :is="iconFor(item.icon)" :size="15" />
              <span>{{ item.label }}</span>
            </RouterLink>
          </div>
        </section>
      </nav>
      <div class="sidebar-foot">
        <div class="connection">
          <span class="status-dot" :class="connectionState"></span>
          <span>{{ connectionState === "live" ? "局域网实时协同" : connectionState === "polling" ? "轮询连接中" : "主机暂不可达" }}</span>
        </div>
        <div class="user-block">
          <div>
            <strong>{{ session.user?.display_name }}</strong>
            <small>{{ session.user?.role === "admin" ? "管理员" : "协同人员" }}</small>
          </div>
          <a-button type="text" size="small" aria-label="退出登录" @click="logout">
            <template #icon><IconPoweroff /></template>
          </a-button>
        </div>
      </div>
    </aside>
    <main
      class="main-area"
      :class="{ 'oriental-route': showOrientalArt }"
      :data-scene="artConfig.scene"
    >
      <OrientalArtLayer
        v-if="showOrientalArt"
        :config="artConfig"
        :solar-term="orientalDate.solarTerm"
        :active-solar-term="orientalDate.activeSolarTerm"
      />
      <header class="topbar">
        <div class="crumb">
          <span>基层党建工作闭环协同系统</span>
          <small>一个事项 · 一条责任链 · 一份最终档案 · {{ orientalDate.compact }}</small>
        </div>
        <a-space>
          <a-button
            v-if="pendingUpdateVersion"
            status="warning"
            aria-label="打开系统更新"
            @click="router.push('/settings/updates')"
          >
            <template #icon><IconCloudDownload /></template>
            {{ pendingUpdateVersion }} 更新准备中
          </a-button>
          <a-popover trigger="click" position="br">
            <a-badge :count="unreadNotifications" :max-count="99" :dot="false">
              <a-button aria-label="打开最近通知"><template #icon><IconNotification /></template>通知</a-button>
            </a-badge>
            <template #content>
              <div class="recent-notifications">
                <header><b>最近通知</b><RouterLink to="/notifications">查看全部</RouterLink></header>
                <button v-for="item in recentNotifications" :key="item.id" type="button" :class="{ unread: !item.read_at }" @click="openNotification(item)">
                  <span>{{ item.title }}</span><small>{{ item.body || zhLabel(item.notification_type, '协同通知') }}</small>
                </button>
                <p v-if="!recentNotifications.length">暂无通知</p>
              </div>
            </template>
          </a-popover>
          <a-button aria-label="打开 AI 工作助手" @click="router.push('/assistant')"><template #icon><IconRobot /></template>AI 助手</a-button>
          <a-button aria-label="打开 Ctrl+K 全局指令中心" @click="openCommandCenter"><template #icon><IconCommand /></template>Ctrl+K 全局指令</a-button>
          <a-button type="primary" aria-label="快速新建事项" @click="drawerVisible = true">
            <template #icon><IconPlus /></template>
            快速新建
          </a-button>
        </a-space>
      </header>
      <slot />
    </main>
    <QuickCreateDrawer v-model:visible="drawerVisible" @created="router.push(`/tasks/${$event.id}`)" />
    <a-modal v-model:visible="commandVisible" title="全局搜索与指令" :footer="false" width="680px">
      <a-input
        v-model="commandQuery"
        size="large"
        allow-clear
        autofocus
        placeholder="搜索任务、文件、联系人、日志、报告、知识或设备……"
      >
        <template #prefix><IconCommand /></template>
      </a-input>
      <div class="command-section">
        <label>常用操作</label>
        <button v-for="item in filteredCommands" :key="item.id" type="button" @click="chooseCommand(item)">
          <span><strong>{{ item.title }}</strong><small>{{ item.subtitle }}</small></span>
          <kbd>↵</kbd>
        </button>
      </div>
      <div v-if="commandQuery.trim()" class="command-section">
        <label>全局结果</label>
        <a-spin :loading="commandLoading" style="width: 100%">
          <button v-for="item in commandResults" :key="`${item.type}-${item.id}`" type="button" @click="chooseCommand(item)">
            <span><strong>{{ item.title }}</strong><small>{{ zhLabel(item.type, "业务结果") }} · {{ localizeEmbeddedCodes(item.subtitle) }}</small></span>
            <kbd>打开</kbd>
          </button>
          <p v-if="!commandLoading && !commandResults.length" class="command-empty">没有找到匹配内容。</p>
        </a-spin>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  background-color: var(--ivory);
  background-image: url("../assets/paper-texture.png");
  background-size: 720px;
}

.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 20;
  display: flex;
  width: 252px;
  flex-direction: column;
  background: rgba(247, 241, 231, 0.94);
  border-right: 1px solid var(--line);
  backdrop-filter: blur(10px);
}

.brand-lockup {
  display: flex;
  height: 84px;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  width: 100%;
  padding: 0 16px;
  text-align: center;
  border-bottom: 1px solid var(--line);
}

.brand-cn {
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", SimSun, serif;
  font-size: 26px;
  letter-spacing: 0.08em;
}

.brand-cn b {
  color: var(--cinnabar);
  font-weight: 600;
}

.brand-en {
  margin-top: 4px;
  color: var(--cinnabar);
  font-family: Georgia, serif;
  font-size: 13px;
  letter-spacing: 0.08em;
}

.command-section {
  margin-top: 18px;
  border-top: 1px solid var(--line);
}

.command-section > label {
  display: block;
  padding: 12px 4px 6px;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.14em;
}

.command-section button {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  padding: 10px 8px;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--line-light);
  cursor: pointer;
}

.command-section button:hover {
  color: var(--cinnabar);
  background: rgba(180, 35, 24, 0.05);
}

.command-section strong,
.command-section small {
  display: block;
}

.command-section small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
}

.command-section kbd {
  padding: 2px 6px;
  color: var(--muted);
  font: 10px Georgia, serif;
  background: rgba(0, 0, 0, 0.035);
  border: 1px solid var(--line);
}

.command-empty {
  padding: 24px 8px;
  color: var(--muted);
  text-align: center;
}

.recent-notifications { width: 340px; max-height: 430px; overflow-y: auto; }
.recent-notifications header { display: flex; justify-content: space-between; align-items: center; padding: 7px 8px 10px; border-bottom: 1px solid var(--line); }
.recent-notifications header a { color: var(--cinnabar); font-size: 11px; }
.recent-notifications button { display: block; width: 100%; padding: 10px 8px; text-align: left; color: var(--ink); background: transparent; border: 0; border-bottom: 1px solid var(--line-light); cursor: pointer; }
.recent-notifications button.unread { background: rgba(180,35,24,.06); box-shadow: inset 2px 0 0 var(--cinnabar); }
.recent-notifications button:hover { background: rgba(180,35,24,.09); }
.recent-notifications span, .recent-notifications small { display: block; }
.recent-notifications small { margin-top: 4px; color: var(--muted); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.recent-notifications p { padding: 24px; color: var(--muted); text-align: center; }

.nav-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0;
  padding: 8px 12px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(180, 35, 24, 0.24) transparent;
}

.nav-group {
  padding: 2px 0 7px;
  border-top: 1px solid rgba(217, 208, 195, 0.72);
}

.nav-group:first-child {
  border-top: 0;
}

.domain-toggle {
  display: flex;
  width: 100%;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  color: #4d4943;
  background: transparent;
  border: 0;
  border-left: 3px solid transparent;
  cursor: pointer;
  transition: color 140ms ease, background 140ms ease;
}

.domain-toggle:hover {
  color: var(--charcoal);
  background: rgba(180, 35, 24, 0.045);
}

.nav-group.current > .domain-toggle {
  color: var(--cinnabar);
  border-left-color: var(--cinnabar);
}

.domain-identity,
.domain-meta {
  display: inline-flex;
  align-items: center;
}

.domain-identity {
  gap: 11px;
}

.domain-identity strong {
  font-family: var(--serif);
  font-size: 16px;
  font-weight: 500;
  letter-spacing: 0.06em;
}

.domain-meta {
  gap: 8px;
  color: var(--muted);
}

.domain-meta small {
  font-size: 11px;
}

.domain-items {
  display: grid;
  gap: 2px;
  padding: 2px 0 3px;
}

.nav-item {
  display: flex;
  min-height: 36px;
  align-items: center;
  gap: 9px;
  padding: 0 12px 0 32px;
  color: #615b53;
  font-size: 13px;
  border-left: 3px solid transparent;
  transition: 140ms ease;
}

.nav-item:hover {
  color: var(--charcoal);
  background: rgba(180, 35, 24, 0.045);
}

.nav-item.active {
  color: var(--cinnabar);
  font-weight: 600;
  background: rgba(180, 35, 24, 0.07);
  border-left-color: var(--cinnabar);
}

.sidebar-foot {
  padding: 9px 18px 12px;
  border-top: 1px solid var(--line);
}

.connection {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 12px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green);
}

.status-dot.polling {
  background: var(--amber);
}

.status-dot.offline {
  background: var(--danger);
}

.user-block {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.user-block strong,
.user-block small {
  display: block;
}

.user-block small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}

.main-area {
  position: relative;
  isolation: isolate;
  width: calc(100% - 252px);
  margin-left: 252px;
}

.main-area > :not(.topbar):not(.oriental-art-layer) {
  position: relative;
  z-index: 1;
}

:global(html[data-reduce-motion="true"] *) {
  transition-duration: 0.01ms !important;
  animation-duration: 0.01ms !important;
  animation-iteration-count: 1 !important;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  height: 84px;
  align-items: center;
  justify-content: space-between;
  padding: 0 36px;
  background: rgba(247, 241, 231, 0.9);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(12px);
}

.crumb span,
.crumb small {
  display: block;
}

.crumb span {
  color: #5f594f;
  font-family: var(--serif);
  font-size: 13px;
  font-weight: 400;
  letter-spacing: 0.06em;
}

.crumb small {
  margin-top: 3px;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.04em;
}

@media (max-width: 1380px) {
  .topbar {
    padding: 0 24px;
  }

  .crumb small {
    display: none;
  }

  .topbar :deep(.arco-btn) {
    padding-right: 10px;
    padding-left: 10px;
  }
}

@media (max-height: 820px) {
  .brand-lockup {
    height: 72px;
  }

  .nav-list {
    padding-top: 3px;
    padding-bottom: 3px;
  }

  .nav-group {
    padding-top: 0;
    padding-bottom: 4px;
  }

  .domain-toggle {
    min-height: 42px;
  }

  .nav-item {
    min-height: 32px;
  }
}
</style>
