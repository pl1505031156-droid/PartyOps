# PartyOps 公文排版技术来源与规则追溯

更新时间：2026-08-24（北京时间）
适用版本：PartyOps 1.4.5-rc.3

研究冻结点：2026-08-24；上游公开页面显示主线社区版 `v1.8.8.3`、55 个提交。只读取 README、LICENSE、LICENSE-HISTORY、DISCLAIMER、公开测试文件名和 Issues 标题，不下载或审阅实现源码。

## 1. 边界

- 唯一规范依据是用户提供的 `9704-2012-gbt-cd-300.pdf` 与国家标准信息公共服务平台公开的 [GB/T 9704-2012](https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=F3CC9BEF482524C895FDA7A08BB4A70E)。
- [docformat-gui](https://github.com/KaguraNanaga/docformat-gui) 仅作为公开产品能力、兼容性风险和测试维度的研究样本；PartyOps 没有复制、导入、改写其源码、模板、素材、预设或算法。
- 上游 `v1.8.8.3` 起采用 [PolyForm Noncommercial 1.0.0](https://github.com/KaguraNanaga/docformat-gui/blob/main/LICENSE)，历史文件说明 `v1.8.8.2` 及以前曾使用 MIT。为避免版本归属、后续维护和商业使用边界混淆，PartyOps 不使用任何上游版本代码。

## 2. 研究结论与 PartyOps 独立实现

| 公开经验 | 识别出的工程风险 | PartyOps 自有实现与验收 |
| --- | --- | --- |
| 诊断、标点修复和完整排版应分阶段反馈 | 一键改写而没有修改前后报告，用户无法确认误判 | 只提供“诊断 → 修改项预览 → 一键排版 → 重新读取复核 → 导出”闭环；每一问题带 PartyOps 错误码和标准条款 |
| `.doc/.wps` 的能力依赖系统和办公套件 | 把本地转换能力宣称为跨平台原生支持会误导用户 | DOCX 原生 OOXML；DOC/WPS 仅调用已安装的 WPS、Office 或 LibreOffice，失败返回明确错误，只导出 DOCX |
| 中文禁则、已有页脚、图片附件、标题拆分、表格边距应单独测试 | 只验证普通段落会在复杂真实公文上破坏页脚、媒体、合并单元格或标点换行 | 自有实现增加 `w:kinsoku`/标点压缩、已有页脚原位保留并补页码、媒体部件逐项复制、合并单元格保真、表格内边距和原对齐保留回归 |
| 上游公开问题仍出现“文档网格”“页码样式”“段落间距和缩进”“引号字体”遗漏 | 宣称支持某个能力不等于成品 OOXML 中存在可复现约束 | PartyOps 同时锁定 Normal 三号字、`docGrid` 22 行/28 字字符间距、28 磅行距和页码物理位置；诊断逐项检查，不接受只有页边距正确的成品 |
| 版头可能先出现份号、密级、紧急程度、发文机关标志和发文字号 | 把第一段一律当标题会把真实公文版头改成黑色居中标题 | PartyOps 使用上下文角色序列；版头要素优先于标题识别，遇到信函、命令（令）、纪要、签发人或印章区域强制显示视觉复核项 |
| 字体、架构、DPI、只读安装目录、FUSE/glibc 等属于启动和渲染风险 | 文档算法正确不等于冻结程序能打开，也不等于另一台电脑渲染一致 | 排版前枚举字体；缺字体禁止显示“符合标准”；配置和临时文件位于用户私有目录；平台安装启动另走原生门禁 |
| 原文件不覆盖、本地离线处理是公文工具的基础安全预期 | 路径、哈希或文本进入主机日志同样可能泄露 | Web 入口只传随机 UUID；文件只进入回环本机助手；进程级阻断非回环网络；日志只写版本、阶段、耗时和脱敏错误码；退出/导出/取消/空闲 15 分钟清理 |

## 3. 规则—实现—测试映射

| 规则 | 标准依据 | PartyOps 实现 | 自动化证据 |
| --- | --- | --- | --- |
| A4、156 mm × 225 mm 版心、天头/订口 | 5.1、5.2.1 | `_configure_sections` | `test_format_docx_preserves_content_images_and_merged_tables` |
| 每面 22 行、每行 28 字且以三号 Normal 为基准 | 5.2.3 | `_configure_normal_style`、`_configure_sections` | `test_formatter_applies_header_roles_exact_grid_and_page_number_position` |
| 份号、密级、紧急程度、发文机关标志、发文字号不得误判标题 | 7.2.1—7.2.6 | `_classify_document_paragraphs` | `test_contextual_classification_does_not_turn_document_header_into_title` |
| 标题、正文、一至四级标题字体字号 | 7.3.1、7.3.3 | `_paragraph_role`、`_format_paragraph` | 标题/正文 OOXML 断言与缺字体阻断测试 |
| 28 磅行距、正文首行二字符 | 5.2.3—5.2.4 | `_format_paragraph` | 行距、首行缩进 OOXML 断言 |
| 中文标点且保护 URL、邮箱、小数、法规编号 | 5.2.4 与中文书写上下文 | `normalize_chinese_punctuation` | `test_punctuation_normalization_is_conservative` |
| 中文行首行尾禁则 | 版面完整性 | `_configure_east_asian_typography` | `test_formatter_preserves_footer_emphasis_and_table_alignment` |
| 奇数页右、偶数页左页码，不覆盖既有页脚 | 7.5 | `_add_page_footers` | 同上：既有页脚文字与 PAGE 域同时存在 |
| 表格数据、合并关系、原对齐和内边距 | 7.2、特殊版式复核 | `_format_tables` | 图片、合并单元格、右对齐和 `tcMar` 回归 |
| 字体缺失不得误报合规 | 成品可验证性 | `_font_issues`、`diagnose_docx` | `test_diagnosis_rejects_zip_slip_and_explicitly_blocks_missing_fonts` |
| ZIP 越界、压缩炸弹与超大部件拒绝 | 本机输入安全 | `_validated_members`、`_safe_xml` | ZIP Slip、展开体积和压缩比错误码回归 |

## 4. 明确不采用的上游能力

- 不提供学术、法律或任意自定义模板；PartyOps 只有 GB/T 9704-2012 公文预设。
- 不提供自由字体、字号、页边距、行距、颜色、背景或加粗配置。
- 不把“智能对齐”用于猜测表格数据含义；默认保留原对齐。
- 不批量接收服务器文件，不上传、同步或存储排版文件。
- 不因上游能够运行就跳过 PartyOps 自己的 Windows、Linux、macOS 原生构建和真机门禁。

## 5. 后续黄金样本门禁

通用公文、信函、命令、纪要、横排表格各自至少包含正常与对抗样本；对抗样本覆盖网址、邮箱、小数、法规条号、金额、已有页眉页脚、奇偶页、分页符、批注、图片、文本框、复杂合并表格、缺字体、损坏 OOXML、超大压缩比和办公套件转换失败。只有 OOXML 结构检查与渲染 PDF 视觉复核同时通过，才允许标记排版结果“可导出，仍需人工终审”。

## 6. 提前吸收的上游教训与不变门禁

| 上游公开证据 | PartyOps 在 rc.2 前锁定的门禁 |
| --- | --- |
| README `v1.8.8` 专门补充页脚保护、中文禁则、标题字体细分和图片附件保护 | 页脚 PAGE 域与原页脚共存；正文/表格/页眉页脚的中文禁则；媒体关系和合并单元格保真 |
| 公开测试目录单列 `detect_para_type`、`split_heading`、`east_asian_typography`、`media_attachment`、`page_number_customization`、`style_reset`、`table_cell_margins` | PartyOps 不复用其实现，但建立同维度、不同样本和自有断言的回归；来源记录只写公开能力名称，不写上游算法 |
| Issues #18、#19、#25、#31 分别暴露文档网格、页码、段间距/缩进、符号字体问题 | 这些项进入阻断式 OOXML 检查；标点替换后仍继承当前公文角色字体，不创建默认字体新 run |
| Issues #17、#20、#22、#23、#26、#29 暴露 macOS、Win7、32 位、高 DPI 和拖拽启动风险 | 文档引擎测试与冻结程序启动测试分开；未通过对应原生系统的包保持 `unavailable`，不因算法测试通过就标记支持 |

研究链接：[项目首页](https://github.com/KaguraNanaga/docformat-gui)、[公开测试目录](https://github.com/KaguraNanaga/docformat-gui/tree/main/tests)、[公开问题清单](https://github.com/KaguraNanaga/docformat-gui/issues)、[当前许可证](https://github.com/KaguraNanaga/docformat-gui/blob/main/LICENSE)。
