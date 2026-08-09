import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { RuntimeContext, User } from "../types";
import { useSessionStore } from "./session";

const admin: User = {
  id: "user-1",
  username: "admin",
  display_name: "管理员",
      role: "admin",
      active: true,
      version: 1,
      created_at: "2026-07-28T00:00:00Z",
};

const runtimeContext: RuntimeContext = {
  node_mode: "host",
  device_id: null,
  device_name: "",
  platform: "windows",
  user_role: "admin",
  capabilities: ["workspace.manage_host_roots", "fleet.manage"],
};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("会话状态", () => {
  it("加载首次配置并复用已登录用户", async () => {
    vi.spyOn(api, "get").mockImplementation(async (path) => (
      path === "/runtime/context"
        ? runtimeContext
        : {
            configured: true,
            mode: "host",
            app_name: "党建智办",
            host: "127.0.0.1",
            port: 18765,
          }
    ));
    const store = useSessionStore();
    expect((await store.loadBootstrap()).configured).toBe(true);
    store.user = admin;
    expect(await store.ensure()).toEqual(admin);
  });

  it("读取当前用户并在失效时进入就绪未登录状态", async () => {
    const get = vi.spyOn(api, "get");
    get.mockResolvedValueOnce(admin);
    get.mockResolvedValueOnce(runtimeContext);
    const store = useSessionStore();
    expect(await store.ensure()).toEqual(admin);
    expect(store.ready).toBe(true);

    store.user = null;
    store.ready = false;
    get.mockRejectedValueOnce(new Error("401"));
    expect(await store.ensure()).toBeNull();
    expect(store.ready).toBe(true);
  });

  it("支持登录、首次设置与注销", async () => {
    vi.spyOn(api, "get").mockImplementation(async (path) => (
      path === "/runtime/context"
        ? runtimeContext
        : {
            configured: true,
            mode: "host",
            app_name: "党建智办",
            host: "127.0.0.1",
            port: 18765,
          }
    ));
    const post = vi.spyOn(api, "post").mockResolvedValue(admin);
    const store = useSessionStore();
    expect(await store.login("admin", "password")).toEqual(admin);
    expect(await store.setup("admin", "管理员", "password")).toEqual(admin);
    expect(post).toHaveBeenCalledWith("/bootstrap/host", {
      username: "admin",
      display_name: "管理员",
      password: "password",
    });
    await store.logout();
    expect(store.user).toBeNull();
    expect(post).toHaveBeenCalledWith("/auth/logout");
  });
});
