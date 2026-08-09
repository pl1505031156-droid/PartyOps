import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

// 根目录名称只需传入稳定前缀，避免外部 QA 工具的控制台编码影响中文名称。
const rootName = process.env.PARTYOPS_E2E_PREVIEW_ROOT || "Firecrawl";
const captureDocs = process.env.PARTYOPS_E2E_CAPTURE_DOCS === "1";
const screenshotDir = resolve(process.cwd(), "../docs/images");

async function login(page: Page) {
  const response = await page.request.post("/api/v1/auth/login", {
    data: { username: "admin", password: "PartyOps@2026" },
  });
  expect(response.ok()).toBeTruthy();
}

async function openPreview(page: Page, fileName: RegExp, rootPattern: string | RegExp) {
  const root = page.locator(".root-strip button", { hasText: rootPattern });
  await expect(root).toBeVisible();
  await root.click();
  const row = page.locator(".file-row", { hasText: fileName });
  await expect(row).toBeVisible();
  await row.click();
  await page.getByRole("button", { name: "预览", exact: true }).click();
  const modal = page.locator(".arco-modal:visible");
  await expect(modal).toBeVisible();
  await expect(modal.locator(".arco-spin-loading")).toHaveCount(0, { timeout: 60_000 });
  return modal;
}

test("文件中心使用官方 Firecrawl WASM 阅读 PDF、Office 与 CSV", async ({ page }) => {
  const consoleErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failedResponses.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
  });

  await login(page);
  await page.goto("/workspace");
  await expect(page.getByRole("heading", { name: "原始文件中心" })).toBeVisible();

  const docxModal = await openPreview(page, /升级建议\.docx/, new RegExp(`${rootName}.*主机文件`));
  await expect(docxModal).toContainText("anydoc 0.1.7");
  await expect(docxModal.locator(".document-reading")).toContainText(/PartyOps|党建智办/);
  if (captureDocs) {
    mkdirSync(screenshotDir, { recursive: true });
    await page.screenshot({
      path: resolve(screenshotDir, "file-center-office-preview.png"),
      fullPage: false,
    });
  }
  await page.keyboard.press("Escape");
  await expect(docxModal).toBeHidden();

  const pdfModal = await openPreview(page, /路线图\.pdf/, new RegExp(`${rootName}.*主机文件`));
  await expect(pdfModal).toContainText("pdf-inspector");
  await expect(pdfModal.locator(".preview-metadata")).toContainText("页");
  await expect(pdfModal.locator(".document-reading")).not.toBeEmpty();
  if (captureDocs) {
    await page.screenshot({
      path: resolve(screenshotDir, "file-center-pdf-preview.png"),
      fullPage: false,
    });
  }
  await page.keyboard.press("Escape");
  await expect(pdfModal).toBeHidden();

  const csvModal = await openPreview(page, /组织生活统计\.csv/, new RegExp(`${rootName}.*主机文件`));
  await expect(csvModal).toContainText("anydoc 0.1.7");
  await expect(csvModal.locator(".document-reading")).toContainText("党员人数");
  await page.keyboard.press("Escape");

  expect(consoleErrors).toEqual([]);
  expect(failedResponses).toEqual([]);
});

test("协同机文件经真实 Agent 中转、校验后可用两套解析器阅读", async ({ page }) => {
  const consoleErrors: string[] = [];
  const failedResponses: string[] = [];
  const transferRequests: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failedResponses.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
    if (
      response.request().method() === "POST"
      && response.url().endsWith("/api/v1/workspace/downloads")
    ) {
      transferRequests.push(`${response.status()} ${response.url()}`);
    }
  });

  await login(page);
  await page.goto("/workspace");

  const docxModal = await openPreview(page, /协同机党建智办建议\.docx/, /共享 · 3 个文件/);
  await expect(docxModal).toContainText("anydoc 0.1.7");
  await expect(docxModal.locator(".document-reading")).toContainText(/PartyOps|党建智办/);
  if (captureDocs) {
    mkdirSync(screenshotDir, { recursive: true });
    await page.screenshot({
      path: resolve(screenshotDir, "cross-device-office-preview.png"),
      fullPage: false,
    });
  }
  const popupPromise = page.waitForEvent("popup");
  await docxModal.getByRole("button", { name: "浏览器另存为" }).click();
  const downloadPage = await popupPromise;
  const download = await downloadPage.waitForEvent("download", { timeout: 60_000 });
  const downloadedPath = await download.path();
  expect(download.suggestedFilename()).toBe("协同机党建智办建议.docx");
  expect(downloadedPath).toBeTruthy();
  expect(readFileSync(downloadedPath!)).toEqual(
    readFileSync(resolve(process.cwd(), "../.qa-remote-document-share/协同机党建智办建议.docx")),
  );
  await downloadPage.close();
  await page.keyboard.press("Escape");
  await expect(docxModal).toBeHidden();

  const pdfModal = await openPreview(page, /协同机PartyOps路线图\.pdf/, /共享 · 3 个文件/);
  await expect(pdfModal).toContainText("pdf-inspector");
  await expect(pdfModal.locator(".preview-metadata")).toContainText("页");
  await expect(pdfModal.locator(".document-reading")).not.toBeEmpty();
  await page.keyboard.press("Escape");

  expect(transferRequests).toHaveLength(3);
  expect(transferRequests.every((item) => item.startsWith("201 "))).toBeTruthy();
  expect(consoleErrors).toEqual([]);
  expect(failedResponses).toEqual([]);
});
