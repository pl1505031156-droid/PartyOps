export default async (page) => {
  const base = "http://127.0.0.1:18765";
  const findings = [];
  const checked = [];
  const consoleErrors = [];

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(`页面异常：${error.message}`));

  async function api(path, options = {}) {
    return page.evaluate(
      async ({ path, options }) => {
        const response = await fetch(`/api/v1${path}`, {
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
          },
          ...options,
        });
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(`${response.status} ${path}: ${JSON.stringify(body)}`);
        }
        return body;
      },
      { path, options },
    );
  }

  async function ensureFixtures() {
    const me = await api("/auth/me");
    const users = await api("/users");
    const tasks = await api("/tasks?page_size=100");
    let project = tasks.items.find((item) => item.title === "界面回归专项任务");
    if (!project) {
      project = await api("/tasks", {
        method: "POST",
        body: JSON.stringify({
          title: "界面回归专项任务",
          description: "仅用于隔离测试数据中的条件弹窗回归。",
          task_type: "project",
          sensitivity: "normal",
          priority: "normal",
          source: "界面自动化回归",
          source_kind: "manual",
          owner_id: me.id,
          reviewer_id: users.find((item) => item.id !== me.id)?.id || null,
          collaborator_ids: [],
          steps: [{ title: "核对弹窗显示" }],
          materials: [{ category: "final", name: "界面回归材料", required: true }],
        }),
      });
    }

    let reports = await api("/period-reports?limit=200");
    let report = reports.find((item) => item.title === "界面回归周报");
    if (!report) {
      report = await api("/period-reports", {
        method: "POST",
        body: JSON.stringify({
          period_type: "week",
          anchor_at: "2026-07-29T00:00:00Z",
          title: "界面回归周报",
          auto_fill: false,
        }),
      });
    }

    const categories = await api("/archives/categories");
    const category = categories.find((item) => item.code === "other_important") || categories[0];
    let records = await api(`/archives/records?archive_year=2026&category_id=${category.id}&limit=500`);
    let archive = records.find((item) => item.title === "界面回归重要档案");
    if (!archive) {
      archive = await api("/archives/records", {
        method: "POST",
        body: JSON.stringify({
          category_id: category.id,
          archive_year: 2026,
          title: "界面回归重要档案",
          summary: "仅用于隔离测试数据中的条件弹窗回归。",
          involved_persons: [],
          tags: ["界面回归"],
          custom_fields: {},
        }),
      });
    }
    return { project, report, archive, category };
  }

  async function inspectModal(label, viewport) {
    await page.waitForTimeout(240);
    const modal = page.locator(".arco-modal:visible").last();
    if (!(await modal.count())) {
      findings.push({ label, viewport, issue: "操作后未出现居中弹窗" });
      return;
    }
    let box = await modal.boundingBox();
    if (!box) {
      await page.waitForTimeout(180);
      box = await modal.boundingBox();
    }
    const size = page.viewportSize();
    if (!box || !size) {
      findings.push({ label, viewport, issue: "无法取得弹窗尺寸" });
      return;
    }
    const centerX = Math.abs(box.x + box.width / 2 - size.width / 2);
    const centerY = Math.abs(box.y + box.height / 2 - size.height / 2);
    const body = modal.locator(".arco-modal-body");
    const bodyMetrics = await body.count()
      ? await body.evaluate((element) => ({
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth,
          scrollHeight: element.scrollHeight,
          clientHeight: element.clientHeight,
          overflowY: getComputedStyle(element).overflowY,
        }))
      : null;
    const footer = modal.locator(".arco-modal-footer");
    const footerBox = await footer.count() ? await footer.boundingBox() : null;
    checked.push({
      label,
      viewport,
      box,
      centerX: Math.round(centerX),
      centerY: Math.round(centerY),
      bodyMetrics,
      footerVisible: !footerBox || footerBox.y + footerBox.height <= size.height + 1,
    });
    if (
      box.x < -1
      || box.y < -1
      || box.x + box.width > size.width + 1
      || box.y + box.height > size.height + 1
    ) {
      findings.push({ label, viewport, issue: "弹窗超出可视区域", box });
    }
    if (centerX > 10 || centerY > 40) {
      findings.push({ label, viewport, issue: "弹窗未居中", centerX, centerY });
    }
    if (footerBox && footerBox.y + footerBox.height > size.height + 1) {
      findings.push({ label, viewport, issue: "弹窗操作按钮不可见" });
    }
    if (bodyMetrics && bodyMetrics.scrollWidth > bodyMetrics.clientWidth + 4) {
      findings.push({ label, viewport, issue: "弹窗正文横向溢出", bodyMetrics });
    }
    if (
      bodyMetrics
      && bodyMetrics.scrollHeight > bodyMetrics.clientHeight + 4
      && !["auto", "scroll"].includes(bodyMetrics.overflowY)
    ) {
      findings.push({ label, viewport, issue: "长弹窗正文无法滚动", bodyMetrics });
    }
  }

  async function openAndCheck(buttonName, label, viewport, exact = false) {
    const button = page.getByRole("button", { name: buttonName, exact }).first();
    if (!(await button.count()) || !(await button.isVisible()) || !(await button.isEnabled())) {
      findings.push({ label, viewport, issue: `未找到可用入口：${buttonName}` });
      return;
    }
    await button.evaluate((element) => element.scrollIntoView({ block: "center", inline: "center" }));
    await button.evaluate((element) => element.click());
    await inspectModal(label, viewport);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(100);
  }

  async function chooseTab(name) {
    const tab = page.locator(".arco-tabs-tab", { hasText: name }).first();
    if (!(await tab.count())) {
      throw new Error(`未找到页签：${name}`);
    }
    await tab.evaluate((element) => element.click());
    await page.waitForTimeout(120);
  }

  const fixtures = await ensureFixtures();
  for (const viewport of [
    { width: 1366, height: 768, name: "1366x768" },
    { width: 1440, height: 1024, name: "1440x1024" },
  ]) {
    await page.setViewportSize(viewport);

    await page.goto(`${base}/settings`);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(400);
    await chooseTab("用户与权限");
    await openAndCheck("新增用户", "设置/新增用户", viewport.name);
    await openAndCheck("权限与密码", "设置/用户权限与密码", viewport.name);
    await chooseTab("备份与灾备");
    await openAndCheck("配置协同终端", "设置/协同终端配对", viewport.name);

    await page.goto(`${base}/tasks/${fixtures.project.id}`);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(400);
    await openAndCheck("编辑", "事项/编辑事项", viewport.name, true);
    await openAndCheck("等待反馈", "事项/确认办理动作", viewport.name);
    await chooseTab("项目子任务");
    await openAndCheck("添加子任务", "事项/添加项目子任务", viewport.name);
    await chooseTab("办理清单");
    await openAndCheck("添加步骤", "事项/添加办理步骤", viewport.name);
    await chooseTab("一事一档");
    await openAndCheck("添加材料项", "事项/添加材料项", viewport.name);
    await openAndCheck("上传版本", "事项/上传材料版本", viewport.name);
    await openAndCheck("不适用", "事项/材料标记不适用", viewport.name);
    await chooseTab("协同分工");
    await openAndCheck("添加协办人", "事项/添加协办人", viewport.name);

    await page.goto(`${base}/reports`);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(400);
    await openAndCheck("添加条目", "周期报告/补充报告条目", viewport.name);

    await page.goto(`${base}/archives?record=${fixtures.archive.id}`);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(400);
    await openAndCheck("编辑", "重要档案/编辑档案", viewport.name, true);
    await openAndCheck("历史", "重要档案/修订历史", viewport.name, true);
    await openAndCheck("作废", "重要档案/作废档案", viewport.name, true);
  }

  return { checked, findings, consoleErrors };
}
