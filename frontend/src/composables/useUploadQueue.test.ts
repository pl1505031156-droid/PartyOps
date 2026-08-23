import { describe, expect, it, vi } from "vitest";
import { useUploadQueue } from "./useUploadQueue";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("useUploadQueue", () => {
  it("最多并行两个文件且单个失败不影响其他文件", async () => {
    const gates = [deferred(), deferred(), deferred()];
    let active = 0;
    let peak = 0;
    const upload = vi.fn(async (item: { file: File }, context: { onProgress: (value: number) => void }) => {
      active += 1;
      peak = Math.max(peak, active);
      context.onProgress(45);
      const index = Number(item.file.name[0]);
      await gates[index].promise;
      active -= 1;
      if (index === 1) throw new Error("文件格式损坏");
    });
    const queue = useUploadQueue(upload, 2);
    queue.addFiles([
      new File(["a"], "0-a.txt"),
      new File(["b"], "1-b.txt"),
      new File(["c"], "2-c.txt"),
    ]);
    await Promise.resolve();
    expect(peak).toBe(2);
    gates[0].resolve();
    await Promise.resolve();
    await Promise.resolve();
    gates[1].resolve();
    gates[2].resolve();
    await queue.waitForIdle();
    expect(queue.succeeded.value).toBe(2);
    expect(queue.failed.value).toBe(1);
    expect(queue.items.value[1].error).toContain("文件格式损坏");
  });

  it("允许取消等待项并重试", async () => {
    let fail = true;
    const queue = useUploadQueue(async () => {
      if (fail) throw new Error("临时网络错误");
    }, 1);
    const [item] = queue.addFiles([new File(["a"], "材料.txt")]);
    await queue.waitForIdle();
    expect(item.status).toBe("failed");
    fail = false;
    queue.retry(item.id);
    await queue.waitForIdle();
    expect(item.status).toBe("succeeded");

    const gate = deferred();
    const waitingQueue = useUploadQueue(async () => gate.promise, 1);
    const rows = waitingQueue.addFiles([new File(["a"], "一.txt"), new File(["b"], "二.txt")]);
    waitingQueue.cancel(rows[1].id);
    expect(rows[1].status).toBe("cancelled");
    gate.resolve();
    await waitingQueue.waitForIdle();
    waitingQueue.clearSettled();
    expect(waitingQueue.items.value).toHaveLength(0);
  });

  it("覆盖活动取消、无效操作、降级标识和非标准异常", async () => {
    const originalCrypto = globalThis.crypto;
    vi.stubGlobal("crypto", undefined);
    const fallbackQueue = useUploadQueue(async () => {
      throw "非标准异常";
    }, 0);
    const [failed] = fallbackQueue.addFiles([new File(["a"], "降级.txt")]);
    await fallbackQueue.waitForIdle();
    expect(failed.clientUploadId).toMatch(/^business-upload-/);
    expect(failed.error).toBe("上传失败，请重试。");
    fallbackQueue.retry("missing");
    fallbackQueue.cancel("missing");
    fallbackQueue.retry(failed.id);
    await fallbackQueue.waitForIdle();
    expect(failed.status).toBe("failed");
    vi.stubGlobal("crypto", originalCrypto);

    const activeQueue = useUploadQueue(
      async (_item, context) => new Promise<void>((_resolve, reject) => {
        context.signal.addEventListener("abort", () => {
          reject(new DOMException("cancelled", "AbortError"));
        }, { once: true });
      }),
      1,
    );
    const [active] = activeQueue.addFiles([new File(["b"], "活动.txt")]);
    await Promise.resolve();
    activeQueue.cancel(active.id);
    await activeQueue.waitForIdle();
    expect(active.status).toBe("cancelled");
    expect(active.error).toBe("已取消");
  });

  it("上传进度只前进并限制在零到一百", async () => {
    const emptyQueue = useUploadQueue(async () => undefined);
    await expect(emptyQueue.waitForIdle()).resolves.toBeUndefined();

    const gate = deferred();
    const queue = useUploadQueue(async (_item, context) => {
      context.onProgress(-10);
      context.onProgress(120);
      context.onProgress(50);
      await gate.promise;
    });
    const [item] = queue.addFiles([new File(["x"], "进度.txt")]);
    await Promise.resolve();
    expect(item.progress).toBe(100);
    expect(queue.settled.value).toBe(false);
    gate.resolve();
    await queue.waitForIdle();
    expect(queue.settled.value).toBe(true);
    queue.retry(item.id);
    expect(item.status).toBe("succeeded");
  });
});
