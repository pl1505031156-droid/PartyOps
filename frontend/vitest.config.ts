import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    include: ["src/**/*.test.ts"],
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    // 路由用例会真实加载全部懒加载页面；低性能 Win7 构建机上不应因默认 10 秒误报。
    hookTimeout: 30_000,
    coverage: {
      provider: "v8",
      // 发布门槛必须统计所有业务页面、组件、路由和状态管理，不能只统计
      // 已有测试的少量文件而形成虚假的 100% 覆盖率。
      include: ["src/**/*.{ts,vue}"],
      exclude: ["src/**/*.test.ts", "src/**/*.d.ts", "src/main.ts"],
      reporter: ["text", "html", "json-summary"],
      thresholds: {
        // 发布门槛按用户要求同时约束全仓行、语句与分支覆盖率 90%。
        // Vue 模板生成函数仍单独保留可解释的函数覆盖率防回退下限。
        lines: 90,
        functions: 45,
        statements: 90,
        branches: 90,
      },
    },
  },
});
