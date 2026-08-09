import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { AdminAppearance, AppearanceContext, UserAppearance } from "../types";
import { useAppearanceStore } from "./appearance";

const rootDataset: Record<string, string> = {};

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
  Object.keys(rootDataset).forEach((key) => delete rootDataset[key]);
  vi.stubGlobal("document", { documentElement: { dataset: rootDataset } });
});

afterEach(() => vi.unstubAllGlobals());

describe("东方主题状态", () => {
  it("加载服务端上下文并只更新根节点属性", async () => {
    const context: AppearanceContext = {
      effective_season: "autumn",
      art_level: "reduced",
      reduce_motion: true,
      theme_mode: "fixed",
    };
    vi.spyOn(api, "get").mockResolvedValue(context);
    const store = useAppearanceStore();

    expect(await store.loadContext()).toEqual(context);
    expect(rootDataset).toEqual({
      season: "autumn",
      artLevel: "reduced",
      reduceMotion: "true",
    });
    expect(store.loaded).toBe(true);
  });

  it("接口异常时安全降级为标准春季主题", async () => {
    vi.spyOn(api, "get").mockRejectedValue(new Error("offline"));
    const store = useAppearanceStore();

    expect(await store.loadContext()).toEqual({
      effective_season: "spring",
      art_level: "standard",
      reduce_motion: false,
      theme_mode: "auto",
    });
    expect(rootDataset.season).toBe("spring");
    expect(rootDataset.artLevel).toBe("standard");
  });

  it("个人和管理员偏好均携带版本号保存并刷新有效主题", async () => {
    const user: UserAppearance = {
      user_id: "user-1",
      art_level: "standard",
      reduce_motion: false,
      theme_override: null,
      version: 3,
      updated_at: "2026-08-02T00:00:00Z",
    };
    const admin: AdminAppearance = {
      theme_mode: "auto",
      fixed_theme: "spring",
      default_art_level: "standard",
      default_reduce_motion: false,
      version: 5,
    };
    const context: AppearanceContext = {
      effective_season: "summer",
      art_level: "reduced",
      reduce_motion: true,
      theme_mode: "auto",
    };
    const get = vi.spyOn(api, "get");
    get.mockResolvedValueOnce(user).mockResolvedValueOnce(context).mockResolvedValueOnce(admin).mockResolvedValueOnce(context);
    const patch = vi.spyOn(api, "patch");
    patch.mockResolvedValueOnce({ ...user, art_level: "reduced", reduce_motion: true, version: 4 });
    patch.mockResolvedValueOnce({ ...admin, default_art_level: "reduced", version: 6 });
    const store = useAppearanceStore();

    const savedUser = await store.saveUser({
      art_level: "reduced",
      reduce_motion: true,
      theme_override: null,
    });
    expect(savedUser.version).toBe(4);
    expect(patch).toHaveBeenNthCalledWith(
      1,
      "/me/appearance",
      { art_level: "reduced", reduce_motion: true, theme_override: null },
      { "If-Match": "3" },
    );

    const savedAdmin = await store.saveAdmin({
      theme_mode: "auto",
      fixed_theme: "spring",
      default_art_level: "reduced",
      default_reduce_motion: false,
    });
    expect(savedAdmin.version).toBe(6);
    expect(patch).toHaveBeenNthCalledWith(
      2,
      "/admin/appearance",
      {
        theme_mode: "auto",
        fixed_theme: "spring",
        default_art_level: "reduced",
        default_reduce_motion: false,
      },
      { "If-Match": "5" },
    );
  });
});
