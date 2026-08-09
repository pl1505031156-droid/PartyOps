import { describe, expect, it } from "vitest";
import {
  domainForPath,
  expandedDomainsForPath,
  navigationDomains,
} from "./navigation";

describe("五域导航", () => {
  it("只暴露五个稳定工作域且不再出现效率工具中心", () => {
    expect(navigationDomains.map((item) => item.key)).toEqual([
      "today",
      "work",
      "materials",
      "collaboration",
      "management",
    ]);
    expect(
      navigationDomains.flatMap((item) => item.items).map((item) => item.label),
    ).not.toContain("效率工具中心");
  });

  it("详情页和管理工具能归入正确工作域", () => {
    expect(domainForPath("/tasks/task-1").key).toBe("work");
    expect(domainForPath("/calendar").key).toBe("work");
    expect(domainForPath("/archives").key).toBe("materials");
    expect(domainForPath("/fleet").key).toBe("collaboration");
    expect(domainForPath("/ai-approvals").key).toBe("management");
  });

  it("纵向目录完整暴露 1.3.3 协同和运维入口", () => {
    const labels = navigationDomains.flatMap((item) => item.items).map((item) => item.label);
    expect(labels).toEqual(expect.arrayContaining([
      "文件接收箱",
      "传输任务",
      "设备授权与状态",
      "系统更新",
      "备份恢复",
      "运行诊断",
    ]));
    expect(labels).not.toContain("工作首页");
  });

  it("首次只展开当前工作域，并能恢复合法的收纳状态", () => {
    expect(expandedDomainsForPath("/calendar", null)).toEqual(["work"]);
    expect(
      expandedDomainsForPath(
        "/fleet/inbox",
        JSON.stringify(["today", "materials", "unknown"]),
      ),
    ).toEqual(["today", "materials", "collaboration"]);
    expect(expandedDomainsForPath("/settings/updates", "not-json")).toEqual([
      "management",
    ]);
  });
});
