import dayjs, { type ConfigType, type Dayjs } from "dayjs";
import utc from "dayjs/plugin/utc";
import timezone from "dayjs/plugin/timezone";
import "dayjs/locale/zh-cn";

dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.locale("zh-cn");

export const DISPLAY_TIMEZONE = "Asia/Shanghai";
const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

/** 当前北京时间。所有界面中的“今天 / 本月 / 当前时间”都从这里取得。 */
export function beijingNow(): Dayjs {
  return dayjs().tz(DISPLAY_TIMEZONE);
}

/** 当前时刻的 ISO 8601 表示，显式携带北京时间偏移。 */
export function beijingNowIso(): string {
  return beijingNow().format("YYYY-MM-DDTHH:mm:ss.SSSZ");
}

/** 服务端历史无时区字符串按 UTC 解释，再统一转换为北京时间。 */
export function serverTime(value: ConfigType): Dayjs {
  if (typeof value === "string" && value && !HAS_ZONE.test(value)) {
    return dayjs.utc(value).tz(DISPLAY_TIMEZONE);
  }
  return dayjs(value).tz(DISPLAY_TIMEZONE);
}

export function formatServerTime(
  value: ConfigType | null | undefined,
  pattern = "YYYY-MM-DD HH:mm",
  fallback = "—",
): string {
  if (value === null || value === undefined || value === "") return fallback;
  const parsed = serverTime(value);
  return parsed.isValid() ? parsed.format(pattern) : fallback;
}

export function localNowInput(pattern = "YYYY-MM-DD HH:mm:ss"): string {
  return beijingNow().format(pattern);
}

/** 日期控件返回的是北京时间墙上时间；接口提交值显式携带 +08:00。 */
export function localInputToUtc(value: ConfigType): string {
  return dayjs.tz(value, DISPLAY_TIMEZONE).format("YYYY-MM-DDTHH:mm:ssZ");
}

/** 把服务端 UTC 瞬时值填回北京时间控件，避免旧值被浏览器本地时区二次解释。 */
export function utcToLocalInput(value: ConfigType | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = serverTime(value);
  return parsed.isValid() ? parsed.format("YYYY-MM-DD HH:mm:ss") : null;
}

export { dayjs };
