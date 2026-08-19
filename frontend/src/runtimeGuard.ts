import packageMetadata from "../package.json";

export const FRONTEND_VERSION = packageMetadata.version;
export const ROUTE_RELOAD_KEY = "partyops:route-reload";
export const RUNTIME_RELOAD_KEY = "partyops:runtime-reload";

export type RuntimeCompatibility =
  | { status: "compatible"; expected: string; actual: string }
  | { status: "mismatch"; expected: string; actual: string }
  | { status: "unavailable"; expected: string; actual: ""; detail: string };

type FetchLike = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

type SessionStorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function safeSessionStorage(): SessionStorageLike | null {
  try {
    return window.sessionStorage;
  } catch {
    // 隐私模式、组织策略或嵌入式浏览器可能在读取属性时直接抛出 SecurityError。
    return null;
  }
}

export function safeSessionStorageGet(key: string): string | null {
  try {
    return safeSessionStorage()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function safeSessionStorageSet(key: string, value: string): boolean {
  try {
    const storage = safeSessionStorage();
    if (!storage) return false;
    storage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function safeSessionStorageRemove(key: string): void {
  try {
    safeSessionStorage()?.removeItem(key);
  } catch {
    // 清理刷新指纹失败不应阻断应用启动。
  }
}

export async function checkRuntimeCompatibility(
  fetcher: FetchLike = fetch,
): Promise<RuntimeCompatibility> {
  try {
    const response = await fetcher("/api/v1/health", {
      cache: "no-store",
      credentials: "include",
      headers: { "X-PartyOps-Frontend-Version": FRONTEND_VERSION },
    });
    if (!response.ok) {
      return {
        status: "unavailable",
        expected: FRONTEND_VERSION,
        actual: "",
        detail: `主机健康检查返回 ${response.status}`,
      };
    }
    const payload = await response.json() as { app_version?: unknown };
    const actual = typeof payload.app_version === "string"
      ? payload.app_version.trim()
      : "";
    if (!actual) {
      return {
        status: "unavailable",
        expected: FRONTEND_VERSION,
        actual: "",
        detail: "主机未返回应用版本",
      };
    }
    return actual === FRONTEND_VERSION
      ? { status: "compatible", expected: FRONTEND_VERSION, actual }
      : { status: "mismatch", expected: FRONTEND_VERSION, actual };
  } catch {
    return {
      status: "unavailable",
      expected: FRONTEND_VERSION,
      actual: "",
      detail: "无法读取主机健康状态",
    };
  }
}

export function tryRecoverRuntimeMismatch(
  compatibility: Extract<RuntimeCompatibility, { status: "mismatch" }>,
  storage?: Pick<Storage, "getItem" | "setItem"> | null,
  navigate: (url: string) => void = (url) => window.location.replace(url),
  href: string = window.location.href,
): boolean {
  const fingerprint = `${compatibility.expected}:${compatibility.actual}`;
  try {
    const targetStorage = storage ?? safeSessionStorage();
    if (!targetStorage) return false;
    if (targetStorage.getItem(RUNTIME_RELOAD_KEY) === fingerprint) return false;
    targetStorage.setItem(RUNTIME_RELOAD_KEY, fingerprint);
    const target = new URL(href);
    target.searchParams.set("partyops_runtime", compatibility.actual);
    navigate(target.toString());
    return true;
  } catch {
    // 隐私模式或组织策略可能禁用 sessionStorage；此时停止自动刷新，
    // 由调用方显示一次中文恢复页，不能因存储异常变成空白页面或刷新环。
    return false;
  }
}

export function isFrontendAssetError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return [
    "Failed to fetch dynamically imported module",
    "Importing a module script failed",
    "error loading dynamically imported module",
    "Unable to preload CSS",
    "Failed to load module script",
  ].some((pattern) => message.includes(pattern));
}

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    })[character] || character,
  );
}

export function renderStartupProblem(
  title: string,
  detail: string,
  code: string,
): void {
  const safeTitle = escapeHtml(title);
  const safeDetail = escapeHtml(detail);
  const safeCode = escapeHtml(code);
  document.title = `${title} · 党建智办`;
  document.body.innerHTML = `
    <main id="partyops-startup-problem" style="
      min-height:100vh;display:grid;place-items:center;padding:32px;
      color:#292724;background:#f7f1e7;
      font:14px/1.75 system-ui,'Noto Sans CJK SC','Microsoft YaHei',sans-serif">
      <section style="
        width:min(680px,100%);padding:38px 42px;background:#fbf8f1;
        border:1px solid #d9d0c3;border-top:4px solid #b42318;
        box-shadow:0 24px 70px rgba(93,48,34,.12)">
        <p style="margin:0 0 10px;color:#b42318;font:12px Georgia,serif;letter-spacing:.12em">
          PARTYOPS RECOVERY
        </p>
        <h1 style="margin:0;font:600 28px/1.35 SimSun,serif">${safeTitle}</h1>
        <p style="margin:18px 0;color:#5f5952">${safeDetail}</p>
        <ol style="margin:18px 0;padding-left:20px;color:#5f5952">
          <li>先关闭其他“党建智办”浏览器页签。</li>
          <li>由管理员重启 PartyOps 服务或重新双击桌面图标。</li>
          <li>再点击下方按钮重新载入，业务数据库不会因刷新而改变。</li>
        </ol>
        <button id="partyops-reload" type="button" style="
          min-width:150px;height:42px;padding:0 22px;color:white;
          background:#b42318;border:0;cursor:pointer">重新载入系统</button>
        <p style="margin:18px 0 0;color:#8a8178;font-size:12px">
          诊断代码：${safeCode}
        </p>
      </section>
    </main>`;
  document.getElementById("partyops-reload")?.addEventListener(
    "click",
    () => window.location.reload(),
  );
}
