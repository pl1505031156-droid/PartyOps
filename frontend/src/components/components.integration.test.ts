import { flushPromises, shallowMount } from "@vue/test-utils";
import ArcoVue from "@arco-design/web-vue";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("../api", () => ({
  api: {
    get: apiMocks.get,
    post: apiMocks.post,
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  downloadUrl: (path: string) => `/api/v1${path}`,
}));

import AppShell from "./AppShell.vue";
import QuickCreateDrawer from "./QuickCreateDrawer.vue";
import { useSessionStore } from "../stores/session";

const now = "2026-08-11T08:00:00Z";
const user = { id: "user-1", username: "admin", display_name: "测试管理员", role: "admin", active: true, version: 1, created_at: now };
const createdTask = {
  id: "task-1",
  title: "季度党建台账报送",
  description: "",
  task_type: "quick",
  status: "pending_receipt",
  sensitivity: "normal",
  priority: "normal",
  source: "工作群",
  source_kind: "manual",
  category: "",
  tags: [],
  formal_due_at: null,
  internal_due_at: null,
  planned_start_at: null,
  planned_end_at: null,
  work_area: "",
  annual_focus: "",
  reporting_scope: "",
  owner_id: user.id,
  reviewer_id: null,
  parent_task_id: null,
  template_id: null,
  recurrence_rule_id: null,
  experience_notes: "",
  contact_ids: [],
  allow_sensitive_content: false,
  version: 1,
  created_by: user.id,
  updated_by: user.id,
  created_at: now,
  updated_at: now,
  completed_at: null,
  archived_at: null,
  participants: [],
  steps: [],
  materials: [],
  comments: [],
  events: [],
  subtasks: [],
  missing_required_materials: 0,
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, EventListener>();
  closed = false;
  constructor(public url: string, public options?: EventSourceInit) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, listener: EventListener) { this.listeners.set(name, listener); }
  close() { this.closed = true; }
  emit(name: string) { this.listeners.get(name)?.(new Event(name)); }
}

function setupContext() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const session = useSessionStore();
  session.user = user as never;
  session.ready = true;
  session.runtimeContext = {
    node_mode: "host",
    platform: "windows",
    user_role: "admin",
    device_id: null,
    device_name: "主机",
    capabilities: ["admin.access", "updates.manage", "backups.manage", "fleet.manage", "ai.manage"],
  };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div>今日</div>" } },
      { path: "/:pathMatch(.*)*", component: { template: "<div>业务页</div>" } },
    ],
  });
  return { pinia, session, router };
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  apiMocks.get.mockImplementation(async (path: string) => {
    if (path === "/reminders/preferences") {
      return { enabled: true, desktop_enabled: false, quiet_start: "22:00", quiet_end: "07:00", version: 1 };
    }
    if (path.startsWith("/notifications")) {
      return [{ id: "notice-1", notification_type: "comment", title: "收到评论", body: "请复核", entity_type: "task", entity_id: "task-1", read_at: null, created_at: now }];
    }
    if (path === "/users") return [user];
    if (path === "/auth/me") return user;
    if (path.startsWith("/global-search")) return { items: [{ type: "task", id: "task-1", title: "季度党建台账报送", subtitle: "事项", route: "/tasks/task-1" }] };
    return [];
  });
  apiMocks.post.mockImplementation(async (path: string) => path === "/tasks" ? createdTask : {});
});

describe("应用壳与快捷操作", () => {
  it("按真实能力显示导航、建立事件流并响应命令面板", async () => {
    const { pinia, router } = setupContext();
    await router.push("/");
    await router.isReady();
    const wrapper = shallowMount(AppShell, {
      global: { plugins: [pinia, router, ArcoVue] },
      slots: { default: "<main>当前业务内容</main>" },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("党建智办");
    const state = (wrapper.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState;
    const invoke = async (name: string, ...args: unknown[]) => {
      const action = state[name] as (...values: unknown[]) => unknown;
      expect(action, `${name} 应存在`).toBeTypeOf("function");
      await action(...args);
      await flushPromises();
    };
    await invoke("iconFor", "home");
    await invoke("domainIconFor", "today");
    await invoke("isDomainExpanded", "today");
    await invoke("toggleDomain", "today");
    await invoke("toggleDomain", "today");
    await invoke("persistExpandedDomains");
    await invoke("isNavigationActive", "/");
    await invoke("isNavigationActive", "/tasks");
    await invoke("timeToMinutes", "22:30");
    await invoke("isQuietTime");
    await invoke("loadReminderPreference");
    await invoke("loadNotifications");
    const notification = { id: "notice-2", notification_type: "comment", title: "业务提醒", body: "请处理", entity_type: "task", entity_id: "task-1", read_at: null, created_at: now };
    await invoke("openNotification", { ...notification });
    await invoke("openNotification", { ...notification, id: "notice-3", entity_type: "transfer" });
    await invoke("openNotification", { ...notification, id: "notice-4", entity_type: "workspace_root" });
    await invoke("openNotification", { ...notification, id: "notice-5", entity_type: "other" });
    await invoke("openCommandCenter");
    await invoke("searchCommandCenter", "台账");
    await invoke("searchCommandCenter", "");
    await invoke("chooseCommand", { id: "new-task", route: "" });
    await invoke("chooseCommand", { id: "calendar", route: "/calendar" });
    await invoke("globalKeydown", new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    expect(FakeEventSource.instances).toHaveLength(1);
    const source = FakeEventSource.instances[0];
    source.onopen?.();
    source.onmessage?.();
    source.emit("task.updated");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    await flushPromises();
    expect(wrapper.text()).toContain("常用操作");

    source.onerror?.();
    await flushPromises();
    expect(wrapper.text()).toMatch(/轮询连接中|主机暂不可达/);
    await invoke("logout");
    wrapper.unmount();
    expect(source.closed).toBe(true);
  });

  it("快捷新建校验必填项并提交完整最小事项", async () => {
    const { pinia, router } = setupContext();
    await router.push("/");
    const wrapper = shallowMount(QuickCreateDrawer, {
      props: { visible: true },
      global: { plugins: [pinia, router, ArcoVue] },
    });
    await flushPromises();
    const state = (wrapper.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState;
    const form = state.form as { title: string; owner_id: string; source: string };
    const submit = state.submit as () => Promise<void>;
    form.title = "";
    form.owner_id = "";
    await submit();
    form.title = "季度党建台账报送";
    form.owner_id = user.id;
    form.source = "工作群";
    apiMocks.post.mockRejectedValueOnce(new Error("模拟创建失败"));
    await submit();
    form.title = "季度党建台账报送";
    await submit();
    expect(apiMocks.post).toHaveBeenCalledWith("/tasks", expect.objectContaining({ title: "季度党建台账报送", task_type: "quick" }));
    expect(wrapper.emitted("created")?.[0]?.[0]).toMatchObject({ id: "task-1" });
    expect(wrapper.emitted("update:visible")?.[0]).toEqual([false]);
    wrapper.unmount();
  });
});
