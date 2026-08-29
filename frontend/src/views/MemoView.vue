<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Message } from "@arco-design/web-vue";
import { useRoute } from "vue-router";
import PageHelp from "../components/PageHelp.vue";
import { beijingNow, beijingNowIso, formatServerTime } from "../utils/datetime";
import { useSessionStore } from "../stores/session";
import {
  LocalMemoRepository,
  createMemo,
  decryptMemoBackup,
  encryptMemoBackup,
  filterMemos,
  memoDisplayTitle,
  memoScope,
  type LocalMemo,
  type MemoColor,
  type MemoImportPolicy,
  type MemoScope,
} from "../localMemo";

const session = useSessionStore();
const route = useRoute();
const repository = new LocalMemoRepository();
const scope = ref<MemoScope | null>(null);
const memos = ref<LocalMemo[]>([]);
const draft = ref<LocalMemo | null>(null);
const query = ref("");
const showingTrash = ref(false);
const loading = ref(true);
const storageError = ref("");
const saveState = ref<"idle" | "saving" | "saved" | "failed">("idle");
const tagText = ref("");
const lastDeletedId = ref("");
const backupVisible = ref(false);
const backupMode = ref<"export" | "import">("export");
const backupPassword = ref("");
const backupPasswordConfirm = ref("");
const importPolicy = ref<MemoImportPolicy>("newer");
const pendingImport = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
let saveTimer: number | null = null;
let suppressAutosave = false;

const visibleMemos = computed(() => filterMemos(memos.value, query.value, showingTrash.value));
const activeCount = computed(() => memos.value.filter((memo) => !memo.deletedAt).length);
const trashCount = computed(() => memos.value.filter((memo) => memo.deletedAt).length);
const saveLabel = computed(() => ({
  idle: "等待编辑",
  saving: "正在本机保存…",
  saved: "已保存到本机",
  failed: "未保存",
})[saveState.value]);
const colorOptions: Array<{ value: MemoColor; label: string }> = [
  { value: "paper", label: "素笺" },
  { value: "cinnabar", label: "朱砂" },
  { value: "pine", label: "松青" },
  { value: "ochre", label: "赭石" },
  { value: "ink", label: "墨色" },
];

function cloneMemo(memo: LocalMemo): LocalMemo {
  return JSON.parse(JSON.stringify(memo)) as LocalMemo;
}

function replaceMemo(saved: LocalMemo) {
  const index = memos.value.findIndex((memo) => memo.id === saved.id);
  if (index >= 0) memos.value[index] = saved;
  else memos.value.push(saved);
}

function selectMemo(memo: LocalMemo) {
  suppressAutosave = true;
  draft.value = cloneMemo(memo);
  tagText.value = memo.tags.join("、");
  saveState.value = "idle";
  nextTick(() => { suppressAutosave = false; });
}

async function persistDraft() {
  if (!draft.value || !scope.value || draft.value.deletedAt) return;
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = null;
  saveState.value = "saving";
  try {
    draft.value.tags = tagText.value.split(/[，,、]/).map((tag) => tag.trim()).filter(Boolean);
    const saved = await repository.save(scope.value, draft.value);
    replaceMemo(saved);
    suppressAutosave = true;
    draft.value = cloneMemo(saved);
    tagText.value = saved.tags.join("、");
    saveState.value = "saved";
    storageError.value = "";
    nextTick(() => { suppressAutosave = false; });
  } catch (error) {
    saveState.value = "failed";
    storageError.value = error instanceof Error ? error.message : "备忘录未保存。";
  }
}

function scheduleSave() {
  if (suppressAutosave || !draft.value || draft.value.deletedAt) return;
  saveState.value = "idle";
  if (saveTimer) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(persistDraft, 500);
}

async function newMemo(kind: "note" | "checklist" = "note") {
  if (!scope.value) return;
  await persistDraft();
  try {
    const saved = await repository.save(scope.value, createMemo(kind));
    replaceMemo(saved);
    showingTrash.value = false;
    selectMemo(saved);
  } catch (error) {
    storageError.value = error instanceof Error ? error.message : "无法新建备忘录。";
  }
}

async function changeMemoKind(kind: "note" | "checklist") {
  if (!draft.value || draft.value.kind === kind) return;
  draft.value.kind = kind;
  if (kind === "checklist" && draft.value.checklist.length === 0) addChecklistItem();
}

function addChecklistItem() {
  if (!draft.value) return;
  draft.value.checklist.push({ id: `${Date.now()}-${Math.random()}`, text: "", done: false });
}

function removeChecklistItem(index: number) {
  draft.value?.checklist.splice(index, 1);
}

async function softDelete() {
  if (!draft.value || !scope.value) return;
  if (saveTimer) window.clearTimeout(saveTimer);
  const deleted = { ...cloneMemo(draft.value), deletedAt: beijingNowIso() };
  try {
    const saved = await repository.save(scope.value, deleted);
    replaceMemo(saved);
    lastDeletedId.value = saved.id;
    draft.value = null;
    Message.success("已移入回收站，可在本页撤销");
  } catch (error) {
    storageError.value = error instanceof Error ? error.message : "删除失败。";
  }
}

async function restoreMemo(memo = draft.value) {
  if (!memo || !scope.value) return;
  const saved = await repository.save(scope.value, { ...cloneMemo(memo), deletedAt: null });
  replaceMemo(saved);
  lastDeletedId.value = "";
  showingTrash.value = false;
  selectMemo(saved);
}

async function undoDelete() {
  const memo = memos.value.find((item) => item.id === lastDeletedId.value);
  if (memo) await restoreMemo(memo);
}

async function permanentlyDelete(memo = draft.value) {
  if (!memo || !scope.value) return;
  await repository.remove(scope.value, memo.id);
  memos.value = memos.value.filter((item) => item.id !== memo.id);
  draft.value = null;
  Message.success("已从当前电脑永久删除");
}

function openExport() {
  backupMode.value = "export";
  backupPassword.value = "";
  backupPasswordConfirm.value = "";
  backupVisible.value = true;
}

function openImportPicker() {
  fileInput.value?.click();
}

async function readImportFile(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = "";
  if (!file) return;
  if (file.size > 50 * 1024 * 1024) {
    Message.error("备份文件不能超过 50MB");
    return;
  }
  pendingImport.value = await file.text();
  backupMode.value = "import";
  backupPassword.value = "";
  backupPasswordConfirm.value = "";
  backupVisible.value = true;
}

async function confirmBackup() {
  if (!scope.value) return false;
  try {
    if (backupMode.value === "export") {
      if (backupPassword.value !== backupPasswordConfirm.value) throw new Error("两次输入的备份密码不一致。");
      const encoded = await encryptMemoBackup(scope.value, memos.value, backupPassword.value);
      const url = URL.createObjectURL(new Blob([encoded], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `PartyOps-本机备忘录-${beijingNow().format("YYYY-MM-DD")}.partyops-memos`;
      anchor.click();
      URL.revokeObjectURL(url);
      Message.success("加密备份已导出，请妥善保管密码");
    } else {
      const imported = await decryptMemoBackup(scope.value, pendingImport.value, backupPassword.value);
      const result = await repository.import(scope.value, imported, importPolicy.value);
      memos.value = await repository.list(scope.value);
      Message.success(`导入完成：新增 ${result.imported}，更新 ${result.updated}，跳过 ${result.skipped}`);
    }
    pendingImport.value = "";
    return true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "备份操作失败。");
    return false;
  }
}

async function load() {
  loading.value = true;
  try {
    if (!session.user) throw new Error("无法识别当前登录账号。");
    scope.value = memoScope(session.user.id, session.runtimeContext);
    await repository.purgeExpiredTrash(scope.value);
    memos.value = await repository.list(scope.value);
    if (route.query.new) await newMemo();
    else if (visibleMemos.value[0]) selectMemo(visibleMemos.value[0]);
  } catch (error) {
    storageError.value = error instanceof Error ? error.message : "本机备忘录不可用。";
  } finally {
    loading.value = false;
  }
}

watch(draft, scheduleSave, { deep: true });
watch(tagText, scheduleSave);
watch(() => route.query.new, async (value, previous) => {
  if (value && value !== previous && scope.value) await newMemo();
});
watch(showingTrash, () => {
  draft.value = null;
  if (visibleMemos.value[0]) selectMemo(visibleMemos.value[0]);
});

onMounted(load);
onBeforeUnmount(() => {
  if (saveTimer) window.clearTimeout(saveTimer);
  void persistDraft();
});
</script>

<template>
  <div class="page memo-page">
    <header class="page-header memo-header">
      <div>
        <p class="page-kicker">今日 · 私人笺记</p>
        <h1 class="page-title">备忘录</h1>
        <p class="page-description">随手记下不必立项的小事。内容只留在当前电脑，主机与其他协同机均不可见。</p>
      </div>
      <a-space wrap>
        <PageHelp title="备忘录" :tips="['内容仅保存在当前电脑和当前账号范围，不会同步到主机。', '输入后自动保存；清理浏览器站点数据前应先导出加密备份。', '误删可从回收站恢复，永久删除前请确认不再需要。']" help-query="备忘录 本机保存 加密备份" />
        <a-button @click="openImportPicker">导入加密备份</a-button>
        <a-button @click="openExport">导出加密备份</a-button>
        <a-button type="primary" @click="newMemo('note')">新建备忘</a-button>
      </a-space>
    </header>

    <a-alert class="privacy-alert" type="warning" show-icon>
      <template #title>仅保存在当前电脑；主机备份不包含</template>
      退出账号不会删除内容，但换电脑、换浏览器或清理站点数据后不可见。需要迁移时请手工导出带密码的加密备份。
    </a-alert>
    <a-alert v-if="storageError" class="storage-alert" type="error" show-icon :title="storageError">
      系统已经停止自动保存，且不会把内容回退上传到主机。请先复制正文或导出备份，再检查浏览器存储空间。
    </a-alert>

    <section class="memo-workspace" :aria-busy="loading">
      <aside class="memo-list-panel">
        <div class="memo-list-tools">
          <a-input-search v-model="query" allow-clear placeholder="搜索标题、正文、标签或清单" />
          <div class="memo-tabs" role="tablist" aria-label="备忘录范围">
            <button :class="{ active: !showingTrash }" @click="showingTrash = false">备忘 {{ activeCount }}</button>
            <button :class="{ active: showingTrash }" @click="showingTrash = true">回收站 {{ trashCount }}</button>
          </div>
          <a-button v-if="lastDeletedId" long status="warning" @click="undoDelete">撤销最近删除</a-button>
        </div>
        <div v-if="visibleMemos.length" class="memo-list">
          <button
            v-for="memo in visibleMemos"
            :key="memo.id"
            class="memo-card"
            :class="[`color-${memo.color}`, { selected: draft?.id === memo.id }]"
            @click="selectMemo(memo)"
          >
            <span class="memo-card-title"><b v-if="memo.pinned">置顶</b>{{ memoDisplayTitle(memo) }}</span>
            <span class="memo-card-preview">{{ memo.kind === 'checklist' ? `${memo.checklist.filter((item) => item.done).length}/${memo.checklist.length} 项完成` : (memo.body || '还没有正文') }}</span>
            <span class="memo-card-meta">{{ formatServerTime(memo.updatedAt, 'MM-DD HH:mm') }} 北京时间</span>
          </button>
        </div>
        <a-empty v-else :description="showingTrash ? '回收站是空的' : '还没有备忘，按 Ctrl+Alt+M 快速新建'" />
      </aside>

      <main v-if="draft" class="memo-editor" :class="`color-${draft.color}`">
        <div class="editor-toolbar">
          <a-radio-group :model-value="draft.kind" type="button" @change="changeMemoKind($event as 'note' | 'checklist')">
            <a-radio value="note">普通文本</a-radio>
            <a-radio value="checklist">清单</a-radio>
          </a-radio-group>
          <span class="save-state" :class="saveState">{{ saveLabel }}</span>
          <a-space>
            <a-button v-if="!draft.deletedAt" size="small" @click="draft.pinned = !draft.pinned">{{ draft.pinned ? '取消置顶' : '置顶' }}</a-button>
            <a-button v-if="draft.deletedAt" size="small" type="primary" @click="restoreMemo()">恢复</a-button>
            <a-popconfirm v-if="draft.deletedAt" content="永久删除后无法恢复，确认继续？" @ok="permanentlyDelete()">
              <a-button size="small" status="danger">永久删除</a-button>
            </a-popconfirm>
            <a-button v-else size="small" status="danger" @click="softDelete">移入回收站</a-button>
          </a-space>
        </div>
        <input v-model="draft.title" class="memo-title-input" maxlength="160" placeholder="写一个标题（可选）" />
        <textarea v-if="draft.kind === 'note'" v-model="draft.body" class="memo-body-input" maxlength="100000" placeholder="从这里开始记录……" />
        <div v-else class="checklist-editor">
          <label v-for="(item, index) in draft.checklist" :key="item.id" class="checklist-row">
            <a-checkbox v-model="item.done" />
            <input v-model="item.text" maxlength="1000" :class="{ done: item.done }" placeholder="清单内容" />
            <button aria-label="删除清单项" @click.prevent="removeChecklistItem(index)">×</button>
          </label>
          <a-button size="small" @click="addChecklistItem">添加一项</a-button>
        </div>
        <footer class="memo-properties">
          <label>标签 <a-input v-model="tagText" placeholder="用逗号分隔，最多 10 个" :max-length="240" /></label>
          <div class="color-picker" aria-label="便签颜色">
            <span>纸色</span>
            <button
              v-for="color in colorOptions"
              :key="color.value"
              :class="[`swatch-${color.value}`, { active: draft.color === color.value }]"
              :aria-label="color.label"
              :title="color.label"
              @click="draft.color = color.value"
            />
          </div>
        </footer>
      </main>
      <main v-else class="memo-empty-editor">
        <div><strong>{{ showingTrash ? '选择一条已删除备忘' : '留一张只属于自己的笺记' }}</strong><p>普通文本适合灵感与电话记录，清单适合零碎待办。</p><a-button v-if="!showingTrash" type="primary" @click="newMemo('checklist')">新建清单</a-button></div>
      </main>
    </section>

    <input ref="fileInput" hidden type="file" accept=".partyops-memos,application/json" @change="readImportFile" />
    <a-modal
      v-model:visible="backupVisible"
      :title="backupMode === 'export' ? '导出加密备份' : '导入加密备份'"
      :mask-closable="false"
      :on-before-ok="confirmBackup"
    >
      <a-alert type="info" show-icon>{{ backupMode === 'export' ? '密码不会保存；忘记密码将无法恢复备份。' : '只有同一 PartyOps 账号导出的文件可以导入。' }}</a-alert>
      <a-form :model="{ backupPassword, backupPasswordConfirm, importPolicy }" layout="vertical" class="backup-form">
        <a-form-item label="备份密码（至少 8 位）"><a-input-password v-model="backupPassword" /></a-form-item>
        <a-form-item v-if="backupMode === 'export'" label="再次输入密码"><a-input-password v-model="backupPasswordConfirm" /></a-form-item>
        <a-form-item v-else label="遇到相同记录">
          <a-radio-group v-model="importPolicy">
            <a-radio value="newer">仅用较新版本更新</a-radio>
            <a-radio value="copy">保留为导入副本</a-radio>
          </a-radio-group>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.memo-page { max-width: 1500px; }
.memo-header { align-items: flex-end; }
.privacy-alert, .storage-alert { margin-bottom: 16px; }
.memo-workspace { display: grid; grid-template-columns: 330px minmax(0, 1fr); min-height: 650px; border: 1px solid rgba(112, 72, 47, .18); border-radius: 22px; overflow: hidden; background: rgba(255, 252, 244, .92); box-shadow: 0 18px 48px rgba(73, 43, 26, .08); }
.memo-list-panel { display: flex; flex-direction: column; border-right: 1px solid rgba(112, 72, 47, .15); background: rgba(248, 242, 226, .75); }
.memo-list-tools { display: grid; gap: 10px; padding: 16px; border-bottom: 1px solid rgba(112, 72, 47, .12); }
.memo-tabs { display: grid; grid-template-columns: 1fr 1fr; padding: 3px; border-radius: 12px; background: rgba(102, 72, 45, .08); }
.memo-tabs button { border: 0; border-radius: 9px; padding: 7px; color: #765e4a; background: transparent; cursor: pointer; }
.memo-tabs button.active { color: #8f1d19; background: #fffaf0; box-shadow: 0 3px 10px rgba(83, 47, 28, .08); }
.memo-list { display: grid; gap: 8px; padding: 12px; overflow: auto; max-height: 580px; }
.memo-card { display: grid; gap: 7px; width: 100%; padding: 14px; text-align: left; border: 1px solid transparent; border-radius: 14px; color: #3c3028; cursor: pointer; transition: transform .15s ease, border-color .15s ease; }
.memo-card:hover { transform: translateY(-1px); border-color: rgba(143, 29, 25, .28); }
.memo-card.selected { border-color: #a12b25; box-shadow: 0 0 0 2px rgba(161, 43, 37, .09); }
.memo-card-title { font-size: 15px; font-weight: 650; }
.memo-card-title b { margin-right: 7px; padding: 2px 5px; border-radius: 5px; color: #fff; background: #9a2b25; font-size: 10px; }
.memo-card-preview { color: #806f60; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.memo-card-meta { color: #9a897a; font-size: 11px; }
.memo-editor { display: flex; flex-direction: column; min-width: 0; padding: 24px 28px; }
.editor-toolbar { display: flex; align-items: center; gap: 16px; min-height: 34px; }
.editor-toolbar > :last-child { margin-left: auto; }
.save-state { color: #8c7866; font-size: 12px; }
.save-state.saved { color: #377158; }
.save-state.failed { color: #b42318; font-weight: 600; }
.memo-title-input, .memo-body-input, .checklist-row input { border: 0; outline: 0; color: #352a22; background: transparent; font: inherit; }
.memo-title-input { margin: 28px 0 14px; padding: 0 3px 12px; border-bottom: 1px solid rgba(104, 73, 46, .14); font-family: "Noto Serif SC", "Songti SC", serif; font-size: clamp(24px, 3vw, 34px); font-weight: 700; }
.memo-body-input { flex: 1; min-height: 420px; padding: 4px; resize: none; font-size: 16px; line-height: 1.9; }
.checklist-editor { flex: 1; min-height: 420px; padding-top: 8px; }
.checklist-row { display: grid; grid-template-columns: 24px 1fr 30px; gap: 8px; align-items: center; padding: 9px 3px; border-bottom: 1px dashed rgba(104, 73, 46, .12); }
.checklist-row input { width: 100%; font-size: 15px; }
.checklist-row input.done { color: #9a8b7f; text-decoration: line-through; }
.checklist-row button { border: 0; color: #a08573; background: transparent; font-size: 20px; cursor: pointer; }
.memo-properties { display: grid; grid-template-columns: minmax(280px, 1fr) auto; gap: 20px; align-items: end; padding-top: 18px; border-top: 1px solid rgba(104, 73, 46, .14); }
.memo-properties label { display: grid; gap: 7px; color: #765f4e; font-size: 12px; }
.color-picker { display: flex; align-items: center; gap: 8px; color: #765f4e; font-size: 12px; }
.color-picker button { width: 24px; height: 24px; border: 2px solid rgba(255,255,255,.8); border-radius: 50%; box-shadow: 0 0 0 1px rgba(70,45,28,.2); cursor: pointer; }
.color-picker button.active { box-shadow: 0 0 0 2px #8f1d19; }
.color-paper { background: #fffdf6; }.color-cinnabar { background: #fff0eb; }.color-pine { background: #edf5ee; }.color-ochre { background: #f7edda; }.color-ink { background: #eef0f2; }
.swatch-paper { background: #fffdf6; }.swatch-cinnabar { background: #d96b58; }.swatch-pine { background: #5d846a; }.swatch-ochre { background: #b68143; }.swatch-ink { background: #56606b; }
.memo-empty-editor { display: grid; place-items: center; padding: 40px; text-align: center; color: #7e6857; }
.memo-empty-editor strong { color: #4f392b; font-family: "Noto Serif SC", "Songti SC", serif; font-size: 24px; }
.backup-form { margin-top: 18px; }
@media (max-width: 900px) { .memo-workspace { grid-template-columns: 1fr; }.memo-list-panel { border-right: 0; border-bottom: 1px solid rgba(112, 72, 47, .15); }.memo-list { max-height: 260px; }.memo-properties { grid-template-columns: 1fr; } }
</style>
