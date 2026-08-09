<script setup lang="ts">
import { useRouter } from "vue-router";
import { IconSafe } from "@arco-design/web-vue/es/icon";

const router = useRouter();
</script>

<template>
  <div class="page forbidden-page">
    <section class="panel forbidden-card">
      <IconSafe />
      <p class="page-kicker">有效能力校验</p>
      <h1>当前账号无权进入此页面</h1>
      <p>该入口仅向具备相应管理能力的账号开放。普通协同人员仍可使用团队文件、自己的共享目录、传输与接收文件。</p>
      <a-space>
        <a-button type="primary" @click="router.push('/workspace')">返回文件中心</a-button>
        <a-button @click="router.push('/')">返回今日工作台</a-button>
      </a-space>
    </section>
  </div>
</template>

<style scoped>
.forbidden-page{display:grid;min-height:calc(100vh - 70px);place-items:center}.forbidden-card{width:min(620px,92vw);padding:48px;text-align:center}.forbidden-card>svg{color:var(--cinnabar);font-size:48px}.forbidden-card h1{font:600 28px var(--serif)}.forbidden-card p:not(.page-kicker){margin:14px auto 26px;max-width:500px;color:var(--muted);line-height:1.8}
</style>
