import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MAX_PREVIEW_MARKDOWN_CHARS } from "../documentPreview";

const wasmMocks = vi.hoisted(() => ({
  initAnydoc: vi.fn(async () => undefined),
  formatFromExtension: vi.fn((extension: string) => extension === ".docx" ? "docx" : null),
  toMarkdownBytes: vi.fn(() => "# 转换后的文档"),
  initPdf: vi.fn(async () => undefined),
  processPdf: vi.fn(),
  pdfVersion: vi.fn(() => "0.1.3"),
}));

vi.mock("@firecrawl/anydoc-wasm", () => ({
  default: wasmMocks.initAnydoc,
  formatFromExtension: wasmMocks.formatFromExtension,
  toMarkdownBytes: wasmMocks.toMarkdownBytes,
}));

vi.mock("@firecrawl/pdf-inspector-wasm", () => ({
  default: wasmMocks.initPdf,
  processPdf: wasmMocks.processPdf,
  version: wasmMocks.pdfVersion,
}));

const messages: unknown[] = [];
const scope = {
  onmessage: null as ((event: MessageEvent) => void) | null,
  postMessage: vi.fn((message: unknown) => messages.push(message)),
};

function bufferOf(value: string): ArrayBuffer {
  return new TextEncoder().encode(value).buffer;
}

async function dispatch(data: { requestId: string; name: string; mimeType: string; buffer: ArrayBuffer }) {
  scope.onmessage?.({ data } as MessageEvent);
  await vi.waitFor(() => expect(scope.postMessage).toHaveBeenCalled());
  return messages.at(-1) as Record<string, unknown>;
}

beforeAll(async () => {
  vi.stubGlobal("self", scope);
  await import("./documentPreview.worker");
});

beforeEach(() => {
  messages.length = 0;
  scope.postMessage.mockClear();
  wasmMocks.toMarkdownBytes.mockReset().mockReturnValue("# 转换后的文档");
  wasmMocks.processPdf.mockReset().mockReturnValue({
    markdown: "# PDF 标题",
    pagesNeedingOcr: [2],
    hasEncodingIssues: true,
    pdfType: "scanned",
    pageCount: 2,
    confidence: 0.8,
    processingTimeMs: 12,
    ocrReasonsByPage: { 2: ["image_only"] },
    layout: { pagesWithTables: [1], pagesWithColumns: [] },
  });
});

describe("文档阅读 Worker", () => {
  it("直接读取文本和 Markdown，并对超长内容作可见截断", async () => {
    const text = await dispatch({ requestId: "text-1", name: "说明.TXT", mimeType: "text/plain", buffer: bufferOf("本地文本") });
    expect(text).toMatchObject({ ok: true, engine: "partyops-text", format: "txt", truncated: false });
    expect(text.markdown).toContain("```text");

    scope.postMessage.mockClear();
    const longMarkdown = "甲".repeat(MAX_PREVIEW_MARKDOWN_CHARS + 5);
    const markdown = await dispatch({ requestId: "md-1", name: "说明.md", mimeType: "text/markdown", buffer: bufferOf(longMarkdown) });
    expect(markdown).toMatchObject({ ok: true, format: "md", truncated: true });
    expect(markdown.markdown).toContain("阅读视图已截断");
  });

  it("使用 pdf-inspector 返回版面、OCR 与字体编码风险", async () => {
    const result = await dispatch({ requestId: "pdf-1", name: "通知.PDF", mimeType: "application/pdf", buffer: bufferOf("pdf") });
    expect(wasmMocks.initPdf).toHaveBeenCalledOnce();
    expect(wasmMocks.processPdf).toHaveBeenCalledWith(expect.any(Uint8Array), expect.objectContaining({ profile: "fidelity" }));
    expect(result).toMatchObject({ ok: true, engine: "pdf-inspector", engineVersion: "0.1.3", format: "pdf" });
    expect(result.warnings).toEqual(expect.arrayContaining([expect.stringContaining("中文 OCR"), expect.stringContaining("字体编码异常")]));
    expect(result.pdf).toMatchObject({ pageCount: 2, pagesWithTables: [1], hasEncodingIssues: true });
  });

  it("使用 anydoc 转换 Office 文档并把底层错误安全返回主线程", async () => {
    const converted = await dispatch({ requestId: "docx-1", name: "通知.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: bufferOf("docx") });
    expect(converted).toMatchObject({ ok: true, engine: "anydoc", engineVersion: "0.1.7", format: "docx" });

    scope.postMessage.mockClear();
    wasmMocks.toMarkdownBytes.mockImplementationOnce(() => { throw { code: "invalid_document" }; });
    const failed = await dispatch({ requestId: "bad-1", name: "损坏.docx", mimeType: "application/octet-stream", buffer: bufferOf("bad") });
    expect(failed).toMatchObject({ ok: false, code: "invalid_document", message: "[object Object]" });
  });
});
