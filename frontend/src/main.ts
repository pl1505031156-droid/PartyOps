import { createApp } from "vue";
import { createPinia } from "pinia";
import ArcoVue from "@arco-design/web-vue";
import "@arco-design/web-vue/dist/arco.css";
import App from "./App.vue";
import router from "./router";
import "./utils/datetime";
import {
  checkRuntimeCompatibility,
  FRONTEND_VERSION,
  isFrontendAssetError,
  renderStartupProblem,
  ROUTE_RELOAD_KEY,
} from "./runtimeGuard";
import "./styles.css";

async function startApplication() {
  const compatibility = await checkRuntimeCompatibility();
  if (compatibility.status === "mismatch") {
    renderStartupProblem(
      "程序升级尚未完成",
      `浏览器页面版本为 ${compatibility.expected}，正在运行的主机服务版本为 ${compatibility.actual}。这通常表示旧进程没有退出，不代表业务数据丢失。`,
      "RUNTIME_VERSION_MISMATCH",
    );
    return;
  }
  if (compatibility.status === "unavailable") {
    renderStartupProblem(
      "主机服务暂不可用",
      `${compatibility.detail}。请先确认主机服务已经启动，再重新载入。`,
      "RUNTIME_HEALTH_UNAVAILABLE",
    );
    return;
  }

  router.onError((error, target) => {
    const fingerprint = `${FRONTEND_VERSION}:${target.fullPath}`;
    if (
      isFrontendAssetError(error)
      && window.sessionStorage.getItem(ROUTE_RELOAD_KEY) !== fingerprint
    ) {
      window.sessionStorage.setItem(ROUTE_RELOAD_KEY, fingerprint);
      window.location.reload();
      return;
    }
    console.error("PartyOps route load failed", error);
    renderStartupProblem(
      "页面资源加载失败",
      "升级后仍有旧页面资源留在浏览器中，或者安装包中的页面文件不完整。系统已停止继续加载，避免出现没有说明的白屏。",
      isFrontendAssetError(error)
        ? "FRONTEND_ASSET_MISMATCH"
        : "ROUTE_LOAD_FAILED",
    );
  });
  router.afterEach((_to, _from, failure) => {
    if (!failure) window.sessionStorage.removeItem(ROUTE_RELOAD_KEY);
  });

  const app = createApp(App);
  app.config.errorHandler = (error, _instance, info) => {
    console.error("PartyOps page render failed", error, info);
    renderStartupProblem(
      "页面运行异常",
      "当前页面与主机返回的数据不兼容，常见原因是旧版服务进程尚未退出。请按页面提示重启服务后重新载入。",
      "PAGE_RUNTIME_ERROR",
    );
  };
  app.use(createPinia());
  app.use(router);
  app.use(ArcoVue);
  app.mount("#app");
}

void startApplication();
