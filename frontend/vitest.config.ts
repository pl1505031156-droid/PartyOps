import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  test: {
    include: ["src/**/*.test.ts"],
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    coverage: {
      provider: "v8",
      // 发布门槛必须统计所有业务页面、组件、路由和状态管理，不能只统计
      // 已有测试的少量文件而形成虚假的 100% 覆盖率。
      include: ["src/**/*.{ts,vue}"],
      exclude: ["src/**/*.test.ts", "src/**/*.d.ts", "src/main.ts"],
      reporter: ["text", "html", "json-summary"],
      thresholds: {
        // Vue 模板编译器会把每个内联事件与插槽生成独立函数；发布门槛以用户要求的
        // 全仓行/语句覆盖率 90% 为硬门禁，同时保留分支和函数的防回退下限。
        lines: 90,
        functions: 45,
        statements: 90,
        branches: 74,
      },
    },
  },
});
