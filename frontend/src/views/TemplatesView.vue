<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { IconCalendar, IconCopy, IconEdit, IconPlus } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";
import { useSessionStore } from "../stores/session";
import type { Recurrence, Task, Template, User } from "../types";
import { formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const router = useRouter();
const session = useSessionStore();
const templates = ref<Template[]>([]);
const recurrences = ref<Recurrence[]>([]);
const users = ref<User[]>([]);
const contacts = ref<Array<{ id: string; name: string; organization: string }>>([]);
const instantiateVisible = ref(false);
const templateVisible = ref(false);
const recurrenceVisible = ref(false);
const recurrenceSettingsVisible = ref(false);
const recurrencePreviewVisible = ref(false);
const recurrenceExceptionVisible = ref(false);
const selected = ref<Template | null>(null);
const editingTemplate = ref<Template | null>(null);
const selectedRecurrence = ref<Recurrence | null>(null);
const selectedOccurrence = ref<RecurrencePreview | null>(null);
const recurrencePreview = ref<RecurrencePreview[]>([]);
const ownerId = ref("");
const dueAt = ref<string | null>(null);
const templateForm = reactive({
  name: "",
  category: "党建工作",
  task_type: "standard",
  description: "",
  steps_text: "",
  materials_text: "",
  active: true,
});
const recurrenceForm = reactive({
  name: "",
  template_id: "",
  owner_id: "",
  kind: "monthly",
  custom_days: 30,
  internal_lead_days: 2,
  next_run_at: null as string | null,
  schedule_mode: "same_day",
  schedule_day: 1,
  workday_policy: "unchanged",
  paused_until: null as string | null,
  end_at: null as string | null,
  max_occurrences: null as number | null,
  notes: "",
  contact_ids: [] as string[],
});
const recurrenceSettingsForm = reactive({
  active: true,
  paused_until: null as string | null,
  end_at: null as string | null,
  max_occurrences: null as number | null,
});
const exceptionForm = reactive({
  action: "skip",
  rescheduled_at: null as string | null,
  reason: "",
});

interface RecurrencePreview {
  occurrence_at: string;
  effective_at: string;
  action: "" | "skip" | "reschedule";
  reason: string;
}

async function load() {
  templates.value = await api.get<Template[]>(
    `/templates${session.user?.role === "admin" ? "?include_inactive=true" : ""}`,
  );
  users.value = await api.get<User[]>("/users");
  [recurrences.value, contacts.value] = await Promise.all([
    api.get<Recurrence[]>("/recurrences"),
    api.get<Array<{ id: string; name: string; organization: string }>>("/contacts"),
  ]);
  ownerId.value ||= users.value[0]?.id || "";
  recurrenceForm.owner_id ||= users.value[0]?.id || "";
  recurrenceForm.template_id ||= templates.value.find((item) => item.active)?.id || "";
}

function openInstantiate(template: Template) {
  selected.value = template;
  instantiateVisible.value = true;
}

async function instantiate() {
  if (!selected.value) return;
  try {
    const task = await api.post<Task>(`/templates/${selected.value.id}/instantiate`, {
      owner_id: ownerId.value,
      formal_due_at: dueAt.value,
    });
    Message.success("已按模板生成事项");
    await router.push(`/tasks/${task.id}`);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "生成失败");
  }
}

function openTemplate(template?: Template) {
  editingTemplate.value = template || null;
  Object.assign(templateForm, {
    name: template?.name || "",
    category: template?.category || "党建工作",
    task_type: template?.task_type || "standard",
    description: template?.description || "",
    steps_text: template?.steps.join("\n") || "",
    materials_text:
      template?.materials
        .map((item) => `${item.category}|${item.name}|${item.required ? "必备" : "可选"}`)
        .join("\n") || "",
    active: template?.active ?? true,
  });
  templateVisible.value = true;
}

function templatePayload() {
  const materials = templateForm.materials_text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [category = "other", name = "", required = "必备"] = line.split("|");
      return { category: category.trim(), name: name.trim(), required: required.trim() !== "可选" };
    })
    .filter((item) => item.name);
  return {
    name: templateForm.name,
    category: templateForm.category,
    task_type: templateForm.task_type,
    description: templateForm.description,
    steps: templateForm.steps_text.split("\n").map((item) => item.trim()).filter(Boolean),
    materials,
    active: templateForm.active,
  };
}

async function saveTemplate() {
  if (!templateForm.name.trim()) {
    Message.warning("请填写模板名称");
    return;
  }
  try {
    const payload = templatePayload();
    if (editingTemplate.value) {
      await api.patch(
        `/templates/${editingTemplate.value.id}`,
        payload,
        { "If-Match": String(editingTemplate.value.version) },
      );
    } else {
      const { active: _active, ...createPayload } = payload;
      await api.post("/templates", createPayload);
    }
    templateVisible.value = false;
    Message.success("模板已保存，可直接复用");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "模板保存失败");
  }
}

async function saveRecurrence() {
  if (!recurrenceForm.name || !recurrenceForm.template_id || !recurrenceForm.owner_id || !recurrenceForm.next_run_at) {
    Message.warning("请完整填写周期规则");
    return;
  }
  try {
    const {
      schedule_mode,
      schedule_day,
      workday_policy,
      ...payload
    } = recurrenceForm;
    await api.post("/recurrences", {
      ...payload,
      custom_days: recurrenceForm.kind === "custom_days" ? recurrenceForm.custom_days : null,
      schedule_config: {
        mode: schedule_mode,
        day: schedule_mode === "day_of_month" ? schedule_day : null,
        workday_policy,
      },
    });
    recurrenceVisible.value = false;
    Message.success("周期规则已启用");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "周期规则创建失败");
  }
}

function openRecurrenceCreate() {
  Object.assign(recurrenceForm, {
    name: "",
    template_id: templates.value.find((item) => item.active)?.id || "",
    owner_id: users.value[0]?.id || "",
    kind: "monthly",
    custom_days: 30,
    internal_lead_days: 2,
    next_run_at: null,
    schedule_mode: "same_day",
    schedule_day: 1,
    workday_policy: "unchanged",
    paused_until: null,
    end_at: null,
    max_occurrences: null,
    notes: "",
    contact_ids: [],
  });
  recurrenceVisible.value = true;
}

async function toggleRecurrence(rule: Recurrence) {
  try {
    await api.patch(
      `/recurrences/${rule.id}`,
      { active: !rule.active },
      { "If-Match": String(rule.version) },
    );
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "周期规则更新失败");
  }
}

function openRecurrenceSettings(rule: Recurrence) {
  selectedRecurrence.value = rule;
  Object.assign(recurrenceSettingsForm, {
    active: rule.active,
    paused_until: rule.paused_until,
    end_at: rule.end_at,
    max_occurrences: rule.max_occurrences,
  });
  recurrenceSettingsVisible.value = true;
}

async function saveRecurrenceSettings() {
  const rule = selectedRecurrence.value;
  if (!rule) return;
  try {
    await api.patch(
      `/recurrences/${rule.id}`,
      { ...recurrenceSettingsForm },
      { "If-Match": String(rule.version) },
    );
    recurrenceSettingsVisible.value = false;
    Message.success("周期规则运行条件已更新");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "周期规则更新失败");
  }
}

async function openRecurrencePreview(rule: Recurrence) {
  try {
    selectedRecurrence.value = rule;
    recurrencePreview.value = await api.get<RecurrencePreview[]>(
      `/recurrences/${rule.id}/preview?count=12`,
    );
    recurrencePreviewVisible.value = true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "周期预览读取失败");
  }
}

function openRecurrenceException(item: RecurrencePreview, action: "skip" | "reschedule") {
  selectedOccurrence.value = item;
  Object.assign(exceptionForm, {
    action,
    rescheduled_at: action === "reschedule" ? item.effective_at : null,
    reason: "",
  });
  recurrenceExceptionVisible.value = true;
}

async function saveRecurrenceException() {
  const rule = selectedRecurrence.value;
  const item = selectedOccurrence.value;
  if (!rule || !item || !exceptionForm.reason.trim()) {
    Message.warning("请填写本次调整原因");
    return;
  }
  if (exceptionForm.action === "reschedule" && !exceptionForm.rescheduled_at) {
    Message.warning("请选择改期后的日期");
    return;
  }
  try {
    await api.post(
      `/recurrences/${rule.id}/exceptions`,
      {
        occurrence_at: item.occurrence_at,
        action: exceptionForm.action,
        rescheduled_at:
          exceptionForm.action === "reschedule" ? exceptionForm.rescheduled_at : null,
        reason: exceptionForm.reason.trim(),
      },
      { "If-Match": String(rule.version) },
    );
    recurrenceExceptionVisible.value = false;
    Message.success(exceptionForm.action === "skip" ? "已跳过本次计划" : "本次计划已改期");
    await load();
    const refreshed = recurrences.value.find((entry) => entry.id === rule.id);
    if (refreshed) await openRecurrencePreview(refreshed);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "本次计划调整失败");
  }
}

async function runDue() {
  try {
    const ids = await api.post<string[]>("/recurrences/run-due");
    Message.success(ids.length ? `已生成 ${ids.length} 个到期事项` : "当前没有到期规则");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "生成失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">周期事项与模板</h1>
        <p class="page-description">复用上次流程、材料清单和注意事项；模板始终可以调整。</p>
      </div>
      <a-space>
        <PageHelp
          title="周期与模板怎么用"
          :tips="['模板复用办理步骤、材料清单和上期经验。', '周期规则支持月、季度、半年、年度和自定义天数。', '暂停、跳过或临时改期只影响指定实例。']"
          help-query="周期任务"
        />
        <template v-if="session.user?.role === 'admin'">
          <a-button @click="openRecurrenceCreate"><template #icon><IconCalendar /></template>新建周期规则</a-button>
          <a-button type="primary" @click="openTemplate()"><template #icon><IconPlus /></template>新建模板</a-button>
        </template>
      </a-space>
    </header>
    <div class="template-list">
      <article v-for="(template, index) in templates" :key="template.id">
        <div class="template-index">{{ String(index + 1).padStart(2, "0") }}</div>
        <div class="template-main">
          <span>{{ template.category || "党建工作" }}</span>
          <h2>{{ template.name }}</h2>
          <p>{{ template.description }}</p>
          <div class="template-facts">
            <span>{{ template.steps.length }} 个办理步骤</span>
            <span>{{ template.materials.length }} 类材料</span>
            <span>{{ template.task_type === "standard" ? "标准任务" : "项目任务" }}</span>
          </div>
        </div>
        <div class="template-materials">
          <strong>材料清单</strong>
          <span v-for="material in template.materials.slice(0, 4)" :key="material.name">
            {{ material.name }}{{ material.required ? " · 必备" : "" }}
          </span>
        </div>
        <a-button type="outline" @click="openInstantiate(template)">
          <template #icon><IconCopy /></template>
          使用模板
        </a-button>
        <a-button v-if="session.user?.role === 'admin'" type="text" @click="openTemplate(template)">
          <template #icon><IconEdit /></template>编辑
        </a-button>
      </article>
    </div>
    <section class="recurrence-section">
      <div class="section-title">
        <div><span>自动生成</span><h2>周期规则</h2></div>
        <a-button v-if="session.user?.role === 'admin'" size="small" @click="runDue">立即检查到期规则</a-button>
      </div>
      <a-table :data="recurrences" :pagination="false" row-key="id">
        <template #columns>
          <a-table-column title="规则" data-index="name" />
          <a-table-column title="模板"><template #cell="{ record }">{{ templates.find((item) => item.id === record.template_id)?.name || "模板已停用" }}</template></a-table-column>
          <a-table-column title="责任人"><template #cell="{ record }">{{ users.find((item) => item.id === record.owner_id)?.display_name || "未知" }}</template></a-table-column>
          <a-table-column title="下次生成"><template #cell="{ record }">{{ formatServerTime(record.next_run_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
          <a-table-column title="内部提前"><template #cell="{ record }">{{ record.internal_lead_days }} 天</template></a-table-column>
          <a-table-column title="状态"><template #cell="{ record }">{{ !record.active ? "停用" : record.paused_until ? `暂停至 ${formatServerTime(record.paused_until, "MM-DD HH:mm")}` : "启用" }}</template></a-table-column>
          <a-table-column title="已生成"><template #cell="{ record }">{{ record.occurrence_count }}{{ record.max_occurrences ? ` / ${record.max_occurrences}` : "" }} 次</template></a-table-column>
          <a-table-column title="操作" :width="206">
            <template #cell="{ record }">
              <a-space>
                <a-button size="mini" type="text" @click="openRecurrencePreview(record)">预览</a-button>
                <a-button v-if="session.user?.role === 'admin'" size="mini" type="text" @click="openRecurrenceSettings(record)">设置</a-button>
                <a-button v-if="session.user?.role === 'admin'" size="mini" type="text" @click="toggleRecurrence(record)">{{ record.active ? "停用" : "启用" }}</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </section>
    <a-modal v-model:visible="instantiateVisible" title="生成周期事项" @ok="instantiate">
      <p class="muted">将继承模板步骤与材料清单，不需要重复录入。</p>
      <a-form :model="{ ownerId, dueAt }" layout="vertical">
        <a-form-item label="责任人">
          <a-select v-model="ownerId"><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select>
        </a-form-item>
        <a-form-item label="正式截止时间">
          <a-date-picker v-model="dueAt" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" />
        </a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="templateVisible" :title="editingTemplate ? '编辑模板' : '新建模板'" width="680px" @ok="saveTemplate">
      <a-form :model="templateForm" layout="vertical">
        <div class="form-grid">
          <a-form-item label="模板名称"><a-input v-model="templateForm.name" /></a-form-item>
          <a-form-item label="工作类别"><a-input v-model="templateForm.category" /></a-form-item>
          <a-form-item label="任务类型"><a-select v-model="templateForm.task_type"><a-option value="standard">标准任务</a-option><a-option value="project">项目任务</a-option></a-select></a-form-item>
          <a-form-item label="启用"><a-switch v-model="templateForm.active" /></a-form-item>
        </div>
        <a-form-item label="模板说明"><a-textarea v-model="templateForm.description" :auto-size="{ minRows: 2, maxRows: 4 }" /></a-form-item>
        <a-form-item label="办理步骤（每行一项）"><a-textarea v-model="templateForm.steps_text" :auto-size="{ minRows: 4, maxRows: 8 }" /></a-form-item>
        <a-form-item label="材料清单（每行：类别|名称|必备/可选）"><a-textarea v-model="templateForm.materials_text" placeholder="final|实际报送稿|必备" :auto-size="{ minRows: 4, maxRows: 8 }" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="recurrenceVisible" title="新建周期规则" width="620px" @ok="saveRecurrence">
      <a-form :model="recurrenceForm" layout="vertical">
        <a-form-item label="规则名称"><a-input v-model="recurrenceForm.name" /></a-form-item>
        <div class="form-grid">
          <a-form-item label="任务模板"><a-select v-model="recurrenceForm.template_id"><a-option v-for="template in templates.filter((item) => item.active)" :key="template.id" :value="template.id">{{ template.name }}</a-option></a-select></a-form-item>
          <a-form-item label="责任人"><a-select v-model="recurrenceForm.owner_id"><a-option v-for="user in users" :key="user.id" :value="user.id">{{ user.display_name }}</a-option></a-select></a-form-item>
          <a-form-item label="周期"><a-select v-model="recurrenceForm.kind"><a-option value="monthly">每月</a-option><a-option value="quarterly">每季度</a-option><a-option value="half_yearly">每半年</a-option><a-option value="yearly">每年</a-option><a-option value="custom_days">自定义天数</a-option></a-select></a-form-item>
          <a-form-item v-if="recurrenceForm.kind === 'custom_days'" label="间隔天数"><a-input-number v-model="recurrenceForm.custom_days" :min="1" :max="3650" /></a-form-item>
        <a-form-item label="首次正式节点"><a-date-picker v-model="recurrenceForm.next_run_at" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></a-form-item>
          <a-form-item label="内部提前天数"><a-input-number v-model="recurrenceForm.internal_lead_days" :min="0" :max="365" /></a-form-item>
          <a-form-item label="节点规则">
            <a-select v-model="recurrenceForm.schedule_mode">
              <a-option value="same_day">沿用首次节点日期</a-option>
              <a-option value="day_of_month">每月固定日期</a-option>
              <a-option value="month_end">每月最后一天</a-option>
              <a-option value="quarter_end">季度末</a-option>
              <a-option value="last_workday">最后一个工作日</a-option>
            </a-select>
          </a-form-item>
          <a-form-item v-if="recurrenceForm.schedule_mode === 'day_of_month'" label="每月第几日"><a-input-number v-model="recurrenceForm.schedule_day" :min="1" :max="31" /></a-form-item>
          <a-form-item v-if="recurrenceForm.schedule_mode !== 'last_workday'" label="遇非工作日">
            <a-select v-model="recurrenceForm.workday_policy">
              <a-option value="unchanged">保持原日期</a-option>
              <a-option value="previous">提前至上一工作日</a-option>
              <a-option value="next">顺延至下一工作日</a-option>
            </a-select>
          </a-form-item>
        <a-form-item label="暂停至（可选）"><a-date-picker v-model="recurrenceForm.paused_until" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></a-form-item>
        <a-form-item label="终止日期（可选）"><a-date-picker v-model="recurrenceForm.end_at" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></a-form-item>
          <a-form-item label="最多生成次数（可选）"><a-input-number v-model="recurrenceForm.max_occurrences" :min="1" :max="10000" /></a-form-item>
        </div>
        <a-form-item label="注意事项（下期自动复用）"><a-textarea v-model="recurrenceForm.notes" :auto-size="{ minRows: 3, maxRows: 6 }" /></a-form-item>
        <a-form-item label="常用联系人（下期自动复用）">
          <a-select v-model="recurrenceForm.contact_ids" multiple allow-search>
            <a-option v-for="contact in contacts" :key="contact.id" :value="contact.id">{{ contact.name }} · {{ contact.organization }}</a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="recurrenceSettingsVisible" title="周期规则运行设置" width="560px" @ok="saveRecurrenceSettings">
      <a-form :model="recurrenceSettingsForm" layout="vertical">
        <a-alert type="info">暂停不会删除规则或历史事项；到期后将继续按原周期生成。</a-alert>
        <div class="form-grid settings-grid">
          <a-form-item label="启用规则"><a-switch v-model="recurrenceSettingsForm.active" /></a-form-item>
          <a-form-item label="最多生成次数"><a-input-number v-model="recurrenceSettingsForm.max_occurrences" :min="1" :max="10000" allow-clear /></a-form-item>
        <a-form-item label="暂停至"><a-date-picker v-model="recurrenceSettingsForm.paused_until" show-time allow-clear value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></a-form-item>
        <a-form-item label="终止日期"><a-date-picker v-model="recurrenceSettingsForm.end_at" show-time allow-clear value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></a-form-item>
        </div>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="recurrencePreviewVisible" title="未来 12 次周期预览" width="760px" :footer="false">
      <a-alert type="info">预览不会创建事项；管理员可以只调整某一次，不改变后续周期。</a-alert>
      <a-table class="preview-table" :data="recurrencePreview" :pagination="false" row-key="occurrence_at">
        <template #columns>
          <a-table-column title="原计划"><template #cell="{ record }">{{ formatServerTime(record.occurrence_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
          <a-table-column title="实际节点"><template #cell="{ record }">{{ formatServerTime(record.effective_at, "YYYY-MM-DD HH:mm") }}</template></a-table-column>
          <a-table-column title="本次状态"><template #cell="{ record }">{{ record.action ? zhLabel(record.action) : "按规则执行" }}<small v-if="record.reason" class="reason">{{ record.reason }}</small></template></a-table-column>
          <a-table-column v-if="session.user?.role === 'admin'" title="操作" :width="136">
            <template #cell="{ record }">
              <a-space v-if="!record.action">
                <a-button size="mini" type="text" @click="openRecurrenceException(record, 'skip')">跳过</a-button>
                <a-button size="mini" type="text" @click="openRecurrenceException(record, 'reschedule')">改期</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-modal>
    <a-modal v-model:visible="recurrenceExceptionVisible" :title="exceptionForm.action === 'skip' ? '跳过本次计划' : '临时改期'" width="520px" @ok="saveRecurrenceException">
      <a-form :model="exceptionForm" layout="vertical">
        <a-form-item label="原计划时间"><strong>{{ selectedOccurrence ? formatServerTime(selectedOccurrence.occurrence_at, "YYYY-MM-DD HH:mm") : "—" }}</strong></a-form-item>
        <a-form-item v-if="exceptionForm.action === 'reschedule'" label="改期至"><a-date-picker v-model="exceptionForm.rescheduled_at" show-time value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%" /></a-form-item>
        <a-form-item label="调整原因"><a-textarea v-model="exceptionForm.reason" :max-length="2000" show-word-limit :auto-size="{ minRows: 3, maxRows: 6 }" /></a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.template-list {
  border-top: 1px solid var(--line);
}

.template-list article {
  display: grid;
  min-height: 158px;
  align-items: center;
  grid-template-columns: 54px minmax(330px, 1fr) 250px 112px 70px;
  gap: 24px;
  padding: 22px 8px;
  border-bottom: 1px solid var(--line-light);
}

.template-index {
  color: #bdb2a6;
  font-family: Georgia, serif;
  font-size: 20px;
}

.template-main > span {
  color: var(--cinnabar);
  font-size: 11px;
  letter-spacing: 0.1em;
}

.template-main h2 {
  margin: 7px 0;
  font-size: 18px;
}

.template-main p {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.template-facts {
  display: flex;
  gap: 16px;
  margin-top: 13px;
  color: #71695f;
  font-size: 11px;
}

.template-materials {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding-left: 22px;
  border-left: 1px solid var(--line);
}

.template-materials strong {
  margin-bottom: 2px;
  font-size: 12px;
}

.template-materials span {
  color: var(--muted);
  font-size: 11px;
}

.recurrence-section {
  margin-top: 38px;
}

.section-title {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 12px;
}

.section-title span {
  color: var(--cinnabar);
  font-size: 11px;
  letter-spacing: 0.16em;
}

.section-title h2 {
  margin: 5px 0 0;
  font-size: 20px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.settings-grid {
  margin-top: 16px;
}

.preview-table {
  margin-top: 16px;
}

.reason {
  display: block;
  margin-top: 4px;
  color: var(--muted);
}
</style>
