# PartyOps 1.4.2 候选验收记录

最后验证日期：2026-08-11（Asia/Hong_Kong）
结论：**代码候选通过，正式发布 NO-GO**

## 输入与范围

- 基线：当前 1.4.1 候选工作区及数据库模式 0017。
- 上游：`@firecrawl/anydoc-wasm@0.1.7`、`@firecrawl/pdf-inspector-wasm@0.1.3`、`markdown-it@15.0.0`。
- 环境：Windows 11 x64、本机主机服务、一台真实运行的协同 Agent、Chromium/Playwright。
- 未覆盖：Windows 10 真机、UOS V20 amd64/arm64 真机、20GB、24 小时、正式签名与生产恢复演练。

## 验证结果

| 验证项 | 结果 | 证据/说明 |
| --- | --- | --- |
| 后端全量 | 213 passed | 覆盖协同文件、权限撤销、分块、显式 finalize、扫描并发、开源加固与既有业务 |
| 后端覆盖率 | 73.96% | 2026-08-11 在精确发布副本执行 `pytest --cov=app`；低于 90% 门槛 |
| 前端单元 | 41 passed | 新增文档格式、64 MiB、流式读取、中文错误、安全 Markdown 和分页标记回归 |
| 前端覆盖率 | 行/语句 5.47% | 2026-08-11 再次执行 `vitest --coverage`；低于 90% 门槛 |
| Sites 兼容 | 4 passed | 既有边缘部署兼容测试，仅作构建兼容证据 |
| 类型与构建 | 通过 | `vue-tsc`、Vite；产物含两个 Firecrawl WASM Worker 资源 |
| 浏览器端到端 | 2 passed | 主机 DOCX/PDF/CSV；协同机 DOCX/PDF 经真实 Agent、分块、哈希、finalize 后阅读 |
| 依赖审计 | 通过 | pnpm 与 Python 生产依赖均无已知漏洞；cryptography 已升级到 50.0.0 |
| Windows 候选安装 | 通过（Windows 11 x64） | 1.4.2 EXE 冻结冒烟、静默安装/卸载通过；应用 1.4.2、SQLite 3.53.3、迁移 0017；未做 Authenticode 正式签名 |
| 正式平台安装 | 未完成 | Windows 10、UOS 双架构、正式 EXE/DEB/统一更新包与签名未完成 |
| Chrome 账号矩阵再检 | 通过（当前 Windows 11 源码环境） | 管理员和普通协同账号；普通账号管理导航被隐藏，直接访问管理员路由进入明确无权限页 |
| 源码运行时 SQLite | 不通过 | 3.50.4，`safe_version=false`；正式冻结运行时需内置并复验 3.53.3 或更高安全版本 |

## 本轮证实并修复的问题

1. 已完成的远端传输在目录撤权后仍可读取：增加最终来源根、设备、用户和共享权限复核。
2. 最后分块到达即自动完成，Agent 尚未核验源 inode/时间/大小：改为 `transferring`，由 Agent 核验后显式 finalize。
3. 自动扫描与“立即同步”并发触发唯一约束：同一共享根串行扫描。
4. 新索引未 flush 导致文件/目录计数为零：计数前刷新，并只统计有效范围。
5. PDF/Office 只能下载、不能阅读：加入一次性 Web Worker 的结构化阅读与原始预览双通道。

## 浏览器证据

- [主机 Office 结构化阅读](images/file-center-office-preview.png)
- [PDF 结构化与原始预览入口](images/file-center-pdf-preview.png)
- [协同机文件真实中转阅读](images/cross-device-office-preview.png)

浏览器用例断言无未处理 `console.error`、`pageerror` 和业务 `HTTP >= 400` 响应。协同机用例不是预置缓存：测试三次触发 `/workspace/downloads`，Agent 领取设备命令、上传分块、完成哈希和 finalize，随后浏览器读取传输内容并由 WASM 解析；“浏览器另存为”得到的 DOCX 文件名正确，下载字节与协同机源文件逐字节相等。

## 边界

“实时预览”表示用户点击后系统即时按需取得当前授权的内容并打开阅读视图。远端文件必须先完成受控中转和哈希校验；PartyOps 不直接挂载另一台电脑的路径，也不承诺未传完时逐页流式解析。64 MiB 以上结构化文档降级到原始预览或下载，单次传输总上限仍为 20GB。

## 判定

文件中心的跨机查看、阅读、浏览器下载和当前设备接收闭环已在 Windows 11 源码环境中验证。由于覆盖率和目标平台/容量/长稳/签名门禁未关闭，不能据此发布“稳定正式版”或承诺所有环境没有问题。

## 2026-08-11 开源安全回归补充

- 更新包/模型包只信任部署端公钥；固定演示口令、备份解压炸弹、SVG inline、AI SSRF、设备 IP 回退越权和 Windows ProgramData 宽 ACL 已修复。
- 设备浏览器入口改为 60 秒 URL 启动票据换取长期 HttpOnly 上下文，两个令牌用途不可混用。
- Chromium 真实操作完成主机目录纳管、59 个文件索引、Markdown 结构化阅读和浏览器下载，服务端记录 `POST /workspace/downloads = 201`、内容读取 `GET = 200`。
- 生产构建重新加载后无新增控制台错误；恶意跨站 Origin 的 Cookie 写请求返回 403。
- 完整分级问题和未关闭门禁见[最终开源安全审查](open-source-final-security-audit-2026-08-11.md)。
