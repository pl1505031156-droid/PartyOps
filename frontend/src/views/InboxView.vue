<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { IconDelete, IconFile, IconScan, IconSend } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, uploadFormWithProgress } from "../api";
import PageHelp from "../components/PageHelp.vue";
import BusinessUploadQueue from "../components/BusinessUploadQueue.vue";
import { useUploadQueue } from "../composables/useUploadQueue";
import type { Task, User } from "../types";

const router = useRouter();
const users = ref<User[]>([]);
const selectedFiles = ref<File[]>([]);
const selectedFile = computed(() => selectedFiles.value[0] || null);
const fileInput = ref<HTMLInputElement | null>(null);
const parsing = ref(false);
const creating = ref(false);
const createdTaskId = ref("");
const inboxArchiveTaskId = ref("");
const candidate = ref<null | {
  title: string;
  formal_due_at: string | null;
  requirements: string[];
  extracted_text: string;
  source_kind: string;
  warnings: string[];
  source_filename: string;
  parser_label: string;
}>(null);
const form = reactive({
  pastedText: "",
  title: "",
  formalDueAt: null as string | null,
  ownerId: "",
  source: "快速收件箱",
});
const identifiedCount = computed(
  () =>
    Number(Boolean(candidate.value?.title)) +
    Number(Boolean(candidate.value?.formal_due_at)) +
    Number(Boolean(candidate.value?.requirements.length)),
);
const inboxUploadQueue = useUploadQueue(async (item, context) => {
  if (!inboxArchiveTaskId.value) throw new Error("事项尚未建立，请重新确认后重试。");
  const upload = new FormData();
  upload.append("file", item.file);
  upload.append("category", "notice");
  upload.append("stage", "submitted");
  upload.append("is_final", "true");
  upload.append("note", "快速收件箱原始文件");
  upload.append("client_upload_id", item.clientUploadId);
  await uploadFormWithProgress<Task>(
    `/tasks/${inboxArchiveTaskId.value}/materials/quick-upload`,
    upload,
    { signal: context.signal, onProgress: context.onProgress },
  );
}, 2);
const inboxUploadItems = inboxUploadQueue.items;

onMounted(async () => {
  try {
    users.value = await api.get<User[]>("/users");
  } catch {
    users.value = [await api.get<User>("/auth/me")];
  }
  form.ownerId = users.value[0]?.id || "";
});

async function parse() {
  if (!form.pastedText.trim() && !selectedFile.value) {
    Message.warning("请粘贴通知或选择文件");
    return;
  }
  const data = new FormData();
  data.append("pasted_text", form.pastedText);
  if (selectedFile.value) data.append("file", selectedFile.value);
  parsing.value = true;
  try {
    const result = await api.post<NonNullable<typeof candidate.value>>("/intake/parse", data);
    candidate.value = result;
    form.title = result.title;
    form.formalDueAt = result.formal_due_at;
    Message.success("已提取候选信息，请人工确认");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "解析失败");
  } finally {
    parsing.value = false;
  }
}

function selectFiles(files: FileList | null) {
  selectedFiles.value = Array.from(files || []);
  candidate.value = null;
  createdTaskId.value = "";
  inboxUploadQueue.clearSettled();
}

// 保留旧页面测试和书签脚本使用的单文件入口；真实界面统一走多文件选择。
function selectFile(file: File | null) {
  selectedFiles.value = file ? [file] : [];
  candidate.value = null;
  createdTaskId.value = "";
  inboxUploadQueue.clearSettled();
}

function resetIntake() {
  selectedFiles.value = [];
  candidate.value = null;
  form.pastedText = "";
  form.title = "";
  form.formalDueAt = null;
  form.source = "快速收件箱";
  if (fileInput.value) fileInput.value.value = "";
}

async function create() {
  if (createdTaskId.value) {
    if (inboxUploadQueue.pending.value) {
      Message.info("文件仍在归档，请稍候。");
      return;
    }
    if (inboxUploadQueue.failed.value) {
      Message.warning("请先重试失败文件，或稍后在事项中继续补充。");
      return;
    }
    await router.push(`/tasks/${createdTaskId.value}`);
    return;
  }
  if (!form.title || !form.ownerId) {
    Message.warning("请确认事项名称与责任人");
    return;
  }
  creating.value = true;
  try {
    let task = await api.post<Task>("/tasks", {
      title: form.title,
      description: candidate.value?.extracted_text || "",
      task_type: "quick",
      sensitivity: "normal",
      priority: "normal",
      source: form.source,
      source_kind: candidate.value?.source_kind || "wechat",
      formal_due_at: form.formalDueAt,
      internal_due_at: null,
      owner_id: form.ownerId,
      reviewer_id: null,
      collaborator_ids: [],
      steps: [],
      materials: [],
    });
    createdTaskId.value = task.id;
    if (selectedFiles.value.length) {
      inboxArchiveTaskId.value = task.id;
      inboxUploadQueue.addFiles(selectedFiles.value);
      await inboxUploadQueue.waitForIdle();
      if (inboxUploadQueue.failed.value) {
        Message.warning("事项已创建，但有原始文件未归档；请重试失败文件后进入事项。");
        return;
      }
    }
    Message.success(selectedFiles.value.length > 1 ? "事项已创建，原始文件已逐项归档" : "事项已创建，原始通知已归档");
    await router.push(`/tasks/${task.id}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "创建失败");
  } finally {
    creating.value = false;
  }
}
</script>

<template>
  <div class="page inbox-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">快速收件箱</h1>
        <p class="page-description">粘贴工作群通知或上传文件，本地提取候选信息；确认后才进入任务闭环。</p>
      </div>
      <a-space>
        <PageHelp
          title="快速收件箱怎么用"
          :tips="['粘贴通知或上传原件后，先核对系统提取的候选信息。', '确认前不会创建事项，也不会改变原文件。', '原始通知随事项归档，避免重复录入。']"
          help-query="快速收件箱"
        />
        <a-button v-if="form.pastedText || selectedFiles.length || candidate" @click="resetIntake">
          <template #icon><IconDelete /></template>清空本次收件
        </a-button>
      </a-space>
    </header>
    <div class="intake-steps" aria-label="快速收件流程">
      <span class="active"><b>1</b>放入通知</span>
      <span :class="{ active: candidate }"><b>2</b>核对候选</span>
      <span :class="{ active: false }"><b>3</b>创建事项并归档原件</span>
    </div>
    <div class="inbox-grid">
      <section class="source-pane">
        <div class="pane-title"><span>01</span><h2>放入原始通知</h2></div>
        <a-textarea
          v-model="form.pastedText"
          :auto-size="{ minRows: 12, maxRows: 18 }"
          placeholder="将微信通知、上级要求或办理说明粘贴到这里……"
        />
        <label class="file-drop">
          <input ref="fileInput" type="file" multiple accept=".docx,.doc,.wps,.pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,.txt,.md" @change="selectFiles(($event.target as HTMLInputElement).files)" />
          <IconFile :size="24" />
          <span>{{ selectedFiles.length > 1 ? `已选择 ${selectedFiles.length} 个文件` : selectedFile?.name || "选择一个或多个 Word、WPS、PDF、图片或文本附件" }}</span>
          <small>{{ selectedFile ? `以“${selectedFile.name}”提取候选信息；全部文件会分别归档 · 点击可重新选择` : "单个文件不超过 50 MB，识别过程留在主机本地" }}</small>
        </label>
        <BusinessUploadQueue :items="inboxUploadItems" @retry="inboxUploadQueue.retry" @cancel="inboxUploadQueue.cancel" @clear="inboxUploadQueue.clearSettled" />
        <a-button type="primary" size="large" long :loading="parsing" @click="parse">
          <template #icon><IconScan /></template>
          本地识别并进入确认
        </a-button>
      </section>
      <section class="confirm-pane" :class="{ inactive: !candidate }">
        <div class="pane-title"><span>02</span><h2>人工确认</h2></div>
        <template v-if="candidate">
          <div class="recognition-summary">
            <span>本地识别完成</span>
            <strong>{{ identifiedCount }} / 3 个关键信息有候选</strong>
            <small>{{ candidate.parser_label }}{{ candidate.source_filename ? ` · ${candidate.source_filename}` : "" }}</small>
          </div>
          <a-alert v-for="warning in candidate.warnings" :key="warning" type="warning" class="warning">{{ warning }}</a-alert>
          <a-form :model="form" layout="vertical">
            <a-form-item label="事项名称" required><a-input v-model="form.title" /></a-form-item>
            <a-form-item label="正式截止时间">
              <a-date-picker v-model="form.formalDueAt" show-time value-format="YYYY-MM-DDTHH:mm:ssZ" style="width: 100%" />
            </a-form-item>
            <a-form-item label="责任人" required>
              <a-select v-model="form.ownerId"><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select>
            </a-form-item>
            <a-form-item label="任务来源"><a-input v-model="form.source" /></a-form-item>
          </a-form>
          <div v-if="candidate.requirements.length" class="requirements">
            <strong>识别到的报送要求</strong>
            <p v-for="item in candidate.requirements" :key="item">{{ item }}</p>
          </div>
          <details v-if="candidate.extracted_text" class="source-preview">
            <summary>核对识别原文（{{ candidate.extracted_text.length }} 字）</summary>
            <pre>{{ candidate.extracted_text }}</pre>
          </details>
          <a-button type="primary" size="large" long :loading="creating" @click="create">
            <template #icon><IconSend /></template>
            {{ createdTaskId ? "进入已创建事项" : "确认并创建事项" }}
          </a-button>
        </template>
        <div v-else class="waiting">
          <span>02</span>
          <p>完成左侧识别后，在这里确认名称、时限和责任人。</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.inbox-grid {
  display: grid;
  grid-template-columns: 1fr 0.86fr;
  gap: 36px;
}

.intake-steps {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin: -4px 0 22px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.intake-steps span {
  display: flex;
  min-height: 48px;
  align-items: center;
  gap: 9px;
  padding: 0 14px;
  color: var(--muted);
  font-size: 12px;
  border-right: 1px solid var(--line-light);
}

.intake-steps span:last-child {
  border-right: 0;
}

.intake-steps b {
  display: grid;
  width: 22px;
  height: 22px;
  place-items: center;
  color: var(--muted);
  font: 11px Georgia, serif;
  border: 1px solid var(--line);
  border-radius: 50%;
}

.intake-steps .active {
  color: var(--charcoal);
  box-shadow: inset 0 -2px var(--cinnabar);
}

.intake-steps .active b {
  color: var(--cinnabar);
  border-color: rgba(180, 35, 24, 0.5);
}

.source-pane,
.confirm-pane {
  padding: 26px;
  background: rgba(251, 248, 241, 0.66);
  border: 1px solid var(--line);
}

.pane-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 20px;
}

.pane-title span {
  color: var(--cinnabar);
  font-family: Georgia, serif;
  font-size: 13px;
}

.pane-title h2 {
  margin: 0;
  font-size: 18px;
}

.file-drop {
  display: flex;
  min-height: 104px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 6px;
  margin: 18px 0;
  color: var(--muted);
  background: #f0e8dc;
  border: 1px dashed #c8baaa;
  cursor: pointer;
}

.file-drop input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.file-drop small {
  font-size: 11px;
}

.confirm-pane.inactive {
  opacity: 0.68;
}

.waiting {
  display: grid;
  min-height: 430px;
  color: var(--muted);
  text-align: center;
  place-content: center;
}

.waiting span {
  color: #cfc3b5;
  font-family: Georgia, serif;
  font-size: 64px;
}

.waiting p {
  max-width: 260px;
  line-height: 1.8;
}

.warning {
  margin-bottom: 10px;
}

.recognition-summary {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin-bottom: 14px;
  padding: 12px 14px;
  border-top: 2px solid var(--green);
  background: rgba(47, 125, 76, 0.05);
}

.recognition-summary span {
  color: var(--green);
  font-size: 11px;
}

.recognition-summary strong {
  text-align: right;
  font-size: 12px;
}

.recognition-summary small {
  grid-column: 1 / -1;
  color: var(--muted);
  font-size: 10px;
}

.requirements {
  margin: 2px 0 18px;
  padding: 14px;
  background: #f0e8dc;
  border-left: 3px solid var(--cinnabar);
}

.requirements p {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.source-preview {
  margin: 0 0 18px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.source-preview summary {
  padding: 11px 4px;
  color: var(--cinnabar);
  font-size: 12px;
  cursor: pointer;
}

.source-preview pre {
  max-height: 210px;
  margin: 0 0 12px;
  padding: 12px;
  overflow: auto;
  color: var(--muted);
  white-space: pre-wrap;
  background: rgba(238, 229, 215, 0.48);
  border: 1px solid var(--line-light);
  font: 11px/1.7 inherit;
}
</style>
