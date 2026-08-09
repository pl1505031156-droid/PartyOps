async (page) => {
  const base = "http://127.0.0.1:18765";
  const routes = [
    "/",
    "/workbench",
    "/inbox",
    "/tasks",
    "/reports",
    "/workspace",
    "/archives",
    "/journal",
    "/templates",
    "/inspection",
    "/knowledge",
    "/assistant",
    "/settings",
    "/fleet",
    "/efficiency",
    "/help",
  ];
  const openers = {
    "/": ["快速新建事项", "打开 Ctrl+K 全局指令中心"],
    "/tasks": ["保存智能文件夹"],
    "/reports": ["建立周期报告", "添加条目"],
    "/workspace": ["纳管目录", "选择接入文件夹"],
    "/archives": ["新建档案", "管理档案类别"],
    "/journal": ["记录工作"],
    "/templates": ["新建周期规则", "新建模板"],
    "/knowledge": ["新增联系人", "新增知识"],
    "/fleet": ["新增协同电脑"],
    "/efficiency": ["新建专题", "新建规则", "添加节假日/调休", "新建模板"],
  };
  const findings = [];
  const routeResults = [];
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(`页面异常：${error.message}`));

  async function inspectModal(route, opener, viewport) {
    const modal = page.locator(".arco-modal:visible").last();
    if (!(await modal.count())) {
      findings.push({ route, opener, viewport, issue: "点击后未出现居中弹窗" });
      return;
    }
    const box = await modal.boundingBox();
    const size = page.viewportSize();
    if (!box || !size) {
      findings.push({ route, opener, viewport, issue: "无法取得弹窗尺寸" });
      return;
    }
    const horizontalCenterDelta = Math.abs(box.x + box.width / 2 - size.width / 2);
    const verticalCenterDelta = Math.abs(box.y + box.height / 2 - size.height / 2);
    if (
      box.x < -1
      || box.y < -1
      || box.x + box.width > size.width + 1
      || box.y + box.height > size.height + 1
    ) {
      findings.push({ route, opener, viewport, issue: "弹窗超出可视区域", box });
    }
    if (horizontalCenterDelta > 10 || verticalCenterDelta > 40) {
      findings.push({
        route,
        opener,
        viewport,
        issue: "弹窗未居中",
        horizontalCenterDelta,
        verticalCenterDelta,
      });
    }
    const footer = modal.locator(".arco-modal-footer");
    if (await footer.count()) {
      const footerBox = await footer.boundingBox();
      if (footerBox && footerBox.y + footerBox.height > size.height + 1) {
        findings.push({ route, opener, viewport, issue: "弹窗操作按钮不可见" });
      }
    }
    const body = modal.locator(".arco-modal-body");
    if (await body.count()) {
      const metrics = await body.evaluate((element) => ({
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        overflowY: getComputedStyle(element).overflowY,
      }));
      if (metrics.scrollWidth > metrics.clientWidth + 4) {
        findings.push({ route, opener, viewport, issue: "弹窗正文发生未预期横向溢出", metrics });
      }
      if (
        metrics.scrollHeight > metrics.clientHeight + 4
        && !["auto", "scroll"].includes(metrics.overflowY)
      ) {
        findings.push({ route, opener, viewport, issue: "长弹窗正文无法滚动", metrics });
      }
    }
  }

  for (const viewport of [
    { width: 1366, height: 768, name: "1366x768" },
    { width: 1440, height: 1024, name: "1440x1024" },
  ]) {
    await page.setViewportSize(viewport);
    for (const route of routes) {
      await page.goto(`${base}${route}`);
      await page.waitForLoadState("networkidle");
      const pageMetrics = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
        textLength: (document.querySelector("main")?.textContent || "").trim().length,
      }));
      routeResults.push({ route, viewport: viewport.name, ...pageMetrics });
      if (pageMetrics.width > pageMetrics.viewport + 4) {
        findings.push({ route, viewport: viewport.name, issue: "页面发生横向溢出", pageMetrics });
      }
      if (pageMetrics.textLength < 20) {
        findings.push({ route, viewport: viewport.name, issue: "页面主要内容疑似空白" });
      }
      for (const opener of openers[route] || []) {
        const button = page.getByRole("button", { name: opener, exact: false }).first();
        if (!(await button.count()) || !(await button.isVisible()) || !(await button.isEnabled())) {
          continue;
        }
        await button.evaluate((element) =>
          element.scrollIntoView({ block: "center", inline: "center" }),
        );
        await button.evaluate((element) => element.click());
        await page.waitForTimeout(150);
        await inspectModal(route, opener, viewport.name);
        await page.keyboard.press("Escape");
        await page.waitForTimeout(120);
      }
    }
  }

  return { routeResults, findings, consoleErrors };
}
