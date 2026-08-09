<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  IconEye,
  IconEyeInvisible,
  IconLock,
  IconSettings,
  IconUser,
} from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { useSessionStore } from "../stores/session";
import { orientalDateLabel } from "../utils/lunar";
import OrientalArtLayer from "../components/OrientalArtLayer.vue";
import { sceneConfigForPath } from "../theme/oriental";

const router = useRouter();
const route = useRoute();
const session = useSessionStore();
const loading = ref(false);
const showPassword = ref(false);
const showConnection = ref(false);
const configured = ref(true);
const form = reactive({
  username: "",
  password: "",
  displayName: "",
});
const orientalDate = orientalDateLabel();
const artConfig = sceneConfigForPath("/login");
const connectionLabel = computed(() => {
  const bootstrap = session.bootstrap;
  if (!bootstrap) return "正在检测主机";
  return `主机已连接 · ${bootstrap.host}:${bootstrap.port}`;
});

onMounted(async () => {
  const status = await session.loadBootstrap();
  configured.value = status.configured;
  if (await session.ensure()) await router.replace("/");
});

async function submit() {
  if (!form.username || !form.password) {
    Message.warning("请输入用户名和密码");
    return;
  }
  loading.value = true;
  try {
    if (configured.value) {
      await session.login(form.username, form.password);
    } else {
      if (!form.displayName) {
        Message.warning("请填写管理员姓名");
        return;
      }
      await session.setup(form.username, form.displayName, form.password);
    }
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/";
    await router.replace(redirect);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "无法进入系统");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-screen">
    <OrientalArtLayer
      :config="artConfig"
      :solar-term="orientalDate.solarTerm"
      :active-solar-term="orientalDate.activeSolarTerm"
      standalone
    />
    <section class="identity-pane">
      <div class="identity-content">
        <h1><span>党建</span>智办</h1>
        <div class="brand-rule"><b></b><i></i></div>
        <p class="subtitle">基层党建工作闭环协同系统</p>
        <p class="partyops">PartyOps</p>
        <p class="oriental-date">{{ orientalDate.gregorian }}　{{ orientalDate.weekday }}　{{ orientalDate.lunar }}<b v-if="orientalDate.solarTerm">{{ orientalDate.solarTerm }}</b></p>
      </div>
      <img class="motif" src="../assets/launch-motif.png" alt="" aria-hidden="true" />
    </section>
    <section class="login-pane">
      <div class="login-form-wrap">
        <div class="connection-status">
          <span class="connection-dot"></span>
          <strong>{{ connectionLabel }}</strong>
        </div>
        <div class="form-rule"></div>
        <form @submit.prevent="submit">
          <label v-if="!configured">
            <span class="field-label">管理员姓名</span>
            <a-input v-model="form.displayName" size="large" placeholder="请输入姓名">
              <template #prefix><IconUser /></template>
            </a-input>
          </label>
          <label>
            <span class="field-label">用户名</span>
            <a-input v-model="form.username" size="large" placeholder="请输入用户名" autocomplete="username">
              <template #prefix><IconUser /></template>
            </a-input>
          </label>
          <label>
            <span class="field-label">密码</span>
            <a-input
              v-model="form.password"
              size="large"
              :type="showPassword ? 'text' : 'password'"
              placeholder="请输入密码"
              :autocomplete="configured ? 'current-password' : 'new-password'"
            >
              <template #prefix><IconLock /></template>
              <template #suffix>
                <button
                  class="eye-button"
                  type="button"
                  :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                  @click="showPassword = !showPassword"
                >
                  <IconEyeInvisible v-if="showPassword" />
                  <IconEye v-else />
                </button>
              </template>
            </a-input>
          </label>
          <a-button html-type="submit" type="primary" size="large" long :loading="loading">
            {{ configured ? "进入系统" : "完成主机配置并进入" }}
          </a-button>
        </form>
        <div class="form-rule bottom"></div>
        <button class="connection-settings" type="button" @click="showConnection = true">
          <IconSettings />
          连接设置
        </button>
        <p v-if="!configured" class="setup-note">首次使用：当前将初始化为主机模式，业务数据只保存在本机。</p>
      </div>
    </section>
    <a-modal v-model:visible="showConnection" title="局域网连接设置" :footer="false">
      <div class="connection-modal">
        <span class="connection-dot"></span>
        <div>
          <strong>当前连接正常：{{ session.bootstrap?.service_url }}</strong>
          <p v-if="session.bootstrap?.lan_candidates.length">本机检测到的局域网地址：{{ session.bootstrap.lan_candidates.join("、") }}。协同终端应使用主机明确绑定的地址。</p>
          <p v-else>当前仅检测到本机访问地址。若要多设备协同，请运行配置向导并选择主机局域网 IP。</p>
        </div>
      </div>
    </a-modal>
  </main>
</template>

<style scoped>
.login-screen {
  display: grid;
  min-height: 100vh;
  grid-template-columns: 55% 45%;
  overflow: hidden;
  background-color: #f7f1e7;
  background-image: url("../assets/paper-texture.png");
  background-size: 760px;
}

.identity-pane,
.login-pane {
  position: relative;
  min-height: 100vh;
}

.identity-content {
  position: relative;
  z-index: 2;
  width: min(590px, 76%);
  margin: 20vh 0 0 7.9vw;
}

h1 {
  margin: 0;
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", STSong, SimSun, serif;
  font-size: clamp(76px, 8.6vw, 124px);
  font-weight: 500;
  line-height: 1;
  letter-spacing: 0.025em;
}

h1 span {
  color: var(--cinnabar);
}

.brand-rule {
  display: flex;
  align-items: center;
  width: 100%;
  margin: 24px 0 30px;
}

.brand-rule b {
  width: 38px;
  height: 5px;
  background: var(--cinnabar);
}

.brand-rule i {
  height: 1px;
  flex: 1;
  background: rgba(180, 35, 24, 0.48);
}

.subtitle {
  margin: 0;
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", STSong, SimSun, serif;
  font-size: clamp(30px, 3vw, 45px);
  letter-spacing: 0.12em;
  white-space: nowrap;
}

.partyops {
  margin: 22px 0 0;
  color: var(--cinnabar);
  font-family: Georgia, "Times New Roman", serif;
  font-size: 28px;
  letter-spacing: 0.05em;
}

.oriental-date {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 20px 0 0;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.06em;
}

.oriental-date b {
  padding: 2px 6px;
  color: var(--cinnabar);
  font-family: var(--serif);
  font-weight: 500;
  border: 1px solid rgba(180, 35, 24, 0.5);
}

.motif {
  position: absolute;
  bottom: 0;
  left: 0;
  z-index: 1;
  width: min(58vw, 820px);
  height: auto;
  mix-blend-mode: multiply;
  pointer-events: none;
}

.login-pane {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 7.8vw 40px 4vw;
}

.login-form-wrap {
  width: min(500px, 100%);
  transform: translateY(3.7vh);
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 17px;
  letter-spacing: 0.04em;
}

.connection-dot {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
  border-radius: 50%;
  background: var(--cinnabar);
}

.form-rule {
  height: 1px;
  margin: 43px 0 37px;
  background: var(--line);
}

.form-rule.bottom {
  margin: 30px 0 22px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 35px;
}

.field-label {
  display: block;
  margin-bottom: 11px;
  font-size: 16px;
}

:deep(.arco-input-wrapper) {
  height: 72px;
  padding: 0 18px;
  font-size: 16px;
  background: rgba(255, 255, 255, 0.38) !important;
}

:deep(.arco-btn-size-large) {
  height: 78px;
  margin-top: 8px;
  border-radius: 2px;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0.12em;
}

.eye-button {
  display: grid;
  padding: 5px;
  color: #79736c;
  background: transparent;
  border: 0;
  cursor: pointer;
  place-items: center;
}

.connection-settings {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 auto;
  padding: 8px 12px;
  color: var(--cinnabar);
  background: transparent;
  border: 0;
  cursor: pointer;
}

.setup-note {
  margin: 18px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
}

.connection-modal {
  display: flex;
  gap: 14px;
  padding: 12px 4px 4px;
}

.connection-modal p {
  color: var(--muted);
  line-height: 1.7;
}

@media (max-height: 800px) {
  .identity-content {
    margin-top: 13vh;
  }

  h1 {
    font-size: 72px;
  }

  .login-form-wrap {
    transform: translateY(1vh) scale(0.9);
  }
}
</style>
