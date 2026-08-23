export type ArtProfile = "rich" | "standard" | "dense";

export type ArtComposition = "balanced" | "calendar" | "open" | "dense";

export type ArtMotif =
  | "four-seasons"
  | "mountain-path"
  | "time-water"
  | "messenger"
  | "cycle"
  | "flow"
  | "scholar"
  | "open-space"
  | "archive"
  | "inspection"
  | "knowledge"
  | "documents"
  | "network"
  | "transfer"
  | "management"
  | "help"
  | "gateway";

export type OrientalScene =
  | "dashboard"
  | "tasks"
  | "calendar"
  | "inbox"
  | "reports"
  | "journal"
  | "topic"
  | "workspace"
  | "archives"
  | "inspection"
  | "knowledge"
  | "comparison"
  | "collaboration"
  | "transfer"
  | "management"
  | "help"
  | "login";

export type ArtSlot =
  | "header"
  | "lower_scroll"
  | "empty"
  | "solar_term";

export interface OrientalSceneConfig {
  scene: OrientalScene;
  profile: ArtProfile;
  composition: ArtComposition;
  motif: ArtMotif;
  slots: readonly ArtSlot[];
}

export const SCENE_ART_MOTIFS: Readonly<Record<OrientalScene, ArtMotif>> = {
  dashboard: "four-seasons",
  tasks: "mountain-path",
  calendar: "time-water",
  inbox: "messenger",
  reports: "cycle",
  journal: "flow",
  topic: "scholar",
  workspace: "open-space",
  archives: "archive",
  inspection: "inspection",
  knowledge: "knowledge",
  comparison: "documents",
  collaboration: "network",
  transfer: "transfer",
  management: "management",
  help: "help",
  login: "gateway",
};

export const SOLAR_TERM_NAMES = [
  "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
  "立夏", "小满", "芒种", "夏至", "小暑", "大暑",
  "立秋", "处暑", "白露", "秋分", "寒露", "霜降",
  "立冬", "小雪", "大雪", "冬至", "小寒", "大寒",
] as const;

const RICH_SLOTS = [
  "header",
  "lower_scroll",
  "empty",
  "solar_term",
] as const satisfies readonly ArtSlot[];

const STANDARD_SLOTS = [
  "header",
  "lower_scroll",
  "empty",
  "solar_term",
] as const satisfies readonly ArtSlot[];

const DENSE_SLOTS = [
  "header",
  "lower_scroll",
  "empty",
  "solar_term",
] as const satisfies readonly ArtSlot[];

export const SEASON_CAPTIONS = {
  spring: "春·花信",
  summer: "夏·荷风",
  autumn: "秋·桂月",
  winter: "冬·梅雪",
} as const;

function config(
  scene: OrientalScene,
  profile: ArtProfile,
  composition: ArtComposition,
  slots: readonly ArtSlot[],
): OrientalSceneConfig {
  return { scene, profile, composition, motif: SCENE_ART_MOTIFS[scene], slots };
}

/**
 * 全系统东方画卷场景表。
 *
 * 业务页面只提供路由；季节、素材、透明度和固定坐标均由主题层统一处理。
 * 高密度页面仅保留安全页头与空状态，避免画面进入表格、表单和敏感正文。
 */
export function sceneConfigForPath(path: string): OrientalSceneConfig {
  if (path === "/login") return config("login", "rich", "balanced", RICH_SLOTS);
  if (path === "/") return config("dashboard", "rich", "balanced", RICH_SLOTS);
  if (path.startsWith("/calendar")) return config("calendar", "rich", "calendar", RICH_SLOTS);
  if (path.startsWith("/tasks/")) return config("tasks", "dense", "dense", DENSE_SLOTS);
  if (path.startsWith("/tasks")) return config("tasks", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/inbox")) return config("inbox", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/reports")) return config("reports", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/journal")) return config("journal", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/memos")) return config("journal", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/party-life")) return config("journal", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/study-center")) return config("calendar", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/party-development")) return config("calendar", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/topics")) return config("topic", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/workspace")) return config("workspace", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/archives")) return config("archives", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/inspection")) return config("inspection", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/knowledge")) return config("knowledge", "standard", "open", STANDARD_SLOTS);
  if (path.startsWith("/document-comparisons")) {
    return config("comparison", "dense", "dense", DENSE_SLOTS);
  }
  if (path.startsWith("/fleet/inbox") || path.startsWith("/fleet/transfers")) {
    return config("transfer", "standard", "open", STANDARD_SLOTS);
  }
  if (path.startsWith("/fleet")) {
    return config("collaboration", "standard", "open", STANDARD_SLOTS);
  }
  if (path.startsWith("/help")) return config("help", "standard", "open", STANDARD_SLOTS);
  return config("management", "dense", "dense", DENSE_SLOTS);
}

export function artProfileForPath(path: string): ArtProfile {
  return sceneConfigForPath(path).profile;
}

/** 所有业务路由都有明确场景；减少装饰由用户外观偏好单独控制。 */
export function shouldShowOrientalArt(path: string): boolean {
  return path !== "/required-update";
}

/** 将中文节气转换为稳定的数据属性，未知日期使用 none。 */
export function solarTermToken(value: string): string {
  return value.trim() || "none";
}
