import initAnydoc, {
  formatFromExtension,
  toMarkdownBytes,
} from "@firecrawl/anydoc-wasm";
import initPdfInspector, {
  processPdf,
  version as pdfInspectorVersion,
} from "@firecrawl/pdf-inspector-wasm";
import {
  MAX_PREVIEW_MARKDOWN_CHARS,
  type DocumentPreviewFailure,
  type DocumentPreviewRequest,
  type DocumentPreviewSuccess,
} from "../documentPreview";

interface PreviewWorkerScope {
  onmessage: ((event: MessageEvent<DocumentPreviewRequest>) => void) | null;
  postMessage(message: DocumentPreviewSuccess | DocumentPreviewFailure): void;
}

const workerScope = self as unknown as PreviewWorkerScope;
let anydocReady: Promise<void> | undefined;
let pdfInspectorReady: Promise<void> | undefined;

function ensureAnydoc(): Promise<void> {
  anydocReady ||= initAnydoc().then(() => undefined);
  return anydocReady;
}

function ensurePdfInspector(): Promise<void> {
  pdfInspectorReady ||= initPdfInspector().then(() => undefined);
  return pdfInspectorReady;
}

function extensionOf(name: string): string {
  const normalized = name.trim().toLowerCase();
  const index = normalized.lastIndexOf(".");
  return index >= 0 ? normalized.slice(index) : "";
}

function limitMarkdown(markdown: string): { value: string; truncated: boolean } {
  if (markdown.length <= MAX_PREVIEW_MARKDOWN_CHARS) {
    return { value: markdown, truncated: false };
  }
  return {
    value: `${markdown.slice(0, MAX_PREVIEW_MARKDOWN_CHARS)}\n\n> 阅读视图已截断，请下载原文件查看完整内容。`,
    truncated: true,
  };
}

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "code" in error) {
    return String((error as { code?: unknown }).code || "unknown");
  }
  return "unknown";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "document preview failed");
}

async function parseDocument(request: DocumentPreviewRequest): Promise<DocumentPreviewSuccess> {
  const bytes = new Uint8Array(request.buffer);
  const extension = extensionOf(request.name);
  if (extension === ".pdf" || request.mimeType.toLowerCase() === "application/pdf") {
    await ensurePdfInspector();
    const result = processPdf(bytes, {
      profile: "fidelity",
      includePageMarkers: true,
      includeImages: false,
    });
    const limited = limitMarkdown(result.markdown || "");
    const warnings: string[] = [];
    if (result.pagesNeedingOcr.length) {
      warnings.push(`第 ${result.pagesNeedingOcr.join("、")} 页可能需要中文 OCR，可切换“原始预览”直接阅读扫描页。`);
    }
    if (result.hasEncodingIssues) {
      warnings.push("PDF 字体编码异常，结构化文字可能不完整，请对照原始预览。 ");
    }
    return {
      requestId: request.requestId,
      ok: true,
      markdown: limited.value,
      engine: "pdf-inspector",
      engineVersion: pdfInspectorVersion(),
      format: "pdf",
      truncated: limited.truncated,
      warnings,
      pdf: {
        pdfType: result.pdfType,
        pageCount: result.pageCount,
        confidence: result.confidence,
        processingTimeMs: result.processingTimeMs,
        pagesNeedingOcr: result.pagesNeedingOcr,
        ocrReasonsByPage: result.ocrReasonsByPage,
        pagesWithTables: result.layout.pagesWithTables,
        pagesWithColumns: result.layout.pagesWithColumns,
        hasEncodingIssues: result.hasEncodingIssues,
      },
    };
  }

  if ([".txt", ".md", ".json", ".xml", ".html", ".log"].includes(extension)
      || (request.mimeType.toLowerCase().startsWith("text/") && extension !== ".csv")) {
    const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    const limited = limitMarkdown(extension === ".md" ? decoded : `\`\`\`text\n${decoded}\n\`\`\``);
    return {
      requestId: request.requestId,
      ok: true,
      markdown: limited.value,
      engine: "partyops-text",
      engineVersion: "1",
      format: extension.slice(1) || "text",
      truncated: limited.truncated,
      warnings: [],
    };
  }

  await ensureAnydoc();
  const detected = formatFromExtension(extension) || undefined;
  const converted = toMarkdownBytes(bytes, detected);
  const limited = limitMarkdown(converted);
  return {
    requestId: request.requestId,
    ok: true,
    markdown: limited.value,
    engine: "anydoc",
    engineVersion: "0.1.7",
    format: detected || extension.slice(1) || "document",
    truncated: limited.truncated,
    warnings: [],
  };
}

workerScope.onmessage = (event) => {
  void parseDocument(event.data)
    .then((result) => workerScope.postMessage(result))
    .catch((error: unknown) => workerScope.postMessage({
      requestId: event.data.requestId,
      ok: false,
      code: errorCode(error),
      message: errorMessage(error),
    }));
};
