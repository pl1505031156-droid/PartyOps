import { computed, ref } from "vue";

export type UploadQueueStatus = "queued" | "uploading" | "succeeded" | "failed" | "cancelled";

export interface UploadQueueItem {
  id: string;
  clientUploadId: string;
  file: File;
  status: UploadQueueStatus;
  progress: number;
  error: string;
}

export interface UploadQueueContext {
  signal: AbortSignal;
  onProgress: (percent: number) => void;
}

function makeId(prefix: string): string {
  const random = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`;
}

export function useUploadQueue(
  upload: (item: UploadQueueItem, context: UploadQueueContext) => Promise<void>,
  concurrency = 2,
) {
  const items = ref<UploadQueueItem[]>([]);
  const active = ref(0);
  const controllers = new Map<string, AbortController>();
  const idleWaiters: Array<() => void> = [];

  const pending = computed(() => items.value.filter((item) => item.status === "queued" || item.status === "uploading").length);
  const succeeded = computed(() => items.value.filter((item) => item.status === "succeeded").length);
  const failed = computed(() => items.value.filter((item) => item.status === "failed").length);
  const settled = computed(() => items.value.length > 0 && pending.value === 0);

  function addFiles(files: File[] | FileList): UploadQueueItem[] {
    const added = Array.from(files).map((file) => ({
      id: makeId("queue"),
      clientUploadId: makeId("business-upload"),
      file,
      status: "queued" as const,
      progress: 0,
      error: "",
    }));
    items.value.push(...added);
    void drain();
    return added;
  }

  async function run(item: UploadQueueItem): Promise<void> {
    const controller = new AbortController();
    controllers.set(item.id, controller);
    item.status = "uploading";
    item.progress = 0;
    item.error = "";
    active.value += 1;
    try {
      await upload(item, {
        signal: controller.signal,
        onProgress: (percent) => {
          item.progress = Math.max(item.progress, Math.min(100, percent));
        },
      });
      item.status = "succeeded";
      item.progress = 100;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        item.status = "cancelled";
        item.error = "已取消";
      } else {
        item.status = "failed";
        item.error = error instanceof Error ? error.message : "上传失败，请重试。";
      }
    } finally {
      controllers.delete(item.id);
      active.value -= 1;
      void drain();
    }
  }

  async function drain(): Promise<void> {
    while (active.value < Math.max(1, concurrency)) {
      const next = items.value.find((item) => item.status === "queued");
      if (!next) {
        if (active.value === 0) idleWaiters.splice(0).forEach((resolve) => resolve());
        return;
      }
      void run(next);
    }
  }

  function retry(id: string): void {
    const item = items.value.find((candidate) => candidate.id === id);
    if (!item || !["failed", "cancelled"].includes(item.status)) return;
    item.status = "queued";
    item.progress = 0;
    item.error = "";
    void drain();
  }

  function cancel(id: string): void {
    const item = items.value.find((candidate) => candidate.id === id);
    if (!item) return;
    if (item.status === "queued") {
      item.status = "cancelled";
      item.error = "已取消";
      return;
    }
    controllers.get(id)?.abort();
  }

  function clearSettled(): void {
    items.value = items.value.filter((item) => item.status === "queued" || item.status === "uploading");
  }

  function waitForIdle(): Promise<void> {
    if (active.value === 0 && !items.value.some((item) => item.status === "queued")) {
      return Promise.resolve();
    }
    return new Promise((resolve) => idleWaiters.push(resolve));
  }

  return { items, pending, succeeded, failed, settled, addFiles, retry, cancel, clearSettled, waitForIdle };
}
