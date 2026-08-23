<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  IconArrowLeft,
  IconCheck,
  IconDownload,
  IconDelete,
  IconEdit,
  IconFile,
  IconMessage,
  IconPlus,
  IconUpload,
} from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { ApiError, api, downloadUrl, uploadFormWithProgress } from "../api";
import type { Comment as TaskComment, Material, MaterialVersion, Task, TaskStatus, User } from "../types";
import TaskStatusTag from "../components/TaskStatusTag.vue";
import ObjectContextPanel from "../components/ObjectContextPanel.vue";
import PageHelp from "../components/PageHelp.vue";
import BusinessUploadQueue from "../components/BusinessUploadQueue.vue";
import { useUploadQueue } from "../composables/useUploadQueue";
import { formatServerTime } from "../utils/datetime";
import { fieldLabel, localizeEmbeddedCodes } from "../utils/labels";
import { useSessionStore } from "../stores/session";

const route = useRoute();
const router = useRouter();
const session = useSessionStore();
const task = ref<Task | null>(null);
const users = ref<User[]>([]);
const contacts = ref<Array<{ id: string; name: string; organization: string }>>([]);
const loading = ref(true);
const tab = ref("steps");
const comment = ref("");
const actionVisible = ref(false);
const actionKey = ref("");
const actionNote = ref("");
const editVisible = ref(false);
const materialVisible = ref(false);
const stepVisible = ref(false);
const subtaskVisible = ref(false);
const participantVisible = ref(false);
const notApplicableVisible = ref(false);
const uploadVisible = ref(false);
const uploadMaterial = ref<Material | null>(null);
const uploadFile = ref<File | null>(null);
const batchUploadVisible = ref(false);
const batchFileInput = ref<HTMLInputElement | null>(null);
const deleteAttachmentVisible = ref(false);
const deleteAttachmentMaterial = ref<Material | null>(null);
const deleteAttachmentVersion = ref<MaterialVersion | null>(null);
const deleteAttachmentReason = ref("");
const rollbackVisible = ref(false);
const rollbackMaterial = ref<Material | null>(null);
const rollbackVersion = ref<MaterialVersion | null>(null);
const rollbackReason = ref("");
const conflict = ref<Record<string, unknown> | null>(null);
const notApplicableMaterial = ref<Material | null>(null);
const notApplicableReason = ref("");
const replyTo = ref<string | null>(null);
const mentionedUserIds = ref<string[]>([]);
const editForm = reactive({
  title: "",
  description: "",
  source: "",
  owner_id: "",
  reviewer_id: "",
  formal_due_at: null as string | null,
  internal_due_at: null as string | null,
  planned_start_at: null as string | null,
  planned_end_at: null as string | null,
  priority: "normal",
  category: "",
  work_area: "",
  annual_focus: "",
  reporting_scope: "",
  tags_text: "",
  experience_notes: "",
  contact_ids: [] as string[],
  allow_sensitive_content: false,
});
const materialForm = reactive({ category: "final", name: "", required: true });
const fallbackMaterialCategories = [
  { value: "final", label: "最终报送稿", custom: false },
  { value: "draft", label: "初稿／起草材料", custom: false },
  { value: "revision", label: "修改稿", custom: false },
  { value: "leader_approved", label: "领导审定稿", custom: false },
  { value: "notice", label: "通知／文件依据", custom: false },
  { value: "receipt", label: "报送回执", custom: false },
  { value: "form", label: "表格／台账", custom: false },
  { value: "roster", label: "名册／人员清单", custom: false },
  { value: "plan", label: "方案／计划", custom: false },
  { value: "summary", label: "总结／报告", custom: false },
  { value: "minutes", label: "会议记录／纪要", custom: false },
  { value: "signin", label: "签到表", custom: false },
  { value: "photo", label: "图片／影像资料", custom: false },
  { value: "evidence", label: "佐证材料", custom: false },
  { value: "approval", label: "请示／审批材料", custom: false },
  { value: "feedback", label: "反馈意见", custom: false },
  { value: "publicity", label: "宣传稿件", custom: false },
  { value: "certificate", label: "证书／证明", custom: false },
  { value: "statistics", label: "统计数据", custom: false },
  { value: "correspondence", label: "函件／往来材料", custom: false },
  { value: "other", label: "其他材料", custom: false },
];
const materialCategories = ref([...fallbackMaterialCategories]);
const stepForm = reactive({ title: "", assignee_id: "", due_at: null as string | null });
const subtaskForm = reactive({
  title: "",
  owner_id: "",
  formal_due_at: null as string | null,
  internal_due_at: null as string | null,
  category: "",
});
const participantUserId = ref("");
const uploadForm = reactive({ stage: "draft", is_final: false, note: "" });
const batchUploadForm = reactive({
  category: "other",
  required: false,
  stage: "draft",
  is_final: false,
  note: "",
});

const batchQueue = useUploadQueue(async (item, context) => {
  if (!task.value) throw new Error("事项尚未加载，请刷新后重试。");
  const formData = new FormData();
  formData.append("file", item.file);
  formData.append("category", batchUploadForm.category.trim() || "other");
  formData.append("required", String(batchUploadForm.required));
  formData.append("stage", batchUploadForm.stage);
  formData.append("is_final", String(batchUploadForm.is_final));
  formData.append("note", batchUploadForm.note);
  formData.append("client_upload_id", item.clientUploadId);
  await uploadFormWithProgress<Task>(
    `/tasks/${task.value.id}/materials/quick-upload`,
    formData,
    { signal: context.signal, onProgress: context.onProgress },
  );
}, 2);
const batchUploadItems = batchQueue.items;

watch(batchQueue.pending, (pending, previous) => {
  if (previous > 0 && pending === 0) {
    void load();
    if (batchQueue.failed.value === 0) Message.success("所选文件已分别建立材料项");
  }
});

const userNames = computed(() => Object.fromEntries(users.value.map((item) => [item.id, item.display_name])));
const mentionableUsers = computed(() => {
  if (!task.value) return [];
  const ids = new Set([
    task.value.owner_id,
    task.value.reviewer_id || "",
    ...task.value.participants.map((item) => item.user_id),
  ]);
  return users.value.filter((item) => ids.has(item.id));
});
const replyTarget = computed(() => task.value?.comments.find((item) => item.id === replyTo.value) || null);
const canManageTask = computed(() => Boolean(
  task.value
  && session.user
  && (
    session.user.role === "admin"
    || task.value.owner_id === session.user.id
    || task.value.created_by === session.user.id
  ),
));
const threadedComments = computed(() => {
  const comments = task.value?.comments || [];
  const children = new Map<string | null, TaskComment[]>();
  for (const item of comments) {
    const key = item.parent_id && comments.some((parent) => parent.id === item.parent_id)
      ? item.parent_id
      : null;
    children.set(key, [...(children.get(key) || []), item]);
  }
  const rows: Array<{ item: TaskComment; depth: number }> = [];
  const visit = (parentId: string | null, depth: number) => {
    for (const item of children.get(parentId) || []) {
      rows.push({ item, depth });
      visit(item.id, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
});
const actionOptions = computed(() => {
  if (!task.value) return [];
  const map: Record<TaskStatus, Array<[string, string, string]>> = {
    pending_receipt: [["accept", "接收并办理", "primary"]],
    pending_breakdown: [["start", "开始办理", "primary"]],
    in_progress: [
      ["wait_feedback", "等待反馈", "secondary"],
      task.value.reviewer_id ? ["submit_review", "提交审核", "primary"] : ["complete", "完成办理", "primary"],
    ],
    waiting_feedback: [
      ["resume", "继续办理", "secondary"],
      task.value.reviewer_id ? ["submit_review", "提交审核", "primary"] : ["complete", "完成办理", "primary"],
    ],
    pending_review: ([
      ["return", "退回修改", "secondary"],
      ["approve", "审核通过", "primary"],
    ] as Array<[string, string, string]>).filter(
      () => session.user?.role === "admin" || task.value?.reviewer_id === session.user?.id,
    ),
    returned: [["start", "修改并继续", "primary"]],
    completed: [
      ["reopen", "重新打开", "secondary"],
      ["archive", "完成归档", "primary"],
    ],
    archived: [["reopen", "重新打开", "secondary"]],
  };
  if (task.value.status !== "pending_review" && !canManageTask.value) return [];
  return map[task.value.status] || [];
});
const conflictRows = computed(() => {
  const current = (conflict.value?.current || {}) as Record<string, unknown>;
  const submitted = (conflict.value?.submitted || {}) as Record<string, unknown>;
  return Array.from(new Set([...Object.keys(current), ...Object.keys(submitted)])).map((field) => ({
    field,
    current: current[field],
    submitted: submitted[field],
  }));
});

async function load() {
  loading.value = true;
  try {
    task.value = await api.get<Task>(`/tasks/${route.params.id}`);
    try {
      users.value = await api.get<User[]>("/users");
    } catch {
      users.value = [await api.get<User>("/auth/me")];
    }
    contacts.value = await api.get<Array<{ id: string; name: string; organization: string }>>("/contacts");
    try {
      materialCategories.value = await api.get<typeof materialCategories.value>("/material-categories");
    } catch {
      materialCategories.value = [...fallbackMaterialCategories];
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "事项加载失败");
    await router.replace("/tasks");
  } finally {
    loading.value = false;
  }
}

function materialCategoryLabel(value: string) {
  return materialCategories.value.find((item) => item.value === value)?.label || value;
}

function openEdit() {
  if (!task.value) return;
  Object.assign(editForm, {
    title: task.value.title,
    description: task.value.description,
    source: task.value.source,
    owner_id: task.value.owner_id,
    reviewer_id: task.value.reviewer_id || "",
    formal_due_at: task.value.formal_due_at,
    internal_due_at: task.value.internal_due_at,
    planned_start_at: task.value.planned_start_at,
    planned_end_at: task.value.planned_end_at,
    priority: task.value.priority,
    category: task.value.category,
    work_area: task.value.work_area,
    annual_focus: task.value.annual_focus,
    reporting_scope: task.value.reporting_scope,
    tags_text: task.value.tags.join("、"),
    experience_notes: task.value.experience_notes,
    contact_ids: [...task.value.contact_ids],
    allow_sensitive_content: task.value.allow_sensitive_content,
  });
  editVisible.value = true;
}

async function saveEdit() {
  if (!task.value) return;
  try {
    const payload: Record<string, unknown> = {
      ...editForm,
      reviewer_id: editForm.reviewer_id || null,
      tags: editForm.tags_text.split(/[、,，\s]+/).filter(Boolean),
      tags_text: undefined,
    };
    if (!canManageTask.value) {
      delete payload.owner_id;
      delete payload.reviewer_id;
      delete payload.allow_sensitive_content;
    }
    task.value = await api.patch<Task>(
      `/tasks/${task.value.id}`,
      payload,
      { "If-Match": String(task.value.version) },
    );
    editVisible.value = false;
    Message.success("事项信息已更新");
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
      conflict.value = error.problem;
      editVisible.value = false;
    } else {
      Message.error(error instanceof Error ? error.message : "保存失败");
    }
  }
}

function openAction(action: string) {
  actionKey.value = action;
  actionNote.value = "";
  actionVisible.value = true;
}

async function applyAction() {
  if (!task.value) return;
  try {
    task.value = await api.post<Task>(
      `/tasks/${task.value.id}/actions`,
      { action: actionKey.value, note: actionNote.value },
      { "If-Match": String(task.value.version) },
    );
    actionVisible.value = false;
    Message.success("事项状态已更新");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "操作失败");
  }
}

async function deleteCurrentTask() {
  if (!task.value || !canManageTask.value) return;
  try {
    const current = task.value;
    await api.delete(
      `/tasks/${current.id}`,
      { "If-Match": String(current.version) },
    );
    Message.success("事项已删除，相关审计记录仍会保留");
    await router.replace("/tasks");
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
      Message.warning("事项刚被其他电脑更新，已刷新，请确认后再删除");
      await load();
    } else {
      Message.error(error instanceof Error ? error.message : "事项删除失败");
    }
  }
}

async function toggleStep(stepId: string, done: boolean, version: number) {
  if (!task.value) return;
  try {
    await api.patch(
      `/tasks/${task.value.id}/steps/${stepId}`,
      { done },
      { "If-Match": String(version) },
    );
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "步骤更新失败");
  }
}

async function addComment() {
  if (!task.value || !comment.value.trim()) return;
  try {
    await api.post(
      `/tasks/${task.value.id}/comments`,
      {
        body: comment.value.trim(),
        parent_id: replyTo.value,
        mentioned_user_ids: mentionedUserIds.value,
      },
    );
    comment.value = "";
    replyTo.value = null;
    mentionedUserIds.value = [];
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "评论发送失败");
  }
}

async function addMaterial() {
  if (!task.value || !materialForm.name.trim() || !materialForm.category.trim()) {
    Message.warning("请填写材料名称和材料类别");
    return;
  }
  try {
    await api.post(
      `/tasks/${task.value.id}/materials`,
      materialForm,
      { "If-Match": String(task.value.version) },
    );
    materialVisible.value = false;
    materialForm.name = "";
    Message.success("材料项已添加，并已写入工作日志");
    await load();
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
      Message.warning("事项刚被其他电脑更新，已刷新材料清单，请重新确认后添加");
      await load();
    } else {
      Message.error(error instanceof Error ? error.message : "材料项创建失败");
    }
  }
}

async function addStep() {
  if (!task.value || !stepForm.title.trim()) return;
  try {
    await api.post(
      `/tasks/${task.value.id}/steps`,
      { ...stepForm, assignee_id: stepForm.assignee_id || null },
      { "If-Match": String(task.value.version) },
    );
    stepVisible.value = false;
    Object.assign(stepForm, { title: "", assignee_id: "", due_at: null });
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "步骤创建失败");
  }
}

async function addSubtask() {
  if (!task.value || !subtaskForm.title.trim() || !subtaskForm.owner_id) return;
  try {
    const child = await api.post<Task>("/tasks", {
      ...subtaskForm,
      task_type: "standard",
      source: task.value.source,
      source_kind: "project_subtask",
      parent_task_id: task.value.id,
      collaborator_ids: [],
      steps: [],
      materials: [],
    });
    subtaskVisible.value = false;
    Object.assign(subtaskForm, {
      title: "",
      owner_id: task.value.owner_id,
      formal_due_at: null,
      internal_due_at: null,
      category: task.value.category,
    });
    await load();
    Message.success(`子任务“${child.title}”已建立独立责任链`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "子任务创建失败");
  }
}

function openSubtask() {
  if (!task.value) return;
  Object.assign(subtaskForm, {
    title: "",
    owner_id: task.value.owner_id,
    formal_due_at: task.value.formal_due_at,
    internal_due_at: task.value.internal_due_at,
    category: task.value.category,
  });
  subtaskVisible.value = true;
}

async function addParticipant() {
  if (!task.value || !participantUserId.value) return;
  try {
    task.value = await api.post<Task>(
      `/tasks/${task.value.id}/participants`,
      { user_id: participantUserId.value, role: "collaborator" },
      { "If-Match": String(task.value.version) },
    );
    participantVisible.value = false;
    participantUserId.value = "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "协办人添加失败");
  }
}

async function removeParticipant(participantId: string) {
  if (!task.value) return;
  try {
    task.value = await api.delete<Task>(
      `/tasks/${task.value.id}/participants/${participantId}`,
      { "If-Match": String(task.value.version) },
    );
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "参与人移除失败");
  }
}

function openNotApplicable(material: Material) {
  notApplicableMaterial.value = material;
  notApplicableReason.value = material.not_applicable_reason;
  notApplicableVisible.value = true;
}

async function markNotApplicable() {
  if (!task.value || !notApplicableMaterial.value) return;
  if (!notApplicableReason.value.trim()) {
    Message.warning("请说明不适用原因");
    return;
  }
  try {
    await api.patch(
      `/tasks/${task.value.id}/materials/${notApplicableMaterial.value.id}`,
      { not_applicable: true, reason: notApplicableReason.value.trim() },
      { "If-Match": String(notApplicableMaterial.value.version) },
    );
    notApplicableVisible.value = false;
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "材料项更新失败");
  }
}

function openUpload(material: Material) {
  uploadMaterial.value = material;
  uploadFile.value = null;
  uploadForm.stage = "draft";
  uploadForm.is_final = false;
  uploadForm.note = "";
  uploadVisible.value = true;
}

async function uploadVersion() {
  if (!task.value || !uploadMaterial.value || !uploadFile.value) {
    Message.warning("请选择文件");
    return;
  }
  const formData = new FormData();
  formData.append("file", uploadFile.value);
  formData.append("stage", uploadForm.stage);
  formData.append("is_final", String(uploadForm.is_final));
  formData.append("note", uploadForm.note);
  try {
    task.value = await api.post<Task>(
      `/tasks/${task.value.id}/materials/${uploadMaterial.value.id}/versions`,
      formData,
      { "If-Match": String(task.value.version) },
    );
    uploadVisible.value = false;
    Message.success("材料版本已归档");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "上传失败");
  }
}

function toggleFinal(value: boolean) {
  uploadForm.is_final = value;
  if (value) uploadForm.stage = "submitted";
}

function openBatchUpload() {
  batchUploadVisible.value = true;
}

function chooseBatchFiles() {
  batchFileInput.value?.click();
}

function addBatchFiles(files: FileList | null) {
  if (!files?.length) return;
  batchQueue.addFiles(files);
  if (batchFileInput.value) batchFileInput.value.value = "";
}

function toggleBatchFinal(value: boolean) {
  batchUploadForm.is_final = value;
  if (value) batchUploadForm.stage = "submitted";
}

function canDeleteAttachment(version: MaterialVersion): boolean {
  return Boolean(
    task.value
    && task.value.status !== "archived"
    && session.user
    && (canManageTask.value || (version.uploaded_by === session.user.id && !version.is_final)),
  );
}

function openDeleteAttachment(material: Material, version: MaterialVersion) {
  deleteAttachmentMaterial.value = material;
  deleteAttachmentVersion.value = version;
  deleteAttachmentReason.value = "";
  deleteAttachmentVisible.value = true;
}

async function confirmDeleteAttachment() {
  if (!task.value || !deleteAttachmentMaterial.value || !deleteAttachmentVersion.value) return;
  if (deleteAttachmentReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的删除原因");
    return;
  }
  try {
    task.value = await api.delete<Task>(
      `/tasks/${task.value.id}/materials/${deleteAttachmentMaterial.value.id}/versions/${deleteAttachmentVersion.value.id}?reason=${encodeURIComponent(deleteAttachmentReason.value.trim())}`,
      { "If-Match": String(task.value.version) },
    );
    deleteAttachmentVisible.value = false;
    Message.success("文件已移入回收站，30 天内可恢复");
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") await load();
    Message.error(error instanceof Error ? error.message : "文件删除失败");
  }
}

async function restoreAttachment(material: Material, version: MaterialVersion) {
  if (!task.value) return;
  try {
    task.value = await api.post<Task>(
      `/tasks/${task.value.id}/materials/${material.id}/versions/${version.id}/restore`,
      undefined,
      { "If-Match": String(task.value.version) },
    );
    Message.success("文件已恢复到材料目录");
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") await load();
    Message.error(error instanceof Error ? error.message : "文件恢复失败");
  }
}

function openRollback(material: Material, version: MaterialVersion) {
  rollbackMaterial.value = material;
  rollbackVersion.value = version;
  rollbackReason.value = "";
  rollbackVisible.value = true;
}

async function rollbackAttachment() {
  if (!task.value || !rollbackMaterial.value || !rollbackVersion.value) return;
  if (rollbackReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的回退原因");
    return;
  }
  try {
    task.value = await api.post<Task>(
      `/tasks/${task.value.id}/materials/${rollbackMaterial.value.id}/versions/${rollbackVersion.value.id}/rollback`,
      { reason: rollbackReason.value.trim() },
      { "If-Match": String(task.value.version) },
    );
    rollbackVisible.value = false;
    Message.success("已引用所选旧版形成新的最终版本，原版本历史完整保留");
  } catch (error) {
    if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
      Message.warning("事项刚被其他电脑更新，已刷新版本清单，请重新确认回退");
      await load();
    } else {
      Message.error(error instanceof Error ? error.message : "材料回退失败");
    }
  }
}

async function applyConflictDraft() {
  const draftId = String(conflict.value?.draft_id || "");
  const currentVersion = String(conflict.value?.current_version || "");
  if (!draftId || !currentVersion) return;
  try {
    task.value = await api.post<Task>(
      `/conflicts/${draftId}/apply`,
      undefined,
      { "If-Match": currentVersion },
    );
    conflict.value = null;
    Message.success("已将草稿应用到最新版本");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "草稿应用失败");
  }
}

onMounted(() => {
  load();
  window.addEventListener("partyops:refresh", load);
});
onBeforeUnmount(() => window.removeEventListener("partyops:refresh", load));
</script>

<template>
  <div class="page task-detail">
    <a-spin :loading="loading" class="detail-spin">
      <template v-if="task">
        <button class="back-link" type="button" @click="router.back()">
          <IconArrowLeft /> 返回事项清单
        </button>
        <header class="task-head">
          <div>
            <div class="head-kicker">
              <span>{{ task.task_type === "quick" ? "快捷任务" : task.task_type === "standard" ? "标准任务" : "项目任务" }}</span>
              <span v-if="task.sensitivity === 'restricted'" class="restricted">敏感事项 · 最小保存</span>
            </div>
            <h1>{{ task.title }}</h1>
            <p>{{ task.source || "未填写任务来源" }}</p>
          </div>
          <div class="head-actions">
            <PageHelp
              title="事项详情怎么用"
              :tips="['按办理清单、材料和活动时间线推进同一事项。', '提交审核、退回、完成和重新打开都必须填写说明。', '归档前检查必备材料，避免形成不完整档案。']"
              help-query="事项详情"
            />
            <a-button @click="openEdit">
              <template #icon><IconEdit /></template>
              编辑
            </a-button>
            <a-popconfirm
              v-if="canManageTask"
              content="确认删除这个事项？事项会从工作清单中移除，审计记录仍会保留。"
              ok-text="确认删除"
              cancel-text="取消"
              @ok="deleteCurrentTask"
            >
              <a-button status="danger">
                <template #icon><IconDelete /></template>
                删除事项
              </a-button>
            </a-popconfirm>
            <a-button
              v-for="[key, label, type] in actionOptions"
              :key="key"
              :type="type === 'primary' ? 'primary' : 'outline'"
              @click="openAction(key)"
            >
              {{ label }}
            </a-button>
          </div>
        </header>

        <section class="summary-strip">
          <div>
            <span>当前状态</span>
            <TaskStatusTag :status="task.status" />
          </div>
          <div>
            <span>主办人</span>
            <strong>{{ userNames[task.owner_id] || "未知" }}</strong>
          </div>
          <div>
            <span>内部完成</span>
            <strong>{{ formatServerTime(task.internal_due_at, "YYYY-MM-DD HH:mm", "未设置") }}</strong>
          </div>
          <div>
            <span>正式截止</span>
            <strong>{{ formatServerTime(task.formal_due_at, "YYYY-MM-DD HH:mm", "未设置") }}</strong>
          </div>
          <div>
            <span>材料完整性</span>
            <strong :class="task.missing_required_materials ? 'text-danger' : 'text-success'">
              {{ task.missing_required_materials ? `缺 ${task.missing_required_materials} 项` : "必备材料齐全" }}
            </strong>
          </div>
        </section>
        <section class="context-line">
          <span>工作类别：{{ task.category || "未分类" }}</span>
          <span>工作领域：{{ task.work_area || "未设置" }}</span>
          <span v-if="task.annual_focus">年度重点：{{ task.annual_focus }}</span>
          <span v-if="task.reporting_scope">汇报口径：{{ task.reporting_scope }}</span>
          <span v-if="task.tags.length">标签：{{ task.tags.join(" · ") }}</span>
          <span v-if="task.parent_task_id">项目子任务</span>
          <span v-if="task.contact_ids.length">联系人：{{ task.contact_ids.map((id) => contacts.find((item) => item.id === id)?.name).filter(Boolean).join("、") }}</span>
          <a v-if="task.status === 'archived'" :href="downloadUrl(`/tasks/${task.id}/archive-package`)" target="_blank">
            <IconDownload /> 下载规范归档包
          </a>
        </section>

        <a-tabs v-model:active-key="tab" class="detail-tabs">
          <a-tab-pane v-if="task.task_type === 'project'" key="subtasks" title="项目子任务">
            <div class="tab-toolbar">
              <p>每个子任务都有独立责任人、时限、材料和闭环状态。</p>
              <a-button size="small" @click="openSubtask"><template #icon><IconPlus /></template> 添加子任务</a-button>
            </div>
            <div v-if="task.subtasks.length" class="subtask-list">
              <RouterLink v-for="child in task.subtasks" :key="child.id" :to="`/tasks/${child.id}`">
                <div><strong>{{ child.title }}</strong><span>{{ userNames[child.owner_id] || "未知责任人" }}</span></div>
                <TaskStatusTag :status="child.status" />
                <span>{{ formatServerTime(child.internal_due_at, "MM-DD HH:mm", "未设内部节点") }}</span>
                <b :class="child.missing_required_materials ? 'text-danger' : 'text-success'">
                  {{ child.missing_required_materials ? `缺 ${child.missing_required_materials} 项` : "材料正常" }}
                </b>
              </RouterLink>
            </div>
            <div v-else class="empty-state">尚无子任务；按责任边界拆分即可，不必过度细化。</div>
          </a-tab-pane>

          <a-tab-pane key="steps" title="办理清单">
            <div class="tab-toolbar">
              <p>复杂事项按需拆解；普通事项不强制填写进度百分比。</p>
              <a-button size="small" @click="stepVisible = true">
                <template #icon><IconPlus /></template> 添加步骤
              </a-button>
            </div>
            <div v-if="task.steps.length" class="step-list">
              <label v-for="(step, index) in task.steps" :key="step.id" class="step-row">
                <a-checkbox
                  :model-value="step.done"
                  @change="toggleStep(step.id, Boolean($event), step.version)"
                />
                <span class="step-number">{{ String(index + 1).padStart(2, "0") }}</span>
                <span :class="{ completed: step.done }">{{ step.title }}</span>
                <small>{{ step.assignee_id ? userNames[step.assignee_id] : "未分配" }}</small>
              </label>
            </div>
            <div v-else class="empty-state">快捷任务无需拆解；如有需要可增加步骤。</div>
          </a-tab-pane>

          <a-tab-pane key="materials" title="一事一档">
            <div class="tab-toolbar">
              <p>一个材料项保留多版过程，但只确认一个最终版本。</p>
              <a-space v-if="task.status !== 'archived'">
                <a-button size="small" @click="openBatchUpload"><template #icon><IconUpload /></template>批量上传文件</a-button>
                <a-button size="small" @click="materialVisible = true"><template #icon><IconPlus /></template> 添加材料项</a-button>
              </a-space>
            </div>
            <div v-if="task.materials.length" class="material-list">
              <section v-for="material in task.materials" :key="material.id" class="material-row">
                <div class="material-info">
                  <IconFile />
                  <div>
                    <strong>{{ material.name }}</strong>
                    <span>{{ materialCategoryLabel(material.category) }} · {{ material.required ? "必备" : "可选" }}</span>
                  </div>
                </div>
                <div class="versions">
                  <div v-for="version in material.versions" :key="version.id" class="version-chip">
                    <a :href="downloadUrl(`/attachments/${version.id}/download`)" target="_blank">
                      <span>v{{ version.version_no }}</span>
                      {{ version.original_name }}
                      <b v-if="version.is_final">最终</b>
                      <IconDownload />
                    </a>
                    <a-button
                      v-if="canManageTask && task.status !== 'archived' && !version.is_final && material.versions.some((candidate) => candidate.is_final)"
                      size="mini"
                      type="text"
                      @click="openRollback(material, version)"
                    >回退到此版</a-button>
                    <a-button v-if="canDeleteAttachment(version)" size="mini" type="text" status="danger" @click="openDeleteAttachment(material, version)"><template #icon><IconDelete /></template>删除</a-button>
                  </div>
                  <span v-if="!material.versions.length" class="muted">尚未上传</span>
                </div>
                <div class="material-status">
                  <span :class="material.complete ? 'text-success' : 'text-danger'">
                    {{ material.complete ? "已齐全" : "待补充" }}
                  </span>
                  <a-button v-if="task.status !== 'archived' && !material.versions.some((version) => version.is_final)" size="mini" @click="openUpload(material)">
                    <template #icon><IconUpload /></template> 上传版本
                  </a-button>
                  <small v-else class="muted">{{ task.status === "archived" ? "归档锁定" : "终稿已锁定" }}</small>
                  <a-button v-if="task.status !== 'archived' && !material.not_applicable && !material.versions.some((version) => version.is_final)" size="mini" type="text" @click="openNotApplicable(material)">
                    不适用
                  </a-button>
                </div>
                <details v-if="(material.deleted_versions || []).length" class="recycle-bin">
                  <summary>回收站（{{ (material.deleted_versions || []).length }}）</summary>
                  <div v-for="version in (material.deleted_versions || [])" :key="version.id" class="recycled-version">
                    <div><strong>{{ version.original_name }}</strong><span>{{ version.delete_reason }} · {{ formatServerTime(version.deleted_at, 'YYYY-MM-DD HH:mm') }}</span></div>
                    <a-button v-if="canManageTask || version.uploaded_by === session.user?.id" size="mini" @click="restoreAttachment(material, version)">恢复</a-button>
                  </div>
                </details>
              </section>
            </div>
            <div v-else class="empty-state">尚未设置材料清单。</div>
          </a-tab-pane>

          <a-tab-pane key="comments" title="协同反馈">
            <div class="comment-composer">
              <div class="comment-input">
                <a-alert v-if="replyTo" type="info" closable @close="replyTo = null">正在回复 {{ userNames[replyTarget?.author_id || ''] || '协同人员' }} 的说明</a-alert>
                <a-textarea v-model="comment" :auto-size="{ minRows: 3, maxRows: 6 }" placeholder="记录关键反馈、协作说明或交接事项" />
                <a-select v-model="mentionedUserIds" multiple allow-search allow-clear placeholder="提及相关人员（可选）">
                  <a-option v-for="user in mentionableUsers" :key="user.id" :value="user.id">@{{ user.display_name }}</a-option>
                </a-select>
              </div>
              <a-button type="primary" :disabled="!comment.trim()" @click="addComment">
                <template #icon><IconMessage /></template> 发送说明
              </a-button>
            </div>
            <div v-if="task.comments.length" class="comment-list">
              <article v-for="row in threadedComments" :key="row.item.id" :class="{ reply: row.depth > 0 }" :style="{ marginLeft: `${Math.min(row.depth, 4) * 24}px` }">
                <div>
                  <strong>{{ userNames[row.item.author_id] || "协同人员" }}</strong>
                  <span><small v-if="row.item.parent_id">回复</small><time>{{ formatServerTime(row.item.created_at, "MM-DD HH:mm") }}</time></span>
                </div>
                <p>{{ row.item.body }}</p>
                <div v-if="row.item.mentioned_user_ids.length" class="comment-mentions">
                  <span v-for="userId in row.item.mentioned_user_ids" :key="userId">@{{ userNames[userId] || "协同人员" }}</span>
                </div>
                <button type="button" @click="replyTo = row.item.id">回复</button>
              </article>
            </div>
            <div v-else class="empty-state">尚无协同说明。</div>
          </a-tab-pane>
          <a-tab-pane key="related" title="相关内容与时间线">
            <ObjectContextPanel object-type="task" :object-id="task.id" />
          </a-tab-pane>

          <a-tab-pane key="participants" title="协同分工">
            <div class="tab-toolbar">
              <p>主办人负责推进，协办人共同办理，审核人完成把关。</p>
              <a-button v-if="canManageTask" size="small" @click="participantVisible = true"><template #icon><IconPlus /></template> 添加协办人</a-button>
            </div>
            <div class="participant-list">
              <article v-for="item in task.participants" :key="item.id">
                <div><strong>{{ userNames[item.user_id] || "未知用户" }}</strong><span>{{ item.role === "owner" ? "主办人" : item.role === "reviewer" ? "审核人" : "协办人" }}</span></div>
                <a-button v-if="canManageTask && item.role === 'collaborator'" size="mini" type="text" @click="removeParticipant(item.id)">移除</a-button>
              </article>
            </div>
          </a-tab-pane>

          <a-tab-pane key="events" title="全过程留痕">
            <a-timeline>
              <a-timeline-item v-for="event in task.events" :key="event.id" :label="formatServerTime(event.created_at, 'MM-DD HH:mm')">
                <strong>{{ userNames[event.actor_id] || "系统" }}</strong>
                将状态更新为 <TaskStatusTag :status="event.to_status" />
                <p v-if="event.note">{{ event.note }}</p>
              </a-timeline-item>
            </a-timeline>
          </a-tab-pane>
        </a-tabs>
      </template>
    </a-spin>

    <a-modal v-model:visible="editVisible" title="编辑事项" width="640px" @ok="saveEdit">
      <a-form :model="editForm" layout="vertical">
        <a-form-item label="事项名称"><a-input v-model="editForm.title" /></a-form-item>
        <a-form-item label="办理说明">
          <a-textarea v-model="editForm.description" :disabled="task?.sensitivity === 'restricted' && !editForm.allow_sensitive_content" :auto-size="{ minRows: 3, maxRows: 6 }" />
        </a-form-item>
        <div class="two-columns">
          <a-form-item label="工作类别"><a-input v-model="editForm.category" placeholder="如：党员教育、材料报送" /></a-form-item>
          <a-form-item label="工作领域"><a-input v-model="editForm.work_area" placeholder="如：组织建设、宣传教育" /></a-form-item>
          <a-form-item label="标签"><a-input v-model="editForm.tags_text" placeholder="用顿号或逗号分隔" /></a-form-item>
          <a-form-item label="年度重点"><a-input v-model="editForm.annual_focus" placeholder="关联年度重点工作" /></a-form-item>
        </div>
        <a-form-item label="汇报口径"><a-input v-model="editForm.reporting_scope" placeholder="例如：党委周例会、季度汇报" /></a-form-item>
        <a-form-item label="经验与交接说明">
          <a-textarea v-model="editForm.experience_notes" :auto-size="{ minRows: 2, maxRows: 5 }" placeholder="下期可直接复用的口径、联系人和易错点" />
        </a-form-item>
        <a-form-item label="常用联系人">
          <a-select v-model="editForm.contact_ids" multiple allow-search>
            <a-option v-for="contact in contacts" :key="contact.id" :value="contact.id">{{ contact.name }} · {{ contact.organization }}</a-option>
          </a-select>
        </a-form-item>
        <a-form-item v-if="canManageTask && task?.sensitivity === 'restricted'" label="敏感内容保存授权">
          <a-switch v-model="editForm.allow_sensitive_content" />
          <span class="inline-note">开启后才允许在本机保存正文和附件，操作会留痕。</span>
        </a-form-item>
        <div class="two-columns">
          <a-form-item v-if="canManageTask" label="主办人">
            <a-select v-model="editForm.owner_id"><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select>
          </a-form-item>
          <a-form-item v-if="canManageTask" label="审核人">
            <a-select v-model="editForm.reviewer_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select>
          </a-form-item>
          <a-form-item label="内部完成时间"><a-date-picker v-model="editForm.internal_due_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
          <a-form-item label="正式截止时间"><a-date-picker v-model="editForm.formal_due_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
          <a-form-item label="计划开始"><a-date-picker v-model="editForm.planned_start_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
          <a-form-item label="计划完成"><a-date-picker v-model="editForm.planned_end_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
        </div>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="actionVisible" title="确认办理动作" @ok="applyAction">
      <p>该动作将写入全过程留痕，并同步给协同人员。</p>
      <a-textarea v-model="actionNote" :placeholder="actionKey === 'reopen' || actionKey === 'return' ? '请填写原因（必填）' : '可填写办理说明'" :auto-size="{ minRows: 3, maxRows: 5 }" />
    </a-modal>

    <a-modal v-model:visible="materialVisible" title="添加材料项" @ok="addMaterial">
      <a-form :model="materialForm" layout="vertical">
        <a-form-item label="材料名称"><a-input v-model="materialForm.name" /></a-form-item>
        <a-form-item label="材料类别">
          <a-select
            v-model="materialForm.category"
            allow-create
            allow-search
            placeholder="选择预设类别，或输入新类别后按回车"
          >
            <a-option v-for="category in materialCategories" :key="category.value" :value="category.value">
              {{ category.label }}<template v-if="category.custom">（自定义）</template>
            </a-option>
          </a-select>
          <small class="muted">可直接输入单位自己的材料类别；使用一次后会保留在类别列表中。</small>
        </a-form-item>
        <a-form-item label="是否必备"><a-switch v-model="materialForm.required" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="stepVisible" title="添加办理步骤" @ok="addStep">
      <a-form :model="stepForm" layout="vertical">
        <a-form-item label="步骤名称"><a-input v-model="stepForm.title" /></a-form-item>
        <a-form-item label="责任人"><a-select v-model="stepForm.assignee_id" allow-clear><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item>
        <a-form-item label="节点时间"><a-date-picker v-model="stepForm.due_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="subtaskVisible" title="添加项目子任务" @ok="addSubtask">
      <a-form :model="subtaskForm" layout="vertical">
        <a-form-item label="子任务名称"><a-input v-model="subtaskForm.title" /></a-form-item>
        <div class="two-columns">
          <a-form-item label="责任人"><a-select v-model="subtaskForm.owner_id"><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item>
          <a-form-item label="工作类别"><a-input v-model="subtaskForm.category" /></a-form-item>
          <a-form-item label="内部完成"><a-date-picker v-model="subtaskForm.internal_due_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
          <a-form-item label="正式截止"><a-date-picker v-model="subtaskForm.formal_due_at" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" /></a-form-item>
        </div>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="participantVisible" title="添加协办人" @ok="addParticipant">
      <a-select v-model="participantUserId" placeholder="选择协办人" style="width: 100%">
        <a-option v-for="user in users.filter((candidate) => !task?.participants.some((item) => item.user_id === candidate.id && item.role === 'collaborator'))" :key="user.id" :value="user.id">{{ user.display_name }}</a-option>
      </a-select>
    </a-modal>

    <a-modal v-model:visible="notApplicableVisible" title="材料标记为不适用" @ok="markNotApplicable">
      <p>归档前会保留此说明，避免把真实“不适用”误判为缺项。</p>
      <a-textarea v-model="notApplicableReason" placeholder="请填写不适用原因" :auto-size="{ minRows: 3, maxRows: 5 }" />
    </a-modal>

    <a-modal v-model:visible="uploadVisible" title="上传材料版本" @ok="uploadVersion">
      <a-form :model="uploadForm" layout="vertical">
        <a-form-item label="选择文件">
          <input type="file" @change="uploadFile = ($event.target as HTMLInputElement).files?.[0] || null" />
        </a-form-item>
        <a-form-item label="版本阶段">
          <a-select v-model="uploadForm.stage">
            <a-option value="draft">初稿</a-option>
            <a-option value="revision">修改稿</a-option>
            <a-option value="leader_approved">领导审定稿</a-option>
            <a-option value="submitted">实际报送稿</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="确认最终版本"><a-switch :model-value="uploadForm.is_final" @change="toggleFinal(Boolean($event))" /></a-form-item>
        <a-form-item label="版本说明"><a-input v-model="uploadForm.note" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:visible="batchUploadVisible" title="批量上传业务材料" width="680px" :footer="false" :mask-closable="false">
      <a-alert type="info" show-icon>一次可选择多个文件。每个文件会建立独立材料项；最多同时上传 2 个，单个失败不会影响其他文件。</a-alert>
      <a-form :model="batchUploadForm" layout="vertical" class="batch-upload-form">
        <div class="two-columns">
          <a-form-item label="材料类别">
            <a-select v-model="batchUploadForm.category" allow-create allow-search>
              <a-option v-for="category in materialCategories" :key="category.value" :value="category.value">{{ category.label }}</a-option>
            </a-select>
          </a-form-item>
          <a-form-item label="版本阶段"><a-select v-model="batchUploadForm.stage"><a-option value="draft">初稿</a-option><a-option value="revision">修改稿</a-option><a-option value="leader_approved">领导审定稿</a-option><a-option value="submitted">实际报送稿</a-option></a-select></a-form-item>
        </div>
        <a-space><a-checkbox v-model="batchUploadForm.required">设为必备材料</a-checkbox><span>确认最终版本</span><a-switch :model-value="batchUploadForm.is_final" @change="toggleBatchFinal(Boolean($event))" /></a-space>
        <a-form-item label="统一说明（可选）"><a-input v-model="batchUploadForm.note" /></a-form-item>
        <input ref="batchFileInput" hidden type="file" multiple @change="addBatchFiles(($event.target as HTMLInputElement).files)" />
        <a-button type="primary" long @click="chooseBatchFiles"><template #icon><IconUpload /></template>选择多个文件并开始上传</a-button>
      </a-form>
      <BusinessUploadQueue :items="batchUploadItems" @retry="batchQueue.retry" @cancel="batchQueue.cancel" @clear="batchQueue.clearSettled" />
    </a-modal>

    <a-modal v-model:visible="deleteAttachmentVisible" title="将文件移入回收站" @ok="confirmDeleteAttachment">
      <a-alert type="warning" show-icon>文件会保留 30 天并可恢复。已定稿文件删除后，材料项会重新显示“待补充”。</a-alert>
      <a-form :model="{ reason: deleteAttachmentReason }" layout="vertical" class="delete-attachment-form"><a-form-item label="删除原因（必填）"><a-textarea v-model="deleteAttachmentReason" :max-length="2000" show-word-limit :auto-size="{ minRows: 3, maxRows: 6 }" /></a-form-item></a-form>
    </a-modal>

    <a-modal v-model:visible="rollbackVisible" title="回退材料版本" @ok="rollbackAttachment">
      <a-alert type="warning" show-icon>
        将引用 v{{ rollbackVersion?.version_no }}“{{ rollbackVersion?.original_name }}”形成一个新的最终版本；不会删除或覆盖任何历史文件。
      </a-alert>
      <a-form :model="{ reason: rollbackReason }" layout="vertical" style="margin-top: 16px">
        <a-form-item label="回退原因（必填）">
          <a-textarea v-model="rollbackReason" :max-length="1000" show-word-limit placeholder="例如：现终稿引用数据有误，经复核恢复上一版" :auto-size="{ minRows: 3, maxRows: 6 }" />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal :visible="Boolean(conflict)" title="发现并发修改" :footer="false" @cancel="conflict = null">
      <a-alert type="warning">另一位同事已先保存。你的内容已自动另存为冲突草稿，没有丢失。</a-alert>
      <div class="conflict-table">
        <div class="conflict-head"><span>字段</span><span>当前值</span><span>我的提交</span></div>
        <div v-for="row in conflictRows" :key="row.field"><strong>{{ fieldLabel(row.field) }}</strong><span>{{ localizeEmbeddedCodes(row.current ?? "—") }}</span><span>{{ localizeEmbeddedCodes(row.submitted ?? "—") }}</span></div>
      </div>
      <a-space>
        <a-button @click="conflict = null; load()">刷新最新版本</a-button>
        <a-button @click="conflict = null">保留草稿，稍后处理</a-button>
        <a-button type="primary" @click="applyConflictDraft">以我的提交更新最新版本</a-button>
      </a-space>
    </a-modal>
  </div>
</template>

<style scoped>
.detail-spin {
  display: block;
  min-height: 460px;
}

.back-link {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 18px;
  padding: 0;
  color: var(--muted);
  font-size: 13px;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.task-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding-bottom: 24px;
}

.head-kicker {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  color: var(--cinnabar);
  font-size: 11px;
  letter-spacing: 0.12em;
}

.head-kicker .restricted {
  color: #7f241c;
}

.task-head h1 {
  margin: 0;
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", SimSun, serif;
  font-size: 30px;
  font-weight: 600;
}

.task-head p {
  margin: 8px 0 0;
  color: var(--muted);
}

.head-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.summary-strip {
  display: grid;
  grid-template-columns: 1fr 1fr 1.25fr 1.25fr 1.1fr;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.summary-strip > div {
  min-height: 82px;
  padding: 17px 18px;
  border-right: 1px solid var(--line-light);
}

.summary-strip > div:last-child {
  border-right: 0;
}

.summary-strip span,
.summary-strip strong {
  display: block;
}

.summary-strip > div > span {
  margin-bottom: 9px;
  color: var(--muted);
  font-size: 11px;
}

.summary-strip strong {
  font-size: 13px;
}

.context-line {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 42px;
  color: var(--muted);
  font-size: 11px;
  border-bottom: 1px solid var(--line-light);
}

.context-line a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  color: var(--cinnabar);
}

.detail-tabs {
  margin-top: 22px;
}

.tab-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0 14px;
}

.tab-toolbar p {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.step-list,
.material-list,
.comment-list {
  border-top: 1px solid var(--line);
}

.step-row {
  display: grid;
  min-height: 60px;
  align-items: center;
  grid-template-columns: 28px 42px 1fr 120px;
  border-bottom: 1px solid var(--line-light);
}

.step-number {
  color: #a79d91;
  font-family: Georgia, serif;
  font-size: 12px;
}

.step-row small {
  color: var(--muted);
  text-align: right;
}

.completed {
  color: var(--muted);
  text-decoration: line-through;
}

.material-row {
  display: grid;
  min-height: 86px;
  align-items: center;
  grid-template-columns: 260px 1fr 150px;
  gap: 20px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line-light);
}

.material-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.material-info strong,
.material-info span {
  display: block;
}

.material-info span {
  margin-top: 5px;
  color: var(--muted);
  font-size: 11px;
}

.versions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.version-chip {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 1px solid var(--line);
  background: #efe7db;
}

.versions a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 9px;
  font-size: 11px;
  background: transparent;
  border: 0;
}

.versions a b {
  padding: 1px 4px;
  color: #fff;
  background: var(--cinnabar);
}

.material-status {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.recycle-bin {
  grid-column: 1 / -1;
  padding: 10px 12px;
  color: var(--muted);
  background: rgba(98, 84, 66, 0.045);
  border-left: 2px solid var(--line);
}

.recycle-bin summary { cursor: pointer; font-size: 12px; }

.recycled-version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0 0;
}

.recycled-version strong,
.recycled-version span { display: block; }
.recycled-version span { margin-top: 3px; font-size: 11px; }
.batch-upload-form { margin-top: 16px; }
.delete-attachment-form { margin-top: 14px; }

.comment-composer {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 24px;
}

.comment-input {
  flex: 1;
}

.comment-input :deep(.arco-alert) {
  margin-bottom: 6px;
}

.comment-input :deep(.arco-select-view) {
  margin-top: 8px;
}

.comment-list article {
  padding: 16px 4px;
  border-bottom: 1px solid var(--line-light);
}

.comment-list article.reply {
  padding-right: 12px;
  padding-left: 12px;
  background: rgba(180, 35, 24, 0.035);
  border-left: 2px solid rgba(180, 35, 24, 0.24);
}

.comment-list article > button {
  margin-top: 7px;
  padding: 0;
  color: var(--cinnabar);
  font-size: 11px;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.subtask-list,
.participant-list {
  border-top: 1px solid var(--line);
}

.subtask-list > a {
  display: grid;
  min-height: 68px;
  align-items: center;
  grid-template-columns: minmax(260px, 1fr) 110px 160px 90px;
  gap: 18px;
  border-bottom: 1px solid var(--line-light);
}

.subtask-list strong,
.subtask-list span {
  display: block;
}

.subtask-list div > span {
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
}

.subtask-list > a > span,
.subtask-list > a > b {
  font-size: 12px;
  font-weight: 400;
  text-align: right;
}

.participant-list article {
  display: flex;
  min-height: 62px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line-light);
}

.participant-list strong,
.participant-list span {
  display: block;
}

.participant-list span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}

.comment-list article > div {
  display: flex;
  justify-content: space-between;
}

.comment-list time {
  color: var(--muted);
  font-size: 11px;
}

.comment-list p {
  margin: 9px 0 0;
  line-height: 1.7;
}

.comment-mentions {
  display: flex !important;
  justify-content: flex-start !important;
  gap: 6px;
  margin-top: 7px;
}

.comment-mentions span {
  padding: 2px 6px;
  color: var(--cinnabar);
  font-size: 10px;
  background: rgba(180, 35, 24, 0.08);
}

.two-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.inline-note {
  margin-left: 10px;
  color: var(--muted);
  font-size: 12px;
}

.conflict-table {
  max-height: 280px;
  overflow: auto;
  margin: 14px 0;
  background: #eee6d9;
}

.conflict-table > div {
  display: grid;
  padding: 9px 11px;
  grid-template-columns: 110px 1fr 1fr;
  gap: 12px;
  font-size: 11px;
  border-bottom: 1px solid #d9cfc2;
}

.conflict-head {
  color: var(--muted);
}

@media (max-width: 860px) {
  .material-row { grid-template-columns: 1fr; gap: 10px; padding: 16px 0; }
  .material-status { justify-content: flex-start; flex-wrap: wrap; }
  .two-columns { grid-template-columns: 1fr; }
  .tab-toolbar { align-items: flex-start; flex-direction: column; }
}
</style>
