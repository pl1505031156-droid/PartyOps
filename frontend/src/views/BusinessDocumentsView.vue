<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { IconDelete, IconDownload, IconPlus, IconSave } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, downloadUrl } from "../api";
import { formatServerTime } from "../utils/datetime";
import PageHelp from "../components/PageHelp.vue";

interface BusinessDocument { id: string; meeting_id: string | null; task_step_id: string | null; document_type: string; title: string; content: { blocks?: Array<{ type: string; text: string; level?: number }> }; version: number; archived_at: string | null; archive_reason: string; updated_at: string; }
interface Revision { id: string; revision_no: number; change_note: string; created_at: string; }
interface Meeting { id: string; title: string; }

const documents = ref<BusinessDocument[]>([]);
const meetings = ref<Meeting[]>([]);
const revisions = ref<Revision[]>([]);
const selectedId = ref("");
const createVisible = ref(false);
const saving = ref(false);
const lifecycle = ref<"active" | "archived">("active");
const archiveVisible = ref(false);
const archiveReason = ref("");
const deletionImpact = ref<{ revisions: number; meeting_linked: boolean; task_step_linked: boolean } | null>(null);
const saveState = ref("已保存");
const editor = reactive({ title: "", text: "", version: 0 });
const createForm = reactive({ meeting_id: "", task_step_id: null as string | null, document_type: "agenda", title: "", content: { blocks: [] as Array<{ type: string; text: string }> } });
const selected = computed(() => documents.value.find((item) => item.id === selectedId.value) || null);
let saveTimer: number | undefined;
let loadingSelection = false;

function blocksToText(value: BusinessDocument["content"]): string {
  return (value?.blocks || []).map((block) => block.text || "").join("\n\n");
}

function textToBlocks(value: string) {
  return value.split(/\n{2,}/).map((text, index) => ({ type: index === 0 ? "heading" : "paragraph", text: text.trim(), ...(index === 0 ? { level: 1 } : {}) })).filter((block) => block.text);
}

async function load() {
  try {
    [documents.value, meetings.value] = await Promise.all([api.get<BusinessDocument[]>(`/business-documents?lifecycle=${lifecycle.value}`), api.get<Meeting[]>("/business-meetings")]);
    if (!selectedId.value || !documents.value.some((item) => item.id === selectedId.value)) selectedId.value = documents.value[0]?.id || "";
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "在线文档读取失败");
  }
}

async function loadSelection() {
  const item = selected.value;
  if (!item) return;
  loadingSelection = true;
  Object.assign(editor, { title: item.title, text: blocksToText(item.content), version: item.version });
  revisions.value = await api.get<Revision[]>(`/business-documents/${item.id}/revisions`);
  saveState.value = "已保存";
  loadingSelection = false;
}

async function saveDocument() {
  const item = selected.value;
  if (!item || item.archived_at || saving.value) return;
  saving.value = true;
  saveState.value = "正在保存";
  try {
    const updated = await api.patch<BusinessDocument>(`/business-documents/${item.id}`, { title: editor.title, content: { blocks: textToBlocks(editor.text) }, change_note: "在线编辑自动保存" }, { "If-Match": String(editor.version) });
    editor.version = updated.version;
    Object.assign(item, updated);
    saveState.value = `已保存 · v${updated.version}`;
    revisions.value = await api.get<Revision[]>(`/business-documents/${item.id}/revisions`);
  } catch (error) {
    saveState.value = "保存冲突";
    Message.error(error instanceof Error ? error.message : "文档保存失败");
    await load();
  } finally {
    saving.value = false;
  }
}

async function createDocument() {
  if (!createForm.title.trim()) return;
  try {
    const created = await api.post<{ id: string }>("/business-documents", { ...createForm, meeting_id: createForm.meeting_id || null, content: { blocks: [{ type: "heading", text: createForm.title, level: 1 }] } });
    createVisible.value = false;
    selectedId.value = created.id;
    createForm.title = "";
    await load();
    await loadSelection();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文档创建失败");
  }
}

async function openArchive() {
  if (!selected.value) return;
  try {
    deletionImpact.value = await api.get(`/business-documents/${selected.value.id}/deletion-impact`);
    archiveReason.value = "";
    archiveVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文档归档影响读取失败");
  }
}

async function archiveDocument() {
  if (!selected.value || archiveReason.value.trim().length < 2) {
    Message.warning("请填写至少两个字的归档原因");
    return;
  }
  try {
    await api.deleteBody(`/business-documents/${selected.value.id}`, { reason: archiveReason.value }, { "If-Match": String(selected.value.version) });
    archiveVisible.value = false;
    Message.success("文档已归档，正文和全部修订历史均保留");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文档归档失败");
  }
}

async function restoreDocument() {
  if (!selected.value) return;
  try {
    await api.post(`/business-documents/${selected.value.id}/restore`, { reason: "经办人核对后恢复在线协作" }, { "If-Match": String(selected.value.version) });
    Message.success("文档已恢复，可继续协同编辑");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "文档恢复失败");
  }
}

watch(selectedId, loadSelection);
watch(() => [editor.title, editor.text], () => {
  if (loadingSelection || !selected.value || selected.value.archived_at) return;
  saveState.value = "有未保存修改";
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(saveDocument, 900);
});
onMounted(async () => { await load(); await loadSelection(); });
onBeforeUnmount(() => window.clearTimeout(saveTimer));
</script>

<template>
  <div class="page documents-page">
    <header class="page-header"><div><p class="page-kicker">资料 · 可追溯协同写作</p><h1 class="page-title">在线业务文档</h1><p class="page-description">议程、通知和纪要采用结构化正文在线修改；自动保存、版本冲突检查和修订记录始终开启。</p></div><a-space><PageHelp title="在线业务文档" :tips="['有创建权限的主机或协同用户都可新建并关联会议。', '正文自动保存并校验版本；发生并发冲突时先刷新核对，系统不会静默覆盖。', '文档删除采用可恢复归档，正文和修订历史不会被物理删除。']" help-query="在线业务文档 协同 修订 归档" /><a-radio-group v-model="lifecycle" type="button" @change="load"><a-radio value="active">协作文档</a-radio><a-radio value="archived">已归档</a-radio></a-radio-group><a-button v-if="lifecycle === 'active'" type="primary" @click="createVisible = true"><template #icon><IconPlus /></template>新建文档</a-button></a-space></header>
    <section class="document-studio">
      <aside><button v-for="item in documents" :key="item.id" type="button" :class="{ active: selectedId === item.id }" @click="selectedId = item.id"><span>{{ ({ agenda:'议程', notice:'通知', minutes:'记录', summary:'纪要', materials:'材料', other:'其他' } as Record<string,string>)[item.document_type] }}</span><strong>{{ item.title }}</strong><small>v{{ item.version }} · {{ formatServerTime(item.updated_at, "MM-DD HH:mm") }}</small></button><div v-if="!documents.length" class="empty-state">暂无在线文档。</div></aside>
      <main v-if="selected" class="editor-shell">
        <div class="editor-toolbar"><a-input v-model="editor.title" :disabled="Boolean(selected.archived_at)" aria-label="文档标题" /><a-space><span :class="{ conflict: saveState === '保存冲突' }">{{ selected.archived_at ? `已归档：${selected.archive_reason}` : saveState }}</span><a-button v-if="!selected.archived_at" :loading="saving" @click="saveDocument"><template #icon><IconSave /></template>保存</a-button><a-button v-if="!selected.archived_at" status="danger" @click="openArchive"><template #icon><IconDelete /></template>归档</a-button><a-button v-else type="primary" @click="restoreDocument">恢复</a-button><a-button v-if="!selected.archived_at" :href="downloadUrl(`/business-documents/${selected.id}/export.docx`)" target="_blank"><template #icon><IconDownload /></template>导出 DOCX</a-button></a-space></div>
        <div class="paper"><a-textarea v-model="editor.text" :disabled="Boolean(selected.archived_at)" aria-label="结构化文档正文" :auto-size="{ minRows: 24 }" placeholder="首段作为标题，其余段落用空行分隔。" /></div>
      </main>
      <aside v-if="selected" class="revision-rail"><h2>修订历史</h2><article v-for="item in revisions" :key="item.id"><b>v{{ item.revision_no }}</b><span>{{ item.change_note || "内容更新" }}</span><small>{{ formatServerTime(item.created_at, "MM-DD HH:mm") }}</small></article></aside>
    </section>
    <a-modal v-model:visible="createVisible" title="新建在线业务文档" @ok="createDocument"><a-form :model="createForm" layout="vertical"><a-form-item label="文档类型"><a-select v-model="createForm.document_type"><a-option value="agenda">会议议程</a-option><a-option value="notice">人员通知</a-option><a-option value="minutes">会议记录</a-option><a-option value="summary">会议纪要</a-option><a-option value="materials">会议资料</a-option><a-option value="other">其他</a-option></a-select></a-form-item><a-form-item label="关联会议"><a-select v-model="createForm.meeting_id" allow-clear><a-option v-for="item in meetings" :key="item.id" :value="item.id">{{ item.title }}</a-option></a-select></a-form-item><a-form-item label="标题"><a-input v-model="createForm.title" /></a-form-item></a-form></a-modal>
    <a-modal v-model:visible="archiveVisible" title="归档在线业务文档" ok-text="确认归档" @ok="archiveDocument"><a-alert type="warning">归档不删除正文和修订历史，可在“已归档”中恢复。</a-alert><div v-if="deletionImpact" class="document-impact"><span>修订版本 <b>{{ deletionImpact.revisions }}</b></span><span>关联会议 <b>{{ deletionImpact.meeting_linked ? '是' : '否' }}</b></span><span>关联步骤 <b>{{ deletionImpact.task_step_linked ? '是' : '否' }}</b></span></div><a-form-item label="归档原因" required><a-textarea v-model="archiveReason" /></a-form-item></a-modal>
  </div>
</template>

<style scoped>
.documents-page{max-width:1580px}.document-studio{display:grid;grid-template-columns:270px minmax(520px,1fr) 210px;min-height:720px;border:1px solid var(--line);background:#eee5d5}.document-studio>aside{background:#faf4e8;border-right:1px solid var(--line)}.document-studio>aside>button{display:grid;width:100%;gap:6px;padding:17px 19px;border:0;border-bottom:1px solid var(--line);background:transparent;text-align:left;cursor:pointer}.document-studio>aside>button.active{background:#fffdf7;box-shadow:inset 4px 0 #9b2b24}.document-studio button span{color:#9b2b24;font-size:11px}.document-studio button strong{color:#4d382c}.document-studio button small,.editor-toolbar span,.revision-rail small{color:var(--muted);font-size:11px}.editor-shell{padding:18px;background:#d9cfbe}.editor-toolbar{display:flex;gap:18px;align-items:center;margin-bottom:14px}.editor-toolbar>.arco-input-wrapper{max-width:520px;background:#fffdf7}.editor-toolbar span.conflict{color:#b42318}.paper{max-width:860px;min-height:630px;margin:0 auto;padding:58px 64px;background:#fffdf8;box-shadow:0 18px 42px rgba(62,44,28,.18)}.paper :deep(textarea){color:#332820;font-family:"Noto Serif SC","Songti SC",serif;font-size:16px;line-height:2;background:transparent}.revision-rail{padding:18px;border-right:0!important;border-left:1px solid var(--line)}.revision-rail h2{color:#4d382c;font-size:16px}.revision-rail article{display:grid;gap:4px;padding:11px 0;border-bottom:1px solid var(--line)}@media(max-width:1100px){.document-studio{grid-template-columns:220px 1fr}.revision-rail{display:none}}@media(max-width:760px){.document-studio{grid-template-columns:1fr}.document-studio>aside{max-height:240px;overflow:auto}.paper{padding:28px 22px}}
.document-impact{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;margin:16px 0;background:var(--line);border:1px solid var(--line)}.document-impact span{padding:14px;background:#fffaf0;color:var(--muted);font-size:11px}.document-impact b{display:block;margin-top:4px;color:var(--charcoal);font:20px var(--serif)}@media(max-width:760px){.document-impact{grid-template-columns:1fr}}
</style>
