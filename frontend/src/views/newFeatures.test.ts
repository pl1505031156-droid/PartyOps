import { flushPromises, shallowMount } from "@vue/test-utils";
import ArcoVue from "@arco-design/web-vue";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn(),
}));
const saveBlobDownload = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({ api: apiMocks, saveBlobDownload }));

import MemoView from "./MemoView.vue";
import PartyDevelopmentView from "./PartyDevelopmentView.vue";
import PartyDevelopmentSettingsView from "./PartyDevelopmentSettingsView.vue";
import OfficialFormatView from "./OfficialFormatView.vue";
import PartyDevelopmentMaterialsView from "./PartyDevelopmentMaterialsView.vue";
import { useSessionStore } from "../stores/session";
import { encryptMemoBackup } from "../localMemo";

const now = "2026-08-11T08:00:00Z";
const rule = {
  version: "2026.05", published_at: "2026-05-18", title: "中国共产党发展党员工作细则（2026年5月修订）",
  source_url: "https://www.12371.cn/2026/05/18/ARTI1779102179030620.shtml", principles: [],
  phase_labels: { application: "申请入党", activist: "培养考察" },
};
const result = {
  name: "张三", application_date: "2026-05-20", rule_version: "2026.05", rule_published_at: "2026-05-18",
  rule_title: rule.title, source_url: rule.source_url, generated_at: now, provisional: true,
  warnings: [{ code: "WORK_CALENDAR_INCOMPLETE", level: "medium", message: "工作日暂算" }],
  manual_confirmation_items: ["确定入党积极分子"],
  nodes: [{
    key: "application", title: "提交入党申请书", phase: "application", date_kind: "actual", date: "2026-05-20",
    end_date: null, provisional: false, status: "completed", article: "第六条", basis: "自愿申请",
    actual_at: "2026-05-20", legal_earliest_at: null, legal_deadline_at: null, reference_at: null,
    reference_end_at: null, adjusted_at: null, rule_version: "2026.05", reference_basis: "", is_reference: false,
    requires_manual_confirmation: false, materials: [{ phase: "application", name: "入党申请书", responsible_party: "申请人", guidance: "", required: true, national: true, source: rule.title }],
  }],
};
const profile = {
  id: "profile-1", name: "基层材料", description: "只追加材料", source_label: "单位制度", active: false, version: 1,
  created_by: "user-1", created_at: now, updated_at: now,
  items: [{ id: "item-1", profile_id: "profile-1", phase: "activist", name: "三考材料", responsible_party: "党支部", guidance: "待确认", required: false, enabled: true, sort_order: 10, version: 1, created_by: "user-1", created_at: now, updated_at: now }],
};

async function mount(component: typeof MemoView, path: string) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const session = useSessionStore();
  session.user = { id: "user-1", username: "admin", display_name: "管理员", role: "admin", active: true, version: 1, created_at: now };
  session.runtimeContext = { node_mode: "client", platform: "windows", user_role: "admin", device_id: "device-new-features", device_name: "测试协同机", capabilities: ["admin.access"] };
  session.ready = true;
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/:pathMatch(.*)*", component: { template: "<div />" } }] });
  await router.push(path);
  await router.isReady();
  const wrapper = shallowMount(component, { global: { plugins: [pinia, router, ArcoVue] } });
  await flushPromises();
  return wrapper;
}

function state(wrapper: Awaited<ReturnType<typeof mount>>) {
  return (wrapper.vm as unknown as { $: { setupState: Record<string, any> } }).$.setupState;
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  apiMocks.get.mockImplementation(async (path: string) => {
    if (path === "/party-development/rules/current") return rule;
    if (path === "/admin/party-development/profiles") return [profile];
    return [];
  });
  apiMocks.post.mockImplementation(async (path: string) => path.endsWith("export.docx") ? new Blob(["docx"]) : result);
  apiMocks.patch.mockImplementation(async () => ({ ...profile, version: 2 }));
  apiMocks.put.mockImplementation(async () => ({ ...profile, version: 3 }));
  apiMocks.delete.mockResolvedValue(undefined);
});

describe("1.4.3 新增页面", () => {
  it("公文排版页在本页兑换设备票据并完成本机诊断", async () => {
    apiMocks.post.mockResolvedValueOnce({
      ticket: "signed-ticket",
      expires_at: now,
      local_base_url: "http://127.0.0.1:18768",
    });
    const fetchMock = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ service: "official-format", status: "ready" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: "session-1", session_token: "local-token", expires_in_seconds: 900 }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        converted: false,
        document_id: "document-1",
        report: { compliant: false, paragraph_count: 3, table_count: 1, changed_count: 0, issues: [{ code: "TITLE_REVIEW", severity: "warning", title: "标题需复核", detail: "请确认标题层级", clause: "7.3" }] },
      }), { status: 200 }));
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);
    vm.selectedFile = new File(["docx"], "基层通知.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    await vm.diagnose();
    await flushPromises();
    expect(apiMocks.post).toHaveBeenCalledWith("/official-format/local-ticket", { origin: window.location.origin });
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "http://127.0.0.1:18768/health",
      "http://127.0.0.1:18768/v1/sessions",
      "http://127.0.0.1:18768/v1/sessions/session-1/diagnose",
    ]);
    expect(vm.stage).toBe("diagnosed");
    expect(wrapper.text()).toContain("标题需复核");
    expect(wrapper.text()).toContain("不得使用 PartyOps 处理涉密文件");
    expect(wrapper.text()).toContain("服务端存储");
    expect(wrapper.text()).not.toContain("启动本机排版助手");
    wrapper.unmount();
    fetchMock.mockRestore();
  });

  it("公文排版本机服务未启动时显示精确诊断而不是静默无反应", async () => {
    apiMocks.post.mockResolvedValueOnce({
      ticket: "signed-ticket",
      expires_at: now,
      local_base_url: "http://127.0.0.1:18768",
    });
    const fetchMock = vi.spyOn(window, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);
    vm.selectedFile = new File(["docx"], "基层通知.docx");
    await vm.diagnose();
    await flushPromises();
    expect(vm.errorCode).toBe("LOCAL_HELPER_UNREACHABLE");
    expect(wrapper.text()).toContain("公文排版服务未启动");
    expect(wrapper.text()).toContain("official-format.log");
    wrapper.unmount();
    fetchMock.mockRestore();
  });

  it("公文排版覆盖文件边界、异常响应、排版复检、导出与本机清理", async () => {
    apiMocks.post.mockResolvedValue({
      ticket: "signed-ticket",
      expires_at: now,
      local_base_url: "http://127.0.0.1:18768",
    });
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);

    await vm.diagnose();
    vm.selectedFile = new File([new Uint8Array(51 * 1024 * 1024)], "过大.docx");
    await vm.diagnose();
    expect(vm.errorCode).toBe("FILE_SIZE_LIMIT");
    vm.showFailure(null);
    expect(vm.errorDetail).toContain("本机排版未完成");

    const click = vi.fn();
    vm.fileInput = { click };
    vm.chooseFile();
    expect(click).toHaveBeenCalledOnce();
    await vm.onFileChange({ target: { files: [], value: "x" } } as unknown as Event);

    vm.localBaseUrl = "http://127.0.0.1:18768";
    vm.localSessionId = "old-session";
    vm.localSessionToken = "old-token";
    let fetchMock = vi.spyOn(window, "fetch").mockResolvedValueOnce(new Response(null, { status: 204 }));
    await vm.onFileChange({ target: { files: [new File(["docx"], ".docx")], value: "x" } } as unknown as Event);
    expect(vm.safeOutputName).toBe("公文-公文规范版.docx");
    fetchMock.mockRestore();

    vm.selectedFile = new File(["docx"], "基层通知.docx");
    fetchMock = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(new Response("unhealthy", { status: 503 }));
    await vm.diagnose();
    expect(vm.errorCode).toBe("LOCAL_HELPER_HEALTH_FAILED");
    fetchMock.mockRestore();

    fetchMock = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockResolvedValueOnce(new Response("not-json", { status: 500 }));
    await vm.diagnose();
    expect(vm.errorCode).toBe("LOCAL_HELPER_RESPONSE_INVALID");
    fetchMock.mockRestore();
    await expect(vm.parseLocalResponse(new Response(JSON.stringify({ title: "明确失败" }), { status: 400 }))).rejects.toMatchObject({
      code: "LOCAL_FORMAT_FAILED",
      message: "明确失败",
    });

    fetchMock = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(new Response("{}", { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: "session-2", session_token: "token-2", expires_in_seconds: 900 }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        converted: true,
        document_id: "document-2",
        report: { compliant: true, paragraph_count: 0, table_count: 0, changed_count: 0, issues: [] },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        document_id: "document-2",
        report: { compliant: false, paragraph_count: 4, table_count: 1, changed_count: 3, issues: [{ code: "FONT_MISSING", severity: "error", title: "字体缺失", detail: "请安装方正小标宋", clause: "5.2.2" }] },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response("formatted-docx", { status: 200 }));
    await vm.diagnose();
    await flushPromises();
    expect(wrapper.text()).toContain("原文件已由本机办公套件转换为 DOCX");
    expect(wrapper.text()).toContain("未发现阻断性版式问题");
    await vm.formatDocument();
    await flushPromises();
    expect(wrapper.text()).toContain("字体缺失");
    expect(wrapper.text()).toContain("仍有需要人工处理的阻断项");
    await vm.downloadResult();
    expect(saveBlobDownload).toHaveBeenCalledWith(expect.any(Blob), "基层通知-公文规范版.docx");
    fetchMock.mockRestore();

    await vm.formatDocument();
    await vm.downloadResult();
    vm.localBaseUrl = "http://127.0.0.1:18768";
    vm.localSessionId = "session-errors";
    vm.localSessionToken = "token-errors";
    vm.documentId = "document-errors";
    fetchMock = vi.spyOn(window, "fetch")
      .mockRejectedValueOnce(new TypeError("format offline"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: "DOWNLOAD_DENIED", detail: "导出授权失效" }), { status: 403 }));
    await vm.formatDocument();
    expect(vm.errorCode).toBe("LOCAL_FORMAT_FAILED");
    await vm.downloadResult();
    expect(vm.errorCode).toBe("DOWNLOAD_DENIED");
    fetchMock.mockRestore();
    vm.localBaseUrl = "http://127.0.0.1:18768";
    vm.localSessionId = "session-cleanup";
    vm.localSessionToken = "token-cleanup";
    fetchMock = vi.spyOn(window, "fetch").mockRejectedValueOnce(new TypeError("closed"));
    await vm.cleanupLocalSession();
    expect(vm.localSessionId).toBe("");
    fetchMock.mockRestore();
    await vm.reset();
    expect(vm.stage).toBe("select");
    wrapper.unmount();
  });

  it("发展党员材料页区分标准异常与非标准异常", async () => {
    apiMocks.get.mockRejectedValueOnce(new Error("材料服务离线"));
    const wrapper = await mount(PartyDevelopmentMaterialsView as typeof MemoView, "/party-development/materials");
    const vm = state(wrapper);
    expect(vm.loading).toBe(false);
    apiMocks.get.mockRejectedValueOnce("非标准错误");
    await vm.load();
    expect(vm.loading).toBe(false);
    apiMocks.get.mockResolvedValueOnce({
      rule: { version: "2026.05", title: rule.title, source_url: rule.source_url },
      disclaimer: "测试材料边界",
      phases: [
        { phase: "application", label: "申请入党", items: [] },
        { phase: "activist", label: "积极分子", items: [{ name: "单位补充材料", source: "测试党委", responsible_party: "支部", guidance: "按需", required: false, national: false }] },
      ],
    });
    await vm.load();
    await flushPromises();
    expect(wrapper.text()).toContain("单位补充 · 测试党委");
    expect(wrapper.text()).toContain("本阶段暂无固定材料");
    wrapper.unmount();
  });

  it("备忘录在本机完成新建、清单、删除、撤销和回收站操作", async () => {
    const wrapper = await mount(MemoView, "/memos");
    const vm = state(wrapper);
    await vm.newMemo("checklist");
    expect(vm.draft.kind).toBe("checklist");
    vm.draft.title = "只在本机的清单";
    vm.addChecklistItem();
    vm.draft.checklist[0].text = "记录零碎事项";
    vm.draft.checklist[0].done = true;
    vm.tagText = "党建、今日";
    await vm.persistDraft();
    expect(vm.memos.some((item: { title: string }) => item.title === "只在本机的清单")).toBe(true);
    await vm.softDelete();
    expect(vm.lastDeletedId).not.toBe("");
    await vm.undoDelete();
    expect(vm.draft.deletedAt).toBeNull();
    await vm.softDelete();
    vm.showingTrash = true;
    await flushPromises();
    const deleted = vm.memos.find((item: { deletedAt: string | null }) => item.deletedAt);
    vm.selectMemo(deleted);
    await vm.permanentlyDelete();
    expect(vm.memos.some((item: { id: string }) => item.id === deleted.id)).toBe(false);
    await vm.persistDraft();
    wrapper.unmount();
  });

  it("备忘录完成加密导出导入并覆盖错误密码、超大文件与模式切换", async () => {
    const wrapper = await mount(MemoView, "/memos");
    const vm = state(wrapper);
    await vm.newMemo("note");
    vm.draft.title = "加密迁移测试";
    await vm.persistDraft();
    vm.changeMemoKind("checklist");
    expect(vm.draft.kind).toBe("checklist");
    vm.changeMemoKind("checklist");
    vm.removeChecklistItem(0);
    vm.openExport();
    expect(vm.backupVisible).toBe(true);
    vm.backupPassword = "password-123";
    vm.backupPasswordConfirm = "not-matched";
    expect(await vm.confirmBackup()).toBe(false);
    vm.backupPasswordConfirm = "password-123";
    expect(await vm.confirmBackup()).toBe(true);

    const encoded = await encryptMemoBackup(vm.scope, vm.memos, "password-123");
    await vm.readImportFile({ target: { files: [], value: "x" } } as unknown as Event);
    await vm.readImportFile({ target: { files: [{ size: 51 * 1024 * 1024 }], value: "x" } } as unknown as Event);
    await vm.readImportFile({ target: { files: [{ size: encoded.length, text: async () => encoded }], value: "x" } } as unknown as Event);
    expect(vm.backupMode).toBe("import");
    vm.backupPassword = "wrong-password";
    expect(await vm.confirmBackup()).toBe(false);
    vm.backupPassword = "password-123";
    vm.importPolicy = "copy";
    expect(await vm.confirmBackup()).toBe(true);
    vm.openImportPicker();
    wrapper.unmount();
  });

  it("计算器保存本机草稿、补录真实节点、计算风险并导出 Word", async () => {
    const wrapper = await mount(PartyDevelopmentView as typeof MemoView, "/party-development");
    const vm = state(wrapper);
    vm.form.name = "张三";
    vm.form.application_date = "2026-05-20";
    vm.form.actual_dates.activist_date = "2026-06-01";
    vm.form.actual_dates.training_days = 3;
    await vm.calculate();
    expect(apiMocks.post).toHaveBeenCalledWith("/party-development/calculate", expect.objectContaining({ name: "张三" }));
    expect(vm.result.rule_version).toBe("2026.05");
    expect(vm.nodeDate(result.nodes[0])).toBe("实际：2026-05-20");
    expect(vm.nodeDate({ ...result.nodes[0], date: null, actual_at: null, reference_at: "2026-11-20", is_reference: true })).toContain("参考计划：2026-11-20");
    expect(vm.nodeDate({ ...result.nodes[0], end_date: "2026-05-25", provisional: true })).toContain("暂算");
    expect(vm.dateKindLabel("deadline")).toBe("法定截止");
    expect(vm.dateKindLabel("unknown")).toBe("unknown");
    vm.saveDraft();
    expect(localStorage.length).toBe(1);
    await vm.exportWord();
    expect(apiMocks.post).toHaveBeenCalledWith("/party-development/export.docx", expect.any(Object));
    expect(saveBlobDownload).toHaveBeenCalledWith(expect.any(Blob), "张三-党员发展时间节点.docx");
    vm.clearDraft();
    expect(vm.form.name).toBe("");
    wrapper.unmount();
  });

  it("计算器对损坏本机草稿、存储失败和接口失败给出明确降级", async () => {
    localStorage.setItem("partyops.party-development.draft.v1:user-1:device-new-features", "not-json");
    apiMocks.get.mockRejectedValueOnce(new Error("规则读取失败"));
    const wrapper = await mount(PartyDevelopmentView as typeof MemoView, "/party-development");
    const vm = state(wrapper);
    expect(vm.localWarning).toContain("草稿已损坏");
    await vm.calculate();
    expect(apiMocks.post).not.toHaveBeenCalled();
    vm.form.name = "失败测试";
    vm.form.application_date = "2026-05-20";
    apiMocks.post.mockRejectedValueOnce(new Error("计算接口失败"));
    await vm.calculate();
    expect(vm.result).toBeNull();
    apiMocks.post.mockResolvedValueOnce(result);
    await vm.calculate();
    apiMocks.post.mockRejectedValueOnce(new Error("导出失败"));
    await vm.exportWord();
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new DOMException("full", "QuotaExceededError"); });
    vm.saveDraft();
    expect(vm.localWarning).toContain("无法保存本机草稿");
    setItem.mockRestore();
    wrapper.unmount();
  });

  it("管理员补充材料页保持国家规则锁定并完成增删改停用", async () => {
    const wrapper = await mount(PartyDevelopmentSettingsView as typeof MemoView, "/party-development-settings");
    const vm = state(wrapper);
    expect(wrapper.text()).toContain("管理员不能删除、改写或缩短国家规则");
    vm.openProfile(profile);
    vm.form.description = "更新后的说明";
    expect(await vm.saveProfile()).toBe(true);
    expect(apiMocks.patch).toHaveBeenCalledWith(expect.stringContaining(profile.id), expect.any(Object), { "If-Match": "1" });
    expect(apiMocks.put).toHaveBeenCalled();
    await vm.toggle(profile);
    expect(apiMocks.patch).toHaveBeenCalledWith(expect.stringContaining(profile.id), { active: true }, { "If-Match": "1" });
    await vm.remove(profile);
    expect(apiMocks.delete).toHaveBeenCalledWith(expect.stringContaining(profile.id), { "If-Match": "1" });
    vm.openProfile();
    vm.form.name = "新模板";
    vm.form.materials_text = "activist|思想汇报|本人|可选|单位补充";
    expect(await vm.saveProfile()).toBe(true);
    expect(apiMocks.post).toHaveBeenCalledWith("/admin/party-development/profiles", expect.objectContaining({ name: "新模板" }));
    vm.form.materials_text = "invalid|错误材料";
    expect(await vm.saveProfile()).toBe(false);
    vm.form.materials_text = "";
    vm.form.name = "";
    expect(await vm.saveProfile()).toBe(false);
    wrapper.unmount();
  });

  it("管理员补充材料接口失败时保持页面可操作且不伪造成功", async () => {
    apiMocks.get.mockRejectedValue(new Error("加载失败"));
    const wrapper = await mount(PartyDevelopmentSettingsView as typeof MemoView, "/party-development-settings");
    const vm = state(wrapper);
    expect(vm.loading).toBe(false);
    apiMocks.patch.mockRejectedValueOnce(new Error("停用失败"));
    await vm.toggle({ ...profile, active: true });
    apiMocks.delete.mockRejectedValueOnce(new Error("删除失败"));
    await vm.remove(profile);
    vm.openProfile(profile);
    apiMocks.patch.mockRejectedValueOnce(new Error("保存失败"));
    expect(await vm.saveProfile()).toBe(false);
    wrapper.unmount();
  });

  it("发展党员计算器覆盖草稿恢复、全部日期表达和导出安全回退", async () => {
    localStorage.setItem(
      "partyops.party-development.draft.v1:user-1:device-new-features",
      JSON.stringify({
        name: "李四",
        application_date: "2026-01-02",
        actual_dates: { conversation_date: "2026-01-20", training_hours: 24 },
      }),
    );
    apiMocks.get.mockRejectedValueOnce("规则服务暂不可用");
    const wrapper = await mount(PartyDevelopmentView as typeof MemoView, "/party-development");
    const vm = state(wrapper);
    expect(vm.form.name).toBe("李四");
    expect(vm.form.application_date).toBe("2026-01-02");
    expect(vm.form.actual_dates.conversation_date).toBe("2026-01-20");

    const blankNode = {
      ...result.nodes[0], actual_at: null, date: null, end_date: null, adjusted_at: null,
      reference_at: null, reference_end_at: null, provisional: false,
    };
    expect(vm.nodeDate({ ...blankNode, date: "2026-02-01", end_date: "2026-02-05", date_kind: "deadline" }))
      .toBe("法定：2026-02-01 至 2026-02-05");
    expect(vm.nodeDate({ ...blankNode, date: "2026-02-01", date_kind: "window" }))
      .toBe("建议：2026-02-01");
    expect(vm.nodeDate({ ...blankNode, adjusted_at: "2026-03-01" })).toBe("人工调整：2026-03-01");
    expect(vm.nodeDate({ ...blankNode, reference_at: "2026-04-01", reference_end_at: "2026-04-10" }))
      .toBe("参考计划：2026-04-01 至 2026-04-10");
    expect(vm.nodeDate(blankNode)).toBe("待组织确认");

    vm.result = null;
    vm.form.name = "李四";
    vm.form.application_date = "2026-01-02";
    apiMocks.post.mockRejectedValueOnce("计算失败");
    await vm.exportWord();
    expect(vm.result).toBeNull();

    vm.result = result;
    vm.form.name = "";
    apiMocks.post.mockResolvedValueOnce(new Blob(["docx"]));
    await vm.exportWord();
    expect(saveBlobDownload).toHaveBeenCalledWith(expect.any(Blob), "党员发展-党员发展时间节点.docx");

    vm.result = {
      ...result,
      provisional: false,
      warnings: [{ code: "OVERDUE", level: "high", message: "节点已逾期" }],
      nodes: [
        { ...result.nodes[0], key: "overdue", status: "overdue", phase: "unknown", date_kind: "deadline" },
        { ...result.nodes[0], key: "waiting", status: "waiting_manual", phase: "activist", date_kind: "manual", materials: [] },
        {
          ...result.nodes[0], key: "reference", status: "planned", phase: "activist", date_kind: "window",
          actual_at: null, date: null, reference_at: "2026-06-01", reference_basis: "单位参考间隔",
          materials: [{ ...result.nodes[0].materials[0], national: false, source: "单位制度" }],
        },
      ],
    };
    await flushPromises();
    expect(wrapper.text()).toContain("需要先核查的风险");
    expect(wrapper.text()).toContain("单位补充 · 单位制度");
    expect(wrapper.text()).toContain("unknown");
    wrapper.unmount();
  });

  it("单位材料模板覆盖可选项、缺省展示、停用成功与非标准错误", async () => {
    const wrapper = await mount(PartyDevelopmentSettingsView as typeof MemoView, "/party-development-settings");
    const vm = state(wrapper);
    expect(vm.materialsText(profile.items)).toContain("可选");
    vm.profiles = [{
      ...profile,
      active: true,
      description: "",
      items: [{ ...profile.items[0], id: "", phase: "custom", responsible_party: "" }],
    }];
    await flushPromises();
    expect(wrapper.text()).toContain("已启用");
    expect(wrapper.text()).toContain("尚未填写模板说明");
    expect(wrapper.text()).toContain("责任主体待确认");
    expect(wrapper.text()).toContain("custom");

    apiMocks.patch.mockResolvedValueOnce({ ...profile, active: false, version: 2 });
    await vm.toggle({ ...profile, active: true });
    apiMocks.patch.mockRejectedValueOnce("状态更新失败");
    await vm.toggle(profile);
    apiMocks.delete.mockRejectedValueOnce("删除失败");
    await vm.remove(profile);
    vm.openProfile(profile);
    apiMocks.patch.mockRejectedValueOnce("保存失败");
    expect(await vm.saveProfile()).toBe(false);
    wrapper.unmount();
  });
});
