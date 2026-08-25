<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { IconCheck, IconRefresh, IconUpload } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api, uploadFormWithProgress } from "../api";

interface SheetProfile {
  name: string;
  rows: number;
  columns: number;
  hidden: boolean;
}

interface FieldChoice {
  key: string;
  label: string;
  field_type: string;
  required: boolean;
}

interface ColumnProfile {
  index: number;
  header: string;
  non_empty: number;
  empty: number;
  unique: number;
  samples: unknown[];
  inferred_type: string;
  type_confidence: number;
  date_ambiguous: boolean;
  formula_like: number;
  suggestion?: {
    target_field?: string | null;
    label?: string;
    confidence: "high" | "medium" | "conflict";
    score: number;
  } | null;
}

interface ValidationIssue {
  row_number: number;
  level: "error" | "warning";
  messages: string[];
}

interface LedgerJob {
  id: string;
  target_type: "party_development" | "archive";
  target_id: string | null;
  sheet_name: string;
  header_row: number;
  status: string;
  version: number;
  total_rows: number;
  valid_rows: number;
  warning_rows: number;
  error_rows: number;
  profile: {
    sheets: SheetProfile[];
    selected: {
      header_row: number;
      total_rows: number;
      columns: ColumnProfile[];
      duplicate_headers: string[];
      header_signature: string;
    };
    available_fields: FieldChoice[];
  };
  validation: {
    issues?: ValidationIssue[];
    issues_truncated?: number;
  };
}

interface MappingRow {
  source_column: string;
  action: "map" | "create" | "ignore";
  target_field?: string;
  create_label?: string;
  create_type?: "text" | "textarea" | "number" | "date" | "select";
  confidence: string;
}

const props = defineProps<{
  visible: boolean;
  targetType: "party_development" | "archive";
  targetId?: string;
  targetLabel: string;
}>();
const emit = defineEmits<{
  "update:visible": [value: boolean];
  completed: [job: LedgerJob];
}>();

const step = ref(0);
const busy = ref(false);
const uploadPercent = ref(0);
const file = ref<File | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const job = ref<LedgerJob | null>(null);
const selectedSheet = ref("");
const headerRow = ref(1);
const mappings = ref<MappingRow[]>([]);
const sharedConfirmed = ref(false);
const newFieldsConfirmed = ref(false);
const rowActions = ref<Record<string, string>>({});

const steps = ["选择台账", "识别表头", "确认目标", "字段映射", "全量校验", "提交归档"];
const selectedProfile = computed(() => job.value?.profile.selected);
const newFieldCount = computed(() => mappings.value.filter((item) => item.action === "create").length);
const canCommit = computed(() => Boolean(job.value && job.value.error_rows === 0 && sharedConfirmed.value && (!newFieldCount.value || newFieldsConfirmed.value)));

function reset() {
  step.value = 0;
  busy.value = false;
  uploadPercent.value = 0;
  file.value = null;
  job.value = null;
  selectedSheet.value = "";
  headerRow.value = 1;
  mappings.value = [];
  sharedConfirmed.value = false;
  newFieldsConfirmed.value = false;
  rowActions.value = {};
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) reset();
  },
);

function close() {
  emit("update:visible", false);
}

function chooseFile() {
  fileInput.value?.click();
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  file.value = input.files?.[0] || null;
  input.value = "";
}

function initializeMappings(current: LedgerJob) {
  mappings.value = current.profile.selected.columns.map((column) => {
    const suggestion = column.suggestion;
    if (suggestion?.target_field) {
      return {
        source_column: column.header,
        action: "map",
        target_field: suggestion.target_field,
        confidence: suggestion.confidence,
      };
    }
    return {
      source_column: column.header,
      action: "ignore",
      create_label: column.header,
      create_type: "text",
      confidence: suggestion?.confidence || "unknown",
    };
  });
}

async function inspectFile() {
  if (!file.value) {
    Message.warning("请先选择台账文件");
    return;
  }
  busy.value = true;
  uploadPercent.value = 0;
  try {
    const form = new FormData();
    form.append("target_type", props.targetType);
    if (props.targetId) form.append("target_id", props.targetId);
    form.append("file", file.value);
    job.value = await uploadFormWithProgress<LedgerJob>("/ledger-imports/inspect", form, {
      onProgress: (value) => (uploadPercent.value = value),
    });
    selectedSheet.value = job.value.sheet_name;
    headerRow.value = job.value.header_row;
    initializeMappings(job.value);
    step.value = 1;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "台账检查失败");
  } finally {
    busy.value = false;
  }
}

async function applyProfile() {
  if (!job.value) return;
  busy.value = true;
  try {
    job.value = await api.patch<LedgerJob>(`/ledger-imports/${job.value.id}/profile`, {
      sheet_name: selectedSheet.value,
      header_row: headerRow.value,
      version: job.value.version,
    });
    initializeMappings(job.value);
    step.value = 2;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "表头识别失败");
  } finally {
    busy.value = false;
  }
}

function confirmTarget() {
  if (!sharedConfirmed.value) {
    Message.warning("请确认台账将进入共享 PartyOps 主机");
    return;
  }
  step.value = 3;
}

function updateMappingAction(row: MappingRow, value: unknown) {
  if (value !== "map" && value !== "create" && value !== "ignore") return;
  const action: MappingRow["action"] = value;
  row.action = action;
  if (action === "map" && !row.target_field) {
    row.target_field = job.value?.profile.available_fields[0]?.key;
  }
  if (action === "create") {
    row.create_label ||= row.source_column;
    row.create_type ||= "text";
  }
}

async function saveMapping() {
  if (!job.value) return;
  const mapped = mappings.value.filter((item) => item.action !== "ignore");
  if (!mapped.length) {
    Message.warning("至少映射一个字段");
    return;
  }
  if (mapped.some((item) => item.action === "map" && !item.target_field)) {
    Message.warning("请补齐所有目标字段");
    return;
  }
  busy.value = true;
  try {
    job.value = await api.patch<LedgerJob>(`/ledger-imports/${job.value.id}/mapping`, {
      sheet_name: selectedSheet.value,
      header_row: headerRow.value,
      version: job.value.version,
      mappings: mappings.value.map((item) => ({
        source_column: item.source_column,
        action: item.action,
        target_field: item.action === "map" ? item.target_field : undefined,
        create_label: item.action === "create" ? item.create_label : undefined,
        create_type: item.action === "create" ? item.create_type : undefined,
        confirmed: true,
      })),
    });
    step.value = 4;
    await validateAll();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "字段映射保存失败");
  } finally {
    busy.value = false;
  }
}

async function validateAll() {
  if (!job.value) return;
  busy.value = true;
  try {
    job.value = await api.post<LedgerJob>(`/ledger-imports/${job.value.id}/validate`, {
      version: job.value.version,
      row_actions: rowActions.value,
    });
    if (!job.value.error_rows) Message.success(`全量校验通过：${job.value.valid_rows} 行可导入`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "全量校验失败");
  } finally {
    busy.value = false;
  }
}

function goCommit() {
  if (job.value?.error_rows) {
    Message.warning("请先处理全部错误行并重新校验");
    return;
  }
  step.value = 5;
}

async function commit() {
  if (!job.value || !canCommit.value) {
    Message.warning("请完成共享归档和新增字段确认");
    return;
  }
  busy.value = true;
  try {
    job.value = await api.post<LedgerJob>(`/ledger-imports/${job.value.id}/commit`, {
      version: job.value.version,
      confirm_shared_storage: sharedConfirmed.value,
      confirm_new_fields: newFieldsConfirmed.value,
      row_actions: rowActions.value,
    });
    Message.success(`已导入 ${job.value.valid_rows} 行；可在本次窗口内安全撤销`);
    emit("completed", job.value);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "台账提交失败");
  } finally {
    busy.value = false;
  }
}

async function undo() {
  if (!job.value || job.value.status !== "committed") return;
  busy.value = true;
  try {
    job.value = await api.post<LedgerJob>(`/ledger-imports/${job.value.id}/undo`, {
      version: job.value.version,
    });
    Message.success("本次导入已安全撤销；后续人工修改未被覆盖");
    emit("completed", job.value);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "导入撤销失败");
  } finally {
    busy.value = false;
  }
}

function confidenceLabel(value: string) {
  return value === "high" ? "精确匹配" : value === "medium" ? "需人工确认" : value === "conflict" ? "多候选" : "未识别";
}
</script>

<template>
  <a-modal :visible="visible" :footer="false" width="1120px" :mask-closable="false" @cancel="close">
    <template #title>导入本地台账 · {{ targetLabel }}</template>
    <div class="ledger-wizard">
      <a-steps :current="step + 1" small>
        <a-step v-for="item in steps" :key="item" :title="item" />
      </a-steps>

      <section v-if="step === 0" class="wizard-panel file-stage">
        <div>
          <span class="stage-kicker">01 / 本地选择</span>
          <h3>选择电子表格台账</h3>
          <p>支持 xlsx、xls、xlsb、ods、csv；不会执行公式、宏或外部链接。确认前不写入业务表。</p>
        </div>
        <button type="button" class="file-drop" @click="chooseFile">
          <IconUpload />
          <strong>{{ file?.name || "选择台账文件" }}</strong>
          <small>{{ file ? `${Math.ceil(file.size / 1024)} KB` : "单文件不超过 50 MiB" }}</small>
        </button>
        <input ref="fileInput" type="file" accept=".xlsx,.xls,.xlsb,.ods,.csv" hidden @change="onFileChange" />
        <a-progress v-if="busy" :percent="uploadPercent" />
        <a-button type="primary" size="large" :loading="busy" @click="inspectFile">检查文件并识别结构</a-button>
      </section>

      <section v-else-if="step === 1 && job" class="wizard-panel">
        <header><span class="stage-kicker">02 / 结构识别</span><h3>选择工作表与表头行</h3><p>隐藏工作表会明确标记；只有当前选择的工作表进入后续全量校验。</p></header>
        <div class="profile-controls">
          <a-form-item label="工作表">
            <a-select v-model="selectedSheet">
              <a-option v-for="sheet in job.profile.sheets" :key="sheet.name" :value="sheet.name">{{ sheet.name }} · {{ sheet.rows }} 行<span v-if="sheet.hidden">（隐藏）</span></a-option>
            </a-select>
          </a-form-item>
          <a-form-item label="表头所在行"><a-input-number v-model="headerRow" :min="1" :max="50" /></a-form-item>
        </div>
        <a-alert v-if="selectedProfile?.duplicate_headers.length" type="error">检测到重名表头：{{ selectedProfile.duplicate_headers.join("、") }}。请先修改源台账。</a-alert>
        <div class="column-preview">
          <article v-for="column in selectedProfile?.columns || []" :key="column.header">
            <strong>{{ column.header }}</strong><span>{{ column.inferred_type }} · {{ column.non_empty }} 个有效值</span><small>样例：{{ column.samples.slice(0, 3).join(" / ") || "无" }}</small>
          </article>
        </div>
        <footer><a-button @click="step = 0">上一步</a-button><a-button type="primary" :loading="busy" @click="applyProfile">确认表头</a-button></footer>
      </section>

      <section v-else-if="step === 2" class="wizard-panel target-stage">
        <header><span class="stage-kicker">03 / 归档边界</span><h3>确认共享业务目标</h3></header>
        <div class="target-card"><span>本次归档到</span><strong>{{ targetLabel }}</strong><small>{{ targetType === "party_development" ? "创建或更新人员档案及真实进度" : "创建或更新重要档案目录记录" }}</small></div>
        <a-alert type="warning">台账导入与“公文规范排版”不同：确认后，所选字段会通过当前认证连接进入 PartyOps 主机，供有权限人员协作。</a-alert>
        <a-checkbox v-model="sharedConfirmed">我已确认该台账可以进入当前 PartyOps 共享业务系统</a-checkbox>
        <footer><a-button @click="step = 1">上一步</a-button><a-button type="primary" @click="confirmTarget">继续字段映射</a-button></footer>
      </section>

      <section v-else-if="step === 3 && job" class="wizard-panel mapping-stage">
        <header><span class="stage-kicker">04 / 字段映射</span><h3>逐列确认，不按猜测落库</h3><p>精确别名可默认选择；语义相似、多候选和未知列必须由你决定。</p></header>
        <div class="mapping-table">
          <article v-for="row in mappings" :key="row.source_column">
            <div><strong>{{ row.source_column }}</strong><a-tag :color="row.confidence === 'high' ? 'green' : row.confidence === 'medium' ? 'orange' : 'gray'">{{ confidenceLabel(row.confidence) }}</a-tag></div>
            <a-select :model-value="row.action" @change="updateMappingAction(row, $event)">
              <a-option value="map">映射已有字段</a-option><a-option value="create">创建新字段</a-option><a-option value="ignore">忽略此列</a-option>
            </a-select>
            <a-select v-if="row.action === 'map'" v-model="row.target_field" placeholder="选择目标字段">
              <a-option v-for="field in job.profile.available_fields" :key="field.key" :value="field.key">{{ field.label }}<span v-if="field.required"> · 必填</span></a-option>
            </a-select>
            <div v-else-if="row.action === 'create'" class="new-field"><a-input v-model="row.create_label" placeholder="字段名称" /><a-select v-model="row.create_type"><a-option value="text">短文本</a-option><a-option value="textarea">长文本</a-option><a-option value="number">数字</a-option><a-option value="date">日期</a-option><a-option value="select">选项</a-option></a-select></div>
            <span v-else class="ignored">该列不会进入系统</span>
          </article>
        </div>
        <footer><a-button @click="step = 2">上一步</a-button><a-button type="primary" :loading="busy" @click="saveMapping">保存映射并全量校验</a-button></footer>
      </section>

      <section v-else-if="step === 4 && job" class="wizard-panel validation-stage">
        <header><span class="stage-kicker">05 / 全量校验</span><h3>已检查全部 {{ job.total_rows }} 行</h3></header>
        <div class="validation-summary"><article><span>可导入</span><strong>{{ job.valid_rows }}</strong></article><article><span>警告</span><strong>{{ job.warning_rows }}</strong></article><article :class="{ danger: job.error_rows }"><span>错误/待确认</span><strong>{{ job.error_rows }}</strong></article></div>
        <div v-if="job.validation.issues?.length" class="issue-list">
          <article v-for="issue in job.validation.issues" :key="`${issue.row_number}-${issue.level}`" :class="issue.level">
            <div><strong>第 {{ issue.row_number }} 行</strong><p>{{ issue.messages.join("；") }}</p></div>
            <a-select v-if="issue.messages.some((message) => message.includes('重复记录'))" v-model="rowActions[String(issue.row_number)]" placeholder="选择处理方式">
              <a-option value="new">保留为新记录</a-option><a-option value="skip">跳过该行</a-option><a-option value="fill">仅填补空值</a-option>
            </a-select>
          </article>
        </div>
        <a-alert v-else type="success"><IconCheck /> 全部行通过字段、类型和重复检查。</a-alert>
        <small v-if="job.validation.issues_truncated">另有 {{ job.validation.issues_truncated }} 条问题未展开；修复源文件后重新检查更高效。</small>
        <footer><a-button @click="step = 3">上一步</a-button><a-button :loading="busy" @click="validateAll"><template #icon><IconRefresh /></template>重新校验</a-button><a-button type="primary" :disabled="Boolean(job.error_rows)" @click="goCommit">进入提交确认</a-button></footer>
      </section>

      <section v-else-if="step === 5 && job" class="wizard-panel commit-stage">
        <header><span class="stage-kicker">06 / 原子提交</span><h3>{{ job.status === "committed" ? "台账已完成导入" : job.status === "undone" ? "本次导入已撤销" : "最后核对" }}</h3></header>
        <div class="commit-paper"><span>{{ targetLabel }}</span><strong>{{ job.valid_rows }} 行</strong><p>提交时一次性写入；中途失败不会留下半批数据。撤销会在覆盖前检查后续人工编辑。</p></div>
        <a-checkbox v-if="newFieldCount && job.status !== 'committed'" v-model="newFieldsConfirmed">我确认创建 {{ newFieldCount }} 个模块字段，并了解其会影响后续录入表单</a-checkbox>
        <a-alert v-if="job.status === 'committed'" type="success">导入完成。新记录、更新前快照和字段变化均已记录，可安全撤销。</a-alert>
        <a-alert v-else-if="job.status === 'undone'" type="info">已从活动业务中撤销；审计记录和必要历史仍然保留。</a-alert>
        <footer><a-button v-if="job.status !== 'committed' && job.status !== 'undone'" @click="step = 4">上一步</a-button><a-button v-if="job.status === 'committed'" status="danger" :loading="busy" @click="undo">安全撤销本次导入</a-button><a-button v-if="job.status === 'committed' || job.status === 'undone'" type="primary" @click="close">完成</a-button><a-button v-else type="primary" :loading="busy" :disabled="!canCommit" @click="commit">确认并提交</a-button></footer>
      </section>
    </div>
  </a-modal>
</template>

<style scoped>
.ledger-wizard{padding:4px 8px 10px}.ledger-wizard :deep(.arco-steps){padding:8px 4px 24px;border-bottom:1px solid var(--line)}.wizard-panel{display:grid;gap:18px;min-height:520px;padding:28px 12px 4px}.wizard-panel header h3,.file-stage h3{margin:6px 0;color:#493328;font:500 25px var(--serif)}.wizard-panel header p,.file-stage p{max-width:760px;margin:0;color:var(--muted);line-height:1.7}.stage-kicker{color:var(--cinnabar);font:10px Georgia,serif;letter-spacing:.16em}.wizard-panel footer{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:auto;padding-top:18px;border-top:1px solid var(--line)}.file-stage{place-items:center;text-align:center}.file-drop{display:grid;width:min(560px,100%);min-height:190px;place-items:center;padding:28px;color:var(--charcoal);background:#fbf6eb;border:1px dashed #b99f7d;cursor:pointer}.file-drop svg{font-size:38px;color:var(--cinnabar)}.file-drop strong{font-size:17px}.file-drop small{color:var(--muted)}.profile-controls{display:grid;grid-template-columns:2fr 1fr;gap:16px}.column-preview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));max-height:290px;border-top:1px solid var(--line);border-left:1px solid var(--line);overflow:auto}.column-preview article{display:grid;gap:6px;min-height:98px;padding:14px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);background:#fffaf0}.column-preview span,.column-preview small{color:var(--muted);font-size:11px}.target-stage{align-content:start}.target-card,.commit-paper{display:grid;max-width:640px;padding:28px;color:#f8efe4;background:var(--charcoal);border-left:5px solid var(--cinnabar)}.target-card span,.commit-paper span{color:#d38a7e;font-size:11px}.target-card strong,.commit-paper strong{margin:8px 0;font:28px var(--serif)}.target-card small,.commit-paper p{margin:0;color:#c8beb2}.mapping-table{display:grid;max-height:400px;border:1px solid var(--line);overflow:auto}.mapping-table article{display:grid;grid-template-columns:minmax(170px,.8fr) 150px minmax(260px,1.2fr);gap:12px;align-items:center;padding:11px 14px;border-bottom:1px solid var(--line-light)}.mapping-table article>div:first-child{display:flex;align-items:center;gap:8px}.new-field{display:grid;grid-template-columns:1fr 130px;gap:8px}.ignored{color:var(--muted);font-size:12px}.validation-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}.validation-summary article{padding:18px;background:#fffaf0}.validation-summary span,.validation-summary strong{display:block}.validation-summary span{color:var(--muted);font-size:11px}.validation-summary strong{margin-top:6px;font:28px Georgia,serif}.validation-summary .danger strong{color:#b42318}.issue-list{display:grid;max-height:300px;border:1px solid var(--line);overflow:auto}.issue-list article{display:grid;grid-template-columns:1fr 220px;gap:12px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--line-light)}.issue-list article.error{border-left:4px solid #b42318}.issue-list article.warning{border-left:4px solid #da7b20}.issue-list p{margin:4px 0 0;color:var(--muted);font-size:11px}.commit-stage{align-content:start}.commit-paper strong{font-size:36px}@media(max-width:820px){.wizard-panel{padding-inline:0}.profile-controls,.column-preview,.validation-summary{grid-template-columns:1fr}.mapping-table article{grid-template-columns:1fr}.issue-list article{grid-template-columns:1fr}.new-field{grid-template-columns:1fr}.ledger-wizard :deep(.arco-steps-item-title){display:none}}
</style>
