<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Message } from "@arco-design/web-vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import { useSessionStore } from "../stores/session";
import type { OnboardingProgress } from "../types";

const session = useSessionStore();
const route = useRoute();
const keyword = ref(String(route.query.q || ""));
const selectedId = ref("start");
const completed = ref<string[]>([]);
const onboarding = ref<OnboardingProgress | null>(null);

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
    summary: "原始目录只读纳管，最终材料固化保存",
    steps: [
      "管理员只选择允许纳管的主机或协同设备文件夹。",
      "原始文件中心只读取目录、文件名、类型、大小和修改时间；点击文件仍由 WPS 等系统默认程序打开。",
      "把文件关联到任务、专题、日志或报告；关联不复制原件。",
      "领导修改前后两个版本可在“资料 → 文档比较与查重”核对文字差异。",
      "最终材料执行固化归档并校验 SHA-256，原始目录变化不会丢失终稿。",
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
    summary: "设备和用户双重授权，不使用微信或 U 盘中转",
    steps: [
      "主机管理员生成十分钟有效的一次性入网码。",
      "终端完成入网后，由管理员批准共享根和浏览、预览、下载、发送权限。",
      "跨设备文件统一经过主机中转并分块校验，不建立终端直连。",
      "危险文件和敏感目录进入审批；撤销授权后下一分块停止。",
      "设备离线时任务排队，上线后继续；长期离线设备应撤销证书。",
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
      "更新前导入签名更新包，确认备份与磁盘空间，再分批升级终端。",
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
const progress = computed(() => Math.round(completed.value.length / visibleGuides.value.length * 100));

async function loadProgress() {
  try {
    onboarding.value = await api.get<OnboardingProgress>("/me/onboarding");
    completed.value = [...onboarding.value.completed_steps];
  } catch {
    Message.warning("学习进度暂时无法读取，教程内容仍可正常查看");
  }
}

async function toggleComplete(id: string) {
  if (!onboarding.value) return;
  const next = completed.value.includes(id)
    ? completed.value.filter((item) => item !== id)
    : [...completed.value, id];
  try {
    onboarding.value = await api.patch<OnboardingProgress>(
      "/me/onboarding",
      { completed_steps: next },
      { "If-Match": String(onboarding.value.version) },
    );
    completed.value = [...onboarding.value.completed_steps];
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "学习进度保存失败");
    await loadProgress();
  }
}

onMounted(loadProgress);
</script>

<template>
  <div class="page help-page">
    <header class="help-hero">
      <div>
        <p>党建智办使用指南</p>
        <h1>使用帮助与防错指南</h1>
        <span>从第一次建档到年度归档，每一步都可以在这里查到。</span>
      </div>
      <div class="progress-seal"><strong>{{ progress }}%</strong><small>上手清单</small></div>
    </header>

    <div class="help-search"><a-input-search v-model="keyword" size="large" allow-clear placeholder="搜索“周期任务”“最终稿”“设备入网”“备份恢复”……" /></div>

    <div class="help-layout">
      <aside>
        <button
          v-for="guide in filteredGuides"
          :key="guide.id"
          type="button"
          :class="{ active: selected?.id === guide.id, done: completed.includes(guide.id) }"
          @click="selectedId = guide.id"
        >
          <i>{{ completed.includes(guide.id) ? "✓" : String(visibleGuides.indexOf(guide) + 1).padStart(2, "0") }}</i>
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
          <a-button :type="completed.includes(selected.id) ? 'outline' : 'primary'" @click="toggleComplete(selected.id)">
            {{ completed.includes(selected.id) ? "标记为需要重看" : "我已掌握这一部分" }}
          </a-button>
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
  </div>
</template>

<style scoped>
.help-page{max-width:1380px}.help-hero{display:flex;align-items:flex-end;justify-content:space-between;min-height:190px;padding:34px 42px;color:#f8efe4;background:var(--charcoal);border-bottom:5px solid var(--cinnabar)}.help-hero p{margin:0 0 12px;color:#d38a7e;font:11px Georgia,serif;letter-spacing:.2em}.help-hero h1{margin:0;font-family:var(--serif);font-size:34px;letter-spacing:.06em}.help-hero span{display:block;margin-top:12px;color:#c8beb2;font-size:12px}.progress-seal{display:grid;width:92px;height:92px;place-content:center;text-align:center;border:1px solid #d38a7e;border-radius:50%}.progress-seal strong{font:26px Georgia,serif}.progress-seal small{margin-top:4px;color:#d38a7e;font-size:9px;letter-spacing:.12em}.help-search{padding:22px 0 16px}.help-layout{display:grid;grid-template-columns:310px minmax(0,1fr);min-height:560px;border:1px solid var(--line);background:rgba(251,248,241,.72)}.help-layout aside{padding:14px;border-right:1px solid var(--line)}.help-layout aside button{display:grid;width:100%;grid-template-columns:34px minmax(0,1fr);gap:10px;padding:13px 10px;text-align:left;background:transparent;border:0;border-bottom:1px solid var(--line-light);cursor:pointer}.help-layout aside button:hover,.help-layout aside button.active{background:rgba(180,35,24,.05)}.help-layout aside button.active{color:var(--cinnabar);border-left:3px solid var(--cinnabar)}.help-layout aside i{display:grid;width:28px;height:28px;place-items:center;color:var(--muted);font:10px Georgia,serif;border:1px solid var(--line);border-radius:50%}.help-layout aside button.done i{color:#fff;background:#3d7653;border-color:#3d7653}.help-layout aside strong,.help-layout aside small{display:block}.help-layout aside small{margin-top:5px;color:var(--muted);font-size:9px;line-height:1.5}.guide-paper{padding:38px 48px}.guide-paper header{padding-bottom:22px;border-bottom:2px solid var(--charcoal)}.guide-paper header span{color:var(--cinnabar);font:10px Georgia,serif;letter-spacing:.2em}.guide-paper h2{margin:9px 0 6px;font-family:var(--serif);font-size:28px}.guide-paper header p{margin:0;color:var(--muted);font-size:11px}.guide-paper ol{margin:24px 0;padding:0;list-style:none}.guide-paper li{display:grid;grid-template-columns:38px minmax(0,1fr);align-items:start;padding:17px 0;border-bottom:1px solid var(--line-light)}.guide-paper li b{display:grid;width:28px;height:28px;place-items:center;color:var(--cinnabar);font:13px Georgia,serif;border:1px solid rgba(180,35,24,.45);border-radius:50%}.guide-paper li p{margin:3px 0 0;line-height:1.8}.guide-actions{display:flex;align-items:center;justify-content:space-between;margin-top:26px}.guide-actions a{color:var(--cinnabar)}.mistake-board{display:grid;grid-template-columns:repeat(4,1fr);margin-top:22px;border:1px solid var(--line)}.mistake-board>div{min-height:150px;padding:20px;border-right:1px solid var(--line)}.mistake-board>div:last-child{border-right:0}.mistake-board span{color:var(--cinnabar);font:20px Georgia,serif}.mistake-board strong{display:block;margin:12px 0 7px}.mistake-board p{margin:0;color:var(--muted);font-size:10px;line-height:1.7}
.help-page{width:100%;max-width:none}
@media(max-width:1050px){.help-layout{grid-template-columns:250px 1fr}.mistake-board{grid-template-columns:repeat(2,1fr)}}
</style>
