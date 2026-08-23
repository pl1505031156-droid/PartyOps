<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useSessionStore } from "../stores/session";
import PartyDevelopmentView from "./PartyDevelopmentView.vue";
import PartyDevelopmentCasesView from "./PartyDevelopmentCasesView.vue";
import PartyDevelopmentMaterialsView from "./PartyDevelopmentMaterialsView.vue";
import PartyDevelopmentSettingsView from "./PartyDevelopmentSettingsView.vue";

const route = useRoute();
const router = useRouter();
const session = useSessionStore();
const allowedTabs = new Set(["calculate", "cases", "materials", "settings"]);
const activeTab = computed(() => {
  const value = String(route.query.tab || "calculate");
  return allowedTabs.has(value) ? value : "calculate";
});
const canManage = computed(() => session.user?.role === "admin");

function changeTab(value: string | number) {
  const tab = String(value);
  router.replace({ path: "/party-development", query: tab === "calculate" ? {} : { tab } });
}
</script>

<template>
  <div class="page development-hub">
    <header class="hub-header"><div><p class="page-kicker">党务 · 发展党员全周期</p><h1 class="page-title">发展党员</h1><p class="page-description">快速测算、人员台账、材料清单和单位工作口径统一管理；法定边界、参考计划和实际日期始终分栏。</p></div></header>
    <a-tabs :active-key="activeTab" class="hub-tabs" @change="changeTab"><a-tab-pane key="calculate" title="快速测算" /><a-tab-pane key="cases" title="人员台账" /><a-tab-pane key="materials" title="材料清单" /><a-tab-pane key="settings" title="单位口径" /></a-tabs>
    <PartyDevelopmentView v-if="activeTab === 'calculate'" class="embedded-view" />
    <PartyDevelopmentCasesView v-else-if="activeTab === 'cases'" class="embedded-view" />
    <PartyDevelopmentMaterialsView v-else-if="activeTab === 'materials'" class="embedded-view" />
    <PartyDevelopmentSettingsView v-else-if="canManage" class="embedded-view" />
    <a-result v-else status="403" title="仅管理员可以维护单位口径" subtitle="国家规则和材料清单仍可在其他页签查看。" />
  </div>
</template>

<style scoped>
.development-hub{max-width:1540px}.hub-header{margin-bottom:8px}.hub-tabs{position:sticky;top:0;z-index:4;margin-bottom:12px;background:rgba(250,245,235,.96);backdrop-filter:blur(8px)}.embedded-view{padding:0!important}.embedded-view :deep(> .page-header){margin-top:12px}.embedded-view :deep(> .page-header > div:first-child){display:none}.embedded-view :deep(.page-title){font-size:26px}@media(max-width:720px){.hub-tabs{position:static}}
</style>
