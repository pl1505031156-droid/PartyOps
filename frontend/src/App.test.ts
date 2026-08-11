import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it, vi } from "vitest";
import App from "./App.vue";

const apiMocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("./api", () => ({
  api: { get: apiMocks.get, post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

async function mountAt(path: string) {
  apiMocks.get.mockResolvedValue({ effective_season: "spring", art_level: "standard", reduce_motion: false, theme_mode: "auto" });
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", component: { template: "<div>登录</div>" }, meta: { public: true } },
      { path: "/", component: { template: "<div>今日</div>" } },
    ],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = shallowMount(App, {
    global: {
      plugins: [pinia, router],
      stubs: {
        AConfigProvider: { template: "<div><slot /></div>" },
        AppShell: { template: "<section data-test='shell'><slot /></section>" },
        RouterView: { template: "<main data-test='route' />" },
      },
    },
  });
  await flushPromises();
  return wrapper;
}

describe("应用根布局", () => {
  it("公开页不包裹业务壳，登录后页面统一进入业务壳", async () => {
    const publicPage = await mountAt("/login");
    expect(publicPage.find("[data-test='shell']").exists()).toBe(false);
    expect(apiMocks.get).toHaveBeenCalledWith("/appearance/context");
    publicPage.unmount();

    const privatePage = await mountAt("/");
    expect(privatePage.find("[data-test='shell']").exists()).toBe(true);
    privatePage.unmount();
  });
});
