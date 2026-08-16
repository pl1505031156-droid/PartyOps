import { describe, expect, it, vi } from "vitest";
import type { Device } from "../types";
import { fetchFleetSnapshot } from "./fleetData";

const device = {
  id: "device-1",
  name: "党建办协同机",
  status: "online",
} as Device;

describe("设备协同中心数据加载", () => {
  it("辅助面板失败时仍然展示已经成功登记的设备", async () => {
    const get = vi.fn((path: string) => {
      if (path === "/admin/devices") return Promise.resolve([device]);
      if (path === "/transfers") return Promise.reject(new Error("传输接口暂不可用"));
      if (path === "/admin/devices/config") {
        return Promise.resolve({ max_devices: 20 });
      }
      return Promise.resolve([]);
    });

    const snapshot = await fetchFleetSnapshot(get);

    expect(snapshot.devices).toEqual([device]);
    expect(snapshot.config?.max_devices).toBe(20);
    expect(snapshot.failedSections).toEqual(["传输队列"]);
  });

  it("只有核心设备列表失败时才阻止本次刷新", async () => {
    const get = vi.fn((path: string) =>
      path === "/admin/devices"
        ? Promise.reject(new Error("设备列表不可用"))
        : Promise.resolve([]),
    );

    await expect(fetchFleetSnapshot(get)).rejects.toThrow("设备列表不可用");
  });

  it("所有辅助接口失败时保留核心设备并逐项标注缺失面板", async () => {
    const get = vi.fn((path: string) => (
      path === "/admin/devices"
        ? Promise.resolve([device])
        : Promise.reject(new Error(`${path} 不可用`))
    ));

    const snapshot = await fetchFleetSnapshot(get);

    expect(snapshot).toEqual({
      devices: [device],
      config: undefined,
      grants: undefined,
      transfers: undefined,
      versionStatuses: undefined,
      failedSections: ["设备上限", "目录授权", "传输队列", "版本状态"],
    });
  });
});
