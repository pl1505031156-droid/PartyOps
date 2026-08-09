<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { IconCheckCircle, IconRefresh, IconSafe } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import type { DeviceUpdateGate } from "../types";
import { formatServerTime } from "../utils/datetime";
import { zhLabel } from "../utils/labels";

const route = useRoute();
const router = useRouter();
const gate = ref<DeviceUpdateGate | null>(null);
const loading = ref(true);
const starting = ref(false);
let pollTimer: number | undefined;

const isBlocked = computed(() =>
  ["revoked", "quarantined"].includes(gate.value?.state || ""),
);
const isUpdating = computed(() =>
  ["uploaded", "applying"].includes(gate.value?.status || "")
    || gate.value?.state === "updating",
);
const packageUnavailable = computed(() =>
  Boolean(gate.value?.required && !gate.value?.package_id),
);
const progressLabel = computed(() => {
  if (gate.value?.state === "current") return "版本一致";
  if (isBlocked.value) return "设备不可用";
  if (isUpdating.value) return "正在自动更新";
  if (packageUnavailable.value) return "等待主机准备";
  return "等待开始";
});

function schedulePoll(delay = 2500) {
  window.clearTimeout(pollTimer);
  pollTimer = window.setTimeout(loadGate, delay);
}

async function loadGate() {
  try {
    gate.value = await api.get<DeviceUpdateGate>("/device/update-gate");
    if (gate.value.identified && !gate.value.required && gate.value.access_allowed) {
      Message.success("本机已更新至主机版本，正在进入系统");
      const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/";
      await router.replace(redirect);
      window.location.reload();
      return;
    }
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "暂时无法读取更新状态");
  } finally {
    loading.value = false;
  }
  if (isUpdating.value) schedulePoll();
}

async function startUpdate() {
  starting.value = true;
  try {
    gate.value = await api.post<DeviceUpdateGate>("/device/update-start");
    Message.success("更新任务已交给本机党建智办 Agent，完成后将自动进入系统");
    schedulePoll(1500);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "更新启动失败");
  } finally {
    starting.value = false;
  }
}

onMounted(loadGate);
onBeforeUnmount(() => window.clearTimeout(pollTimer));
</script>

<template>
  <main class="update-gate-page">
    <section class="update-card">
      <p class="eyebrow">PartyOps · 设备更新</p>
      <div class="heading">
        <div>
          <h1>协同电脑需要更新</h1>
          <p>为防止不同版本同时修改业务数据，本机更新完成前暂不进入系统。</p>
        </div>
        <span class="state-badge" :class="{ blocked: isBlocked }">{{ progressLabel }}</span>
      </div>

      <a-spin :loading="loading">
        <template v-if="gate">
          <div class="version-flow">
            <div><span>本机版本</span><strong>{{ gate.current_version || "未上报" }}</strong></div>
            <i>→</i>
            <div><span>主机版本</span><strong>{{ gate.target_version }}</strong></div>
          </div>

          <a-alert :type="isBlocked ? 'error' : isUpdating ? 'warning' : 'info'">
            {{ gate.message || "主机已准备好与本机架构匹配的签名更新包。" }}
          </a-alert>

          <section class="release-notes">
            <header>
              <div>
                <span>本次更新</span>
                <h2>{{ gate.release_title || `党建智办 ${gate.target_version}` }}</h2>
              </div>
              <small v-if="gate.installed_at">
                主机安装于 {{ formatServerTime(gate.installed_at, "YYYY-MM-DD HH:mm") }}（北京时间）
              </small>
            </header>
            <ul>
              <li v-for="note in gate.release_notes" :key="note">
                <IconCheckCircle />{{ note }}
              </li>
            </ul>
          </section>

          <div class="safety-note">
            <IconSafe />
            <p>更新由主机签名包和本机受限更新助手完成；不会清空用户配置、灾备副本或接收文件。</p>
          </div>

          <div class="actions">
            <a-button
              type="primary"
              size="large"
              :loading="starting || isUpdating"
              :disabled="isBlocked || isUpdating || packageUnavailable"
              @click="startUpdate"
            >
              {{
                isUpdating
                  ? "正在更新，请保持本机开机"
                  : packageUnavailable
                    ? "等待主机准备更新包"
                    : "开始更新"
              }}
            </a-button>
            <a-button :disabled="loading" @click="loadGate">
              <template #icon><IconRefresh /></template>刷新状态
            </a-button>
          </div>
          <p class="status-line">设备：{{ gate.device_name || "待识别" }} · 状态：{{ zhLabel(gate.status, progressLabel) }}</p>
        </template>
      </a-spin>
    </section>
  </main>
</template>

<style scoped>
.update-gate-page{display:grid;min-height:100vh;place-items:center;padding:32px;background:radial-gradient(circle at 75% 18%,rgba(180,35,24,.08),transparent 32%),#f4eee3;color:#27231f}.update-card{width:min(760px,calc(100vw - 32px));padding:38px 42px;background:rgba(251,248,241,.96);border:1px solid #cfc5b7;box-shadow:0 24px 60px rgba(54,42,31,.12)}.eyebrow{margin:0 0 12px;color:#a7261c;font:12px Georgia,serif;letter-spacing:.16em}.heading{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding-bottom:24px;border-bottom:1px solid #d8cec0}.heading h1{margin:0;font:32px "Noto Serif CJK SC","Songti SC",serif}.heading p{margin:9px 0 0;color:#746c62;line-height:1.7}.state-badge{flex:0 0 auto;padding:7px 12px;color:#8d2a21;background:#f2ddd8;border-radius:999px}.state-badge.blocked{color:#fff;background:#8d2a21}.version-flow{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:18px;margin:26px 0}.version-flow div{padding:18px;background:#eee6d9;border:1px solid #d8cec0}.version-flow span,.version-flow strong{display:block}.version-flow span{color:#746c62;font-size:11px}.version-flow strong{margin-top:6px;font:25px Georgia,serif}.version-flow i{color:#a7261c;font-size:24px;font-style:normal}.release-notes{margin-top:20px;padding:22px;border:1px solid #d8cec0}.release-notes header{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}.release-notes header span,.release-notes header small{color:#746c62;font-size:11px}.release-notes h2{margin:4px 0 0;font-size:19px}.release-notes ul{display:grid;gap:10px;margin:18px 0 0;padding:0;list-style:none}.release-notes li{display:flex;align-items:flex-start;gap:9px;line-height:1.65}.release-notes li svg{flex:0 0 auto;margin-top:4px;color:#2a7245}.safety-note{display:flex;align-items:flex-start;gap:10px;margin:18px 0;color:#625b53;font-size:12px}.safety-note svg{flex:0 0 auto;margin-top:3px;color:#a7261c}.safety-note p{margin:0;line-height:1.7}.actions{display:flex;gap:12px;margin-top:22px}.status-line{margin:16px 0 0;color:#82796e;font-size:11px}@media(max-width:640px){.update-gate-page{padding:16px}.update-card{padding:26px 20px}.heading,.release-notes header{align-items:flex-start;flex-direction:column}.version-flow{grid-template-columns:1fr}.version-flow i{transform:rotate(90deg);justify-self:center}.actions{align-items:stretch;flex-direction:column}}
</style>
