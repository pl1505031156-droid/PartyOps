import { Solar } from "lunar-typescript";

const chinaDate = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  weekday: "long",
});

export interface OrientalDateLabel {
  gregorian: string;
  weekday: string;
  lunar: string;
  /** 只在交节当天显示给用户。 */
  solarTerm: string;
  /** 供东方主题从本节气持续到下一节气，不直接作为普通日期文字展示。 */
  activeSolarTerm: string;
  full: string;
  compact: string;
}

export function orientalDateLabel(value: Date = new Date()): OrientalDateLabel {
  const parts = chinaDate.formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value || "";
  const year = Number(part("year"));
  const month = Number(part("month"));
  const day = Number(part("day"));
  const weekday = part("weekday");
  const lunar = Solar.fromYmd(year, month, day).getLunar();
  const lunarText = `农历${lunar.getMonthInChinese()}月${lunar.getDayInChinese()}`;
  const solarTerm = lunar.getJieQi();
  // 艺术主题按约十五天的完整节气周期持续生效；普通日期回溯到最近节气。
  const activeSolarTerm = solarTerm
    || lunar.getPrevJieQi(true)?.getName()
    || "";
  const gregorian = `${year}年${String(month).padStart(2, "0")}月${String(day).padStart(2, "0")}日`;
  return {
    gregorian,
    weekday,
    lunar: lunarText,
    solarTerm,
    activeSolarTerm,
    full: [gregorian, weekday, lunarText, solarTerm].filter(Boolean).join("　"),
    compact: [lunarText, solarTerm].filter(Boolean).join(" · "),
  };
}
