import { describe, expect, it } from "vitest";
import { formatServerTime, localInputToUtc, localNowInput, serverTime } from "./datetime";
import {
  auditActionLabel,
  auditEntityLabel,
  fieldLabel,
  localizeEmbeddedCodes,
  technicalLabel,
  zhLabel,
} from "./labels";

describe("北京时间与中文标签", () => {
  it("将历史无时区 UTC 时间转换为北京时间", () => {
    expect(formatServerTime("2026-07-29T10:03:00", "YYYY-MM-DD HH:mm")).toBe(
      "2026-07-29 18:03",
    );
    expect(formatServerTime("2026-07-29T10:03:00Z", "HH:mm")).toBe("18:03");
  });

  it("将北京时间输入转换为 UTC 后提交", () => {
    expect(localInputToUtc("2026-07-29 18:03")).toBe("2026-07-29T10:03:00.000Z");
    expect(serverTime("2026-07-29T10:03:00+08:00").format("HH:mm")).toBe("10:03");
    expect(formatServerTime("invalid", "HH:mm", "不可用")).toBe("不可用");
    expect(formatServerTime(null, "HH:mm", "未提供")).toBe("未提供");
    expect(localNowInput("YYYY")).toMatch(/^\d{4}$/);
  });

  it("普通界面不直接显示内部状态代码", () => {
    expect(zhLabel("in_progress")).toBe("办理中");
    expect(zhLabel("unknown_new_code")).toBe("未知状态");
    expect(localizeEmbeddedCodes("in_progress -> completed")).toBe("办理中 -> 已完成");
  });

  it("覆盖空值、审计动作、业务对象和字段的中文降级文案", () => {
    expect(zhLabel(null, "未设置")).toBe("未设置");
    expect(zhLabel(undefined)).toBe("未知状态");
    expect(zhLabel("")).toBe("未知状态");
    expect(technicalLabel(null)).toBe("—");
    expect(technicalLabel(undefined)).toBe("—");
    expect(technicalLabel("")).toBe("—");
    expect(technicalLabel(42)).toBe("42");

    expect(auditActionLabel("workspace.root_create")).toBe("原始文件 · 纳管目录");
    expect(auditActionLabel("task.completed")).toBe("事项 · 已完成");
    expect(auditActionLabel("unknown.custom_action")).toBe("系统操作 · 操作");
    expect(auditActionLabel(null)).toBe("系统操作 · 操作");
    expect(auditEntityLabel("work_journal")).toBe("工作日志");
    expect(auditEntityLabel("task_material")).toBe("事项");
    expect(auditEntityLabel("unknown_record")).toBe("业务记录");
    expect(auditEntityLabel(undefined)).toBe("业务记录");

    expect(fieldLabel("owner_id")).toBe("主办人");
    expect(fieldLabel("unknown_field")).toBe("业务字段");
    expect(fieldLabel(null)).toBe("业务字段");
    expect(localizeEmbeddedCodes(null)).toBe("");
    expect(localizeEmbeddedCodes("in_progress_code")).toBe("in_progress_code");
  });
});
