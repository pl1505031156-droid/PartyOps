import { describe, expect, it } from "vitest";
import {
  MAX_STRUCTURED_PREVIEW_BYTES,
  isRawPreviewSupported,
  isStructuredPreviewSupported,
  previewErrorMessage,
  readResponseWithLimit,
  renderPreviewMarkdown,
} from "./documentPreview";

describe("跨机文档预览能力", () => {
  it("覆盖 Firecrawl 支持的办公格式并限制内存体积", () => {
    for (const name of [
      "通知.doc",
      "方案.docx",
      "汇报.pptx",
      "台账.xlsx",
      "材料.odt",
      "说明.rtf",
      "读本.epub",
      "清单.csv",
      "报告.pdf",
    ]) {
      expect(isStructuredPreviewSupported(name, 1024)).toBe(true);
    }
    expect(isStructuredPreviewSupported("超大报告.pdf", MAX_STRUCTURED_PREVIEW_BYTES + 1)).toBe(false);
    expect(isStructuredPreviewSupported("程序.exe", 1024)).toBe(false);
  });

  it("只把浏览器可以安全内联的类型标记为原始预览", () => {
    expect(isRawPreviewSupported("报告.pdf", "application/pdf")).toBe(true);
    expect(isRawPreviewSupported("照片.png", "image/png")).toBe(true);
    expect(isRawPreviewSupported("说明.txt", "text/plain")).toBe(true);
    expect(isRawPreviewSupported("宏文档.docm", "application/octet-stream")).toBe(false);
    expect(isRawPreviewSupported("跟踪页.html", "text/html")).toBe(false);
    expect(isRawPreviewSupported("外链图.svg", "image/svg+xml")).toBe(false);
    expect(isRawPreviewSupported("样式.xml", "application/xml")).toBe(false);
  });

  it("Markdown 阅读视图转义脚本、危险链接和远程图片", () => {
    const html = renderPreviewMarkdown([
      "# 安全阅读",
      "<script>alert('x')</script>",
      "[危险](javascript:alert(1))",
      "![跟踪像素](https://tracker.invalid/pixel.png)",
      "| 姓名 | 状态 |",
      "| --- | --- |",
      "| 张三 | 完成 |",
    ].join("\n"));
    expect(html).toContain("<h1>安全阅读</h1>");
    expect(html).toContain("<table>");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("<img");
    expect(html).toContain("跟踪像素");
  });

  it("把 pdf-inspector 页标记转换为可读分隔线", () => {
    const html = renderPreviewMarkdown("<!-- Page 1 -->\n\n第一页\n\n<!-- Page 2 -->\n\n第二页");
    expect(html).not.toContain("Page 1");
    expect(html).not.toContain("&lt;!--");
    expect(html).toContain("<hr>");
    expect(html).toContain("第一页");
    expect(html).toContain("第二页");
  });

  it("按 Firecrawl 错误码给出可恢复的中文提示", () => {
    expect(previewErrorMessage("encrypted")).toContain("密码");
    expect(previewErrorMessage("resourceLimit")).toContain("安全限制");
    expect(previewErrorMessage("unsupported")).toContain("原始预览");
    expect(previewErrorMessage("unknown")).toContain("下载");
  });

  it("读取响应时同时校验声明长度和真实流长度", async () => {
    const declared = new Response(new Uint8Array(8), { headers: { "content-length": "100" } });
    await expect(readResponseWithLimit(declared, 16)).rejects.toMatchObject({ code: "previewTooLarge" });

    const streamed = new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(10));
        controller.enqueue(new Uint8Array(10));
        controller.close();
      },
    }));
    await expect(readResponseWithLimit(streamed, 16)).rejects.toMatchObject({ code: "previewTooLarge" });
  });
});
