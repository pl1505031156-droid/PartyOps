import type { RuntimeContext } from "./types";

export type MemoKind = "note" | "checklist";
export type MemoColor = "paper" | "cinnabar" | "pine" | "ochre" | "ink";
export type MemoImportPolicy = "newer" | "copy";

export interface MemoChecklistItem {
  id: string;
  text: string;
  done: boolean;
}

export interface LocalMemo {
  id: string;
  title: string;
  body: string;
  kind: MemoKind;
  checklist: MemoChecklistItem[];
  tags: string[];
  color: MemoColor;
  pinned: boolean;
  deletedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MemoScope {
  key: string;
  ownerId: string;
}

export interface MemoImportResult {
  imported: number;
  updated: number;
  skipped: number;
}

interface StoredMemo extends LocalMemo {
  storageKey: string;
  scopeKey: string;
}

interface EncryptedMemoEnvelope {
  format: "partyops-memos";
  version: 1;
  algorithm: "AES-GCM";
  kdf: "PBKDF2-SHA256";
  iterations: number;
  salt: string;
  iv: string;
  ciphertext: string;
}

interface MemoBackupPayload {
  format: "partyops-memos-payload";
  version: 1;
  ownerFingerprint: string;
  exportedAt: string;
  memos: LocalMemo[];
}

const DATABASE_NAME = "partyops-private-v1";
const DATABASE_VERSION = 1;
const MEMO_STORE = "memos";
const MAX_TITLE_LENGTH = 160;
const MAX_BODY_LENGTH = 100_000;
const MAX_CHECKLIST_ITEMS = 200;
const MAX_CHECKLIST_TEXT = 1_000;
const MAX_TAGS = 10;
const MAX_TAG_LENGTH = 32;
const MAX_IMPORT_COUNT = 5_000;
const TRASH_RETENTION_MS = 30 * 24 * 60 * 60 * 1_000;
const PBKDF2_ITERATIONS = 210_000;
const ALLOWED_COLORS = new Set<MemoColor>(["paper", "cinnabar", "pine", "ochre", "ink"]);

export class MemoStorageError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MemoStorageError";
  }
}

function nowIso(): string {
  return new Date().toISOString();
}

function randomId(): string {
  if (typeof globalThis.crypto !== "undefined" && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  const seed = `${Date.now()}-${Math.random()}-${Math.random()}`;
  return seed.replace(/\D/g, "").slice(0, 36);
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new MemoStorageError("本机备忘录读取失败。"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error || new MemoStorageError("本机备忘录事务已中止。"));
    transaction.onerror = () => reject(transaction.error || new MemoStorageError("本机备忘录保存失败。"));
  });
}

function storageMessage(error: unknown): string {
  if (error instanceof MemoStorageError) return error.message;
  if (error instanceof DOMException && error.name === "QuotaExceededError") {
    return "本机存储空间不足，备忘录没有保存。请先导出备份并清理浏览器空间。";
  }
  return "本机私有存储不可用，备忘录没有上传到主机，也没有静默保存到其他位置。";
}

function normalizeTags(tags: string[]): string[] {
  const normalized = tags
    .map((tag) => tag.trim())
    .filter(Boolean)
    .map((tag) => tag.slice(0, MAX_TAG_LENGTH));
  return [...new Set(normalized)].slice(0, MAX_TAGS);
}

export function validateMemo(memo: LocalMemo): LocalMemo {
  if (!/^[A-Za-z0-9._:-]{1,100}$/.test(String(memo.id || ""))) {
    throw new MemoStorageError("备忘录标识无效，文件可能已损坏。");
  }
  if (!Number.isFinite(Date.parse(memo.createdAt)) || !Number.isFinite(Date.parse(memo.updatedAt))) {
    throw new MemoStorageError("备忘录时间信息无效，文件可能已损坏。");
  }
  if (memo.deletedAt && !Number.isFinite(Date.parse(memo.deletedAt))) {
    throw new MemoStorageError("备忘录删除时间无效，文件可能已损坏。");
  }
  const title = memo.title.trim();
  if (title.length > MAX_TITLE_LENGTH) {
    throw new MemoStorageError(`标题不能超过 ${MAX_TITLE_LENGTH} 个字符。`);
  }
  if (memo.body.length > MAX_BODY_LENGTH) {
    throw new MemoStorageError(`正文不能超过 ${MAX_BODY_LENGTH.toLocaleString()} 个字符。`);
  }
  if (memo.checklist.length > MAX_CHECKLIST_ITEMS) {
    throw new MemoStorageError(`清单不能超过 ${MAX_CHECKLIST_ITEMS} 项。`);
  }
  const checklist = memo.checklist.map((item) => ({
    id: String(item.id || randomId()),
    text: String(item.text || "").slice(0, MAX_CHECKLIST_TEXT),
    done: Boolean(item.done),
  }));
  return {
    ...memo,
    title,
    body: String(memo.body || ""),
    kind: memo.kind === "checklist" ? "checklist" : "note",
    checklist,
    tags: normalizeTags(memo.tags || []),
    color: ALLOWED_COLORS.has(memo.color) ? memo.color : "paper",
    pinned: Boolean(memo.pinned),
    deletedAt: memo.deletedAt || null,
  };
}

export function createMemo(kind: MemoKind = "note"): LocalMemo {
  const timestamp = nowIso();
  return {
    id: randomId(),
    title: "",
    body: "",
    kind,
    checklist: kind === "checklist" ? [{ id: randomId(), text: "", done: false }] : [],
    tags: [],
    color: "paper",
    pinned: false,
    deletedAt: null,
    createdAt: timestamp,
    updatedAt: timestamp,
  };
}

export function memoScope(userId: string, runtime: RuntimeContext | null): MemoScope {
  if (!userId) throw new MemoStorageError("无法识别当前账号，不能打开本机备忘录。");
  const nodeMode = runtime?.node_mode || "unknown";
  const deviceKey = runtime?.device_id || `${nodeMode}:${runtime?.platform || "local"}`;
  return { ownerId: userId, key: `user:${userId}|device:${deviceKey}` };
}

export function memoDisplayTitle(memo: LocalMemo): string {
  if (memo.title.trim()) return memo.title.trim();
  const firstBodyLine = memo.body.split(/\r?\n/).map((line) => line.trim()).find(Boolean);
  const firstChecklist = memo.checklist.map((item) => item.text.trim()).find(Boolean);
  return (firstBodyLine || firstChecklist || "无标题备忘").slice(0, 40);
}

export function filterMemos(
  memos: LocalMemo[],
  query: string,
  includeDeleted = false,
): LocalMemo[] {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  return memos
    .filter((memo) => includeDeleted ? Boolean(memo.deletedAt) : !memo.deletedAt)
    .filter((memo) => {
      if (!normalized) return true;
      return [
        memo.title,
        memo.body,
        memo.tags.join(" "),
        memo.checklist.map((item) => item.text).join(" "),
      ].join("\n").toLocaleLowerCase("zh-CN").includes(normalized);
    })
    .sort((left, right) => {
      if (left.pinned !== right.pinned) return left.pinned ? -1 : 1;
      return right.updatedAt.localeCompare(left.updatedAt);
    });
}

export class LocalMemoRepository {
  private databasePromise: Promise<IDBDatabase> | null = null;

  constructor(private readonly factory: IDBFactory | undefined = globalThis.indexedDB) {}

  private open(): Promise<IDBDatabase> {
    if (!this.factory) {
      return Promise.reject(new MemoStorageError("当前浏览器不支持本机私有存储。"));
    }
    if (this.databasePromise) return this.databasePromise;
    this.databasePromise = new Promise((resolve, reject) => {
      const request = this.factory!.open(DATABASE_NAME, DATABASE_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(MEMO_STORE)) {
          const store = database.createObjectStore(MEMO_STORE, { keyPath: "storageKey" });
          store.createIndex("scopeKey", "scopeKey", { unique: false });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => {
        this.databasePromise = null;
        reject(new MemoStorageError(storageMessage(request.error)));
      };
      request.onblocked = () => {
        this.databasePromise = null;
        reject(new MemoStorageError("备忘录数据库正被旧页面占用，请关闭其他 PartyOps 页面后重试。"));
      };
    });
    return this.databasePromise;
  }

  async list(scope: MemoScope): Promise<LocalMemo[]> {
    try {
      const database = await this.open();
      const transaction = database.transaction(MEMO_STORE, "readonly");
      const index = transaction.objectStore(MEMO_STORE).index("scopeKey");
      const records = await requestResult(index.getAll(scope.key) as IDBRequest<StoredMemo[]>);
      await transactionDone(transaction);
      return records.map(({ storageKey: _storageKey, scopeKey: _scopeKey, ...memo }) => memo);
    } catch (error) {
      throw new MemoStorageError(storageMessage(error));
    }
  }

  async save(scope: MemoScope, memo: LocalMemo): Promise<LocalMemo> {
    const validated = validateMemo({ ...memo, updatedAt: nowIso() });
    const stored: StoredMemo = {
      ...validated,
      scopeKey: scope.key,
      storageKey: `${scope.key}|memo:${validated.id}`,
    };
    try {
      const database = await this.open();
      const transaction = database.transaction(MEMO_STORE, "readwrite");
      transaction.objectStore(MEMO_STORE).put(stored);
      await transactionDone(transaction);
      return validated;
    } catch (error) {
      throw new MemoStorageError(storageMessage(error));
    }
  }

  async remove(scope: MemoScope, memoId: string): Promise<void> {
    try {
      const database = await this.open();
      const transaction = database.transaction(MEMO_STORE, "readwrite");
      transaction.objectStore(MEMO_STORE).delete(`${scope.key}|memo:${memoId}`);
      await transactionDone(transaction);
    } catch (error) {
      throw new MemoStorageError(storageMessage(error));
    }
  }

  async purgeExpiredTrash(scope: MemoScope, referenceTime = Date.now()): Promise<number> {
    const memos = await this.list(scope);
    const expired = memos.filter((memo) => {
      if (!memo.deletedAt) return false;
      const deletedAt = Date.parse(memo.deletedAt);
      return Number.isFinite(deletedAt) && referenceTime - deletedAt >= TRASH_RETENTION_MS;
    });
    await Promise.all(expired.map((memo) => this.remove(scope, memo.id)));
    return expired.length;
  }

  async import(
    scope: MemoScope,
    imported: LocalMemo[],
    policy: MemoImportPolicy,
  ): Promise<MemoImportResult> {
    if (imported.length > MAX_IMPORT_COUNT) {
      throw new MemoStorageError(`单次最多导入 ${MAX_IMPORT_COUNT.toLocaleString()} 条备忘录。`);
    }
    const existing = new Map((await this.list(scope)).map((memo) => [memo.id, memo]));
    const result: MemoImportResult = { imported: 0, updated: 0, skipped: 0 };
    for (const candidate of imported) {
      const validated = validateMemo(candidate);
      const current = existing.get(validated.id);
      if (!current) {
        await this.save(scope, validated);
        result.imported += 1;
        continue;
      }
      if (policy === "copy") {
        await this.save(scope, {
          ...validated,
          id: randomId(),
          title: `${memoDisplayTitle(validated)}（导入副本）`,
          createdAt: nowIso(),
          updatedAt: nowIso(),
        });
        result.imported += 1;
      } else if (validated.updatedAt > current.updatedAt) {
        await this.save(scope, validated);
        result.updated += 1;
      } else {
        result.skipped += 1;
      }
    }
    return result;
  }
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

async function ownerFingerprint(ownerId: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(ownerId));
  return bytesToBase64(new Uint8Array(digest));
}

async function deriveKey(password: string, salt: Uint8Array<ArrayBuffer>, iterations: number) {
  const material = await globalThis.crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return globalThis.crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

export async function encryptMemoBackup(
  scope: MemoScope,
  memos: LocalMemo[],
  password: string,
): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new MemoStorageError("当前浏览器不支持加密备份。");
  if (password.length < 8) throw new MemoStorageError("备份密码至少需要 8 位。");
  const salt = globalThis.crypto.getRandomValues(new Uint8Array(16));
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt, PBKDF2_ITERATIONS);
  const payload: MemoBackupPayload = {
    format: "partyops-memos-payload",
    version: 1,
    ownerFingerprint: await ownerFingerprint(scope.ownerId),
    exportedAt: nowIso(),
    memos: memos.map(validateMemo),
  };
  const ciphertext = await globalThis.crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    new TextEncoder().encode(JSON.stringify(payload)),
  );
  const envelope: EncryptedMemoEnvelope = {
    format: "partyops-memos",
    version: 1,
    algorithm: "AES-GCM",
    kdf: "PBKDF2-SHA256",
    iterations: PBKDF2_ITERATIONS,
    salt: bytesToBase64(salt),
    iv: bytesToBase64(iv),
    ciphertext: bytesToBase64(new Uint8Array(ciphertext)),
  };
  return JSON.stringify(envelope);
}

function isEnvelope(value: unknown): value is EncryptedMemoEnvelope {
  if (!value || typeof value !== "object") return false;
  const envelope = value as Partial<EncryptedMemoEnvelope>;
  return envelope.format === "partyops-memos"
    && envelope.version === 1
    && envelope.algorithm === "AES-GCM"
    && envelope.kdf === "PBKDF2-SHA256"
    && typeof envelope.iterations === "number"
    && envelope.iterations >= 100_000
    && envelope.iterations <= 1_000_000
    && typeof envelope.salt === "string"
    && typeof envelope.iv === "string"
    && typeof envelope.ciphertext === "string";
}

export async function decryptMemoBackup(
  scope: MemoScope,
  encoded: string,
  password: string,
): Promise<LocalMemo[]> {
  if (!globalThis.crypto?.subtle) throw new MemoStorageError("当前浏览器不支持加密备份。");
  let envelope: unknown;
  try {
    envelope = JSON.parse(encoded);
  } catch {
    throw new MemoStorageError("备份文件不是有效的 PartyOps 备忘录文件。");
  }
  if (!isEnvelope(envelope)) throw new MemoStorageError("备份文件格式或版本不受支持。");
  try {
    const salt = base64ToBytes(envelope.salt);
    const iv = base64ToBytes(envelope.iv);
    if (salt.byteLength !== 16 || iv.byteLength !== 12) throw new Error("invalid nonce");
    const key = await deriveKey(password, salt, envelope.iterations);
    const plaintext = await globalThis.crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      key,
      base64ToBytes(envelope.ciphertext),
    );
    const payload = JSON.parse(new TextDecoder().decode(plaintext)) as Partial<MemoBackupPayload>;
    if (
      payload.format !== "partyops-memos-payload"
      || payload.version !== 1
      || !Array.isArray(payload.memos)
    ) throw new Error("invalid payload");
    if (payload.ownerFingerprint !== await ownerFingerprint(scope.ownerId)) {
      throw new MemoStorageError("该备份属于另一个 PartyOps 账号，未导入任何内容。");
    }
    return payload.memos.map((memo) => validateMemo(memo));
  } catch (error) {
    if (error instanceof MemoStorageError) throw error;
    throw new MemoStorageError("备份密码错误，或文件已经损坏。");
  }
}
