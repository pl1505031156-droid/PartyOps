<script setup lang="ts">
import { ref } from "vue";
import { IconFile, IconLaunch, IconSafe } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import PageHelp from "../components/PageHelp.vue";

const launching = ref(false);

function transactionId() {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function launchFormatter() {
  if (!window.crypto?.getRandomValues) {
    Message.error("当前浏览器无法生成安全事务标识，请升级浏览器后重试");
    return;
  }
  launching.value = true;
  const link = document.createElement("a");
  link.href = `partyops-client://official-format/${transactionId()}`;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => {
    launching.value = false;
  }, 1200);
}
</script>

<template>
  <div class="page official-format-page">
    <header class="page-header format-header">
      <div>
        <p class="page-kicker">资料 · 本机一次性工具</p>
        <h1 class="page-title">公文规范排版</h1>
        <p class="page-description">按 GB/T 9704-2012 完成诊断、排版、复核和导出；只提供这一种公文预设。</p>
      </div>
      <PageHelp
        title="公文规范排版怎么用"
        :tips="[
          '点击启动后，在本机助手中选择 DOC、DOCX 或 WPS 文件，文件不会进入 PartyOps 主机。',
          '先查看识别角色和预计修改项；不确定的标题、附件、落款或表格必须人工确认。',
          '下载“公文规范版”后仍须由公文责任人终审；系统不判断内容和审批程序。',
        ]"
        help-query="公文规范排版"
      />
    </header>

    <a-alert class="classified-warning" type="error" show-icon>
      <template #title>安全提醒</template>
      不建议在涉密、敏感电脑上使用本功能，也不得使用 PartyOps 处理涉密文件。
    </a-alert>

    <section class="format-workspace">
      <article class="launch-panel">
        <div class="panel-number">9704</div>
        <div class="local-badge"><IconSafe /> 本机处理 · 不上传</div>
        <h2>一次性完成公文版式整理</h2>
        <p>网页只负责唤起当前电脑上的排版助手，协议中只包含随机事务标识，不包含文件名、路径、正文或哈希。</p>
        <a-button type="primary" size="large" :loading="launching" @click="launchFormatter">
          <template #icon><IconLaunch /></template>启动本机排版助手
        </a-button>
        <small>若浏览器询问是否打开“党建智办 PartyOps”，请选择允许。空闲 15 分钟后本次助手自动退出并清理临时副本。</small>
      </article>

      <div class="rules-panel">
        <header><span>唯一格式预设</span><h2>GB/T 9704-2012 公文格式</h2></header>
        <div class="rule-grid">
          <article><b>01</b><strong>页面与版心</strong><p>A4、天头、订口、版心、行数与字数按标准校准。</p></article>
          <article><b>02</b><strong>标题与正文</strong><p>识别标题、一至四级标题、正文、附件、落款、日期和版记。</p></article>
          <article><b>03</b><strong>段落与标点</strong><p>规范行距、缩进、序号和中文标点，同时保护网址、金额与法规条号。</p></article>
          <article><b>04</b><strong>页码与表格</strong><p>检查奇偶页码、表格结构、合并关系、字体、边框和对齐。</p></article>
        </div>
        <div class="format-boundary">
          <IconFile />
          <p><strong>格式边界</strong><span>DOCX 原生处理；DOC/WPS 需要本机 WPS、Office 或 LibreOffice。无法无损回写时只导出 DOCX，并明确提示。</span></p>
        </div>
      </div>
    </section>

    <section class="privacy-ledger">
      <div><span>服务端存储</span><strong>无</strong><small>不写数据库、不生成主机档案</small></div>
      <div><span>网络传输</span><strong>无</strong><small>仅当前电脑 127.0.0.1 回环</small></div>
      <div><span>原文件</span><strong>不覆盖</strong><small>导出增加“公文规范版”后缀</small></div>
      <div><span>文件上限</span><strong>50 MiB</strong><small>超限或结构异常直接拒绝</small></div>
    </section>
  </div>
</template>

<style scoped>
.official-format-page{max-width:1480px}.format-header{align-items:flex-end}.classified-warning{margin:0 0 18px;border-radius:0}.format-workspace{display:grid;grid-template-columns:410px minmax(0,1fr);min-height:610px;border:1px solid var(--line);background:#fffaf0}.launch-panel{display:flex;align-items:flex-start;flex-direction:column;padding:42px;border-right:1px solid var(--line);background:linear-gradient(145deg,#f5ead8,#fffaf0 66%)}.panel-number{color:#a52b231c;font:700 94px/1 Georgia,serif}.local-badge{display:inline-flex;gap:7px;align-items:center;margin:20px 0 12px;color:#386047;font-size:12px;font-weight:700}.launch-panel h2,.rules-panel h2{margin:0;color:#493328;font-family:"Noto Serif SC","Songti SC",serif}.launch-panel h2{font-size:27px}.launch-panel p{margin:14px 0 24px;color:var(--muted);line-height:1.8}.launch-panel small{display:block;margin-top:14px;color:#857061;line-height:1.7}.rules-panel{padding:36px}.rules-panel>header{padding-bottom:18px;border-bottom:2px solid #a52b23}.rules-panel>header span{color:#a52b23;font-size:11px;letter-spacing:.12em}.rules-panel>header h2{margin-top:5px;font-size:24px}.rule-grid{display:grid;grid-template-columns:repeat(2,1fr);border-top:1px solid var(--line);border-left:1px solid var(--line);margin-top:24px}.rule-grid article{min-height:150px;padding:20px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);background:#fffdf8}.rule-grid b{display:block;color:#a52b23;font:700 12px Georgia,serif}.rule-grid strong{display:block;margin:9px 0;color:#4d382c}.rule-grid p{margin:0;color:var(--muted);font-size:12px;line-height:1.7}.format-boundary{display:grid;grid-template-columns:30px 1fr;gap:12px;margin-top:22px;padding:17px;border-left:3px solid #a6723f;background:#f5ead9;color:#74583f}.format-boundary svg{margin-top:4px}.format-boundary p,.format-boundary strong,.format-boundary span{display:block;margin:0}.format-boundary span{margin-top:4px;font-size:12px}.privacy-ledger{display:grid;grid-template-columns:repeat(4,1fr);margin-top:18px;border:1px solid var(--line);background:var(--line);gap:1px}.privacy-ledger div{padding:20px;background:#fffaf0}.privacy-ledger span,.privacy-ledger strong,.privacy-ledger small{display:block}.privacy-ledger span{color:var(--muted);font-size:11px}.privacy-ledger strong{margin:6px 0;color:#8f2b25;font:700 24px Georgia,"Noto Serif SC",serif}.privacy-ledger small{color:#887364}@media(max-width:980px){.format-workspace{grid-template-columns:1fr}.launch-panel{border-right:0;border-bottom:1px solid var(--line)}.privacy-ledger{grid-template-columns:repeat(2,1fr)}}@media(max-width:640px){.launch-panel,.rules-panel{padding:24px}.rule-grid,.privacy-ledger{grid-template-columns:1fr}.panel-number{font-size:70px}}
</style>
