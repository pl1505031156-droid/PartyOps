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
    expect(vm.nodeDate(result.nodes[0])).toBe("2026-05-20");
    expect(vm.nodeDate({ ...result.nodes[0], date: null })).toContain("等待组织研究");
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
});
