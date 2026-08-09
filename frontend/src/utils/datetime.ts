import dayjs, { type ConfigType, type Dayjs } from "dayjs";
import utc from "dayjs/plugin/utc";
import timezone from "dayjs/plugin/timezone";
import "dayjs/locale/zh-cn";

dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.locale("zh-cn");

export const DISPLAY_TIMEZONE = "Asia/Shanghai";
const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/i;

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
  return dayjs().tz(DISPLAY_TIMEZONE).format(pattern);
}

/** 日期控件返回的是北京时间墙上时间，提交前转换为 UTC。 */
export function localInputToUtc(value: ConfigType): string {
  return dayjs.tz(value, DISPLAY_TIMEZONE).utc().toISOString();
}

export { dayjs };
