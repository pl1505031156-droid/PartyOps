import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("./api", () => ({
  api: {
    get: apiMocks.get,
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  downloadUrl: (path: string) => `/api/v1${path}`,
}));

import router from "./router";
import { useSessionStore } from "./stores/session";

const currentGate = {
  identified: true,
  required: false,
  access_allowed: true,
  state: "current",
  status: "current",
};
const user = {
  id: "user-1",
  username: "admin",
  display_name: "测试管理员",
  role: "admin",
  active: true,
  version: 1,
  created_at: "2026-08-11T08:00:00Z",
};

beforeEach(async () => {
  setActivePinia(createPinia());
  apiMocks.get.mockReset();
  apiMocks.get.mockImplementation(async (path: string) => {
    if (path === "/device/update-gate") return currentGate;
    if (path === "/auth/me") return user;
    if (path === "/runtime/context") {
      return { node_mode: "host", platform: "windows", user_role: "admin", device_id: null, device_name: "主机", capabilities: [] };
    }
    return [];
  });
  await router.replace("/required-update");
});

describe("统一路由权限守卫", () => {
  it("所有懒加载业务页都能解析，并保持统一回顶行为", async () => {
    const loaders = router.getRoutes()
      .map((record) => record.components?.default)
      .filter((component): component is () => Promise<unknown> => typeof component === "function");
    expect(loaders.length).toBeGreaterThan(10);
    for (const load of loaders) await load();
    const scrollBehavior = router.options.scrollBehavior;
    expect(await scrollBehavior?.({} as never, {} as never, null)).toEqual({ top: 0 });
  });

  it("强制更新页始终可达，过期协同机被引导到更新页", async () => {
    await router.push("/required-update");
    expect(router.currentRoute.value.path).toBe("/required-update");

    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === "/device/update-gate") return { ...currentGate, required: true, access_allowed: false, state: "outdated" };
      return user;
    });
    await router.push("/tasks");
    expect(router.currentRoute.value.path).toBe("/required-update");
    expect(router.currentRoute.value.query.redirect).toBe("/tasks");
  });

  it("未登录用户进入登录页，已登录但缺少能力时进入明确无权限页", async () => {
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === "/device/update-gate") return currentGate;
      if (path === "/auth/me") throw new Error("unauthorized");
      return [];
    });
    const anonymous = useSessionStore();
    anonymous.user = null;
    anonymous.ready = false;
    await router.push("/tasks");
    expect(router.currentRoute.value.path).toBe("/login");

    const session = useSessionStore();
    session.user = user as never;
    session.ready = true;
    session.runtimeContext = { node_mode: "host", platform: "windows", user_role: "admin", device_id: null, device_name: "主机", capabilities: [] };
    await router.push("/settings/updates");
    expect(router.currentRoute.value.path).toBe("/forbidden");
    expect(router.currentRoute.value.query.from).toBe("/settings/updates");
  });

  it("具备后端有效能力的管理员可以进入管理路由", async () => {
    const session = useSessionStore();
    session.user = user as never;
    session.ready = true;
    session.runtimeContext = { node_mode: "host", platform: "windows", user_role: "admin", device_id: null, device_name: "主机", capabilities: ["updates.manage"] };
    await router.push("/settings/updates");
    expect(router.currentRoute.value.path).toBe("/settings/updates");
  });
});
