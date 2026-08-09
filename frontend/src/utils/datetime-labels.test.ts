import { describe, expect, it } from "vitest";
import { formatServerTime, localInputToUtc } from "./datetime";
import { localizeEmbeddedCodes, zhLabel } from "./labels";

describe("北京时间与中文标签", () => {
  it("将历史无时区 UTC 时间转换为北京时间", () => {
    expect(formatServerTime("2026-07-29T10:03:00", "YYYY-MM-DD HH:mm")).toBe(
      "2026-07-29 18:03",
    );
    expect(formatServerTime("2026-07-29T10:03:00Z", "HH:mm")).toBe("18:03");
  });

  it("将北京时间输入转换为 UTC 后提交", () => {
    expect(localInputToUtc("2026-07-29 18:03")).toBe("2026-07-29T10:03:00.000Z");
  });

  it("普通界面不直接显示内部状态代码", () => {
    expect(zhLabel("in_progress")).toBe("办理中");
    expect(zhLabel("unknown_new_code")).toBe("未知状态");
    expect(localizeEmbeddedCodes("in_progress -> completed")).toBe("办理中 -> 已完成");
  });
});
