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

function cookieValue(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = `${encodeURIComponent(name)}=`;
  const entry = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : "";
}

export interface UploadProgressOptions {
  headers?: HeadersInit;
  signal?: AbortSignal;
  onProgress?: (percent: number) => void;
}

/**
 * 使用浏览器原生上传事件报告真实进度。业务附件按文件独立调用，
 * 因此单文件失败不会影响同一批次中已经成功的其他文件。
 */
export function uploadFormWithProgress<T>(
  path: string,
  formData: FormData,
  options: UploadProgressOptions = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1${path}`);
    xhr.withCredentials = true;
    const headers = new Headers(options.headers);
    const csrfToken = cookieValue("partyops_csrf");
    if (csrfToken && !headers.has("Authorization")) {
      headers.set("X-PartyOps-CSRF", csrfToken);
    }
    headers.forEach((value, key) => xhr.setRequestHeader(key, value));
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      options.onProgress?.(Math.min(99, Math.round((event.loaded / event.total) * 100)));
    });
    xhr.addEventListener("load", () => {
      let payload: unknown = undefined;
      if (xhr.responseText) {
        try {
          payload = JSON.parse(xhr.responseText);
        } catch {
          payload = xhr.responseText;
        }
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        options.onProgress?.(100);
        resolve(payload as T);
        return;
      }
      const problem = payload && typeof payload === "object"
        ? payload as Record<string, unknown>
        : { detail: `上传失败（${xhr.status || "网络中断"}）` };
      reject(new ApiError(xhr.status || 0, problem));
    });
    xhr.addEventListener("error", () => reject(new ApiError(0, { code: "UPLOAD_NETWORK_ERROR", detail: "网络连接中断，请检查连接后重试。" })));
    xhr.addEventListener("abort", () => reject(new DOMException("上传已取消", "AbortError")));
    const abort = () => xhr.abort();
    if (options.signal?.aborted) {
      abort();
      return;
    }
    options.signal?.addEventListener("abort", abort, { once: true });
    xhr.addEventListener("loadend", () => options.signal?.removeEventListener("abort", abort));
    xhr.send(formData);
  });
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && !headers.has("Authorization")) {
    const csrfToken = cookieValue("partyops_csrf");
    if (csrfToken) headers.set("X-PartyOps-CSRF", csrfToken);
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
  deleteBody: <T>(path: string, body: unknown, headers?: HeadersInit) =>
    request<T>(path, { method: "DELETE", body: JSON.stringify(body), headers }),
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
