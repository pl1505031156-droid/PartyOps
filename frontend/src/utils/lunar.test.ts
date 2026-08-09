import { describe, expect, it } from "vitest";
import { orientalDateLabel } from "./lunar";

describe("东方日期题签", () => {
  it("按北京时间跨日并显示农历", () => {
    const label = orientalDateLabel(new Date("2026-02-16T16:30:00Z"));
    expect(label.gregorian).toBe("2026年02月17日");
    expect(label.weekday).toBe("星期二");
    expect(label.lunar).toBe("农历正月初一");
    expect(label.full).toContain("农历正月初一");
  });

  it("节气名称只在当天显示，艺术主题持续整个节气周期", () => {
    const spring = orientalDateLabel(new Date("2026-04-05T04:00:00+08:00"));
    expect(spring.solarTerm).toBe("清明");
    expect(spring.activeSolarTerm).toBe("清明");
    const ordinary = orientalDateLabel(new Date("2026-04-06T04:00:00+08:00"));
    expect(ordinary.solarTerm).toBe("");
    expect(ordinary.activeSolarTerm).toBe("清明");
    const summer = orientalDateLabel(new Date("2026-08-03T12:00:00+08:00"));
    expect(summer.solarTerm).toBe("");
    expect(summer.activeSolarTerm).toBe("大暑");
  });
});
