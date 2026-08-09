const LABELS: Record<string, string> = {
  admin: "管理员",
  staff: "协同人员",
  quick: "快速事项",
  standard: "标准事项",
  project: "项目事项",
  pending_receipt: "待接收",
  pending_breakdown: "待拆解",
  in_progress: "办理中",
  waiting_feedback: "等待反馈",
  pending_review: "待审核",
  returned: "退回修改",
  completed: "已完成",
  archived: "已归档",
  owner: "主办",
  collaborator: "协办",
  reviewer: "审核",
  normal: "普通",
  restricted: "敏感",
  low: "低",
  high: "高",
  urgent: "紧急",
  draft: "草稿",
  revision: "修改稿",
  leader_approved: "领导审定稿",
  submitted: "实际报送稿",
  monthly: "每月",
  quarterly: "每季度",
  half_yearly: "每半年",
  yearly: "每年",
  custom_days: "自定义天数",
  year: "年度",
  quarter: "季度",
  month: "月份",
  week: "周",
  published: "已发布",
  locked: "已锁定",
  carry_over: "延续事项",
  risk: "重点问题与风险",
  coordination: "需要协调",
  indexed: "已索引",
  changed: "文件已变化",
  missing: "文件已缺失",
  error: "识别失败",
  pending: "等待处理",
  metadata_only: "仅索引文件属性",
  unsupported: "格式暂不提取正文",
  pending_ocr: "等待 OCR",
  search: "检索",
  summarize: "摘要",
  classify: "分类",
  draft_report: "报告草拟",
  suggest_breakdown: "任务拆解建议",
  check_materials: "材料缺项检查",
  online: "在线",
  offline: "离线",
  stale: "连接过期",
  revoked: "已撤销",
  quarantined: "已隔离",
  updating: "升级中",
  host: "主机",
  device: "协同电脑",
  approved: "已批准",
  queued: "等待处理",
  awaiting_approval: "等待审批",
  transferring: "传输中",
  paused: "已暂停",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
  device_to_host: "协同电脑复制到主机",
  host_to_device: "主机发送到协同电脑",
  device_to_device: "协同电脑之间传输",
  uploaded: "已导入",
  validated: "校验通过",
  applying: "正在升级",
  rolled_back: "已回滚",
  active: "有效",
  voided: "已作废",
  ocr_error: "OCR 失败",
  document: "一份文件一档",
  person_year: "一人一档",
  all_users: "所有用户",
  admins_only: "仅管理员",
  selected: "指定人员或设备",
  manual: "人工日志",
  system: "系统事件",
  task: "事项",
  file: "文件",
  archive: "重要档案",
  contact: "联系人",
  journal: "工作日志",
  report: "周期报告",
  knowledge: "知识条目",
  command: "快捷指令",
  workspace_file: "原始文件",
  archive_record: "重要档案",
  period_report: "周期报告",
  topic: "专题空间",
  relates_to: "相关内容",
  supports: "支撑材料",
  produced_by: "产出来源",
  belongs_to: "归属专题",
  mentions: "提及",
  supersedes: "替代版本",
  task_due: "正式截止",
  task_plan: "内部计划",
  recurrence: "周期事项",
  report_boundary: "汇总节点",
  reminder: "提醒",
  holiday: "节假日",
  adjusted_workday: "调休工作日",
  spring: "春季",
  summer: "夏季",
  autumn: "秋季",
  winter: "冬季",
  reduced: "减少装饰",
  verifying: "正在校验",
  installed: "已安装",
  corrupt: "已损坏",
  accepted: "已接受",
  dismissed: "已忽略",
  rules: "规则推荐",
  embedding: "语义推荐",
  local_llm: "本地语言模型",
  external_llm: "外部语言模型",
  skip: "跳过本次",
  reschedule: "临时改期",
  text: "文本",
  textarea: "多行文本",
  date: "日期",
  number: "数字",
  select: "下拉选项",
};

export function zhLabel(value: unknown, fallback = "未知状态"): string {
  if (value === null || value === undefined || value === "") return fallback;
  const key = String(value);
  return LABELS[key] || fallback;
}

export const technicalLabel = (value: unknown): string =>
  value === null || value === undefined || value === "" ? "—" : String(value);

const AUDIT_PREFIX: Record<string, string> = {
  auth: "登录认证",
  task: "事项",
  attachment: "材料",
  workspace: "原始文件",
  backup: "备份",
  restore: "恢复",
  update: "系统更新",
  device: "设备",
  transfer: "文件传输",
  ai: "AI 工作助手",
  work_journal: "工作日志",
  archive: "重要档案",
  bootstrap: "首次配置",
};

const AUDIT_ACTION: Record<string, string> = {
  login: "登录",
  logout: "退出",
  create: "新建",
  update: "修改",
  delete: "删除",
  download: "下载",
  upload: "上传",
  submit_review: "提交审核",
  approve: "审核通过",
  return: "退回修改",
  complete: "完成",
  archive: "归档",
  reopen: "重新打开",
  freeze: "固化归档",
  restore: "恢复",
  apply: "执行升级",
  host: "配置主机",
  root_create: "纳管目录",
  root_update: "修改纳管目录",
  scan: "扫描目录",
  link_create: "建立文件关联",
  link_delete: "移除文件关联",
};

export function auditActionLabel(value: unknown): string {
  const raw = String(value || "");
  const [domain, ...rest] = raw.split(".");
  const action = rest.join(".");
  return [AUDIT_PREFIX[domain] || "系统操作", AUDIT_ACTION[action] || zhLabel(action, "操作")]
    .filter(Boolean)
    .join(" · ");
}

export function auditEntityLabel(value: unknown): string {
  const raw = String(value || "");
  return AUDIT_PREFIX[raw] || AUDIT_PREFIX[raw.split("_")[0]] || "业务记录";
}

export function localizeEmbeddedCodes(value: unknown): string {
  let text = String(value || "");
  for (const [code, label] of Object.entries(LABELS).sort(
    (left, right) => right[0].length - left[0].length,
  )) {
    text = text.replace(new RegExp(`(^|[^A-Za-z0-9_])${code}(?=$|[^A-Za-z0-9_])`, "g"), `$1${label}`);
  }
  return text;
}

const FIELD_LABELS: Record<string, string> = {
  title: "事项名称",
  description: "事项正文",
  status: "办理状态",
  owner_id: "主办人",
  reviewer_id: "审核人",
  formal_due_at: "正式截止时间",
  internal_due_at: "内部截止时间",
  planned_start_at: "计划开始时间",
  planned_end_at: "计划完成时间",
  category: "工作领域",
  tags: "标签",
  work_area: "工作领域",
  annual_focus: "年度重点",
  reporting_scope: "汇报口径",
  priority: "优先级",
  sensitivity: "敏感级别",
};

export function fieldLabel(value: unknown): string {
  return FIELD_LABELS[String(value || "")] || "业务字段";
}
