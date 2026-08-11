import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.e2e.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  outputDir: "../output/playwright/1.4.3-release-candidate",
  reporter: [["list"]],
  use: {
    baseURL: process.env.PARTYOPS_E2E_BASE_URL || "http://127.0.0.1:18950",
    ...devices["Desktop Chrome"],
    viewport: { width: 1366, height: 768 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
});
