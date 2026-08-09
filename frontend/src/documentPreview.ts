import MarkdownIt from "markdown-it";

/**
 * 浏览器结构化预览上限。WASM 解析时会同时持有源字节、线性内存和
 * Markdown，64 MiB 能覆盖常用办公文档，又避免低配协同机内存尖峰。
 * 超限文件仍可使用原始 PDF/图片预览或走 20 GB 下载通道。
 */
export const MAX_STRUCTURED_PREVIEW_BYTES = 64 * 1024 * 1024;
export const MAX_PREVIEW_MARKDOWN_CHARS = 2_000_000;

export interface DocumentPreviewRequest {
  requestId: string;
  name: string;
  mimeType: string;
  buffer: ArrayBuffer;
}

export interface PdfPreviewMetadata {
  pdfType: string;
  pageCount: number;
  confidence: number;
  processingTimeMs: number;
  pagesNeedingOcr: number[];
  ocrReasonsByPage: Array<{ page: number; reasons: string[] }>;
  pagesWithTables: number[];
  pagesWithColumns: number[];
  hasEncodingIssues: boolean;
}

export interface DocumentPreviewSuccess {
  requestId: string;
  ok: true;
  markdown: string;
  engine: "anydoc" | "pdf-inspector" | "partyops-text";
  engineVersion: string;
  format: string;
  truncated: boolean;
  warnings: string[];
  pdf?: PdfPreviewMetadata;
}

export interface DocumentPreviewFailure {
  requestId: string;
  ok: false;
  code: string;
  message: string;
}

export type DocumentPreviewResult = DocumentPreviewSuccess | DocumentPreviewFailure;

const STRUCTURED_EXTENSIONS = new Set([
  ".doc",
  ".docx",
  ".docm",
  ".ppt",
  ".pps",
  ".pot",
  ".pptx",
  ".pptm",
  ".ppsx",
  ".ppsm",
  ".xls",
  ".xlsx",
  ".xlsm",
  ".xlsb",
  ".odt",
  ".ods",
  ".odp",
  ".rtf",
  ".epub",
  ".csv",
  ".pdf",
  ".txt",
  ".md",
  ".json",
  ".xml",
  ".html",
  ".log",
]);

const SAFE_RAW_TEXT_EXTENSIONS = new Set([".txt", ".md", ".json", ".log", ".csv"]);

function fileExtension(name: string): string {
  const normalized = name.trim().toLowerCase();
  const index = normalized.lastIndexOf(".");
  return index >= 0 ? normalized.slice(index) : "";
}

export function isStructuredPreviewSupported(name: string, sizeBytes: number): boolean {
  return sizeBytes >= 0
    && sizeBytes <= MAX_STRUCTURED_PREVIEW_BYTES
    && STRUCTURED_EXTENSIONS.has(fileExtension(name));
}

export function isRawPreviewSupported(name: string, mimeType: string): boolean {
  const normalizedMime = mimeType.toLowerCase();
  const extension = fileExtension(name);
  // HTML、XML 与 SVG 即使在 sandbox iframe 中也可能加载外部资源，泄露
  // 用户正在阅读某份共享文件。它们只进入禁用 HTML/外链的结构化视图。
  if ([".html", ".xml", ".svg"].includes(extension)
      || ["text/html", "text/xml", "application/xml", "image/svg+xml"].includes(normalizedMime)) {
    return false;
  }
  return normalizedMime === "application/pdf"
    || normalizedMime.startsWith("image/")
    || normalizedMime.startsWith("text/")
    || SAFE_RAW_TEXT_EXTENSIONS.has(extension);
}

export class PreviewReadError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "PreviewReadError";
    this.code = code;
  }
}

/**
 * 流式读取预览源，同时校验声明长度与实际长度。不能只信
 * Content-Length，协同机索引可能过期，服务端也可能使用分块响应。
 */
export async function readResponseWithLimit(
  response: Response,
  maxBytes = MAX_STRUCTURED_PREVIEW_BYTES,
): Promise<ArrayBuffer> {
  const declared = Number(response.headers.get("content-length") || "0");
  if (Number.isFinite(declared) && declared > maxBytes) {
    throw new PreviewReadError("previewTooLarge", "文件超过结构化预览上限");
  }
  if (!response.body) {
    const value = await response.arrayBuffer();
    if (value.byteLength > maxBytes) {
      throw new PreviewReadError("previewTooLarge", "文件超过结构化预览上限");
    }
    return value;
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value?.byteLength) continue;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel("preview source too large");
        throw new PreviewReadError("previewTooLarge", "文件超过结构化预览上限");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged.buffer;
}

export function previewErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    encrypted: "文件已加密或受密码保护，请下载后使用有权限的本机程序打开。",
    malformed: "文件结构损坏，无法生成结构化阅读视图；可尝试原始预览或重新下载文件。",
    missingPart: "文档缺少必要组成部分，无法完整阅读；请下载原文件核对。",
    resourceLimit: "文档结构超过安全限制，系统已停止解析；原文件仍可安全下载。",
    unsupported: "该格式暂不支持结构化阅读，请使用原始预览或下载到本机。",
    previewTooLarge: "文件超过 64 MB 结构化预览上限，请使用原始预览或下载到本机。",
    network: "预览内容读取失败，请检查设备在线状态和网络后重试。",
  };
  return messages[code] || "预览未完成，请重试；如仍失败，可下载到本机阅读。";
}

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: false,
});

const defaultLinkOpen = markdown.renderer.rules.link_open;
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet("target", "_blank");
  tokens[index].attrSet("rel", "noopener noreferrer nofollow");
  return defaultLinkOpen
    ? defaultLinkOpen(tokens, index, options, env, self)
    : self.renderToken(tokens, index, options);
};

// 不加载文档内的外部图片，避免打开共享文件时向第三方泄露 IP、Cookie
// 或阅读行为。嵌入图片的原始字节也不会被拼成 data URL 占满页面内存。
markdown.renderer.rules.image = (tokens, index) => {
  const alt = markdown.utils.escapeHtml(tokens[index].content || "文档图片");
  return `<span class="preview-image-alt">[图片：${alt}]</span>`;
};

export function renderPreviewMarkdown(source: string): string {
  // pdf-inspector 用 HTML 注释标识分页。安全阅读视图禁用 HTML，直接交给
  // markdown-it 会把注释显示成正文；转换为分隔线既保留页界又不开放 HTML。
  const withPageBreaks = (source || "_文档没有可提取的文字内容。_").replace(
    /<!--\s*Page\s+\d+\s*-->/gi,
    "\n\n---\n\n",
  );
  // markdown-it 会把危险协议的链接保留为普通文本。虽然不会执行，仍在
  // 插入 DOM 前把协议字样中和，避免后续样式/插件误把它再次链接化。
  const neutralized = withPageBreaks.replace(
    /(\]\(\s*)(?:javascript|vbscript|data):/gi,
    "$1blocked:",
  );
  return markdown.render(neutralized);
}
