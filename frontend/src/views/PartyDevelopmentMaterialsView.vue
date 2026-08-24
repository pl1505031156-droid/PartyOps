<script setup lang="ts">
import { onMounted, ref } from "vue";
import { IconRefresh } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";

interface MaterialItem { name: string; source: string; responsible_party: string; guidance: string; required: boolean; national: boolean; }
interface MaterialPhase { phase: string; label: string; items: MaterialItem[]; }
interface MaterialChecklist { rule: { version: string; title: string; source_url: string }; phases: MaterialPhase[]; disclaimer: string; }

const checklist = ref<MaterialChecklist | null>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    checklist.value = await api.get<MaterialChecklist>("/party-development/materials");
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "材料清单读取失败");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="materials-page">
    <header class="subpage-header"><div><p class="page-kicker">发展党员 · 材料清单</p><h2>按阶段准备，一次归集</h2><p>{{ checklist?.disclaimer || "国家规则材料与本单位启用的补充材料分开标识。" }}</p></div><a-space><PageHelp title="发展党员材料清单" :tips="['国家规则材料和本单位补充材料分开标识，单位模板不能删改国家规则项。', '按当前阶段提前核对责任主体、必备属性和办理说明，避免临近节点才补材料。', '材料清单是工作辅助，具体归档口径仍应由党组织审核确认。']" help-query="发展党员 材料清单 单位补充" /><a-button :loading="loading" @click="load"><template #icon><IconRefresh /></template>刷新</a-button></a-space></header>
    <a-alert v-if="checklist" type="info">规则版本 {{ checklist.rule.version }}：{{ checklist.rule.title }}。国家规则项不可由单位模板删除或降级。</a-alert>
    <div class="phase-grid"><article v-for="(phase, index) in checklist?.phases || []" :key="phase.phase"><header><span>{{ String(index + 1).padStart(2, "0") }}</span><h3>{{ phase.label }}</h3><b>{{ phase.items.length }} 项</b></header><ul><li v-for="item in phase.items" :key="`${item.source}-${item.name}`"><div><strong>{{ item.name }}</strong><em :class="{ national: item.national }">{{ item.national ? "国家规则" : `单位补充 · ${item.source}` }}</em></div><p>{{ item.responsible_party }} · {{ item.guidance }}</p></li></ul><p v-if="!phase.items.length" class="empty-state">本阶段暂无固定材料，按组织要求人工确认。</p></article></div>
  </section>
</template>

<style scoped>
.materials-page{padding-top:8px}.subpage-header{display:flex;justify-content:space-between;align-items:start;margin-bottom:18px}.subpage-header h2{margin:4px 0;color:#463328;font-family:"Noto Serif SC","Songti SC",serif;font-size:27px}.subpage-header p{color:var(--muted)}.phase-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:18px}.phase-grid article{border:1px solid var(--line);background:rgba(255,252,245,.94)}.phase-grid article>header{display:grid;grid-template-columns:42px 1fr auto;align-items:center;padding:13px 15px;border-bottom:1px solid var(--line)}.phase-grid header span{color:var(--cinnabar);font:18px Georgia,serif}.phase-grid h3{margin:0;color:#49352a}.phase-grid header b{color:var(--muted);font-size:11px}.phase-grid ul{margin:0;padding:0;list-style:none}.phase-grid li{padding:13px 15px;border-bottom:1px dashed #e6d8c5}.phase-grid li div{display:flex;justify-content:space-between;gap:12px}.phase-grid li strong{color:#4b382c}.phase-grid li em{color:#8a6c50;font-size:10px;font-style:normal}.phase-grid li em.national{color:var(--cinnabar)}.phase-grid li p{margin:6px 0 0;color:var(--muted);font-size:11px;line-height:1.6}.empty-state{padding:20px;color:var(--muted);text-align:center}@media(max-width:850px){.phase-grid{grid-template-columns:1fr}.subpage-header{flex-direction:column;gap:12px}}
</style>
