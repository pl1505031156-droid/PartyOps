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
  window.localStorage.clear();
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
    const { pinia, session, router } = setupContext();
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
    window.localStorage.removeItem("partyops.online-update-last-check");
    apiMocks.get.mockResolvedValueOnce({ available: true, version: "1.4.3-rc.3" });
    apiMocks.post.mockResolvedValueOnce({ id: "online-package", version: "1.4.3-rc.3" });
    await invoke("prepareProfessionalUpdate");
    expect(window.localStorage.getItem("partyops.pending-online-update")).toContain("online-package");
    expect(wrapper.text()).toContain("更新准备中");
    window.localStorage.removeItem("partyops.online-update-last-check");
    window.localStorage.setItem(
      "partyops.pending-online-update",
      JSON.stringify({ packageId: "old-package", version: "1.4.3-rc.2" }),
    );
    apiMocks.get.mockResolvedValueOnce({ available: true, version: "1.4.3-rc.3" });
    apiMocks.post.mockResolvedValueOnce({ id: "new-package", version: "1.4.3-rc.3" });
    await invoke("prepareProfessionalUpdate");
    expect(window.localStorage.getItem("partyops.pending-online-update")).toContain("new-package");

    // 专业更新检查必须覆盖普通用户、每日限频、已是最新、损坏缓存、
    // 同版本幂等恢复和离线静默重试，避免把日常办公变成反复弹窗。
    session.user = { ...user, role: "member" } as never;
    await invoke("prepareProfessionalUpdate");
    session.user = user as never;
    window.localStorage.setItem("partyops.online-update-last-check", Date.now().toString());
    await invoke("prepareProfessionalUpdate");

    window.localStorage.setItem("partyops.online-update-last-check", "0");
    apiMocks.get.mockResolvedValueOnce({ available: false, version: "1.4.3-rc.3" });
    await invoke("prepareProfessionalUpdate");
    expect(window.localStorage.getItem("partyops.pending-online-update")).toBeNull();

    window.localStorage.setItem("partyops.online-update-last-check", "0");
    window.localStorage.setItem("partyops.pending-online-update", "{损坏缓存");
    apiMocks.get.mockResolvedValueOnce({ available: true, version: "1.4.3-rc.3" });
    apiMocks.post.mockResolvedValueOnce({ id: "same-package", version: "1.4.3-rc.3" });
    await invoke("prepareProfessionalUpdate");
    expect(window.localStorage.getItem("partyops.pending-online-update")).toContain("same-package");

    window.localStorage.setItem("partyops.online-update-last-check", "0");
    window.localStorage.setItem(
      "partyops.pending-online-update",
      JSON.stringify({ packageId: "same-package", version: "1.4.3-rc.3" }),
    );
    apiMocks.get.mockResolvedValueOnce({ available: true, version: "1.4.3-rc.3" });
    apiMocks.post.mockResolvedValueOnce({ id: "same-package", version: "1.4.3-rc.3" });
    await invoke("prepareProfessionalUpdate");

    window.localStorage.setItem("partyops.online-update-last-check", "0");
    apiMocks.get.mockRejectedValueOnce(new Error("模拟离线"));
    await invoke("prepareProfessionalUpdate");
    expect(Number(window.localStorage.getItem("partyops.online-update-retry-after"))).toBeGreaterThan(Date.now());
    const callsDuringBackoff = apiMocks.get.mock.calls.length;
    await invoke("prepareProfessionalUpdate");
    expect(apiMocks.get.mock.calls.length).toBe(callsDuringBackoff);
    window.localStorage.removeItem("partyops.online-update-retry-after");
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

  it("快捷新建覆盖用户回退、空用户与非标准创建错误", async () => {
    const { pinia, router } = setupContext();
    await router.push("/");
    apiMocks.get.mockRejectedValueOnce(new Error("用户列表离线")).mockResolvedValueOnce(user);
    const fallback = shallowMount(QuickCreateDrawer, {
      props: { visible: true },
      global: { plugins: [pinia, router, ArcoVue] },
    });
    await flushPromises();
    const fallbackState = (fallback.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState;
    const fallbackForm = fallbackState.form as { title: string; owner_id: string };
    expect(fallbackForm.owner_id).toBe(user.id);
    fallbackForm.title = "回退用户创建事项";
    apiMocks.post.mockRejectedValueOnce("非标准错误");
    await (fallbackState.submit as () => Promise<void>)();
    fallback.unmount();

    apiMocks.get.mockImplementation(async (path: string) => path === "/users" ? [] : user);
    const empty = shallowMount(QuickCreateDrawer, {
      props: { visible: true },
      global: { plugins: [pinia, router, ArcoVue] },
    });
    await flushPromises();
    const emptyState = (empty.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState;
    const emptyForm = emptyState.form as { title: string; owner_id: string };
    expect(emptyForm.owner_id).toBe("");
    emptyForm.title = "只有标题";
    await (emptyState.submit as () => Promise<void>)();
    expect(apiMocks.post).toHaveBeenCalledTimes(1);
    empty.unmount();
  });

  it("应用壳覆盖离线、静默时段、搜索失败和键盘替代路径", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 7, 11, 12, 30, 0));
    apiMocks.get.mockRejectedValueOnce(new Error("偏好离线")).mockRejectedValueOnce(new Error("通知离线"));
    const { pinia, session, router } = setupContext();
    session.runtimeContext = { ...session.runtimeContext!, capabilities: [] };
    await router.push("/tasks/task-1");
    await router.isReady();
    const wrapper = shallowMount(AppShell, {
      global: { plugins: [pinia, router, ArcoVue] },
      slots: { default: "<main>事项详情</main>" },
    });
    await flushPromises();
    const state = (wrapper.vm as unknown as { $: { setupState: Record<string, unknown> } }).$.setupState;
    const invoke = async (name: string, ...args: unknown[]) => {
      const action = state[name] as (...values: unknown[]) => unknown;
      expect(action, `${name} 应存在`).toBeTypeOf("function");
      const result = await action(...args);
      await flushPromises();
      return result;
    };

    expect(state.activePath).toBe("/tasks");
    expect((state.visibleNavigationDomains as Array<{ items: unknown[] }>).every((domain) => domain.items.length > 0)).toBe(true);
    state.reminderPreference = null;
    expect(await invoke("isQuietTime")).toBe(false);
    state.reminderPreference = { enabled: true, desktop_enabled: true, quiet_start: "08:00", quiet_end: "08:00" };
    expect(await invoke("isQuietTime")).toBe(false);
    state.reminderPreference = { enabled: true, desktop_enabled: true, quiet_start: "12:00", quiet_end: "13:00" };
    expect(await invoke("isQuietTime")).toBe(true);
    state.reminderPreference = { enabled: true, desktop_enabled: true, quiet_start: "23:00", quiet_end: "06:00" };
    expect(await invoke("isQuietTime")).toBe(false);

    apiMocks.get.mockRejectedValueOnce(new Error("偏好失败"));
    await invoke("loadReminderPreference");
    apiMocks.get.mockRejectedValueOnce(new Error("通知失败"));
    await invoke("loadNotifications");
    apiMocks.get.mockRejectedValueOnce(new Error("搜索失败"));
    await invoke("searchCommandCenter", "不存在");
    expect(state.commandResults).toEqual([]);
    await invoke("chooseCommand", { id: "new-memo", route: "/memos" });
    await invoke("chooseCommand", { id: "noop", route: "" });
    await invoke("globalKeydown", new KeyboardEvent("keydown", { key: "m", ctrlKey: true, altKey: true }));
    await invoke("globalKeydown", new KeyboardEvent("keydown", { key: "k", metaKey: true }));

    const source = FakeEventSource.instances[0];
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    source.onerror?.();
    expect(state.connectionState).toBe("offline");
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    source.onerror?.();
    expect(state.connectionState).toBe("polling");
    source.onopen?.();
    expect(state.connectionState).toBe("live");

    wrapper.unmount();
    vi.useRealTimers();
  });
});
