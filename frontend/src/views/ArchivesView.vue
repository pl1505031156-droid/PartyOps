<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  IconArchive,
  IconCheck,
  IconDownload,
  IconEdit,
  IconHistory,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconUpload,
} from "@arco-design/web-vue/es/icon";
import { Message, Modal } from "@arco-design/web-vue";
import { ApiError, api, downloadUrl, uploadFormWithProgress } from "../api";
import PageHelp from "../components/PageHelp.vue";
import BusinessUploadQueue from "../components/BusinessUploadQueue.vue";
import LedgerImportWizard from "../components/LedgerImportWizard.vue";
import { useUploadQueue } from "../composables/useUploadQueue";
import { useSessionStore } from "../stores/session";
import { beijingNow } from "../utils/datetime";
import type {
  ArchiveAttachment,
  ArchiveAccessGrant,
  ArchiveCategory,
  ArchiveFieldDefinition,
  ArchiveRecord,
  ArchiveYearSummary,
  Device,
  User,
} from "../types";
import { zhLabel } from "../utils/labels";

const route = useRoute();
const router = useRouter();
const session = useSessionStore();
const categories = ref<ArchiveCategory[]>([]);
const years = ref<ArchiveYearSummary[]>([]);
const records = ref<ArchiveRecord[]>([]);
const selectedYear = ref(beijingNow().year());
const selectedCategoryId = ref("");
const keyword = ref("");
const statusFilter = ref<"active" | "voided">("active");
const selectedRecord = ref<ArchiveRecord | null>(null);
const loading = ref(false);
const saving = ref(false);
const recordVisible = ref(false);
const categoryVisible = ref(false);
const voidVisible = ref(false);
const historyVisible = ref(false);
const history = ref<Array<{ revision_no: number; change_note: string; created_at: string; snapshot: Record<string, unknown> }>>([]);
const voidReason = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const uploading = ref(false);
const archiveQueueTargetId = ref("");
const deleteAttachmentVisible = ref(false);
const deleteAttachmentTarget = ref<ArchiveAttachment | null>(null);
const deleteAttachmentReason = ref("");
const ledgerImportVisible = ref(false);
const fieldErrors = ref<Record<string, string>>({});
const grants = ref<ArchiveAccessGrant[]>([]);
const grantUsers = ref<User[]>([]);
const grantDevices = ref<Device[]>([]);
const grantSaving = ref(false);
const grantForm = reactive({
  target_type: "user" as "user" | "device",
  target_id: "",
  can_view: true,
  can_download: true,
  can_contribute: false,
});

const archiveUploadQueue = useUploadQueue(async (item, context) => {
  if (!archiveQueueTargetId.value) throw new Error("请重新选择要上传到的档案。");
  const form = new FormData();
  form.append("file", item.file);
  form.append("note", "重要档案扫描件");
  form.append("client_upload_id", item.clientUploadId);
  await uploadFormWithProgress<ArchiveAttachment>(
    `/archives/records/${archiveQueueTargetId.value}/attachments`,
    form,
    { signal: context.signal, onProgress: context.onProgress },
  );
}, 2);
const archiveUploadItems = archiveUploadQueue.items;

watch(archiveUploadQueue.pending, async (pending, previous) => {
  uploading.value = pending > 0;
  if (previous <= 0 || pending !== 0 || !archiveQueueTargetId.value) return;
  const recordId = archiveQueueTargetId.value;
  if (selectedRecord.value?.id === recordId) {
    selectedRecord.value = await api.get<ArchiveRecord>(`/archives/records/${recordId}`);
  }
  await loadYears();
  pollAttachmentRecognition(recordId);
  if (archiveUploadQueue.failed.value === 0) Message.success("所选扫描件均已保存，正文识别将在后台完成");
});

const recordForm = reactive({
  category_id: "",
  archive_year: beijingNow().year(),
  sequence_no: undefined as number | undefined,
  document_no: "",
  title: "",
  summary: "",
  involved_persons: "",
  source_unit: "",
  document_date: "",
  person_name: "",
  person_identifier: "",
  personnel_type: "",
  organization: "",
  assessment_result: "",
  tags: "",
  custom_fields: {} as Record<string, unknown>,
});
const editingRecord = ref<ArchiveRecord | null>(null);
const categoryForm = reactive({
  name: "",
  code: "",
  description: "",
  record_mode: "document" as ArchiveCategory["record_mode"],
  directory_pattern: "{year}/{category}",
  access_mode: "all_users" as ArchiveCategory["access_mode"],
  allow_device_access: true,
  active: true,
});
const categoryFields = ref<ArchiveFieldDefinition[]>([]);
const editingCategory = ref<ArchiveCategory | null>(null);
const newField = reactive({
  key: "",
  label: "",
  type: "text" as ArchiveFieldDefinition["type"],
  required: false,
  options: "",
});

const selectedCategory = computed(() =>
  categories.value.find((item) => item.id === selectedCategoryId.value),
);
const formCategory = computed(() =>
  categories.value.find((item) => item.id === recordForm.category_id),
);
const visibleYears = computed(() => years.value.map((item) => item.year));
const canManage = computed(() => session.user?.role === "admin");
const activeCategories = computed(() => categories.value.filter((item) => item.active));
const canCreate = computed(() => categories.value.some((item) => Boolean(item.permissions?.contribute)));
const canContributeSelected = computed(() => Boolean(selectedRecord.value?.permissions?.contribute));
const assessmentField = computed(() =>
  formCategory.value?.field_schema.find((field) => field.key === "assessment_result"),
);

function errorFor(...keys: string[]) {
  for (const key of keys) {
    if (fieldErrors.value[key]) return fieldErrors.value[key];
  }
  return "";
}

function fieldElementId(key: string) {
  return `archive-field-${key.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

async function focusFirstError() {
  const key = Object.keys(fieldErrors.value)[0];
  if (!key) return;
  await nextTick();
  const container = document.getElementById(fieldElementId(key));
  const target = container?.querySelector<HTMLElement>("input, textarea, [role='combobox'], [tabindex]");
  target?.focus();
}

function categoryName(id: string) {
  return categories.value.find((item) => item.id === id)?.name || "未分类";
}

function formatDate(value: string | null) {
  return value ? value.slice(0, 10) : "—";
}

function statusLabel(value: string) {
  return zhLabel(value);
}

function customFieldText(key: string) {
  const value = recordForm.custom_fields[key];
  return value === null || value === undefined ? "" : String(value);
}

function setCustomField(key: string, value: unknown) {
  recordForm.custom_fields[key] = value;
}

async function loadCategories() {
  categories.value = await api.get<ArchiveCategory[]>(
    `/archives/categories${canManage.value ? "?include_inactive=true" : ""}`,
  );
  if (!activeCategories.value.some((item) => item.id === selectedCategoryId.value)) {
    selectedCategoryId.value = activeCategories.value[0]?.id || "";
  }
}

async function loadGrantOptions() {
  if (!canManage.value) return;
  const [users, devices] = await Promise.all([
    api.get<User[]>("/admin/users"),
    api.get<Device[]>("/admin/devices"),
  ]);
  grantUsers.value = users.filter((item) => item.active);
  grantDevices.value = devices.filter((item) => item.active);
}

async function loadGrants(categoryId: string) {
  if (!canManage.value) return;
  grants.value = await api.get<ArchiveAccessGrant[]>(
    `/archives/categories/${categoryId}/grants?active_only=false`,
  );
}

async function loadYears() {
  const response = await api.get<{ years: ArchiveYearSummary[] }>("/archives/years");
  years.value = response.years;
}

async function loadRecords() {
  loading.value = true;
  try {
    const query = new URLSearchParams({
      archive_year: String(selectedYear.value),
      limit: "500",
    });
    if (selectedCategoryId.value) query.set("category_id", selectedCategoryId.value);
    if (keyword.value.trim()) query.set("keyword", keyword.value.trim());
    query.set("status", statusFilter.value);
    records.value = await api.get<ArchiveRecord[]>(`/archives/records?${query}`);
    const routeRecord = String(route.query.record || "");
    const selectedId = selectedRecord.value?.id || routeRecord;
    if (selectedId) {
      const item = records.value.find((record) => record.id === selectedId);
      if (item) await selectRecord(item);
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "档案列表加载失败");
  } finally {
    loading.value = false;
  }
}

async function load() {
  try {
    await Promise.all([loadCategories(), loadYears()]);
    const routeRecord = String(route.query.record || "");
    if (routeRecord) {
      const detail = await api.get<ArchiveRecord>(`/archives/records/${routeRecord}`);
      selectedYear.value = detail.archive_year;
      selectedCategoryId.value = detail.category_id;
      selectedRecord.value = detail;
    }
    if (!visibleYears.value.includes(selectedYear.value) && visibleYears.value.length) {
      selectedYear.value = visibleYears.value[0];
    }
    await loadRecords();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "重要档案加载失败");
  }
}

async function selectRecord(item: ArchiveRecord) {
  selectedRecord.value = await api.get<ArchiveRecord>(`/archives/records/${item.id}`);
  await router.replace({ query: { ...route.query, record: item.id } });
}

function relationLabel(type: string) {
  return ({ task: "事项", report: "周期报告", journal: "工作日志", knowledge: "知识条目" } as Record<string, string>)[type] || "业务对象";
}

function removeArchiveLink(link: ArchiveRecord["links"][number]) {
  if (!selectedRecord.value) return;
  const record = selectedRecord.value;
  Modal.confirm({
    title: "移除档案关联",
    content: `确认移除与${relationLabel(link.entity_type)}的关联？原业务对象和档案内容都不会删除。`,
    okText: "确认移除",
    onOk: async () => {
      try {
        selectedRecord.value = await api.delete<ArchiveRecord>(
          `/archives/records/${record.id}/links/${link.id}`,
          { "If-Match": String(record.version) },
        );
        Message.success("关联已移除，原业务数据保持不变");
      } catch (error) {
        Message.error(error instanceof Error ? error.message : "档案关联移除失败");
        throw error;
      }
    },
  });
}

function chooseYear(year: number) {
  selectedYear.value = year;
  selectedRecord.value = null;
  void loadRecords();
}

function chooseCategory(categoryId: string) {
  selectedCategoryId.value = categoryId;
  selectedRecord.value = null;
  void loadRecords();
}

function resetRecordForm() {
  fieldErrors.value = {};
  Object.assign(recordForm, {
    category_id: selectedCategoryId.value || categories.value[0]?.id || "",
    archive_year: selectedYear.value,
    sequence_no: undefined,
    document_no: "",
    title: "",
    summary: "",
    involved_persons: "",
    source_unit: "",
    document_date: "",
    person_name: "",
    person_identifier: "",
    personnel_type: "",
    organization: "",
    assessment_result: "",
    tags: "",
    custom_fields: {},
  });
}

function openCreate() {
  editingRecord.value = null;
  resetRecordForm();
  if (!formCategory.value?.permissions.contribute) {
    recordForm.category_id = categories.value.find((item) => item.permissions.contribute)?.id || "";
  }
  recordVisible.value = true;
}

function openEdit() {
  const item = selectedRecord.value;
  if (!item) return;
  editingRecord.value = item;
  fieldErrors.value = {};
  Object.assign(recordForm, {
    category_id: item.category_id,
    archive_year: item.archive_year,
    sequence_no: item.sequence_no,
    document_no: item.document_no,
    title: item.title,
    summary: item.summary,
    involved_persons: item.involved_persons.join("、"),
    source_unit: item.source_unit,
    document_date: item.document_date ? item.document_date.slice(0, 10) : "",
    person_name: item.person_name,
    person_identifier: item.person_identifier,
    personnel_type: item.personnel_type,
    organization: item.organization,
    assessment_result: item.assessment_result,
    tags: item.tags.join("、"),
    custom_fields: { ...item.custom_fields },
  });
  recordVisible.value = true;
}

function payloadFromForm() {
  const date = recordForm.document_date.trim();
  return {
    category_id: recordForm.category_id,
    archive_year: Number(recordForm.archive_year),
    sequence_no: recordForm.sequence_no || undefined,
    document_no: recordForm.document_no.trim(),
    title: recordForm.title.trim(),
    summary: recordForm.summary.trim(),
    involved_persons: recordForm.involved_persons.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean),
    source_unit: recordForm.source_unit.trim(),
    document_date: date ? `${date}T00:00:00+08:00` : null,
    person_name: recordForm.person_name.trim(),
    person_identifier: recordForm.person_identifier.trim(),
    personnel_type: recordForm.personnel_type.trim(),
    organization: recordForm.organization.trim(),
    assessment_result: recordForm.assessment_result.trim(),
    tags: recordForm.tags.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean),
    custom_fields: recordForm.custom_fields,
  };
}

async function saveRecord(): Promise<boolean> {
  if (!recordForm.title.trim() || !recordForm.category_id || !recordForm.archive_year) {
    Message.warning("请填写年度、档案类别和标题");
    return false;
  }
  fieldErrors.value = {};
  saving.value = true;
  try {
    let saved: ArchiveRecord;
    if (editingRecord.value) {
      saved = await api.patch<ArchiveRecord>(
        `/archives/records/${editingRecord.value.id}`,
        { ...payloadFromForm(), change_note: "人工修订档案目录" },
        { "If-Match": String(editingRecord.value.version) },
      );
    } else {
      saved = await api.post<ArchiveRecord>("/archives/records", payloadFromForm());
    }
    selectedYear.value = saved.archive_year;
    selectedCategoryId.value = saved.category_id;
    await loadYears();
    await loadRecords();
    await selectRecord(saved);
    Message.success(editingRecord.value ? "档案已修订并保留历史版本" : "档案已建立");
    return true;
  } catch (error) {
    if (error instanceof ApiError && Object.keys(error.fields).length) {
      fieldErrors.value = error.fields;
      await focusFirstError();
    }
    Message.error(error instanceof Error ? error.message : "档案保存失败");
    return false;
  } finally {
    saving.value = false;
  }
}

async function openCategoryManager() {
  editingCategory.value = null;
  Object.assign(categoryForm, {
    name: "",
    code: "",
    description: "",
    record_mode: "document",
    directory_pattern: "{year}/{category}",
    access_mode: "all_users",
    allow_device_access: true,
    active: true,
  });
  categoryFields.value = [];
  grants.value = [];
  await loadGrantOptions();
  categoryVisible.value = true;
}

async function editCategory(category: ArchiveCategory) {
  editingCategory.value = category;
  Object.assign(categoryForm, {
    name: category.name,
    code: category.code,
    description: category.description,
    record_mode: category.record_mode,
    directory_pattern: category.directory_pattern,
    access_mode: category.access_mode,
    allow_device_access: category.allow_device_access,
    active: category.active,
  });
  categoryFields.value = category.field_schema.map((field) => ({ ...field }));
  await Promise.all([loadGrantOptions(), loadGrants(category.id)]);
}

function grantTargetLabel(grant: ArchiveAccessGrant) {
  if (grant.user_id) {
    return grantUsers.value.find((item) => item.id === grant.user_id)?.display_name || "已停用用户";
  }
  return grantDevices.value.find((item) => item.id === grant.device_id)?.name || "已移除设备";
}

async function saveGrant() {
  if (!editingCategory.value || !grantForm.target_id) {
    Message.warning("请选择授权用户或设备");
    return;
  }
  grantSaving.value = true;
  try {
    await api.post(`/archives/categories/${editingCategory.value.id}/grants`, {
      user_id: grantForm.target_type === "user" ? grantForm.target_id : null,
      device_id: grantForm.target_type === "device" ? grantForm.target_id : null,
      can_view: grantForm.can_view,
      can_download: grantForm.can_download,
      can_contribute: grantForm.can_contribute,
    });
    await loadGrants(editingCategory.value.id);
    Object.assign(grantForm, { target_id: "", can_view: true, can_download: true, can_contribute: false });
    Message.success("档案授权已保存");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "授权保存失败");
  } finally {
    grantSaving.value = false;
  }
}

async function updateGrant(grant: ArchiveAccessGrant, patch: Partial<ArchiveAccessGrant>) {
  try {
    const updated = await api.patch<ArchiveAccessGrant>(
      `/archives/grants/${grant.id}`,
      patch,
      { "If-Match": String(grant.version) },
    );
    const index = grants.value.findIndex((item) => item.id === grant.id);
    if (index >= 0) grants.value[index] = updated;
  } catch (error) {
    await loadGrants(grant.category_id);
    Message.error(error instanceof Error ? error.message : "授权更新失败");
  }
}

function addField() {
  if (!newField.key.trim() || !newField.label.trim()) {
    Message.warning("请填写字段键和字段名称");
    return;
  }
  if (categoryFields.value.some((field) => field.key === newField.key.trim())) {
    Message.warning("字段键不能重复");
    return;
  }
  categoryFields.value.push({
    key: newField.key.trim(),
    label: newField.label.trim(),
    type: newField.type,
    required: newField.required,
    options: newField.options.split(/[、,，]/).map((item) => item.trim()).filter(Boolean),
  });
  Object.assign(newField, { key: "", label: "", type: "text", required: false, options: "" });
}

async function saveCategory(): Promise<boolean> {
  if (!categoryForm.name.trim() || !categoryForm.code.trim()) {
    Message.warning("请填写类别名称和编码");
    return false;
  }
  try {
    if (editingCategory.value) {
      await api.patch<ArchiveCategory>(
        `/archives/categories/${editingCategory.value.id}`,
        {
          name: categoryForm.name.trim(),
          description: categoryForm.description,
          directory_pattern: categoryForm.directory_pattern,
          access_mode: categoryForm.access_mode,
          allow_device_access: categoryForm.allow_device_access,
          active: categoryForm.active,
          field_schema: categoryFields.value,
        },
        { "If-Match": String(editingCategory.value.version) },
      );
    } else {
      await api.post<ArchiveCategory>("/archives/categories", {
        ...categoryForm,
        name: categoryForm.name.trim(),
        code: categoryForm.code.trim().toLowerCase(),
        field_schema: categoryFields.value,
      });
    }
    await loadCategories();
    Message.success(editingCategory.value ? "档案类别和访问权限已更新" : "自定义档案类别已建立");
    return true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "类别保存失败");
    return false;
  }
}

function chooseUpload() {
  fileInput.value?.click();
}

function pollAttachmentRecognition(recordId: string, attempts = 12) {
  if (attempts <= 0) return;
  window.setTimeout(async () => {
    try {
      const detail = await api.get<ArchiveRecord>(`/archives/records/${recordId}`);
      if (selectedRecord.value?.id === recordId) selectedRecord.value = detail;
      if (detail.attachments.some((item) => item.status === "pending_ocr")) {
        pollAttachmentRecognition(recordId, attempts - 1);
      }
    } catch {
      // 后台识别失败不会影响已保存扫描件；用户可在详情中看到最终状态。
    }
  }, 1500);
}

function uploadFiles(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || []);
  if (!selectedRecord.value || !files.length) return;
  archiveQueueTargetId.value = selectedRecord.value.id;
  archiveUploadQueue.addFiles(files);
  if (fileInput.value) fileInput.value.value = "";
}

async function openHistory() {
  if (!selectedRecord.value) return;
  history.value = await api.get<typeof history.value>(`/archives/records/${selectedRecord.value.id}/history`);
  historyVisible.value = true;
}

async function voidRecord(): Promise<boolean> {
  if (!selectedRecord.value || !voidReason.value.trim()) {
    Message.warning("作废必须填写原因");
    return false;
  }
  try {
    selectedRecord.value = await api.post<ArchiveRecord>(
      `/archives/records/${selectedRecord.value.id}/void`,
      { reason: voidReason.value.trim() },
      { "If-Match": String(selectedRecord.value.version) },
    );
    voidReason.value = "";
    await loadYears();
    await loadRecords();
    Message.success("档案已作废，原记录和扫描件仍保留");
    return true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "档案作废失败");
    return false;
  }
}

async function restoreRecord() {
  if (!selectedRecord.value) return;
  try {
    selectedRecord.value = await api.post<ArchiveRecord>(
      `/archives/records/${selectedRecord.value.id}/restore`,
      { reason: "管理员确认恢复有效档案" },
      { "If-Match": String(selectedRecord.value.version) },
    );
    statusFilter.value = "active";
    await loadYears();
    await loadRecords();
    Message.success("档案已恢复为有效状态");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "档案恢复失败");
  }
}

function openDeleteAttachment(attachment: ArchiveAttachment) {
  deleteAttachmentTarget.value = attachment;
  deleteAttachmentReason.value = "";
  deleteAttachmentVisible.value = true;
}

// 兼容旧自动化与历史入口；新界面默认使用 30 天可恢复删除。
async function voidAttachment(attachment: ArchiveAttachment) {
  if (!selectedRecord.value) return;
  try {
    await api.post<ArchiveAttachment>(
      `/archives/attachments/${attachment.id}/void`,
      { reason: "管理员确认该扫描件不再作为有效版本" },
      { "If-Match": String(selectedRecord.value.version) },
    );
    selectedRecord.value = await api.get<ArchiveRecord>(`/archives/records/${selectedRecord.value.id}`);
    Message.success("扫描件已作废，原文件仍保留在受管附件库");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "扫描件作废失败");
  }
}

async function deleteAttachment(): Promise<boolean> {
  if (!selectedRecord.value || !deleteAttachmentTarget.value) return false;
  if (deleteAttachmentReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的删除原因");
    return false;
  }
  try {
    await api.delete<ArchiveAttachment>(
      `/archives/attachments/${deleteAttachmentTarget.value.id}?reason=${encodeURIComponent(deleteAttachmentReason.value.trim())}`,
      { "If-Match": String(selectedRecord.value.version) },
    );
    selectedRecord.value = await api.get<ArchiveRecord>(`/archives/records/${selectedRecord.value.id}`);
    Message.success("扫描件已移入回收站，30 天内可恢复");
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
      selectedRecord.value = await api.get<ArchiveRecord>(`/archives/records/${selectedRecord.value.id}`);
    }
    Message.error(error instanceof Error ? error.message : "扫描件删除失败");
    return false;
  }
}

async function restoreAttachment(attachment: ArchiveAttachment) {
  if (!selectedRecord.value) return;
  try {
    await api.post<ArchiveAttachment>(
      `/archives/attachments/${attachment.id}/restore`,
      undefined,
      { "If-Match": String(selectedRecord.value.version) },
    );
    selectedRecord.value = await api.get<ArchiveRecord>(`/archives/records/${selectedRecord.value.id}`);
    Message.success("扫描件已恢复");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "扫描件恢复失败");
  }
}

function exportYear() {
  window.open(
    downloadUrl(`/archives/export?archive_year=${selectedYear.value}${selectedCategoryId.value ? `&category_id=${selectedCategoryId.value}` : ""}`),
    "_blank",
  );
}

watch([selectedYear, selectedCategoryId, statusFilter], () => {
  if (recordVisible.value) return;
  void loadRecords();
});

onMounted(load);
</script>

<template>
  <div class="page archives-page">
    <header class="page-header">
      <div>
        <p class="page-kicker">重要档案中心</p>
        <h1 class="page-title">重要档案中心</h1>
        <p class="page-description">年度、人事调动、年度考核和其他重要文件统一归档；扫描件受管保存并自动识别正文。</p>
      </div>
      <a-space>
        <PageHelp
          title="重要档案怎么用"
          :tips="['先选择年度和档案类别，再录入目录记录。', '扫描件复制到受管附件库并纳入备份。', '错误档案只能作废或更正，历史版本不会丢失。']"
          help-query="重要档案"
        />
        <a-button @click="exportYear"><template #icon><IconDownload /></template>导出年度档案包</a-button>
        <a-button v-if="canCreate" :disabled="!selectedCategoryId" @click="ledgerImportVisible = true"><template #icon><IconUpload /></template>导入本地台账</a-button>
        <a-button v-if="canManage" @click="openCategoryManager"><template #icon><IconPlus /></template>管理档案类别</a-button>
        <a-button v-if="canCreate" type="primary" @click="openCreate"><template #icon><IconPlus /></template>新建档案</a-button>
      </a-space>
    </header>

    <section class="archive-toolbar">
      <a-input-number v-model="selectedYear" :min="1000" :max="9999" :precision="0" hide-button placeholder="输入任意年度" />
      <a-input-search v-model="keyword" allow-clear placeholder="搜索文号、人员、摘要、扫描件 OCR" @search="loadRecords">
        <template #prefix><IconSearch /></template>
      </a-input-search>
      <a-select v-model="statusFilter" style="width: 120px">
        <a-option value="active">有效档案</a-option>
        <a-option value="voided">已作废档案</a-option>
      </a-select>
      <a-button @click="load"><template #icon><IconRefresh /></template>刷新索引</a-button>
    </section>

    <div class="archives-layout">
      <aside class="archive-tree">
        <h3>年度目录</h3>
        <button
          v-for="item in years"
          :key="item.year"
          type="button"
          class="year-item"
          :class="{ active: selectedYear === item.year }"
          @click="chooseYear(item.year)"
        >
          <span>{{ item.year }} 年</span><small>{{ item.categories.reduce((sum, row) => sum + row.record_count, 0) }} 档</small>
        </button>
        <p v-if="!years.length" class="muted">尚未建立档案，可直接输入历史或未来年度。</p>
        <h3 class="category-heading">档案类别</h3>
        <button
          v-for="category in activeCategories"
          :key="category.id"
          type="button"
          class="category-item"
          :class="{ active: selectedCategoryId === category.id }"
          @click="chooseCategory(category.id)"
        >
          <span>{{ category.name }}</span><small>{{ category.record_mode === "person_year" ? "一人一档" : "文件一档" }}</small>
        </button>
      </aside>

      <main class="archive-list">
        <div class="list-heading">
          <div><b>{{ selectedYear }} 年 · {{ selectedCategory?.name || "全部类别" }}</b><span>{{ records.length }} 条档案</span></div>
          <span class="muted">序号自动生成，可在录入时手动调整</span>
        </div>
        <a-spin :loading="loading">
          <button
            v-for="record in records"
            :key="record.id"
            type="button"
            class="archive-row"
            :class="{ selected: selectedRecord?.id === record.id }"
            @click="selectRecord(record)"
          >
            <span class="archive-sequence">{{ String(record.sequence_no).padStart(3, "0") }}</span>
            <span class="archive-row-main"><b>{{ record.title }}</b><small>{{ record.document_no || "无文号" }} · {{ record.person_name || record.involved_persons.join("、") || "未填写人员" }}</small></span>
            <span class="archive-row-meta"><i>{{ record.attachment_count }} 件扫描件</i><em>{{ statusLabel(record.status) }}</em></span>
          </button>
          <div v-if="!records.length" class="empty-state">该年度暂无档案，可在右上角建立第一条记录。</div>
        </a-spin>
      </main>

      <aside class="archive-detail">
        <template v-if="selectedRecord">
          <div class="detail-heading">
            <div><span class="detail-code">{{ categoryName(selectedRecord.category_id) }} · {{ selectedRecord.archive_year }}</span><h2>{{ selectedRecord.title }}</h2></div>
            <a-space>
              <a-button v-if="canContributeSelected" size="small" @click="openEdit"><template #icon><IconEdit /></template>编辑</a-button>
              <a-button size="small" @click="openHistory"><template #icon><IconHistory /></template>历史</a-button>
              <a-button v-if="canManage && selectedRecord.status === 'active'" size="small" status="danger" @click="voidVisible = true">作废</a-button>
              <a-popconfirm v-if="canManage && selectedRecord.status === 'voided'" content="确认恢复这条档案？" @ok="restoreRecord">
                <a-button size="small" type="primary"><template #icon><IconCheck /></template>恢复</a-button>
              </a-popconfirm>
            </a-space>
          </div>
          <dl class="archive-fields">
            <dt>序号</dt><dd>{{ String(selectedRecord.sequence_no).padStart(3, "0") }}</dd>
            <dt>文号</dt><dd>{{ selectedRecord.document_no || "—" }}</dd>
            <dt>涉及人员</dt><dd>{{ selectedRecord.person_name || (selectedRecord.involved_persons || []).join("、") || "—" }}</dd>
            <dt>来源单位</dt><dd>{{ selectedRecord.source_unit || selectedRecord.organization || "—" }}</dd>
            <dt>日期</dt><dd>{{ formatDate(selectedRecord.document_date) }}</dd>
            <dt>摘要</dt><dd class="long-value">{{ selectedRecord.summary || "—" }}</dd>
            <dt>标签</dt><dd>{{ (selectedRecord.tags || []).join("、") || "—" }}</dd>
          </dl>
          <a-alert v-if="(selectedRecord.duplicate_warnings || []).length" type="warning" class="archive-warning">
            {{ (selectedRecord.duplicate_warnings || []).join("；") }}
          </a-alert>
          <div class="attachment-heading">
            <b>扫描件（{{ (selectedRecord.attachments || []).length }}）</b>
            <a-button v-if="canContributeSelected" size="small" :loading="uploading" @click="chooseUpload"><template #icon><IconUpload /></template>上传扫描件</a-button>
            <input ref="fileInput" type="file" hidden multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.docx,.xlsx,.xlsm,.txt,.csv" @change="uploadFiles" />
          </div>
          <BusinessUploadQueue :items="archiveUploadItems" @retry="archiveUploadQueue.retry" @cancel="archiveUploadQueue.cancel" @clear="archiveUploadQueue.clearSettled" />
          <div v-if="!(selectedRecord.attachments || []).length" class="attachment-empty">尚未上传扫描件，目录仍可先保存，后续补齐。</div>
          <div v-for="attachment in (selectedRecord.attachments || [])" :key="attachment.id" class="attachment-row">
            <span><IconArchive /><b>{{ attachment.display_name }}</b><small>{{ statusLabel(attachment.status) }} · {{ Math.ceil(attachment.size_bytes / 1024) }} KB</small></span>
            <a-space>
              <a-button v-if="attachment.status !== 'voided'" type="text" size="small" :href="downloadUrl(`/archives/attachments/${attachment.id}/download`)" target="_blank"><template #icon><IconDownload /></template></a-button>
              <a-button v-if="canContributeSelected && attachment.status !== 'voided'" type="text" size="mini" status="danger" @click="openDeleteAttachment(attachment)">删除</a-button>
            </a-space>
          </div>
          <details v-if="(selectedRecord.deleted_attachments || []).length" class="archive-recycle-bin">
            <summary>扫描件回收站（{{ (selectedRecord.deleted_attachments || []).length }}）</summary>
            <div v-for="attachment in (selectedRecord.deleted_attachments || [])" :key="attachment.id" class="attachment-row recycled">
              <span><IconArchive /><b>{{ attachment.display_name }}</b><small>{{ attachment.delete_reason }} · 30 天内可恢复</small></span>
              <a-button v-if="canManage || attachment.uploaded_by === session.user?.id" size="mini" @click="restoreAttachment(attachment)">恢复</a-button>
            </div>
          </details>
          <section v-if="(selectedRecord.links || []).length" class="archive-links">
            <div class="attachment-heading"><b>业务关联（{{ selectedRecord.links.length }}）</b><small>移除关联不会删除任一侧业务数据</small></div>
            <div v-for="link in selectedRecord.links" :key="link.id" class="archive-link-row">
              <span><b>{{ relationLabel(link.entity_type) }}</b><small>{{ link.relation }} · {{ link.entity_id }}</small></span>
              <a-button v-if="canContributeSelected" size="mini" type="text" status="danger" @click="removeArchiveLink(link)">移除关联</a-button>
            </div>
          </section>
          <div class="detail-footer">版本 {{ selectedRecord.version }} · 最后修改 {{ formatDate(selectedRecord.updated_at) }}</div>
        </template>
        <div v-else class="inspector-empty"><IconArchive /><p>从左侧选择档案，查看目录字段和扫描件。</p></div>
      </aside>
    </div>

    <a-modal v-model:visible="recordVisible" :title="editingRecord ? '编辑重要档案' : '新建重要档案'" :width="760" :mask-closable="false" :ok-loading="saving" :onBeforeOk="saveRecord">
      <a-form :model="recordForm" layout="vertical" class="archive-form">
        <div class="form-grid">
          <a-form-item :id="fieldElementId('archive_year')" label="年度" required :validate-status="errorFor('archive_year') ? 'error' : undefined" :help="errorFor('archive_year')"><a-input-number v-model="recordForm.archive_year" :min="1000" :max="9999" :precision="0" /></a-form-item>
          <a-form-item :id="fieldElementId('category_id')" label="档案类别" required :validate-status="errorFor('category_id') ? 'error' : undefined" :help="errorFor('category_id')"><a-select v-model="recordForm.category_id" :disabled="Boolean(editingRecord)"><a-option v-for="category in categories.filter((item) => item.permissions.contribute || item.id === editingRecord?.category_id)" :key="category.id" :value="category.id">{{ category.name }}</a-option></a-select></a-form-item>
          <a-form-item :id="fieldElementId('sequence_no')" label="序号" :validate-status="errorFor('sequence_no') ? 'error' : undefined" :help="errorFor('sequence_no')"><a-input-number v-model="recordForm.sequence_no" :min="1" :max="999999" placeholder="留空自动生成" /></a-form-item>
          <a-form-item :id="fieldElementId('document_no')" label="文号" :validate-status="errorFor('document_no') ? 'error' : undefined" :help="errorFor('document_no')"><a-input v-model="recordForm.document_no" placeholder="例如：宣组干（2025）173号" /></a-form-item>
          <a-form-item :id="fieldElementId('title')" label="标题" required :validate-status="errorFor('title') ? 'error' : undefined" :help="errorFor('title')"><a-input v-model="recordForm.title" /></a-form-item>
          <a-form-item :id="fieldElementId('document_date')" label="出文日期" :validate-status="errorFor('document_date') ? 'error' : undefined" :help="errorFor('document_date')"><a-date-picker v-model="recordForm.document_date" value-format="YYYY-MM-DD" style="width: 100%" /></a-form-item>
          <a-form-item label="涉及人员"><a-input v-model="recordForm.involved_persons" placeholder="多人用顿号分隔" /></a-form-item>
          <a-form-item :id="fieldElementId('person_name')" label="人员姓名" :required="formCategory?.record_mode === 'person_year'" :validate-status="errorFor('person_name') ? 'error' : undefined" :help="errorFor('person_name')"><a-input v-model="recordForm.person_name" placeholder="年度考核一人一档必填" /></a-form-item>
          <a-form-item label="人员编号"><a-input v-model="recordForm.person_identifier" /></a-form-item>
          <a-form-item label="编制类型"><a-input v-model="recordForm.personnel_type" placeholder="事业编 / 公务员" /></a-form-item>
          <a-form-item label="来源单位或科室"><a-input v-model="recordForm.organization" /></a-form-item>
          <a-form-item label="来源单位"><a-input v-model="recordForm.source_unit" /></a-form-item>
        </div>
        <a-form-item v-if="formCategory?.record_mode === 'person_year' && assessmentField" :id="fieldElementId('assessment_result')" :label="assessmentField.label" :required="assessmentField.required" :validate-status="errorFor('assessment_result') ? 'error' : undefined" :help="errorFor('assessment_result')">
          <a-select v-if="assessmentField.type === 'select'" v-model="recordForm.assessment_result" allow-clear placeholder="请选择类别定义的考核等次"><a-option v-for="option in assessmentField.options" :key="option" :value="option">{{ option }}</a-option></a-select>
          <a-input v-else v-model="recordForm.assessment_result" />
        </a-form-item>
        <a-form-item v-for="field in formCategory?.field_schema.filter((item) => item.key !== 'assessment_result')" :id="fieldElementId(`custom_fields.${field.key}`)" :key="field.key" :label="field.label" :required="field.required" :validate-status="errorFor(`custom_fields.${field.key}`) ? 'error' : undefined" :help="errorFor(`custom_fields.${field.key}`)">
          <a-select v-if="field.type === 'select'" v-model="recordForm.custom_fields[field.key]"><a-option v-for="option in field.options" :key="option" :value="option">{{ option }}</a-option></a-select>
          <a-input-number v-else-if="field.type === 'number'" v-model="recordForm.custom_fields[field.key]" />
          <a-date-picker v-else-if="field.type === 'date'" :model-value="customFieldText(field.key)" value-format="YYYY-MM-DD" style="width: 100%" @update:model-value="setCustomField(field.key, $event)" />
          <a-textarea v-else-if="field.type === 'textarea'" :model-value="customFieldText(field.key)" @update:model-value="setCustomField(field.key, $event)" />
          <a-input v-else :model-value="customFieldText(field.key)" @update:model-value="setCustomField(field.key, $event)" />
        </a-form-item>
        <a-form-item label="文件内容摘要"><a-textarea v-model="recordForm.summary" :auto-size="{ minRows: 3, maxRows: 7 }" /></a-form-item>
        <a-form-item label="标签"><a-input v-model="recordForm.tags" placeholder="用顿号分隔，例如：年度重点、已核验" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="categoryVisible" title="管理档案类别与访问权限" :width="720" :onBeforeOk="saveCategory">
      <a-alert type="info">新增类别后即可在年度目录和全局搜索中使用，不需要修改程序。字段键使用英文或数字下划线。</a-alert>
      <div class="category-manager-list">
        <a-button size="small" :type="editingCategory ? 'outline' : 'primary'" @click="openCategoryManager">新建类别</a-button>
        <a-button v-for="category in categories" :key="category.id" size="small" :type="editingCategory?.id === category.id ? 'primary' : 'outline'" @click="editCategory(category)">
          {{ category.name }}{{ category.active ? "" : "（已停用）" }}
        </a-button>
      </div>
      <a-form :model="categoryForm" layout="vertical" class="archive-form">
        <div class="form-grid">
          <a-form-item label="类别名称" required><a-input v-model="categoryForm.name" /></a-form-item>
          <a-form-item label="类别编码" required><a-input v-model="categoryForm.code" :disabled="Boolean(editingCategory)" placeholder="例如 meeting_archive" /></a-form-item>
          <a-form-item label="记录模式"><a-select v-model="categoryForm.record_mode" :disabled="Boolean(editingCategory)"><a-option value="document">文件一档</a-option><a-option value="person_year">一人一档</a-option></a-select></a-form-item>
          <a-form-item label="协同访问"><a-select v-model="categoryForm.access_mode"><a-option value="all_users">所有协同人员</a-option><a-option value="admins_only">仅管理员</a-option><a-option value="selected">指定授权</a-option></a-select></a-form-item>
        </div>
        <a-form-item label="归档说明"><a-textarea v-model="categoryForm.description" /></a-form-item>
        <a-form-item label="目录命名规则"><a-input v-model="categoryForm.directory_pattern" /></a-form-item>
        <a-form-item label="允许协同设备访问"><a-switch v-model="categoryForm.allow_device_access" /><span class="switch-note">关闭后，设备授权也不会生效。</span></a-form-item>
        <a-form-item v-if="editingCategory" label="类别状态"><a-switch v-model="categoryForm.active" checked-text="启用" unchecked-text="停用" /><span class="switch-note">停用后不再允许新建或导入，已有档案、扫描件和审计全部保留，可随时恢复。</span></a-form-item>
        <div class="field-builder">
          <div class="field-builder-head"><b>自定义字段</b><a-button size="small" @click="addField"><template #icon><IconPlus /></template>添加字段</a-button></div>
          <div v-for="field in categoryFields" :key="field.key" class="field-chip">{{ field.label }}（{{ field.key }} · {{ zhLabel(field.type, "自定义字段") }}）</div>
          <div class="field-add-row">
            <a-input v-model="newField.key" placeholder="字段键" />
            <a-input v-model="newField.label" placeholder="字段名称" />
            <a-select v-model="newField.type"><a-option value="text">文本</a-option><a-option value="textarea">多行文本</a-option><a-option value="date">日期</a-option><a-option value="number">数字</a-option><a-option value="select">下拉</a-option></a-select>
            <a-input v-if="newField.type === 'select'" v-model="newField.options" placeholder="选项用顿号分隔" />
          </div>
        </div>
        <section v-if="editingCategory && categoryForm.access_mode === 'selected'" class="grant-manager">
          <div class="field-builder-head"><b>指定用户与设备授权</b><small>历史指定授权默认仅查看/下载，贡献权限需明确开启。</small></div>
          <div v-for="grant in grants" :key="grant.id" class="grant-row" :class="{ inactive: !grant.active }">
            <span><b>{{ grantTargetLabel(grant) }}</b><small>{{ grant.user_id ? '用户' : '设备' }}</small></span>
            <label>查看<a-switch :model-value="grant.can_view" size="small" @change="updateGrant(grant, { can_view: Boolean($event) })" /></label>
            <label>下载<a-switch :model-value="grant.can_download" size="small" @change="updateGrant(grant, { can_download: Boolean($event) })" /></label>
            <label>贡献<a-switch :model-value="grant.can_contribute" size="small" @change="updateGrant(grant, { can_contribute: Boolean($event) })" /></label>
            <a-button size="mini" :status="grant.active ? 'danger' : 'normal'" @click="updateGrant(grant, { active: !grant.active })">{{ grant.active ? '停用' : '恢复' }}</a-button>
          </div>
          <div class="grant-add-row">
            <a-select v-model="grantForm.target_type" style="width: 100px"><a-option value="user">用户</a-option><a-option value="device">设备</a-option></a-select>
            <a-select v-model="grantForm.target_id" allow-search placeholder="选择授权对象">
              <a-option v-for="item in grantForm.target_type === 'user' ? grantUsers : grantDevices" :key="item.id" :value="item.id">{{ 'display_name' in item ? item.display_name : item.name }}</a-option>
            </a-select>
            <a-checkbox v-model="grantForm.can_view">查看</a-checkbox>
            <a-checkbox v-model="grantForm.can_download">下载</a-checkbox>
            <a-checkbox v-model="grantForm.can_contribute">贡献</a-checkbox>
            <a-button type="primary" :loading="grantSaving" @click="saveGrant">添加授权</a-button>
          </div>
        </section>
        <a-alert v-else-if="editingCategory && categoryForm.access_mode !== 'selected'" type="info">当前模式由系统统一授权；切换为“指定授权”后可配置用户与设备的查看、下载、贡献能力。</a-alert>
        <a-alert v-else-if="categoryForm.access_mode === 'selected'" type="warning">请先保存新类别，再为具体用户或设备添加授权。</a-alert>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="voidVisible" title="作废档案" :onBeforeOk="voidRecord">
      <a-alert type="warning">原始目录与扫描件不会删除；作废后记录将从默认年度列表隐藏，并保留审计历史。</a-alert>
      <a-textarea v-model="voidReason" placeholder="请填写作废原因" />
    </a-modal>

    <a-modal v-model:visible="deleteAttachmentVisible" title="将扫描件移入回收站" :onBeforeOk="deleteAttachment">
      <a-alert type="warning" show-icon>扫描件会保留 30 天并可恢复，不会影响本批次中其他文件。</a-alert>
      <a-form :model="{ reason: deleteAttachmentReason }" layout="vertical" class="delete-attachment-form"><a-form-item label="删除原因（必填）"><a-textarea v-model="deleteAttachmentReason" :max-length="2000" show-word-limit :auto-size="{ minRows: 3, maxRows: 6 }" /></a-form-item></a-form>
    </a-modal>
    <LedgerImportWizard
      v-model:visible="ledgerImportVisible"
      target-type="archive"
      :target-id="selectedCategoryId"
      :target-label="categories.find((item) => item.id === selectedCategoryId)?.name || '重要档案'"
      @completed="load"
    />

    <a-modal v-model:visible="historyVisible" title="档案修订历史" :width="720">
      <div v-for="item in history" :key="item.revision_no" class="history-row">
        <b>修订 {{ item.revision_no }}</b><span>{{ item.change_note || "创建档案" }}</span><small>{{ formatDate(item.created_at) }}</small>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.archive-toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; }
.archive-toolbar .arco-input-search { width: min(420px, 42vw); }
.archives-layout { display: grid; grid-template-columns: 220px minmax(360px, 1fr) minmax(360px, 0.9fr); min-height: 620px; border: 1px solid var(--line); background: rgba(251, 248, 241, 0.62); }
.archive-tree { padding: 18px 12px; border-right: 1px solid var(--line); }
.archive-tree h3 { margin: 0 8px 10px; font-size: 12px; letter-spacing: .08em; }
.category-heading { margin-top: 24px !important; }
.year-item, .category-item { display: flex; justify-content: space-between; width: 100%; padding: 9px 10px; text-align: left; background: transparent; border: 0; color: var(--ink); cursor: pointer; }
.year-item small, .category-item small { color: var(--muted); font-size: 10px; }
.year-item.active, .category-item.active { color: var(--cinnabar); background: rgba(180,35,24,.08); }
.archive-list { min-width: 0; border-right: 1px solid var(--line); }
.list-heading { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 16px; border-bottom: 1px solid var(--line); }
.list-heading span { display: block; margin-top: 4px; color: var(--muted); font-size: 11px; }
.archive-row { display: grid; grid-template-columns: 44px minmax(0,1fr) 100px; width: 100%; gap: 10px; padding: 13px 16px; text-align: left; border: 0; border-bottom: 1px solid rgba(50,40,30,.08); background: transparent; cursor: pointer; }
.archive-row:hover, .archive-row.selected { background: rgba(180,35,24,.07); }
.archive-sequence { color: var(--cinnabar); font: 16px Georgia, serif; }
.archive-row-main, .archive-row-meta { min-width: 0; }
.archive-row-main b, .archive-row-main small, .archive-row-meta i, .archive-row-meta em { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.archive-row-main small, .archive-row-meta { margin-top: 5px; color: var(--muted); font-size: 11px; font-style: normal; }
.archive-row-meta em { color: var(--green); font-style: normal; }
.archive-detail { min-width: 0; padding: 18px; background: rgba(255,255,255,.24); }
.detail-heading, .attachment-heading, .field-builder-head { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
.detail-code { color: var(--cinnabar); font-size: 11px; }
.detail-heading h2 { margin: 5px 0 18px; font-size: 20px; }
.archive-fields { display: grid; grid-template-columns: 76px minmax(0,1fr); gap: 9px 12px; margin: 0 0 18px; font-size: 13px; }
.archive-fields dt { color: var(--muted); }
.archive-fields dd { margin: 0; word-break: break-word; }
.long-value { white-space: pre-wrap; line-height: 1.7; }
.archive-warning { margin-bottom: 14px; }
.attachment-heading { padding: 12px 0; border-top: 1px solid var(--line); }
.attachment-row { display: flex; justify-content: space-between; gap: 8px; align-items: center; padding: 10px 0; border-bottom: 1px solid rgba(50,40,30,.08); }
.attachment-row span { min-width: 0; display: grid; grid-template-columns: 18px minmax(0,1fr); column-gap: 6px; }
.attachment-row b, .attachment-row small { grid-column: 2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.attachment-row small { color: var(--muted); font-size: 10px; }
.attachment-empty, .inspector-empty { padding: 30px 12px; color: var(--muted); text-align: center; }
.archive-recycle-bin { margin-top: 12px; padding: 10px 12px; color: var(--muted); background: rgba(98,84,66,.045); border-left: 2px solid var(--line); }
.archive-recycle-bin summary { cursor: pointer; font-size: 12px; }
.attachment-row.recycled { padding-bottom: 2px; }
.archive-links { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 12px; }
.archive-link-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid rgba(50,40,30,.08); }
.archive-link-row span { display: grid; gap: 2px; min-width: 0; }
.archive-link-row small { color: var(--muted); overflow-wrap: anywhere; }
.delete-attachment-form { margin-top: 14px; }
.detail-footer { margin-top: 22px; color: var(--muted); font-size: 11px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 14px; }
.category-manager-list { display: flex; gap: 6px; margin: 14px 0 4px; padding-bottom: 12px; overflow-x: auto; border-bottom: 1px solid var(--line); }
.field-builder { padding-top: 10px; border-top: 1px solid var(--line); }
.field-chip { display: inline-block; margin: 8px 6px 0 0; padding: 5px 8px; background: rgba(180,35,24,.08); font-size: 11px; }
.field-add-row { display: grid; grid-template-columns: 1fr 1fr 110px 1.4fr; gap: 8px; margin-top: 10px; }
.switch-note { margin-left: 10px; color: var(--muted); font-size: 11px; }
.grant-manager { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--line); }
.grant-manager .field-builder-head small { color: var(--muted); font-weight: 400; }
.grant-row { display: grid; grid-template-columns: minmax(140px, 1fr) repeat(3, 76px) 58px; gap: 8px; align-items: center; padding: 9px 0; border-bottom: 1px solid rgba(50,40,30,.08); }
.grant-row > span b, .grant-row > span small { display: block; }
.grant-row > span small { margin-top: 3px; color: var(--muted); font-size: 10px; }
.grant-row label { display: inline-flex; gap: 6px; align-items: center; color: var(--muted); font-size: 11px; }
.grant-row.inactive { opacity: .52; }
.grant-add-row { display: grid; grid-template-columns: 100px minmax(140px,1fr) repeat(3, auto) auto; gap: 8px; align-items: center; margin-top: 12px; }
.history-row { display: grid; grid-template-columns: 80px 1fr 100px; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.history-row small { color: var(--muted); }
@media (max-width: 1100px) {
  .archives-layout { grid-template-columns: 180px minmax(320px, 1fr); }
  .archive-detail { grid-column: 1 / -1; border-top: 1px solid var(--line); border-right: 0; }
}
@media (max-width: 700px) {
  .archive-toolbar, .form-grid, .field-add-row, .grant-row, .grant-add-row { grid-template-columns: 1fr; display: grid; }
  .archive-toolbar .arco-input-search { width: 100%; }
  .archives-layout { display: block; }
  .archive-tree, .archive-list { border-right: 0; border-bottom: 1px solid var(--line); }
}
</style>
