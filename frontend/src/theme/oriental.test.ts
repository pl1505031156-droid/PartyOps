import { describe, expect, it } from "vitest";
import artLayerSource from "../components/OrientalArtLayer.vue?raw";
import assetManifest from "../assets/oriental/asset-manifest.json";
import {
  artProfileForPath,
  SCENE_ART_MOTIFS,
  sceneConfigForPath,
  SEASON_CAPTIONS,
  shouldShowOrientalArt,
  SOLAR_TERM_NAMES,
  solarTermToken,
} from "./oriental";

describe("东方四时长卷路由配置", () => {
  it("今日、日历与登录使用完整长卷", () => {
    for (const path of ["/", "/calendar", "/login"]) {
      const current = sceneConfigForPath(path);
      expect(current.profile).toBe("rich");
      expect(current.slots).toEqual(expect.arrayContaining([
        "header",
        "lower_scroll",
        "empty",
      ]));
      expect(["balanced", "calendar"]).toContain(current.composition);
    }
  });

  it("全部主要业务路由都有明确场景", () => {
    const expected = new Map([
      ["/tasks", "tasks"],
      ["/inbox", "inbox"],
      ["/reports", "reports"],
      ["/journal", "journal"],
      ["/party-development", "calendar"],
      ["/topics", "topic"],
      ["/workspace", "workspace"],
      ["/archives", "archives"],
      ["/inspection", "inspection"],
      ["/knowledge", "knowledge"],
      ["/document-comparisons", "comparison"],
      ["/fleet/devices", "collaboration"],
      ["/fleet/transfers", "transfer"],
      ["/fleet/inbox", "transfer"],
      ["/settings/updates", "management"],
      ["/help", "help"],
    ]);

    for (const [path, scene] of expected) {
      expect(sceneConfigForPath(path).scene).toBe(scene);
      expect(shouldShowOrientalArt(path)).toBe(true);
    }
  });

  it("主要页面使用不同场景素材组合而不是重复同一底图", () => {
    const scenes = [
      "dashboard", "tasks", "calendar", "inbox", "reports", "journal",
      "topic", "workspace", "archives", "inspection", "knowledge",
      "comparison", "collaboration", "transfer", "management", "help", "login",
    ] as const;
    expect(Object.keys(SCENE_ART_MOTIFS).sort()).toEqual([...scenes].sort());
    expect(new Set(Object.values(SCENE_ART_MOTIFS)).size).toBe(scenes.length);
    for (const scene of scenes) {
      expect(SCENE_ART_MOTIFS[scene]).toBeTruthy();
    }
  });

  it("详情和管理表单使用同一长卷的克制构图", () => {
    expect(artProfileForPath("/tasks/task-1")).toBe("dense");
    expect(artProfileForPath("/settings/updates")).toBe("dense");
    expect(sceneConfigForPath("/settings/updates").composition).toBe("dense");
    expect(sceneConfigForPath("/settings/updates").slots).toContain("lower_scroll");
  });

  it("所有场景只使用同季页头和底部长卷，不再叠加角落贴片", () => {
    for (const path of ["/", "/tasks", "/calendar", "/reports", "/fleet/devices", "/settings/updates"]) {
      const slots = sceneConfigForPath(path).slots as readonly string[];
      expect(slots).toContain("header");
      expect(slots).toContain("lower_scroll");
      expect(slots).not.toContain("corner");
      expect(slots).not.toContain("divider");
    }
  });

  it("画卷按原始比例展示且不接收交互", () => {
    expect(artLayerSource).toContain("background-size: contain");
    expect(artLayerSource).toContain("100% auto");
    expect(artLayerSource).not.toContain("background-size: 100% 100%");
    expect(artLayerSource).not.toContain("var(--term-lower-x, 0),");
    expect(artLayerSource).toContain("pointer-events: none");
    expect(artLayerSource).toContain("--scene-header-fade-left");
    expect(artLayerSource).toContain("--scene-lower-fade-right");
    expect(artLayerSource).toContain("-webkit-mask-image: var(");
    expect(artLayerSource).toContain("--scene-header-mask");
    expect(artLayerSource).toContain("--scene-lower-mask");
  });

  it("迎检场景保留标准横向长卷插槽", () => {
    const current = sceneConfigForPath("/inspection");

    expect(current.scene).toBe("inspection");
    expect(current.profile).toBe("standard");
    expect(current.composition).toBe("open");
    expect(current.slots).toContain("lower_scroll");
  });

  it("八类页面专属画面均提供四季版本", () => {
    const scenes = [
      "tasks", "inbox", "reports", "journal", "topic", "knowledge",
      "collaboration", "transfer",
    ];
    const seasons = ["spring", "summer", "autumn", "winter"];
    for (const season of seasons) {
      const files = assetManifest.scene_variants[
        season as keyof typeof assetManifest.scene_variants
      ];
      for (const scene of scenes) {
        expect(files).toContain(`scene-${scene}-${season}.webp`);
      }
    }
  });

  it("四季题签不含印章或官方标识", () => {
    expect(SEASON_CAPTIONS).toEqual({
      spring: "春·花信",
      summer: "夏·荷风",
      autumn: "秋·桂月",
      winter: "冬·梅雪",
    });
  });

  it("节气数据属性稳定且可离线降级", () => {
    expect(solarTermToken("立秋")).toBe("立秋");
    expect(solarTermToken(" ")).toBe("none");
  });

  it("二十四节气都有持续到下一节气的画卷微变量", () => {
    expect(SOLAR_TERM_NAMES).toHaveLength(24);
    expect(new Set(SOLAR_TERM_NAMES).size).toBe(24);
    expect(SOLAR_TERM_NAMES).toContain("大暑");
  });

  it("升级等待页不加载画卷，避免干扰恢复诊断", () => {
    expect(shouldShowOrientalArt("/required-update")).toBe(false);
  });
});
