# PartyOps 1.4.5-rc.6 公文排版公开产品基准记录

日期：2026-08-27（北京时间）  
范围：仅研究公开网页描述和可观察的产品流程，不逆向二进制，不复制非公开代码、模板、素材或算法。

## 调研记录

- 关键词：`思享排版助手 公文 排版 功能 DOC WPS 国产系统`、`思享排版助手 使用说明`。
- 公开来源：[思享排版助手官网](https://sxpaiban.cn/)、[安装和使用说明](https://sxpaiban.cn/pages/sysm.html)、[更新日志](https://sxpaiban.cn/pages/gxrz.html)。
- 库行为来源：[python-docx 页眉页脚官方文档](https://python-docx.readthedocs.io/en/latest/user/hdrftr.html)及其 section 实现说明；多节文档的页眉页脚可能继承前一节，启用奇偶页后默认页脚只服务奇数页，因此 PartyOps 必须按节关系分别检查默认/偶数页脚，不能只检查一个 footer XML。
- 采用依据：官网明确展示的要素识别、一键排版、选区排版、图片和表格保护、页码/网格参数、输出转换、进度与失败原因提示。
- 标准依据仍只使用 GB/T 9704-2012；竞品参数不能替代国家标准，也不能作为“合规”结论来源。

## 开源项目许可与能力审查

访问日期：2026-08-27。以下结论来自各项目当日公开的 GitHub 仓库、README 和
LICENSE；只把公开实现作为对抗样例和产品行为参考。PartyOps 本轮没有复制、导入或
改写这些项目的源码、提示词、模板和素材。

| 项目 | 当日许可证据 | 值得吸收的工程思想 | PartyOps 处理 |
| --- | --- | --- | --- |
| [document-format-skills](https://github.com/KaguraNanaga/document-format-skills) | 仓库含 MIT LICENSE | 分离诊断/标点/排版/转换；页码保护；macOS 字体回退；修订痕迹 | 仅对照行为与对抗样例；PartyOps 保持单一国标预设 |
| [gov-doc-formatter](https://github.com/Drenches/gov-doc-formatter) | 当日仓库未发现 LICENSE | Router、Cleaner、Marker、Validator 分阶段校验与有限重试 | 不复用源码；只采用“模型提候选、校验器否决”的流程思想，拒绝把文档发送外部 API |
| [gongwen](https://github.com/hehecat/gongwen) | README 声明 MIT，但当日仓库根目录未发现 LICENSE 文件 | 文档 AST、A4 实时分页预览、版头/版记分层 | 许可澄清前不复用源码；不采用 localStorage 保存公文内容 |
| [GongWenGuiFan](https://github.com/ukiyo99/GongWenGuiFan) | MIT LICENSE | 本地单文件、格式体检、序号校正、人工分页、导出后目标软件复核 | 借鉴“机器检查不代替人工终审”的产品门禁；不保存历史公文 |
| [official-document-drafting](https://github.com/zhaohui-yang/official-document-drafting) | MIT LICENSE | 事实与建议分离、文种边界、来源核验、模型稿必须人工审定 | 纳入内置模型系统提示和验收规则；不复制提示词或文种模板 |
| [Word-Formatter-Pro](https://github.com/cwyalpha/Word-Formatter-Pro) | MIT LICENSE | 保护图片、域、书签和批注引用；核心/CLI/界面解耦；跨平台转换降级 | 增加特殊 OOXML 部件不变性测试；转换失败不影响其他文档且必须给出原因 |
| [gongwenpaiban](https://github.com/zmrblog/gongwenpaiban) | Apache-2.0 LICENSE | 纯文本结构解析、标题/附件/版记要素、自动化测试样例 | 仅作为规则覆盖清单；任何项目自称的默认参数仍需回到国家标准原文核验 |

### 内置模型协作边界

1. DeepSeek 主编排模型只能针对排版引擎输出的脱敏问题码、候选角色、置信度和标准
   条款生成复核建议，不接触原文件、文件路径、完整正文或临时副本。
2. Needle 2 先拦截提示注入、否定执行和越权意图；BGE 只检索本机版本化规则说明；
   Qwen3 仅在正式验签的 DeepSeek 包不可用时回退。
3. 字体、字号、版心、网格、页码、段落、附件、版记和表格的实际写入始终由确定性
   OOXML 引擎完成；模型无权直接修改 OOXML，也不能把低置信度候选当成事实。
4. 角色存在歧义时必须在诊断页标为“人工确认”；模型意见不一致或缺少标准依据时，
   保留原段落并阻止显示“符合标准”。

这套边界保留了多阶段智能识别的效率，同时避免大模型生成错误参数、虚构公文要素或
把敏感正文发送到外部服务。

## 转化为 PartyOps 自有实现

| 公开产品行为 | PartyOps rc.6 落地 | 边界 |
| --- | --- | --- |
| 识别主标题、副标题、各级标题、正文、附件、落款和日期 | OOXML 上下文角色识别；不确定项进入诊断提示 | 不猜测机关权限、印章或政治事实 |
| 一键处理全文并保护图片、表格 | 页面内“诊断—确认—排版—复检—导出”；未触碰 OOXML 部件逐项保留 | 原文件不覆盖，文档不上传主机 |
| 页边距、网格、行距、页码统一 | 固定 A4、156×225 mm 版心、22×28 网格、28 磅行距；奇偶页均校验一字线页码 | PartyOps 只提供 GB/T 9704-2012 单一预设 |
| 复杂对象和失败原因提示 | 字体缺失、转换损坏、文本框、版头、签发人、特殊版式分别返回错误或复核项 | 校验失败不得显示“符合标准” |
| DOC/WPS 与国产系统 | 随目标架构安装包携带经许可审计的无界面 LibreOffice 运行时 | 未完成原生构建和目标系统验证的平台标记不可用 |

## 本轮实证

- 用户提供的 `公文格式模板.docx` 经 PartyOps 引擎处理 23 处结构和格式，页面、版心、正文和页码结构复检通过。
- 首次逐页渲染发现奇数页缺少左右一字线；已修正旧页脚 PAGE 域规范化，并增加诊断和回归测试。
- 修正后 11 页逐页检查未见裁切、重叠、断页异常或奇偶页码不一致。
- 当前 Windows 验证机缺少方正小标宋简体，系统保留 `REQUIRED_FONT_MISSING` 错误并拒绝给出“符合标准”结论。

## 明确不纳入本轮

- 自定义模板、多套地方参数、一键套红、PDF 转 Word、批量正则替换不属于已锁定的单一公文预设范围；未经独立需求、安全评估和测试不得借竞品名义扩张范围。
- 不采用要求关闭安全软件才能安装的做法；PartyOps 安装和转换运行时必须通过自身签名、依赖闭包和诊断门禁解决兼容性。
