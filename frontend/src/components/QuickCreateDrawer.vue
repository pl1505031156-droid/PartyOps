<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { Task, User } from "../types";

defineProps<{ visible: boolean }>();
const emit = defineEmits<{
  "update:visible": [value: boolean];
  created: [task: Task];
}>();

const users = ref<User[]>([]);
const submitting = ref(false);
const form = reactive({
  title: "",
  formal_due_at: null as string | null,
  owner_id: "",
  source: "",
  sensitivity: "normal",
});

onMounted(async () => {
  try {
    users.value = await api.get<User[]>("/users");
    if (users.value[0]) form.owner_id = users.value[0].id;
  } catch {
    const me = await api.get<User>("/auth/me");
    users.value = [me];
    form.owner_id = me.id;
  }
});

async function submit() {
  if (!form.title || !form.owner_id) {
    Message.warning("请填写事项名称并选择责任人");
    return;
  }
  submitting.value = true;
  try {
    const task = await api.post<Task>("/tasks", {
      ...form,
      task_type: "quick",
      priority: "normal",
      source_kind: "manual",
      collaborator_ids: [],
      steps: [],
      materials: [],
    });
    Message.success("事项已进入闭环");
    emit("created", task);
    emit("update:visible", false);
    form.title = "";
    form.source = "";
    form.formal_due_at = null;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "创建失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <a-modal
    :visible="visible"
    :width="520"
    title="30 秒快速新建"
    :footer="false"
    :mask-closable="false"
    @cancel="emit('update:visible', false)"
  >
    <p class="drawer-hint">只填最必要的信息，材料与步骤可在办理过程中补充。</p>
    <a-form layout="vertical" :model="form" @submit-success="submit">
      <a-form-item field="title" label="事项名称" required>
        <a-input v-model="form.title" placeholder="例如：季度党建台账报送" allow-clear />
      </a-form-item>
      <a-form-item field="formal_due_at" label="正式截止时间">
        <a-date-picker
          v-model="form.formal_due_at"
          show-time
          format="YYYY-MM-DD HH:mm"
          value-format="YYYY-MM-DD HH:mm:ss"
          style="width: 100%"
        />
      </a-form-item>
      <a-form-item field="owner_id" label="责任人" required>
        <a-select v-model="form.owner_id">
          <a-option v-for="user in users" :key="user.id" :value="user.id">
            {{ user.display_name }}
          </a-option>
        </a-select>
      </a-form-item>
      <a-form-item field="source" label="任务来源">
        <a-input v-model="form.source" placeholder="工作群通知、上级文件等" />
      </a-form-item>
      <a-form-item field="sensitivity" label="事项敏感级别">
        <a-radio-group v-model="form.sensitivity">
          <a-radio value="normal">普通事项</a-radio>
          <a-radio value="restricted">敏感事项（最小保存）</a-radio>
        </a-radio-group>
      </a-form-item>
      <a-button html-type="submit" type="primary" long size="large" :loading="submitting">
        创建并进入办理
      </a-button>
    </a-form>
  </a-modal>
</template>

<style scoped>
.drawer-hint {
  margin: 0 0 22px;
  padding: 12px 14px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.65;
  background: #f0e8dc;
  border-left: 3px solid var(--cinnabar);
}
</style>
