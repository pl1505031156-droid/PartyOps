<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { IconCheck, IconRefresh, IconRight } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import type { EnablementStatus } from "../types";
import PageHelp from "../components/PageHelp.vue";

const status = ref<EnablementStatus | null>(null);
const loading = ref(false);
const progress = computed(() => {
  if (!status.value?.total_count) return 0;
  return Math.round(status.value.completed_count / status.value.total_count * 100);
});
const personaLabel = computed(() => ({
  host_admin: "主机 · 管理员",
  host_staff: "主机 · 协同人员",
  client_admin: "协同机 · 管理员",
  client_staff: "协同机 · 协同人员",
}[status.value?.persona || "host_staff"]));

async function load() {
  loading.value = true;
  try {
    status.value = await api.get<EnablementStatus>("/me/enablement");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "上手检查加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page getting-started-page">
    <header class="page-header">
      <div>
        <p class="date-kicker">FIRST RUN · FACTUAL CHECKLIST</p>
        <h1 class="page-title">上手与协同检查</h1>
        <p class="page-description">系统根据真实账号、网络、备份、设备、目录、传输和工作数据自动判断；不需要手工勾选“我已掌握”。</p>
      </div>
      <a-space>
        <PageHelp title="为什么要完成上手检查" :tips="['主机地址、备份和设备心跳必须产生真实结果。', '不同电脑和账号只显示自己需要完成的步骤。', '完成状态来自业务数据，刷新后自动更新。']" help-query="首次配置" />
        <a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>重新检查</a-button>
      </a-space>
    </header>

    <a-spin :loading="loading" style="width:100%">
      <section v-if="status" class="enablement-hero">
        <div>
          <span>{{ personaLabel }}</span>
          <h2>{{ status.title }}</h2>
          <p>{{ status.summary }}</p>
        </div>
        <div class="progress-seal"><strong>{{ progress }}%</strong><small>{{ status.completed_count }} / {{ status.total_count }} 已完成</small></div>
      </section>

      <section v-if="status" class="enablement-list">
        <article v-for="(step, index) in status.steps" :key="step.key" :class="{ complete: step.complete }">
          <div class="step-index"><IconCheck v-if="step.complete" /><span v-else>{{ String(index + 1).padStart(2, '0') }}</span></div>
          <div class="step-copy"><small>{{ step.complete ? "真实状态已确认" : "等待完成" }}</small><h3>{{ step.title }}</h3><p>{{ step.description }}</p></div>
          <RouterLink :to="step.route" class="step-action">{{ step.complete ? "查看" : step.action_label }}<IconRight /></RouterLink>
        </article>
      </section>

      <section v-if="status && progress === 100" class="ready-banner"><IconCheck /><div><strong>当前账号与电脑已完成上手闭环</strong><p>你仍可随时返回这里复核权限、共享目录和协同状态。</p></div><RouterLink to="/">进入今日工作台</RouterLink></section>
    </a-spin>
  </div>
</template>

<style scoped>
.getting-started-page{max-width:1180px}.date-kicker{margin:0 0 10px;color:var(--cinnabar);font:12px Georgia,serif;letter-spacing:.11em}.enablement-hero{display:flex;align-items:center;justify-content:space-between;gap:40px;margin-bottom:20px;padding:30px 34px;color:#f9f1e7;background:var(--charcoal);border-top:4px solid var(--cinnabar)}.enablement-hero span{color:#e5b0a5;font-size:11px;letter-spacing:.08em}.enablement-hero h2{margin:8px 0;font:500 28px var(--serif)}.enablement-hero p{max-width:720px;margin:0;color:#c9c0b6;line-height:1.8}.progress-seal{display:grid;width:138px;height:138px;flex:0 0 138px;border:1px solid rgba(255,255,255,.28);border-radius:50%;place-content:center;text-align:center}.progress-seal strong{font:38px Georgia,serif}.progress-seal small{margin-top:4px;color:#c9c0b6}.enablement-list{display:grid;gap:1px;background:var(--line);border:1px solid var(--line)}.enablement-list article{display:grid;grid-template-columns:60px minmax(0,1fr) auto;gap:18px;align-items:center;min-height:126px;padding:20px 24px;background:rgba(251,248,241,.92)}.enablement-list article.complete{background:rgba(242,238,228,.8)}.step-index{display:grid;width:46px;height:46px;color:var(--cinnabar);font:16px Georgia,serif;border:1px solid var(--cinnabar);border-radius:50%;place-items:center}.complete .step-index{color:#2c6a42;border-color:#2c6a42;background:#e8f2e9}.step-copy small{color:var(--cinnabar);font-size:10px;letter-spacing:.08em}.complete .step-copy small{color:#2c6a42}.step-copy h3{margin:5px 0;font-size:17px}.step-copy p{margin:0;color:var(--muted);font-size:12px;line-height:1.7}.step-action{display:flex;align-items:center;gap:7px;padding:9px 12px;color:var(--cinnabar);border:1px solid rgba(180,35,24,.38)}.ready-banner{display:flex;align-items:center;gap:15px;margin-top:20px;padding:18px 22px;color:#234d31;background:#e8f2e9;border-left:4px solid #2c6a42}.ready-banner strong,.ready-banner p{display:block;margin:0}.ready-banner p{margin-top:3px;color:#52705c;font-size:11px}.ready-banner a{margin-left:auto;color:#234d31;font-weight:600}@media(max-width:760px){.enablement-hero{align-items:flex-start;flex-direction:column}.enablement-list article{grid-template-columns:50px 1fr}.step-action{grid-column:2;justify-self:start}.ready-banner{align-items:flex-start;flex-wrap:wrap}.ready-banner a{width:100%;margin:0}}
</style>
