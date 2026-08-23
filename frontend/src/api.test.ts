import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, downloadUrl, saveBlobDownload } from "./api";
import {
  checkRuntimeCompatibility,
  FRONTEND_VERSION,
  isFrontendAssetError,
} from "./runtimeGuard";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "partyops_csrf=; Max-Age=0; Path=/";
});

describe("API 客户端", () => {
  it("发送 JSON、携带同源凭据并解析响应", async () => {
    document.cookie = "partyops_csrf=test-csrf-token; Path=/";
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "task-1" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await api.post<{ id: string }>("/tasks", { title: "事项" });
    expect(result.id).toBe("task-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/tasks");
    expect(init.credentials).toBe("include");
    expect(init.headers.get("Content-Type")).toBe("application/json");
    expect(init.headers.get("X-PartyOps-CSRF")).toBe("test-csrf-token");
  });

  it("保留 FormData 边界并支持 Blob、204 与下载地址", async () => {
    const responses = [
      new Response(new Blob(["file"]), {
        status: 200,
        headers: { "content-type": "application/octet-stream" },
      }),
      new Response(null, { status: 204 }),
    ];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responses[0])
      .mockResolvedValueOnce(responses[1]);
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    form.append("file", new Blob(["x"]), "x.txt");
    const blob = await api.post<Blob>("/upload", form);
    expect(blob).toBeInstanceOf(Blob);
    expect(fetchMock.mock.calls[0][1].headers.has("Content-Type")).toBe(false);
    await expect(api.delete<void>("/sessions/current")).resolves.toBeUndefined();
    expect(downloadUrl("/exports/tasks.xlsx")).toBe("/api/v1/exports/tasks.xlsx");
  });

  it("把下载链接挂入页面并延迟回收 Blob URL", () => {
    vi.useFakeTimers();
    const createObjectURL = vi.mocked(URL.createObjectURL);
    const revokeObjectURL = vi.mocked(URL.revokeObjectURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const blob = new Blob(["docx"], {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    saveBlobDownload(blob, "时间节点.docx");

    const anchor = document.querySelector('a[download="时间节点.docx"]');
    expect(anchor).not.toBeNull();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(anchor?.isConnected).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith(createObjectURL.mock.results.at(-1)?.value);
    vi.useRealTimers();
  });

  it("拒绝把空响应伪装成成功下载", () => {
    expect(() => saveBlobDownload(new Blob([]), "empty.docx")).toThrow(
      "服务端没有返回可下载的文件",
    );
  });

  it("把 problem+json 和非 JSON 错误转换为稳定异常", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({
              code: "VERSION_CONFLICT",
              detail: "事项已更新",
            }),
            {
              status: 409,
              headers: { "content-type": "application/problem+json" },
            },
          ),
        )
        .mockResolvedValueOnce(new Response("bad gateway", { status: 502 })),
    );
    await expect(
      api.patch("/tasks/1", { title: "修改" }, { "If-Match": "1" }),
    ).rejects.toMatchObject({
      status: 409,
      code: "VERSION_CONFLICT",
      message: "事项已更新",
    });
    await expect(api.get("/health")).rejects.toMatchObject({
      status: 502,
      code: "REQUEST_FAILED",
      message: "请求失败（502）",
    });
    expect(new ApiError(400, { title: "标题错误" }).message).toBe("标题错误");
    expect(new ApiError(400, {})).toMatchObject({
      code: "REQUEST_FAILED",
      message: "请求失败",
    });
  });

  it("支持无正文 POST 和缺失内容类型的二进制响应", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await api.post<Blob>("/ping");
    expect(result).toBeInstanceOf(Blob);
    expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
  });

  it("保留调用方内容类型并规范化字段级错误", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await api.put("/raw", "正文", { "Content-Type": "text/plain" });
    expect(fetchMock.mock.calls[0][1].headers.get("Content-Type")).toBe("text/plain");

    expect(new ApiError(422, {
      detail: "字段错误",
      fields: { username: "必填", attempts: 3 },
    })).toMatchObject({
      message: "字段错误",
      fields: { username: "必填", attempts: "3" },
    });
    expect(new ApiError(400, { fields: "invalid" }).fields).toEqual({});
  });

  it("在前后端版本不一致时阻止进入业务页面", async () => {
    const compatible = await checkRuntimeCompatibility(
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ app_version: FRONTEND_VERSION }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const mismatch = await checkRuntimeCompatibility(
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ app_version: "1.0.0" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    expect(compatible).toMatchObject({ status: "compatible" });
    expect(mismatch).toEqual({
      status: "mismatch",
      expected: FRONTEND_VERSION,
      actual: "1.0.0",
    });
  });

  it("识别升级后丢失的动态页面分包并处理健康检查异常", async () => {
    expect(
      isFrontendAssetError(
        new TypeError("Failed to fetch dynamically imported module: /assets/page.js"),
      ),
    ).toBe(true);
    expect(isFrontendAssetError(new Error("普通业务错误"))).toBe(false);
    await expect(
      checkRuntimeCompatibility(
        vi.fn().mockResolvedValue(new Response("bad gateway", { status: 502 })),
      ),
    ).resolves.toMatchObject({
      status: "unavailable",
      detail: "主机健康检查返回 502",
    });
  });
});
