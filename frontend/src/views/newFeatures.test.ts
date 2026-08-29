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
  it("公文排版页以内嵌朱批案台载入六类二十五项能力", async () => {
    const catalog = {
      capability_count: 25,
      external_office_required: false,
      features: [
        { id: "format", display_name: "一键排版", notes: "排版", accepts: [".docx"], capabilities: Array.from({ length: 5 }, (_, index) => ({ capability_id: `format.${index}`, description: "能力" })) },
        { id: "replace", display_name: "一键替换", notes: "替换", accepts: [".docx"], capabilities: Array.from({ length: 5 }, (_, index) => ({ capability_id: `replace.${index}`, description: "能力" })) },
        { id: "redheader", display_name: "一键套红", notes: "套红", accepts: [".docx"], capabilities: Array.from({ length: 4 }, (_, index) => ({ capability_id: `redheader.${index}`, description: "能力" })) },
        { id: "rename", display_name: "一键命名", notes: "命名", accepts: [".docx"], capabilities: Array.from({ length: 3 }, (_, index) => ({ capability_id: `rename.${index}`, description: "能力" })) },
        { id: "convert", display_name: "一键转换", notes: "转换", accepts: [".docx", ".pdf"], capabilities: Array.from({ length: 4 }, (_, index) => ({ capability_id: `convert.${index}`, description: "能力" })) },
        { id: "pdf-to-word", display_name: "PDF 转 Word", notes: "重建", accepts: [".pdf"], capabilities: Array.from({ length: 4 }, (_, index) => ({ capability_id: `pdf.${index}`, description: "能力" })) },
      ],
    };
    apiMocks.post.mockResolvedValue({
      ticket: "signed-ticket",
      expires_at: now,
      local_base_url: "http://127.0.0.1:18768",
    });
    const fetchMock = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ service: "official-format", status: "ready" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: "selftest-session", session_token: "selftest-token", expires_in_seconds: 900 }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(catalog), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ feature_count: 6, capability_count: 25, external_office_required: false }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ service: "official-format", status: "ready" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ session_id: "session-1", session_token: "local-token", expires_in_seconds: 900 }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(catalog), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ document_id: "document-1" }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "job-1", feature_id: "format", state: "completed", progress: 100, message: "排版完成",
        items: [], outputs: [{ id: "output-1", document_id: "document-1", filename: "基层通知-排版后.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", downloaded: false }],
      }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "job-1", feature_id: "format", state: "completed", progress: 100, message: "排版完成",
        items: [{ document_id: "document-1", filename: "基层通知.docx", state: "completed", progress: 100, message: "完成", error_code: "", report: { compliant: true, paragraph_count: 3, table_count: 1, changed_count: 6, issues: [] } }],
        outputs: [{ id: "output-1", document_id: "document-1", filename: "基层通知-排版后.docx", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", downloaded: false }],
      }), { status: 200 }));
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);
    expect(vm.selfTestText).toBe("6 项功能 / 25 项能力已就绪");
    expect(wrapper.text()).toContain("朱批案台");

    vm.addFiles([new File(["docx"], "基层通知.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" })]);
    await vm.startJob();
    await flushPromises();
    expect(apiMocks.post).toHaveBeenCalledWith("/official-format/local-ticket", { origin: window.location.origin });
    expect(vm.activeJob.state).toBe("completed");
    expect(wrapper.text()).toContain("基层通知-排版后.docx");
    expect(wrapper.text()).toContain("全部过程仅在当前电脑");
    expect(wrapper.text()).not.toContain("启动本机排版助手");
    wrapper.unmount();
    fetchMock.mockRestore();
  });

  it("公文排版本机引擎未启动时显示精确诊断且不打开外部窗口", async () => {
    apiMocks.post.mockResolvedValueOnce({
      ticket: "signed-ticket",
      expires_at: now,
      local_base_url: "http://127.0.0.1:18768",
    });
    const fetchMock = vi.spyOn(window, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const openMock = vi.spyOn(window, "open").mockImplementation(() => null);
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);
    expect(vm.errorCode).toBe("LOCAL_HELPER_UNREACHABLE");
    expect(vm.selfTestText).toBe("内置引擎自检失败");
    expect(wrapper.text()).toContain("当前电脑的内置公文引擎未启动");
    expect(openMock).not.toHaveBeenCalled();
    wrapper.unmount();
    fetchMock.mockRestore();
    openMock.mockRestore();
  });

  it("公文排版覆盖批量边界、方案、异常响应、取消、导出与清理", async () => {
    apiMocks.post.mockRejectedValueOnce(new Error("ticket offline"));
    const fetchMock = vi.spyOn(window, "fetch");
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);

    vm.addFiles([]);
    vm.addFiles([new File([], "空.docx")]);
    vm.addFiles([new File(["pdf"], "错误.pdf")]);
    expect(vm.files).toHaveLength(0);
    vm.showFailure(null);
    expect(vm.errorDetail).toContain("本机处理未完成");

    const click = vi.fn();
    vm.fileInput = { click };
    vm.chooseFiles();
    expect(click).toHaveBeenCalledOnce();
    vm.onFileChange({ target: { files: [new File(["docx"], "材料.docx")], value: "x" } } as unknown as Event);
    expect(vm.files).toHaveLength(1);
    vm.onDrop({ dataTransfer: { files: [new File(["docx"], "第二份.docx")] } } as unknown as DragEvent);
    expect(vm.files).toHaveLength(2);
    vm.removeFile(1);
    expect(vm.files).toHaveLength(1);

    vm.options.plan_name = "";
    vm.saveReplacePlan();
    vm.options.plan_name = "机关名称替换";
    vm.options.rules = [{ mode: "text", find: "旧称", replace: "新称", case_sensitive: false }];
    vm.saveReplacePlan();
    expect(vm.savedReplacePlans).toHaveLength(1);
    vm.options.rules[0].replace = "另一个值";
    vm.loadReplacePlan("机关名称替换");
    expect(vm.options.rules[0].replace).toBe("新称");
    vm.deleteReplacePlan();
    expect(vm.savedReplacePlans).toHaveLength(0);
    vm.deleteReplacePlan();

    await expect(vm.parseLocalResponse(new Response("not-json", { status: 500 }))).rejects.toMatchObject({
      code: "LOCAL_HELPER_RESPONSE_INVALID",
    });
    await expect(vm.parseLocalResponse(new Response(JSON.stringify({ code: "DOWNLOAD_DENIED", detail: "导出授权失效" }), { status: 403 }))).rejects.toMatchObject({
      code: "DOWNLOAD_DENIED",
      message: "导出授权失效",
    });

    vm.localBaseUrl = "http://127.0.0.1:18768";
    vm.localSessionId = "session-download";
    vm.localSessionToken = "token-download";
    vm.activeJob = {
      id: "job-1", feature_id: "format", state: "completed", progress: 100, message: "完成", items: [],
      outputs: [{ id: "output-1", document_id: "document-1", filename: "结果.docx", content_type: "application/octet-stream", downloaded: false }],
    };
    fetchMock.mockResolvedValueOnce(new Response("docx", { status: 200 }));
    await vm.downloadOutput(vm.activeJob.outputs[0]);
    expect(saveBlobDownload).toHaveBeenCalledWith(expect.any(Blob), "结果.docx");

    vm.busy = true;
    vm.activeJob.state = "running";
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 202 }));
    await vm.cancelJob();
    expect(fetchMock.mock.calls.at(-1)?.[1]).toMatchObject({ method: "POST" });

    vm.localSessionId = "session-cleanup";
    vm.localSessionToken = "token-cleanup";
    fetchMock.mockRejectedValueOnce(new TypeError("closed"));
    await vm.cleanupLocalSession();
    expect(vm.localSessionId).toBe("");
    vm.busy = false;
    vm.clearFiles();
    expect(vm.files).toHaveLength(0);
    wrapper.unmount();
    fetchMock.mockRestore();
  });

  it("公文排版覆盖六类参数、目录保存、能力降级与五十文件边界", async () => {
    apiMocks.post.mockRejectedValueOnce(new Error("skip automatic self test"));
    const fetchMock = vi.spyOn(window, "fetch");
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);

    await vm.startJob();
    const optionSnapshots: Record<string, Record<string, unknown>> = {};
    for (const feature of ["format", "replace", "redheader", "rename", "convert", "pdf-to-word"]) {
      vm.selectedFeature = feature;
      await flushPromises();
      optionSnapshots[feature] = vm.requestOptions();
    }
    expect(optionSnapshots.format).toMatchObject({ template: "GB/T 9704-2012", scope: "full" });
    expect(optionSnapshots.replace.rules).toHaveLength(1);
    expect(optionSnapshots.redheader).toMatchObject({ document_type: "down", agency: "中共××委员会" });
    expect(optionSnapshots.rename.parts).toEqual(["title", "document_number"]);
    expect(optionSnapshots.convert).toMatchObject({ target_format: "pdf", page_selection: "all", dpi: 200 });
    expect(optionSnapshots["pdf-to-word"]).toMatchObject({ normalize_punctuation: true, reconstruct_tables: true });

    const write = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn().mockResolvedValue(undefined);
    const getFileHandle = vi.fn().mockResolvedValue({ createWritable: vi.fn().mockResolvedValue({ write, close }) });
    const picker = vi.fn().mockResolvedValue({ name: "公文输出", getFileHandle });
    Object.defineProperty(window, "showDirectoryPicker", { configurable: true, value: picker });
    await vm.selectOutputDirectory();
    expect(vm.outputLocationLabel).toBe("公文输出");

    vm.localBaseUrl = "http://127.0.0.1:18768";
    vm.localSessionId = "session-directory";
    vm.localSessionToken = "token-directory";
    vm.activeJob = {
      id: "job-directory", feature_id: "format", state: "completed", progress: 100, message: "完成", items: [],
      outputs: [{ id: "output-directory", document_id: "document-directory", filename: "目录结果.docx", content_type: "application/octet-stream", downloaded: false }],
    };
    fetchMock.mockResolvedValueOnce(new Response("docx", { status: 200 }));
    await vm.downloadOutput(vm.activeJob.outputs[0]);
    expect(getFileHandle).toHaveBeenCalledWith("目录结果.docx", { create: true });
    expect(write).toHaveBeenCalledWith(expect.any(Blob));
    expect(close).toHaveBeenCalledOnce();

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ feature_count: 5, capability_count: 24, external_office_required: true }), { status: 200 }));
    await vm.runSelfTest();
    expect(vm.selfTestText).toContain("能力清单不完整");

    vm.clearFiles();
    vm.selectedFeature = "format";
    const batch = Array.from({ length: 51 }, (_, index) => new File(["x"], `批量-${index}.docx`));
    vm.addFiles(batch);
    expect(vm.files).toHaveLength(50);
    vm.busy = true;
    vm.removeFile(0);
    vm.clearFiles();
    expect(vm.files).toHaveLength(50);
    vm.busy = false;
    vm.clearFiles();

    while (vm.options.rules.length < 100) vm.addReplaceRule();
    vm.addReplaceRule();
    expect(vm.options.rules).toHaveLength(100);

    wrapper.unmount();
    fetchMock.mockRestore();
    Reflect.deleteProperty(window, "showDirectoryPicker");
  });

  it("公文排版覆盖本机协议异常、自愈方案与非主路径状态", async () => {
    apiMocks.post.mockRejectedValueOnce(new Error("skip automatic self test"));
    const fetchMock = vi.spyOn(window, "fetch");
    const wrapper = await mount(OfficialFormatView as typeof MemoView, "/official-format");
    const vm = state(wrapper);

    const originalFeatures = vm.features;
    vm.selectedFeature = "missing-feature";
    expect(vm.currentFeature.id).toBe("format");
    vm.features = [{ id: "format", display_name: "排版", notes: "", accepts: [".docx"] }];
    expect(vm.currentCapabilities).toEqual([]);
    vm.features = originalFeatures;
    vm.selectedFeature = "format";
    expect(vm.fileExtension("README")).toBe("");

    localStorage.setItem("partyops.document-formatter.replace-plans.v1", "{broken");
    vm.loadSavedReplacePlans();
    expect(vm.savedReplacePlans).toEqual([]);
    localStorage.setItem("partyops.document-formatter.replace-plans.v1", JSON.stringify([null, { name: 2 }, { name: "有效", rules: [] }]));
    vm.loadSavedReplacePlans();
    expect(vm.savedReplacePlans).toHaveLength(1);
    const storageFailure = vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new Error("quota"); });
    vm.persistReplacePlans();
    storageFailure.mockRestore();

    vm.options.plan_name = "有效";
    vm.options.rules = [{ mode: "text", find: "甲", replace: "乙", case_sensitive: false }];
    vm.saveReplacePlan();
    expect(vm.savedReplacePlans[0].rules[0].replace).toBe("乙");
    vm.savedReplacePlans = Array.from({ length: 30 }, (_, index) => ({ name: `方案${index}`, rules: [] }));
    vm.options.plan_name = "第三十一套";
    vm.saveReplacePlan();
    expect(vm.savedReplacePlans).toHaveLength(30);
    vm.loadReplacePlan("不存在");

    vm.files = [];
    vm.addFiles([new File(["x"], "无后缀")]);
    vm.onFileChange({ target: { files: null, value: "x" } } as unknown as Event);
    vm.onDrop({} as DragEvent);
    expect(vm.files).toHaveLength(0);

    await expect(vm.parseLocalResponse(new Response(JSON.stringify({ title: "标题错误" }), { status: 422 }))).rejects.toMatchObject({
      code: "LOCAL_FORMAT_FAILED", message: "标题错误",
    });
    await expect(vm.parseLocalResponse(new Response(JSON.stringify({}), { status: 422 }))).rejects.toMatchObject({
      code: "LOCAL_FORMAT_FAILED", message: "本机处理未完成",
    });

    apiMocks.post.mockResolvedValueOnce({ ticket: "bad-health", expires_at: now, local_base_url: "http://127.0.0.1:18768" });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await expect(vm.ensureLocalSession()).rejects.toMatchObject({ code: "LOCAL_HELPER_HEALTH_FAILED" });

    Reflect.deleteProperty(window, "showDirectoryPicker");
    await vm.selectOutputDirectory();
    Object.defineProperty(window, "showDirectoryPicker", { configurable: true, value: vi.fn().mockRejectedValueOnce({ name: "AbortError" }).mockRejectedValueOnce(new Error("denied")) });
    await vm.selectOutputDirectory();
    await vm.selectOutputDirectory();

    vm.features = [{ id: "format", display_name: "排版", notes: "", accepts: [".docx"], capabilities: [] }];
    vm.selectedFeature = "format";
    vm.addFiles([new File(["x"], "待处理.docx")]);
    apiMocks.post.mockRejectedValueOnce(new Error("ticket unavailable"));
    await vm.startJob();
    expect(vm.busy).toBe(false);

    vm.activeJob = null;
    await vm.pollJob();
    vm.localSessionId = "state-session";
    vm.localSessionToken = "state-token";
    vm.activeJob = { id: "state-job", feature_id: "format", state: "running", progress: 30, message: "处理中", items: [], outputs: [] };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...vm.activeJob, state: "running", progress: 50 }), { status: 200 }));
    await vm.pollJob();
    window.clearTimeout(vm.pollTimer);
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...vm.activeJob, state: "completed_with_errors", progress: 100, message: "部分失败" }), { status: 200 }));
    await vm.pollJob();
    fetchMock.mockRejectedValueOnce(new Error("poll failed"));
    await vm.pollJob();

    vm.activeJob = null;
    vm.busy = false;
    await vm.cancelJob();
    vm.activeJob = { id: "cancel-job", feature_id: "format", state: "running", progress: 50, message: "处理中", items: [], outputs: [] };
    vm.busy = true;
    fetchMock.mockRejectedValueOnce(new Error("cancel failed"));
    await vm.cancelJob();
    vm.busy = false;

    vm.activeJob = null;
    await vm.downloadOutput({ id: "none" });
    vm.activeJob = { id: "download-job", feature_id: "format", state: "completed", progress: 100, message: "完成", items: [], outputs: [] };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: "OUTPUT_DENIED", detail: "输出已失效" }), { status: 410 }));
    await vm.downloadOutput({ id: "expired", filename: "过期.docx" });
    expect(vm.errorCode).toBe("OUTPUT_DENIED");

    vm.files = [{ key: "pdf", file: new File(["pdf"], "材料.pdf"), documentId: "", state: "ready", progress: 0, message: "等待" }];
    vm.selectedFeature = "replace";
    await flushPromises();
    vm.busy = true;
    vm.selectedFeature = "format";
    await flushPromises();

    wrapper.unmount();
    fetchMock.mockRestore();
    Reflect.deleteProperty(window, "showDirectoryPicker");
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
