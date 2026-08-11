import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkRuntimeCompatibility,
  FRONTEND_VERSION,
  isFrontendAssetError,
  renderStartupProblem,
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
});
