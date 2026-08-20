export class ApiError extends Error {
  status: number;
  code: string;
  problem: Record<string, unknown>;
  fields: Record<string, string>;

  constructor(status: number, problem: Record<string, unknown>) {
    super(String(problem.detail || problem.title || "请求失败"));
    this.status = status;
    this.code = String(problem.code || "REQUEST_FAILED");
    this.problem = problem;
    const rawFields = problem.fields;
    this.fields = rawFields && typeof rawFields === "object"
      ? Object.fromEntries(
          Object.entries(rawFields as Record<string, unknown>).map(([key, value]) => [key, String(value)]),
        )
      : {};
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!response.ok) {
    let problem: Record<string, unknown> = {};
    try {
      problem = await response.json();
    } catch {
      problem = { detail: `请求失败（${response.status}）` };
    }
    throw new ApiError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.blob() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, headers?: HeadersInit) =>
    request<T>(path, {
      method: "POST",
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
      headers,
    }),
  patch: <T>(path: string, body: unknown, headers?: HeadersInit) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body), headers }),
  put: <T>(path: string, body: unknown, headers?: HeadersInit) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body), headers }),
  delete: <T>(path: string, headers?: HeadersInit) =>
    request<T>(path, { method: "DELETE", headers }),
};

export function downloadUrl(path: string): string {
  return `/api/v1${path}`;
}

export function saveBlobDownload(blob: Blob, filename: string): void {
  if (!(blob instanceof Blob) || blob.size === 0) {
    throw new Error("服务端没有返回可下载的文件");
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  // 部分 Windows WebView/旧 Chromium 会异步读取 Blob URL；立即回收会
  // 出现“提示已导出但没有文件”。下一轮事件循环再清理可兼容这些实现。
  window.setTimeout(() => {
    anchor.remove();
    URL.revokeObjectURL(url);
  }, 1000);
}
