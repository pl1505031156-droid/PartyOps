<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import zhCN from "@arco-design/web-vue/es/locale/lang/zh-cn";
import AppShell from "./components/AppShell.vue";
import { useAppearanceStore } from "./stores/appearance";

const route = useRoute();
const isPublic = computed(() => route.meta.public === true);
const routeReady = computed(() => route.matched.length > 0);
const appearance = useAppearanceStore();

onMounted(() => void appearance.loadContext());
</script>

<template>
  <a-config-provider :locale="zhCN">
    <RouterView v-if="routeReady && isPublic" />
    <AppShell v-else-if="routeReady">
      <RouterView />
    </AppShell>
  </a-config-provider>
</template>
