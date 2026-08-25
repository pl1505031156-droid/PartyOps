<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import { useSessionStore } from "../stores/session";
import type { EnablementStatus } from "../types";

const session = useSessionStore();
const route = useRoute();
const keyword = ref(String(route.query.q || ""));
const selectedId = ref("start");
const enablement = ref<EnablementStatus | null>(null);
const enablementLoading = ref(false);

const guides = [
  {
    id: "start",
    title: "第一次使用",
    summary: "用五步完成从通知到归档的完整闭环",
    steps: [
      "进入“快速收件箱”，粘贴通知或选择 Word、PDF、扫描件。",
      "核对系统提取的名称、正式时限、责任人和材料要求，再人工确认建档。",
      "在事项详情维护办理状态、协办人、反馈、步骤和材料版本。",
      "上传实际报送稿并确认唯一最终版本；缺少材料时说明“不适用”原因。",
      "完成审核后归档，周报、台账、迎检包和交接包直接复用已有数据。",
    ],
  },
  {
    id: "inbox",
    title: "快速收件箱",
    summary: "把通知快速变成可追踪事项，原件同时归档",
    steps: [
      "进入“工作 → 快速收件箱”，可直接粘贴通知，也可选择 Word、WPS、PDF、图片或文本文件。",
      "点击“本地识别并进入确认”；识别只在主机本地进行，不会自动创建事项。",
      "核对事项名称、正式截止时间、责任人、来源和报送要求；未识别到的内容由人员补充。",
      "点击“确认并创建事项”后，系统建立快速任务；上传的原始文件会同时成为“原始通知”材料。",
      "文件损坏或正文暂不支持时仍可人工建档并归档原件；超过 50 MB 的文件在事项创建后作为材料上传。",
    ],
  },
  {
    id: "daily",
    title: "每天怎么用",
    summary: "只处理今天必须做的工作，避免无意义维护",
    steps: [
      "先看“今日工作台”的今天到期、近期到期、待审核和等待反馈。",
      "按 Ctrl+K 搜索任务、文件、联系人、日志、报告或设备。",
      "有实质进展时才更新状态并补充工作日志，不记录在线时长。",
      "需要一次修改多项时，在“事项与清单”勾选后使用批量处理。",
      "处理完站内提醒，确认材料缺项和未归档事项没有被遗漏。",
    ],
  },
  {
    id: "recurrence",
    title: "月度、季度和年度任务",
    summary: "周期任务只配置一次，后续按内部节点自动生成",
    steps: [
      "先在“周期与模板”建立步骤和材料清单模板。",
      "新建周期规则，选择每月、每季度、每半年、每年或自定义天数。",
      "填写正式节点与内部提前天数；系统会在内部节点生成新一期任务。",
      "在独立“工作日历”维护节假日和调休，内部节点自动向前调整。",
      "在“周期汇总”建立月报、季报或年报，系统按完成时间自动归集。",
    ],
  },
  {
    id: "files",
    title: "文件中心与版本",
    summary: "原始目录只读纳管，预览、本机打开与最终固化各有明确边界",
    steps: [
      "管理员只选择允许纳管的主机或协同设备文件夹。",
      "PDF 使用系统同源预览；WPS 等无法可靠结构化预览的格式会直接提示本机打开或下载，不再显示空白页。",
      "本机打开使用五分钟有效的一次性授权；页面会分别说明授权过期、已使用、助手未启动、主机不可达、证书、文件或默认程序错误。",
      "把文件关联到任务、专题、日志或报告；关联不复制原件。",
      "领导修改前后两个版本可在“资料 → 文档比较与查重”核对文字差异。",
      "最终材料执行固化归档并校验 SHA-256，原始目录变化不会丢失终稿。",
    ],
  },
  {
    id: "official-format",
    title: "公文规范排版",
    summary: "本机离线按 GB/T 9704-2012 诊断、排版、复检并导出",
    steps: [
      "进入“资料 → 公文规范排版”，点击选择文件；网页只传随机事务标识，不接收文件名、路径、正文或哈希。",
      "DOCX 由本机助手离线处理；DOC、WPS 需要本机已经安装可用的 WPS、Microsoft Office 或 LibreOffice，转换不可用时会明确停止。",
      "先查看识别角色和预计修改项；标题层级、附件、版记或表格判断不确定时必须人工确认后再排版。",
      "一键排版只使用 GB/T 9704-2012 公文预设，不覆盖原文件；完成后重新校验页面、字体、段落、页码、附件、版记和表格。",
      "导出、取消、异常或空闲十五分钟后清理本机临时副本。普通删除不等于取证级擦除；不得使用 PartyOps 处理涉密文件。",
    ],
  },
  {
    id: "reports",
    title: "周报、月报与交接",
    summary: "任务只录一次，报告和交接清单自动复用",
    steps: [
      "完成事项时填写实际完成情况和经验说明。",
      "建立周期报告后检查系统自动归集的本期完成、下期计划和延续事项。",
      "补充风险及协调事项，发布时保存快照，确认后锁定归档。",
      "在报告设计器选择栏目和顺序，后续新报告直接套用。",
      "离岗或轮岗前从今日工作台生成交接包，核对任务、联系人、文件和材料清单。",
    ],
  },
  {
    id: "archives",
    title: "重要档案与扫描件",
    summary: "按任意年度规范保存人事调动、年度考核和其他重要文件",
    steps: [
      "进入“重要档案”，直接输入需要的四位年度；历史年度和未来年度都不受 2025 年限制。",
      "选择人事调动、事业编考核、公务员考核或管理员自定义类别，再录入文号、人员、日期和摘要。",
      "年度考核按一人一档录入；同一人员编号同年度重复时系统会阻止，同名人员会提示复核。",
      "上传 PDF、图片或 Office 扫描件，系统按 SHA-256 去重保存并在后台执行中文 OCR。",
      "错误记录只能作废或修订，不物理删除；年度目录可导出 Excel、Word、扫描件和校验清单。",
    ],
  },
  {
    id: "devices",
    title: "多设备协同",
    summary: "设备和用户双重授权，回环浏览与协同公布地址严格分开",
    steps: [
      "主机管理员生成十分钟有效的一次性入网码。",
      "“系统管理 → 网络与协同”分别显示监听、本机浏览、自动探测和协同公布地址；127.0.0.1、localhost、0.0.0.0 不能公布给协同机。",
      "终端完成入网后，由管理员批准共享根和浏览、预览、下载、发送权限。",
      "修改协同地址会创建可回滚事务，依次更新证书、检查健康状态并通知旧协同机；确认新地址可用后再结束迁移宽限期。",
      "跨设备文件统一经过主机中转并分块校验，不建立终端直连。",
      "危险文件和敏感目录进入审批；撤销授权后下一分块停止。",
      "业务能力由用户角色和权限决定；具备创建权限的协同用户可以新建档案、会议、发展党员记录和在线业务文档。",
    ],
  },
  {
    id: "party-development",
    title: "发展党员预测与档案",
    summary: "法定边界、参考计划、人工调整和实际日期分开保存",
    steps: [
      "快速测算填写申请书日期后即生成全部后续节点的第一轮参考计划，不再用“等待录入”代替可计算日期。",
      "明确法定期限的节点显示最早或截止边界；组织研究、会议和材料准备使用单位参考间隔，并标注“参考计划”。",
      "补录实际日期后只重算尚未发生的下游节点；实际记录和人工调整不会被系统静默覆盖。",
      "发展档案按 60、30、14、7、1 天和逾期提醒；提醒前仍应由党务人员核对制度和真实办理状态。",
      "Word、Excel 导出同时标明规则版本、依据和参考属性，预测值不能当作已经发生的组织决定。",
    ],
  },
  {
    id: "diagnostics",
    title: "安装、启动与卸载诊断",
    summary: "先看精确错误码和诊断摘要，再决定修复或保留数据卸载",
    steps: [
      "安装或启动失败时先复制界面显示的诊断摘要和日志位置；系统会分别检查可执行文件、运行库、SQLite、配置迁移、端口、权限、子进程输出和健康端点。",
      "出现 RUNTIME_PERMISSION_DENIED 时按摘要中的故障阶段处理：程序或配置不可读请用当前安装包执行修复安装；个人数据目录不可写请回到配置向导选择当前账号可写的本机固定磁盘目录。不要给 Everyone 完全控制，也不要删除原数据。",
      "麒麟安装完成后由可观察验证服务继续启动检查，图形安装器不再长时间卡在 1%；验证失败应查看安装验证日志。",
      "macOS 若应用进程尚未进入 Python 运行时，可在用户配置目录查看 launch-probe.log；若被 Gatekeeper 在执行前拦截则不会有应用日志。",
      "卸载选择“仅删除程序并保留数据”时不会检查或删除数据目录；完整删除预检失败会自动退回保留数据模式。",
      "不要手工删除数据目录或数据库旁的 WAL 文件。升级失败时保留诊断并使用系统回滚入口。",
    ],
  },
  {
    id: "updates",
    title: "官方更新与模型签名",
    summary: "更新、安装包和模型都必须先通过正式签名与哈希验证",
    adminOnly: true,
    steps: [
      "检查更新失败时复制脱敏诊断；DNS、TLS、代理、超时、HTTP 状态、清单格式、签名、哈希和平台不匹配会分别显示。",
      "更新清单必须来自官网 /releases/update-v3.json，若返回网页 HTML、缺少必填字段或签名不匹配，客户端会拒绝使用。",
      "正式 Ed25519 私钥只通过本机文件路径交给发布工具；不得粘贴到聊天、写入源码、日志或安装包。",
      "本机私钥与 PartyOps 内置信任公钥不匹配时，更新清单、安装包和模型发布会整体停止，不能用临时测试密钥替代。",
      "小模型只有在许可、哈希和正式签名全部验证后才提供国内直链；大模型继续跳转官方来源。",
    ],
  },
  {
    id: "ai",
    title: "安全使用 AI",
    summary: "AI 默认关闭、最小范围读取，只生成草稿",
    steps: [
      "管理员在系统设置中配置经批准的内网模型或兼容接口。",
      "明确允许的文件根、任务类别、文件类型和能力；敏感事项始终禁止。",
      "外部接口调用前核对将发送的来源清单和最小片段。",
      "检查草稿下方的任务、文件和原文引用，资料不足时人工补充。",
      "在 AI 审批队列确认后再复制到正式业务页面，AI 不直接改业务数据。",
    ],
  },
  {
    id: "admin",
    title: "管理员维护",
    summary: "备份、更新、权限和诊断的固定检查清单",
    adminOnly: true,
    steps: [
      "每周确认最近自动备份成功，并检查协同终端已拉取灾备副本。",
      "在运行状态页查看数据库、附件、OCR、扫描、SSE 和磁盘空间。",
      "新设备使用一次性入网码；离职、调岗或异常设备立即撤销权限与证书。",
      "更新前确认正式签名清单、自动备份和磁盘空间，再分批升级主机与协同终端。",
      "出现异常先下载诊断日志，不删除数据目录，不手工复制 WAL 状态下的数据库。",
    ],
  },
];

const visibleGuides = computed(() => guides.filter((item) => !item.adminOnly || session.user?.role === "admin"));
const filteredGuides = computed(() => {
  const value = keyword.value.trim().toLowerCase();
  if (!value) return visibleGuides.value;
  return visibleGuides.value.filter((item) => `${item.title}${item.summary}${item.steps.join("")}`.toLowerCase().includes(value));
});
const selected = computed(() => visibleGuides.value.find((item) => item.id === selectedId.value) || filteredGuides.value[0] || visibleGuides.value[0]);
const progress = computed(() => {
  if (!enablement.value?.total_count) return 0;
  return Math.round(enablement.value.completed_count / enablement.value.total_count * 100);
});
const personaLabels: Record<string, string> = {
  host_admin: "主机 · 管理员",
  host_staff: "主机 · 普通用户",
  client_admin: "协同机 · 管理员",
  client_staff: "协同机 · 普通用户",
};
const personaLabel = computed(() => (
  personaLabels[enablement.value?.persona || ""] || "当前账号"
));

async function loadEnablement() {
  enablementLoading.value = true;
  try {
    enablement.value = await api.get<EnablementStatus>("/me/enablement");
  } catch {
    enablement.value = null;
  } finally {
    enablementLoading.value = false;
  }
}

onMounted(loadEnablement);
</script>

<template>
  <div class="page help-page">
    <header class="help-hero">
      <div>
        <p>PARTYOPS · ONE GUIDANCE CENTER</p>
        <h1>帮助、上手与协同检查</h1>
        <span>首次配置后的真实状态、日常操作教程和防错说明统一放在这里。</span>
      </div>
      <div class="progress-seal"><strong>{{ progress }}%</strong><small>事实完成度</small></div>
    </header>

    <section id="setup-check" class="setup-check" :aria-busy="enablementLoading">
      <header>
        <div>
          <span>{{ personaLabel }}</span>
          <h2>{{ enablement?.title || "正在检查当前电脑与账号" }}</h2>
          <p>{{ enablement?.summary || "系统正在读取网络、备份、设备、共享目录、传输与工作状态。" }}</p>
        </div>
        <a-button :loading="enablementLoading" @click="loadEnablement">重新检查真实状态</a-button>
      </header>
      <div v-if="enablement" class="setup-steps">
        <article v-for="(step, index) in enablement.steps" :key="step.key" :class="{ complete: step.complete }">
          <b>{{ step.complete ? "✓" : String(index + 1).padStart(2, "0") }}</b>
          <div><small>{{ step.complete ? "真实状态已确认" : "尚未完成" }}</small><strong>{{ step.title }}</strong><p>{{ step.description }}</p></div>
          <RouterLink :to="step.route">{{ step.complete ? "查看" : step.action_label }}</RouterLink>
        </article>
      </div>
      <p v-else-if="!enablementLoading" class="setup-error">暂时无法读取事实检查；操作教程仍可正常使用，请稍后重新检查。</p>
    </section>

    <div class="help-search"><strong>操作教程</strong><a-input-search v-model="keyword" size="large" allow-clear placeholder="搜索“公文排版”“文件打开”“协同地址”“更新诊断”……" /></div>

    <div class="help-layout">
      <aside>
        <button
          v-for="guide in filteredGuides"
          :key="guide.id"
          type="button"
          :class="{ active: selected?.id === guide.id }"
          @click="selectedId = guide.id"
        >
          <i>{{ String(visibleGuides.indexOf(guide) + 1).padStart(2, "0") }}</i>
          <span><strong>{{ guide.title }}</strong><small>{{ guide.summary }}</small></span>
        </button>
        <p v-if="!filteredGuides.length">没有找到相关教程，请换一个关键词。</p>
      </aside>

      <main v-if="selected" class="guide-paper">
        <header><span>{{ selected.adminOnly ? "管理员指南" : "操作指南" }}</span><h2>{{ selected.title }}</h2><p>{{ selected.summary }}</p></header>
        <ol>
          <li v-for="(step, index) in selected.steps" :key="step"><b>{{ index + 1 }}</b><p>{{ step }}</p></li>
        </ol>
        <div class="guide-actions">
          <span>教程用于随时查阅；上方完成度只认真实业务状态，不接受手工勾选。</span>
          <RouterLink to="/">返回今日工作台</RouterLink>
        </div>
      </main>
    </div>

    <section class="mistake-board">
      <div><span>01</span><strong>不要重复建档</strong><p>先用 Ctrl+K 搜索标题和文件，确认不存在后再新建。</p></div>
      <div><span>02</span><strong>不要覆盖终稿</strong><p>上传新版本并重新确认最终稿，历史版本必须保留。</p></div>
      <div><span>03</span><strong>不要改历史周报</strong><p>已发布报告保留快照，后续变化以重新打开和差异记录处理。</p></div>
      <div><span>04</span><strong>不要删除数据目录</strong><p>升级或故障时先备份和下载诊断，使用系统恢复入口。</p></div>
    </section>

    <section class="source-notice">
      <div><span>开源与许可</span><strong>查看并获取当前对应源代码</strong></div>
      <p>PartyOps 使用 GPL-3.0 开源组件，并组合使用 AGPL-3.0 的 PyMuPDF。你可以审查、下载和改进与当前版本对应的完整源代码。</p>
      <a href="https://github.com/pl1505031156-droid/PartyOps" target="_blank" rel="noopener noreferrer">前往 GitHub 源代码仓库</a>
    </section>
  </div>
</template>

<style scoped>
.help-page{max-width:1380px}.help-hero{display:flex;align-items:flex-end;justify-content:space-between;min-height:190px;padding:34px 42px;color:#f8efe4;background:var(--charcoal);border-bottom:5px solid var(--cinnabar)}.help-hero p{margin:0 0 12px;color:#d38a7e;font:11px Georgia,serif;letter-spacing:.2em}.help-hero h1{margin:0;font-family:var(--serif);font-size:34px;letter-spacing:.06em}.help-hero span{display:block;margin-top:12px;color:#c8beb2;font-size:12px}.progress-seal{display:grid;width:92px;height:92px;place-content:center;text-align:center;border:1px solid #d38a7e;border-radius:50%}.progress-seal strong{font:26px Georgia,serif}.progress-seal small{margin-top:4px;color:#d38a7e;font-size:9px;letter-spacing:.12em}.setup-check{margin-top:20px;border:1px solid var(--line);background:rgba(251,248,241,.82)}.setup-check>header{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:22px 26px;color:#f8efe4;background:#312d29;border-left:4px solid var(--cinnabar)}.setup-check>header span{color:#d38a7e;font:10px Georgia,serif;letter-spacing:.12em}.setup-check>header h2{margin:7px 0 4px;font:500 23px var(--serif)}.setup-check>header p{margin:0;color:#c8beb2;font-size:11px}.setup-steps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.setup-steps article{display:grid;grid-template-columns:40px minmax(0,1fr) auto;gap:12px;align-items:center;min-height:116px;padding:17px 20px;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}.setup-steps article>b{display:grid;width:34px;height:34px;color:var(--cinnabar);font:11px Georgia,serif;border:1px solid rgba(180,35,24,.5);border-radius:50%;place-items:center}.setup-steps article.complete>b{color:#2c6a42;border-color:#2c6a42;background:#e8f2e9}.setup-steps article small,.setup-steps article strong{display:block}.setup-steps article small{color:var(--cinnabar);font-size:9px}.setup-steps article strong{margin:3px 0}.setup-steps article p{margin:0;color:var(--muted);font-size:10px;line-height:1.6}.setup-steps article>a{padding:7px 9px;color:var(--cinnabar);font-size:10px;border:1px solid rgba(180,35,24,.35)}.setup-error{margin:0;padding:20px;color:var(--cinnabar)}.help-search{display:grid;grid-template-columns:auto minmax(320px,720px);align-items:center;justify-content:space-between;gap:20px;padding:28px 0 16px}.help-search>strong{font:500 22px var(--serif)}.help-layout{display:grid;grid-template-columns:310px minmax(0,1fr);min-height:560px;border:1px solid var(--line);background:rgba(251,248,241,.72)}.help-layout aside{padding:14px;border-right:1px solid var(--line)}.help-layout aside button{display:grid;width:100%;grid-template-columns:34px minmax(0,1fr);gap:10px;padding:13px 10px;text-align:left;background:transparent;border:0;border-bottom:1px solid var(--line-light);cursor:pointer}.help-layout aside button:hover,.help-layout aside button.active{background:rgba(180,35,24,.05)}.help-layout aside button.active{color:var(--cinnabar);border-left:3px solid var(--cinnabar)}.help-layout aside i{display:grid;width:28px;height:28px;place-items:center;color:var(--muted);font:10px Georgia,serif;border:1px solid var(--line);border-radius:50%}.help-layout aside strong,.help-layout aside small{display:block}.help-layout aside small{margin-top:5px;color:var(--muted);font-size:9px;line-height:1.5}.guide-paper{padding:38px 48px}.guide-paper header{padding-bottom:22px;border-bottom:2px solid var(--charcoal)}.guide-paper header span{color:var(--cinnabar);font:10px Georgia,serif;letter-spacing:.2em}.guide-paper h2{margin:9px 0 6px;font-family:var(--serif);font-size:28px}.guide-paper header p{margin:0;color:var(--muted);font-size:11px}.guide-paper ol{margin:24px 0;padding:0;list-style:none}.guide-paper li{display:grid;grid-template-columns:38px minmax(0,1fr);align-items:start;padding:17px 0;border-bottom:1px solid var(--line-light)}.guide-paper li b{display:grid;width:28px;height:28px;place-items:center;color:var(--cinnabar);font:13px Georgia,serif;border:1px solid rgba(180,35,24,.45);border-radius:50%}.guide-paper li p{margin:3px 0 0;line-height:1.8}.guide-actions{display:flex;align-items:center;justify-content:space-between;margin-top:26px;color:var(--muted);font-size:11px}.guide-actions a{color:var(--cinnabar)}.mistake-board{display:grid;grid-template-columns:repeat(4,1fr);margin-top:22px;border:1px solid var(--line)}.mistake-board>div{min-height:150px;padding:20px;border-right:1px solid var(--line)}.mistake-board>div:last-child{border-right:0}.mistake-board span{color:var(--cinnabar);font:20px Georgia,serif}.mistake-board strong{display:block;margin:12px 0 7px}.mistake-board p{margin:0;color:var(--muted);font-size:10px;line-height:1.7}
.help-page{width:100%;max-width:none}
.source-notice{display:grid;grid-template-columns:220px minmax(0,1fr) auto;gap:24px;align-items:center;margin-top:22px;padding:22px 26px;color:#f8efe4;background:var(--charcoal);border-left:5px solid var(--cinnabar)}.source-notice span,.source-notice strong{display:block}.source-notice span{margin-bottom:6px;color:#d38a7e;font:10px Georgia,serif;letter-spacing:.14em}.source-notice p{margin:0;color:#c8beb2;font-size:11px;line-height:1.8}.source-notice a{padding:10px 14px;color:#fff;border:1px solid #d38a7e;border-radius:4px}
@media(max-width:1050px){.setup-steps{grid-template-columns:1fr}.help-layout{grid-template-columns:250px 1fr}.mistake-board{grid-template-columns:repeat(2,1fr)}}
</style>
