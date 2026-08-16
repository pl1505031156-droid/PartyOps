import { webcrypto } from "node:crypto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import {
  LocalMemoRepository,
  MemoStorageError,
  createMemo,
  decryptMemoBackup,
  encryptMemoBackup,
  filterMemos,
  memoDisplayTitle,
  memoScope,
  validateMemo,
  type LocalMemo,
} from "./localMemo";

beforeAll(() => {
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  }
});

const originalCrypto = globalThis.crypto;

afterEach(() => {
  Object.defineProperty(globalThis, "crypto", { value: originalCrypto, configurable: true });
});

function scope(userId = "user-1", deviceId = "device-a") {
  return memoScope(userId, {
    node_mode: "client",
    device_id: deviceId,
    device_name: "测试协同机",
    platform: "windows",
    user_role: "staff",
    capabilities: [],
  });
}

describe("本机私有备忘录", () => {
  it("按账号和设备环境隔离，且置顶优先、最近编辑排序", async () => {
    const repository = new LocalMemoRepository(new IDBFactory());
    const first = await repository.save(scope(), { ...createMemo(), title: "第一条", updatedAt: "2026-08-10T01:00:00Z" });
    await repository.save(scope(), { ...createMemo(), title: "置顶条目", pinned: true });
    await repository.save(scope("user-1", "device-b"), { ...createMemo(), title: "另一台电脑" });
    await repository.save(scope("user-2", "device-a"), { ...createMemo(), title: "另一个账号" });

    const own = await repository.list(scope());
    expect(own).toHaveLength(2);
    expect(filterMemos(own, "").map((memo) => memo.title)).toEqual(["置顶条目", "第一条"]);
    expect((await repository.list(scope("user-1", "device-b")))[0].title).toBe("另一台电脑");
    expect((await repository.list(scope("user-2", "device-a")))[0].title).toBe("另一个账号");
    expect(first.updatedAt).not.toBe("2026-08-10T01:00:00Z");
  });

  it("支持正文、标签和清单搜索，并校验边界", () => {
    const note = { ...createMemo(), title: "电话记录", body: "联系组织委员", tags: ["明天"] };
    const checklist = {
      ...createMemo("checklist"),
      checklist: [{ id: "item-1", text: "准备三考材料", done: false }],
    };
    expect(filterMemos([note, checklist], "组织委员")).toEqual([note]);
    expect(filterMemos([note, checklist], "三考")).toEqual([checklist]);
    expect(memoDisplayTitle(checklist)).toBe("准备三考材料");
    expect(validateMemo({ ...note, tags: ["党建", "党建", "  工作  "] }).tags).toEqual(["党建", "工作"]);
    expect(() => validateMemo({ ...note, title: "字".repeat(161) })).toThrow("标题不能超过");
    expect(() => validateMemo({ ...note, checklist: Array.from({ length: 201 }, (_, index) => ({ id: String(index), text: "x", done: false })) })).toThrow("清单不能超过");
  });

  it("对空标题、未知运行环境和损坏字段采用可解释的边界行为", () => {
    const note = createMemo();
    const checklist = createMemo("checklist");
    expect(note.checklist).toEqual([]);
    expect(checklist.checklist).toHaveLength(1);
    expect(memoScope("user-1", null)).toEqual({
      ownerId: "user-1",
      key: "user:user-1|device:unknown:local",
    });
    expect(memoScope("user-1", {
      node_mode: "host",
      device_id: "",
      device_name: "主机",
      platform: "linux",
      user_role: "admin",
      capabilities: [],
    }).key).toBe("user:user-1|device:host:linux");
    expect(() => memoScope("", null)).toThrow("无法识别当前账号");

    expect(memoDisplayTitle({ ...note, body: "\n  正文首行  \n第二行" })).toBe("正文首行");
    expect(memoDisplayTitle({ ...checklist, checklist: [{ id: "1", text: "  清单首项  ", done: false }] })).toBe("清单首项");
    expect(memoDisplayTitle(note)).toBe("无标题备忘");
    expect(memoDisplayTitle({ ...note, body: "甲".repeat(80) })).toHaveLength(40);

    expect(validateMemo({
      ...note,
      body: "正文",
      kind: "invalid" as LocalMemo["kind"],
      color: "invalid" as LocalMemo["color"],
      pinned: 1 as unknown as boolean,
      tags: ["", "甲".repeat(40), ...Array.from({ length: 12 }, (_, index) => `标签${index}`)],
      checklist: [{ id: "", text: "乙".repeat(1_200), done: 1 as unknown as boolean }],
    })).toMatchObject({
      kind: "note",
      color: "paper",
      pinned: true,
      deletedAt: null,
    });
    expect(validateMemo({ ...note, tags: ["甲".repeat(40)] }).tags[0]).toHaveLength(32);
    expect(validateMemo({ ...note, tags: Array.from({ length: 12 }, (_, index) => `标签${index}`) }).tags).toHaveLength(10);
    expect(validateMemo({ ...note, checklist: [{ id: "", text: "乙".repeat(1_200), done: false }] }).checklist[0].text).toHaveLength(1_000);
    expect(() => validateMemo({ ...note, createdAt: "not-a-date" })).toThrow("时间信息无效");
    expect(() => validateMemo({ ...note, updatedAt: "not-a-date" })).toThrow("时间信息无效");
    expect(() => validateMemo({ ...note, deletedAt: "not-a-date" })).toThrow("删除时间无效");
    expect(() => validateMemo({ ...note, body: "字".repeat(100_001) })).toThrow("正文不能超过");
  });

  it("搜索严格区分回收站，并在置顶状态相同的时候按更新时间排序", () => {
    const activeOlder = { ...createMemo(), id: "active-old", title: "活动旧", updatedAt: "2026-01-01T00:00:00Z" };
    const activeNewer = { ...createMemo(), id: "active-new", title: "活动新", updatedAt: "2026-02-01T00:00:00Z" };
    const deleted = { ...createMemo(), id: "deleted", title: "已删除", deletedAt: "2026-03-01T00:00:00Z" };
    expect(filterMemos([activeOlder, deleted, activeNewer], "").map((memo) => memo.id)).toEqual(["active-new", "active-old"]);
    expect(filterMemos([activeOlder, deleted, activeNewer], "", true).map((memo) => memo.id)).toEqual(["deleted"]);
    expect(filterMemos([activeOlder], "不存在")).toEqual([]);
  });

  it("删除进入回收站，超过三十天后只清理当前作用域", async () => {
    const repository = new LocalMemoRepository(new IDBFactory());
    const expired: LocalMemo = {
      ...createMemo(), title: "过期", deletedAt: "2026-07-01T00:00:00Z",
    };
    const recent: LocalMemo = {
      ...createMemo(), title: "近期", deletedAt: "2026-08-10T00:00:00Z",
    };
    await repository.save(scope(), expired);
    await repository.save(scope(), recent);
    await repository.save(scope("user-2"), expired);
    expect(await repository.purgeExpiredTrash(scope(), Date.parse("2026-08-11T00:00:00Z"))).toBe(1);
    expect((await repository.list(scope())).map((memo) => memo.title)).toEqual(["近期"]);
    expect(await repository.list(scope("user-2"))).toHaveLength(1);
  });

  it("支持显式删除、较新备份覆盖，并保留未到期及无删除时间的记录", async () => {
    const repository = new LocalMemoRepository(new IDBFactory());
    const current = await repository.save(scope(), { ...createMemo(), id: "current", title: "当前" });
    const newer = { ...current, title: "更新后", updatedAt: "2099-01-01T00:00:00Z" };
    expect(await repository.import(scope(), [newer], "newer")).toEqual({ imported: 0, updated: 1, skipped: 0 });
    expect((await repository.list(scope()))[0].title).toBe("更新后");
    await repository.remove(scope(), "current");
    expect(await repository.list(scope())).toEqual([]);

    await repository.save(scope(), { ...createMemo(), id: "active", title: "活动记录" });
    await repository.save(scope(), { ...createMemo(), id: "recent", title: "近期删除", deletedAt: "2026-08-13T00:00:00Z" });
    expect(await repository.purgeExpiredTrash(scope(), Date.parse("2026-08-14T00:00:00Z"))).toBe(0);
    expect(await repository.list(scope())).toHaveLength(2);
  });

  it("使用 AES-GCM 加密导出，同账号可导入，错密码和跨账号被拒绝", async () => {
    const sourceScope = scope();
    const memos = [{ ...createMemo(), title: "机密备忘", body: "仅本机可见" }];
    const encoded = await encryptMemoBackup(sourceScope, memos, "correct-password");
    expect(encoded).not.toContain("机密备忘");
    expect((await decryptMemoBackup(scope("user-1", "device-b"), encoded, "correct-password"))[0].title).toBe("机密备忘");
    await expect(decryptMemoBackup(sourceScope, encoded, "wrong-password")).rejects.toThrow("密码错误");
    await expect(decryptMemoBackup(scope("user-2"), encoded, "correct-password")).rejects.toThrow("另一个 PartyOps 账号");
    await expect(encryptMemoBackup(sourceScope, memos, "short")).rejects.toThrow("至少需要 8 位");
  });

  it("导入时支持仅更新较新记录或保留副本，存储不可用时明确失败", async () => {
    const repository = new LocalMemoRepository(new IDBFactory());
    const current = await repository.save(scope(), { ...createMemo(), id: "same-id", title: "当前记录" });
    const older = { ...current, title: "较旧记录", updatedAt: "2020-01-01T00:00:00Z" };
    expect(await repository.import(scope(), [older], "newer")).toEqual({ imported: 0, updated: 0, skipped: 1 });
    expect(await repository.import(scope(), [older], "copy")).toEqual({ imported: 1, updated: 0, skipped: 0 });
    expect(await repository.list(scope())).toHaveLength(2);
    await expect(new LocalMemoRepository(null as unknown as IDBFactory).list(scope())).rejects.toBeInstanceOf(MemoStorageError);
    await expect(decryptMemoBackup(scope(), "not-json", "password-123")).rejects.toThrow("不是有效");
  });

  it("拒绝所有篡改的备份信封参数、nonce 与密文", async () => {
    const encoded = await encryptMemoBackup(scope(), [{ ...createMemo(), title: "原始记录" }], "password-123");
    const envelope = JSON.parse(encoded) as Record<string, unknown>;
    const invalidEnvelopes: Array<Record<string, unknown> | null> = [
      null,
      { ...envelope, format: "other" },
      { ...envelope, version: 2 },
      { ...envelope, algorithm: "AES-CBC" },
      { ...envelope, kdf: "PBKDF2-SHA1" },
      { ...envelope, iterations: "210000" },
      { ...envelope, iterations: 99_999 },
      { ...envelope, iterations: 1_000_001 },
      { ...envelope, salt: 123 },
      { ...envelope, iv: 123 },
      { ...envelope, ciphertext: 123 },
    ];
    for (const candidate of invalidEnvelopes) {
      await expect(decryptMemoBackup(scope(), JSON.stringify(candidate), "password-123")).rejects.toThrow("格式或版本不受支持");
    }
    await expect(decryptMemoBackup(
      scope(),
      JSON.stringify({ ...envelope, salt: btoa("short"), iv: btoa("also-short") }),
      "password-123",
    )).rejects.toThrow("密码错误，或文件已经损坏");
    await expect(decryptMemoBackup(
      scope(),
      JSON.stringify({ ...envelope, ciphertext: "%%%" }),
      "password-123",
    )).rejects.toThrow("密码错误，或文件已经损坏");
  });

  it("WebCrypto 缺失时拒绝导入和导出，不会降级为明文备份", async () => {
    Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
    await expect(encryptMemoBackup(scope(), [createMemo()], "password-123")).rejects.toThrow("不支持加密备份");
    await expect(decryptMemoBackup(scope(), "{}", "password-123")).rejects.toThrow("不支持加密备份");
  });

  it("拒绝超量或损坏的导入数据，并明确报告本机容量不足", async () => {
    const repository = new LocalMemoRepository(new IDBFactory());
    const damaged = { ...createMemo(), id: "包含空格", updatedAt: "invalid-date" };
    await expect(repository.import(scope(), [damaged], "newer")).rejects.toThrow("标识无效");
    await expect(repository.import(
      scope(),
      Array.from({ length: 5_001 }, () => createMemo()),
      "newer",
    )).rejects.toThrow("单次最多导入");

    const quotaFactory = {
      open: () => {
        throw new DOMException("quota", "QuotaExceededError");
      },
    } as unknown as IDBFactory;
    await expect(new LocalMemoRepository(quotaFactory).list(scope())).rejects.toThrow("存储空间不足");
  });
});
