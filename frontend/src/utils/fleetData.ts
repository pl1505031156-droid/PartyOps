import type {
  Device,
  DeviceGrant,
  DeviceVersionStatus,
  Transfer,
} from "../types";

type FleetGetter = (path: string) => Promise<unknown>;

export interface FleetSnapshot {
  devices: Device[];
  config?: { max_devices: number };
  grants?: DeviceGrant[];
  transfers?: Transfer[];
  versionStatuses?: DeviceVersionStatus[];
  failedSections: string[];
}

const SECTION_LABELS = [
  "设备上限",
  "目录授权",
  "传输队列",
  "版本状态",
] as const;

/**
 * 设备列表是协同中心的核心数据，必须独立于授权、传输和版本等辅助面板加载。
 * 辅助接口短暂失败时保留页面已有数据，不能把已经成功返回的设备表清空。
 */
export async function fetchFleetSnapshot(get: FleetGetter): Promise<FleetSnapshot> {
  const results = await Promise.allSettled([
    get("/admin/devices"),
    get("/admin/devices/config"),
    get("/admin/device-grants"),
    get("/transfers"),
    get("/admin/devices/version-status"),
  ]);
  const deviceResult = results[0];
  if (deviceResult.status === "rejected") throw deviceResult.reason;

  const failedSections = results
    .slice(1)
    .flatMap((result, index) =>
      result.status === "rejected" ? [SECTION_LABELS[index]] : [],
    );

  return {
    devices: deviceResult.value as Device[],
    config:
      results[1].status === "fulfilled"
        ? (results[1].value as { max_devices: number })
        : undefined,
    grants:
      results[2].status === "fulfilled"
        ? (results[2].value as DeviceGrant[])
        : undefined,
    transfers:
      results[3].status === "fulfilled"
        ? (results[3].value as Transfer[])
        : undefined,
    versionStatuses:
      results[4].status === "fulfilled"
        ? (results[4].value as DeviceVersionStatus[])
        : undefined,
    failedSections,
  };
}
