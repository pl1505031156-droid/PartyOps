import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import legacy from "@vitejs/plugin-legacy";

export default defineConfig({
  build: {
    outDir: "dist/client",
    rollupOptions: {
      output: {
        // Arco 与框架依赖独立缓存，避免每次业务页面改动都重新下载
        // 近 1 MB 的公共入口；路由页面仍由 Vue Router 按需加载。
        manualChunks: {
          "vendor-ui": ["@arco-design/web-vue"],
          "vendor-core": ["vue", "vue-router", "pinia", "dayjs"],
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    proxy: {
      "/api": {
        // 允许测试与视觉回归使用独立端口，避免误连仍在运行的旧版后端。
        // 正式开发与目标机运行均继续使用 18765。
        target:
          process.env.PARTYOPS_DEV_API ||
          `http://127.0.0.1:${process.env.PARTYOPS_DEV_API_PORT || "18765"}`,
        changeOrigin: true,
      },
    },
  },
  plugins: [
    vue(),
    legacy({
      // Win7 仅支持仍具备原生 ESM 的浏览器；IE11 不运行 Vue 3，入口页会显示中文升级提示。
      modernTargets: ["Chrome >= 64", "Firefox >= 78", "Edge >= 79"],
      modernPolyfills: true,
      renderLegacyChunks: false,
    }),
  ],
});
