import { createRouter, createWebHistory } from "vue-router";
import { api } from "./api";
import { useSessionStore } from "./stores/session";
import type { DeviceUpdateGate } from "./types";

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: "/login",
      component: () => import("./views/LoginView.vue"),
      meta: { public: true },
    },
    {
      path: "/required-update",
      component: () => import("./views/RequiredUpdateView.vue"),
      meta: { public: true, updateGate: true },
    },
    {
      path: "/forbidden",
      component: () => import("./views/ForbiddenView.vue"),
    },
    { path: "/", component: () => import("./views/TodayView.vue") },
    { path: "/memos", component: () => import("./views/MemoView.vue") },
    { path: "/getting-started", redirect: { path: "/help", query: { section: "setup" } } },
    { path: "/workbench", redirect: "/" },
    { path: "/dashboard", redirect: "/" },
    { path: "/tasks", component: () => import("./views/TasksView.vue") },
    { path: "/my-work", component: () => import("./views/MyWorkView.vue") },
    { path: "/notifications", component: () => import("./views/NotificationsView.vue") },
    {
      path: "/tasks/:id",
      component: () => import("./views/TaskDetailView.vue"),
    },
    { path: "/inbox", component: () => import("./views/InboxView.vue") },
    { path: "/calendar", component: () => import("./views/CalendarView.vue") },
    {
      path: "/party-development",
      component: () => import("./views/PartyDevelopmentView.vue"),
    },
    {
      path: "/reports",
      component: () => import("./views/ReportsView.vue"),
    },
    {
      path: "/workspace",
      component: () => import("./views/WorkspaceView.vue"),
    },
    {
      path: "/archives",
      component: () => import("./views/ArchivesView.vue"),
    },
    {
      path: "/journal",
      component: () => import("./views/JournalView.vue"),
    },
    {
      path: "/assistant",
      component: () => import("./views/AssistantView.vue"),
    },
    {
      path: "/templates",
      component: () => import("./views/TemplatesView.vue"),
      meta: { capability: "admin.access" },
    },
    {
      path: "/party-development-settings",
      component: () => import("./views/PartyDevelopmentSettingsView.vue"),
      meta: { capability: "admin.access" },
    },
    {
      path: "/inspection",
      component: () => import("./views/InspectionView.vue"),
    },
    {
      path: "/knowledge",
      component: () => import("./views/KnowledgeView.vue"),
    },
    { path: "/settings", redirect: "/settings/diagnostics" },
    {
      path: "/settings/diagnostics",
      component: () => import("./views/SettingsView.vue"),
      props: { initialTab: "diagnostics" },
      meta: { capability: "admin.access" },
    },
    {
      path: "/settings/updates",
      component: () => import("./views/SettingsView.vue"),
      props: { initialTab: "updates" },
      meta: { capability: "updates.manage" },
    },
    {
      path: "/settings/backups",
      component: () => import("./views/SettingsView.vue"),
      props: { initialTab: "backup" },
      meta: { capability: "backups.manage" },
    },
    { path: "/fleet", redirect: "/fleet/devices" },
    {
      path: "/fleet/devices",
      component: () => import("./views/FleetView.vue"),
      props: { initialSection: "devices" },
    },
    {
      path: "/fleet/inbox",
      component: () => import("./views/FleetView.vue"),
      props: { initialSection: "inbox" },
    },
    {
      path: "/fleet/transfers",
      component: () => import("./views/FleetView.vue"),
      props: { initialSection: "transfers" },
    },
    {
      path: "/fleet/grants",
      component: () => import("./views/FleetView.vue"),
      props: { initialSection: "grants" },
      meta: { capability: "fleet.manage" },
    },
    {
      path: "/efficiency",
      redirect: "/topics",
    },
    {
      path: "/topics",
      component: () => import("./views/EfficiencyView.vue"),
      props: { initialTab: "topics" },
    },
    {
      path: "/automation",
      component: () => import("./views/EfficiencyView.vue"),
      props: { initialTab: "automation" },
      meta: { capability: "admin.access" },
    },
    {
      path: "/document-comparisons",
      component: () => import("./views/EfficiencyView.vue"),
      props: { initialTab: "documents" },
    },
    {
      path: "/report-designer",
      component: () => import("./views/EfficiencyView.vue"),
      props: { initialTab: "templates" },
      meta: { capability: "admin.access" },
    },
    {
      path: "/ai-approvals",
      component: () => import("./views/EfficiencyView.vue"),
      props: { initialTab: "ai" },
      meta: { capability: "ai.manage" },
    },
    {
      path: "/transfers",
      redirect: "/fleet/transfers",
    },
    {
      path: "/help",
      component: () => import("./views/HelpView.vue"),
    },
  ],
});

router.beforeEach(async (to) => {
  if (to.meta.updateGate) return true;
  try {
    const gate = await api.get<DeviceUpdateGate>("/device/update-gate");
    if (gate.identified && (!gate.access_allowed || gate.required)) {
      return {
        path: "/required-update",
        query: { redirect: to.fullPath },
      };
    }
  } catch {
    // 主机重启或首次桥接时由目标页继续显示明确诊断，不能把短暂断线变成白屏。
  }
  const session = useSessionStore();
  if (to.meta.public) return true;
  const user = await session.ensure();
  if (!user) return { path: "/login", query: { redirect: to.fullPath } };
  const capability = typeof to.meta.capability === "string" ? to.meta.capability : "";
  if (capability && !session.runtimeContext?.capabilities.includes(capability)) {
    return { path: "/forbidden", query: { from: to.fullPath } };
  }
  return true;
});

export default router;
