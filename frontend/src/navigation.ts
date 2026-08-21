export type NavigationDomainKey =
  | "today"
  | "work"
  | "materials"
  | "collaboration"
  | "management";

export interface NavigationItem {
  path: string;
  label: string;
  icon: "home" | "memo" | "task" | "calendar" | "inbox" | "report" | "journal" | "topic"
    | "folder" | "archive" | "book" | "compare" | "device" | "transfer"
    | "template" | "automation" | "ai" | "settings" | "help";
  capability?: string;
}

export interface NavigationDomain {
  key: NavigationDomainKey;
  label: string;
  shortLabel: string;
  defaultPath: string;
  items: NavigationItem[];
}

/** 五个稳定工作域。新增业务页面只需登记在所属域，不修改壳层组件。 */
export const navigationDomains: NavigationDomain[] = [
  {
    key: "today",
    label: "今日",
    shortLabel: "今日",
    defaultPath: "/",
    items: [
      { path: "/", label: "今日工作台", icon: "home" },
      { path: "/memos", label: "备忘录", icon: "memo" },
    ],
  },
  {
    key: "work",
    label: "工作",
    shortLabel: "工作",
    defaultPath: "/tasks",
    items: [
      { path: "/tasks", label: "事项与清单", icon: "task" },
      { path: "/my-work", label: "我的工作", icon: "task" },
      { path: "/notifications", label: "通知中心", icon: "inbox" },
      { path: "/calendar", label: "工作日历", icon: "calendar" },
      { path: "/party-development", label: "党员发展计算", icon: "calendar" },
      { path: "/party-development-cases", label: "党员发展档案", icon: "calendar" },
      { path: "/business-meetings", label: "党建会议与筹备", icon: "task" },
      { path: "/inbox", label: "快速收件箱", icon: "inbox" },
      { path: "/reports", label: "周期汇总", icon: "report" },
      { path: "/journal", label: "工作日志", icon: "journal" },
      { path: "/topics", label: "专题工作空间", icon: "topic" },
    ],
  },
  {
    key: "materials",
    label: "资料",
    shortLabel: "资料",
    defaultPath: "/workspace",
    items: [
      { path: "/workspace", label: "原始文件中心", icon: "folder" },
      { path: "/archives", label: "重要档案", icon: "archive" },
      { path: "/inspection", label: "迎检与归档", icon: "archive" },
      { path: "/knowledge", label: "知识与联系人", icon: "book" },
      { path: "/document-comparisons", label: "文档比较与查重", icon: "compare" },
      { path: "/business-documents", label: "在线业务文档", icon: "book" },
    ],
  },
  {
    key: "collaboration",
    label: "协同",
    shortLabel: "协同",
    defaultPath: "/fleet/devices",
    items: [
      { path: "/fleet/devices", label: "设备协同", icon: "device" },
      { path: "/fleet/inbox", label: "文件接收箱", icon: "inbox" },
      { path: "/fleet/transfers", label: "传输任务", icon: "transfer" },
      { path: "/fleet/grants", label: "设备授权与状态", icon: "settings", capability: "fleet.manage" },
    ],
  },
  {
    key: "management",
    label: "管理",
    shortLabel: "管理",
    defaultPath: "/templates",
    items: [
      { path: "/templates", label: "周期与模板", icon: "template", capability: "admin.access" },
      { path: "/party-development-settings", label: "党员发展补充材料", icon: "template", capability: "admin.access" },
      { path: "/automation", label: "自动归档规则", icon: "automation", capability: "admin.access" },
      { path: "/report-designer", label: "报告模板", icon: "template", capability: "admin.access" },
      { path: "/ai-approvals", label: "AI 草稿审批", icon: "ai", capability: "ai.manage" },
      { path: "/settings/updates", label: "系统更新", icon: "settings", capability: "updates.manage" },
      { path: "/settings/backups", label: "备份恢复", icon: "archive", capability: "backups.manage" },
      { path: "/settings/diagnostics", label: "运行诊断", icon: "settings", capability: "admin.access" },
      { path: "/help", label: "帮助与上手", icon: "help" },
    ],
  },
];

export function domainForPath(path: string): NavigationDomain {
  return (
    navigationDomains.find((domain) =>
      domain.items.some((item) =>
        item.path === "/" ? path === "/" : path.startsWith(item.path),
      ) || (
        domain.defaultPath !== "/"
        && path.startsWith(`/${domain.defaultPath.split("/").filter(Boolean)[0]}`)
      ),
    ) || navigationDomains[0]
  );
}

/**
 * 恢复侧栏展开状态。首次使用只展开当前工作域；历史值损坏时安全回退，
 * 并始终保证当前页面所在工作域可见。
 */
export function expandedDomainsForPath(
  path: string,
  storedValue: string | null,
): NavigationDomainKey[] {
  const knownKeys = new Set(navigationDomains.map((domain) => domain.key));
  let restored: NavigationDomainKey[] = [];
  if (storedValue) {
    try {
      const parsed: unknown = JSON.parse(storedValue);
      if (Array.isArray(parsed)) {
        restored = parsed.filter(
          (item): item is NavigationDomainKey =>
            typeof item === "string" && knownKeys.has(item as NavigationDomainKey),
        );
      }
    } catch {
      restored = [];
    }
  }
  const activeKey = domainForPath(path).key;
  return [...new Set([...restored, activeKey])];
}
