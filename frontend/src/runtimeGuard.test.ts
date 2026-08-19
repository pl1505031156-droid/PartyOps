import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkRuntimeCompatibility,
  FRONTEND_VERSION,
  isFrontendAssetError,
  renderStartupProblem,
  safeSessionStorage,
  safeSessionStorageGet,
  safeSessionStorageRemove,
  safeSessionStorageSet,
  tryRecoverRuntimeMismatch,
} from "./runtimeGuard";

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("前后端版本兼容守卫", () => {
  it("区分一致、版本不一致、异常响应和网络失败", async () => {
    const compatible = await checkRuntimeCompatibility(async () => new Response(
      JSON.stringify({ app_version: FRONTEND_VERSION }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    expect(compatible.status).toBe("compatible");

    const mismatch = await checkRuntimeCompatibility(async () => new Response(
      JSON.stringify({ app_version: "0.0.1" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    expect(mismatch).toMatchObject({ status: "mismatch", actual: "0.0.1" });

    const unhealthy = await checkRuntimeCompatibility(async () => new Response("", { status: 503 }));
    expect(unhealthy).toMatchObject({ status: "unavailable", detail: "主机健康检查返回 503" });

    const missingVersion = await checkRuntimeCompatibility(async () => new Response(
      JSON.stringify({}),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    expect(missingVersion).toMatchObject({ status: "unavailable", detail: "主机未返回应用版本" });

    const offline = await checkRuntimeCompatibility(async () => { throw new Error("offline"); });
    expect(offline).toMatchObject({ status: "unavailable", detail: "无法读取主机健康状态" });
  });

  it("识别前端静态资源错误而不误判普通业务异常", () => {
    expect(isFrontendAssetError(new Error("Failed to fetch dynamically imported module"))).toBe(true);
    expect(isFrontendAssetError("Unable to preload CSS /assets/app.css")).toBe(true);
    expect(isFrontendAssetError(new Error("业务校验失败"))).toBe(false);
  });

  it("转义诊断内容并提供可执行的恢复按钮", () => {
    renderStartupProblem("<升级失败>", "服务包含 & 异常", "ERR_</p><script>alert(1)</script>");
    expect(document.body.innerHTML).toContain("&lt;升级失败&gt;");
    expect(document.body.innerHTML).toContain("服务包含 &amp; 异常");
    expect(document.body.innerHTML).toContain("ERR_&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(document.body.querySelector("script")).toBeNull();
    expect(document.getElementById("partyops-reload")).not.toBeNull();
  });

  it("旧页面遇到新服务时只自动强制刷新一次", () => {
    const navigated: string[] = [];
    const mismatch = {
      status: "mismatch" as const,
      expected: "1.4.3-rc.6",
      actual: "1.4.3-rc.7",
    };

    expect(tryRecoverRuntimeMismatch(
      mismatch,
      window.sessionStorage,
      (url) => navigated.push(url),
      "http://127.0.0.1:18775/work?view=today",
    )).toBe(true);
    expect(navigated[0]).toContain("partyops_runtime=1.4.3-rc.7");
    expect(tryRecoverRuntimeMismatch(
      mismatch,
      window.sessionStorage,
      (url) => navigated.push(url),
      "http://127.0.0.1:18775/work?view=today",
    )).toBe(false);
    expect(navigated).toHaveLength(1);
  });

  it("浏览器策略禁用会话存储时显示恢复页而不是空白或刷新环", () => {
    const deniedStorage = {
      getItem: () => { throw new DOMException("denied", "SecurityError"); },
      setItem: () => { throw new DOMException("denied", "SecurityError"); },
    };
    expect(tryRecoverRuntimeMismatch(
      { status: "mismatch", expected: "old", actual: "new" },
      deniedStorage,
      () => { throw new Error("不应导航"); },
      "http://127.0.0.1:18775/",
    )).toBe(false);
  });

  it("读取 sessionStorage 属性本身被策略拒绝时仍安全降级", () => {
    const original = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get: () => { throw new DOMException("denied", "SecurityError"); },
    });
    try {
      expect(safeSessionStorage()).toBeNull();
      expect(safeSessionStorageGet("key")).toBeNull();
      expect(safeSessionStorageSet("key", "value")).toBe(false);
      expect(() => safeSessionStorageRemove("key")).not.toThrow();
      expect(tryRecoverRuntimeMismatch(
        { status: "mismatch", expected: "old", actual: "new" },
        undefined,
        () => { throw new Error("不应导航"); },
        "http://127.0.0.1:18775/",
      )).toBe(false);
    } finally {
      if (original) Object.defineProperty(window, "sessionStorage", original);
      else Reflect.deleteProperty(window, "sessionStorage");
    }
  });

  it("会话存储对象可读取但单项操作被策略拒绝时仍安全降级", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("get denied", "SecurityError");
    });
    expect(safeSessionStorageGet("key")).toBeNull();

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("set denied", "SecurityError");
    });
    expect(safeSessionStorageSet("key", "value")).toBe(false);

    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("remove denied", "SecurityError");
    });
    expect(() => safeSessionStorageRemove("key")).not.toThrow();
  });
});
