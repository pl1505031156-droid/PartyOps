# PartyOps 开源发布前最终安全与稳定性审查

最后验证日期：2026-08-11（Asia/Hong_Kong）  
审查对象：GitHub 发布仓库完整源码、当前 Git 历史、Windows/UOS 打包脚本与开源文档  
审查方法：第一性原理威胁建模、对抗式代码审查、历史/工作区密钥扫描、依赖与许可证审计、全量自动化测试、真实 Chromium 交互回归

## 上线结论

**NO-GO：当前代码候选不能标注为“稳定正式版”，也不能生成新的正式安装包。**

本轮确认的代码级严重/高危缺陷已经修复，当前工作树未发现硬编码生产密钥，生产依赖审计也没有已知漏洞。但是以下发布证据仍未满足：

1. 后端覆盖率 73.96%、前端覆盖率 5.47%，均低于仓库规定的 90%。
2. Windows 10、UOS amd64、UOS arm64 的主机/协同机真机矩阵尚未完成。
3. 20GB、断线续传、主机重启、传输中撤权与 24 小时连续运行尚无完整验收记录。
4. Windows Authenticode、更新包 Ed25519 正式签名和三平台正式安装制品尚未完成。
5. 当前源码 QA 使用 SQLite 3.50.4；正式冻结运行时仍必须内置并复验 3.51.3 或更高安全版本。

“零漏洞”不能由一次审查永久保证。可以保证的是：本报告不隐瞒未验证项；当前已知问题均有状态、代码证据和发布门禁，任何未关闭门禁都会阻止稳定版发布。

## 对两份 2026-08-10 报告的真实性复核

| 原报告主张 | 判定 | 当前证据与处理 |
| --- | --- | --- |
| 三份现场修复文档泄露真实机器标识与部署路径 | **属实** | 当前树已删除；Git 历史也必须重写后才能解除泄密门禁，不能只用删除提交掩盖旧 Blob。 |
| 内部产品规划 DOCX 与本机绝对路径脚本不宜公开 | **属实** | DOCX 与机器专用脚本已删除，公开部署文档改用通用路径。 |
| 演示账号存在固定公开口令 | **属实** | `backend/app/seed.py:162-170` 改为随机口令；正式环境默认不播种演示账号。测试中的固定口令仅存在于隔离测试夹具。 |
| 第三方许可证与 SBOM 不完整 | **属实** | `THIRD_PARTY_NOTICES.md` 已补 Python/前端直接依赖、PyMuPDF AGPL 说明；两份 CycloneDX 1.6 SBOM 已生成。 |
| 默认 development 且更新/模型包可信任包内自带公钥 | **属实** | `backend/app/config.py:40` 默认 production；更新与模型包只信任部署端预置公钥，包内 `public_key` 不再形成信任根。 |
| HTTPS 默认关闭即可直接对局域网提供生产服务 | **部分属实** | loopback 可使用 HTTP 便于本机引导；生产模式对非 loopback 监听执行 fail-secure 校验，不满足 TLS 时拒绝启动。 |
| 历史 P0/P1 在 1.4.2 “全部仍存在” | **不属实/结论过时** | 扫描分批、恢复维护闸门、调度器线程卸载、`.part` 清理、终稿锁、协办治理、心跳合并、保留策略等在基线中已经实现。第二份报告对此判断更准确。 |
| SVG 可被 API 直接 inline | **属实** | `backend/app/content_security.py:17` 与两个内容出口统一只允许 PDF/安全位图 inline；SVG/HTML 强制下载。 |
| 备份导入可无限上传或 ZIP 炸弹耗尽资源 | **属实** | `backend/app/routers/admin.py:86,468-496` 限制上传；`backend/app/backups.py:72-101` 限制成员数、展开体积、压缩比、重复路径、符号链接和路径穿越。 |
| AI Provider 可访问任意内网/云元数据地址 | **属实** | `backend/app/ai_service.py:78` 对 scheme、用户信息、端口、DNS 全部结果、回环/链路本地/保留地址做校验；私网必须显式标记可信内网。 |
| Agent 与更新执行器静默失败 | **属实** | `backend/app/client_agent.py:74-91` 记录轮转日志并对 401/403 标记重新入网；更新 DB 异常采用有上限指数退避。 |
| 后端仍有一个 AI 状态测试失败 | **已过时** | 当前全量为 213 passed，0 failed。 |
| cryptography 49.0.0 存在已知 CVE | **当时属实、当前已修复** | 已锁定 50.0.0；`pip-audit` 当前为 0 个已知漏洞。 |
| 前端/后端覆盖率未达到 90% | **属实且仍未关闭** | 2026-08-11 实测后端 73.96%、前端 5.47%，继续作为高优先级发布阻断。 |

## 问题清单

### 严重

| 编号 | 位置 | 风险 | 修复与状态 |
| --- | --- | --- | --- |
| SEC-001 | Git 历史中的三份现场修复文档、内部规划 DOCX、机器路径脚本 | 删除当前文件并不能删除 GitHub 已公开 Blob；真实机器标识、目录拓扑和开发机路径仍可被历史检索。 | 从所有分支和标签中移除指定路径，重写后重新运行历史 gitleaks 与定向路径检索；**发布前必须关闭**。 |

### 高

| 编号 | 位置 | 风险 | 修复与状态 |
| --- | --- | --- | --- |
| SEC-002 | `backend/app/routers/updates.py:196-203`、`backend/app/model_packs.py:76-89`、`backend/app/update_executor.py:171-199` | 若包内公钥可以自行成为信任根，攻击者可用自己的密钥签名恶意更新包；模型包还可能包含可执行推理运行时，影响更大。 | 只读取部署端预置公钥，导入时移除清单中的签名/公钥字段；**已修复并有签名回归**。 |
| SEC-003 | `backend/app/seed.py:162-170` | 固定演示口令会被代码搜索索引，并在误开演示模式时形成通用后门。 | 每次随机生成不可预测口令，管理员必须显式重置；生产默认关闭 seed；**已修复**。 |
| SEC-004 | `backend/app/routers/admin.py:86,468-496`、`backend/app/backups.py:72-101` | 超大上传、ZIP 炸弹、符号链接、重复成员和路径穿越可耗尽磁盘/内存或覆盖目标目录。 | 分块上传上限、成员/展开/压缩比上限、严格清单与哈希、路径和类型校验；**已修复**。 |
| SEC-005 | `backend/app/device_versions.py:96-150` | 按局域网 IP 推断设备会被 NAT、DHCP 复用或同机代理绕过，造成设备能力越权。 | 业务接口只接受签名设备 Cookie；旧 IP 回退仅保留在只读升级门禁；**已修复**。 |
| SEC-006 | `backend/app/device_versions.py:70-127`、`backend/app/main.py:312-340`、`backend/app/routers/fleet.py:1282-1301` | 30 天设备令牌若直接出现在 URL，会进入历史、代理和访问日志。 | URL 只携带 60 秒 `launch` 票据，换取独立 30 天 HttpOnly `context`；用途不可混用，响应 `no-store/no-referrer`；**已修复**。 |
| SEC-007 | `packaging/windows/PartyOps.iss:28` | `%PROGRAMDATA%\PartyOps` 若允许普通用户修改，可能篡改数据库、TLS 材料、更新状态或服务配置。 | ACL 收敛为 Administrators 与 SYSTEM 完全控制；**已修复并有打包脚本回归**。 |
| REL-001 | `backend/pyproject.toml`、前端 Vitest 配置 | 覆盖率门禁虽然存在，但后端 73.96%、前端 5.47%；关键异常分支可能在重构后无报警。 | 补足服务层、文件传输、更新执行器、Agent、核心 Vue 页面和浏览器组件测试；**未关闭，阻断正式发布**。 |
| REL-002 | `docs/release-readiness-1.4.2.md` | 目标平台、20GB、断线/重启/撤权和 24 小时长稳没有证据，无法外推到外部环境。 | 必须在规定真机矩阵完成并保存哈希、日志、恢复和资源曲线；**未关闭，阻断正式发布**。 |
| REL-003 | 正式安装/更新制品 | 未完成 Authenticode、隔离 Ed25519 签名和平台级回滚，供应链与升级恢复不能验收。 | 只在隔离发布环境签名，执行安装、升级、回滚和备份恢复；**未关闭，阻断正式发布**。 |

### 中

| 编号 | 位置 | 风险 | 修复与状态 |
| --- | --- | --- | --- |
| SEC-008 | `backend/app/intake.py:17,150-185` | Office Open XML 内嵌 DTD/实体可能触发实体扩展或外部实体风险。 | 使用 defusedxml，并在解析前拒绝 DTD/ENTITY；**已修复**。 |
| SEC-009 | `backend/app/content_security.py:17`、`routers/workspace.py:934`、`routers/fleet.py:1734` | SVG/HTML 在同源 inline 打开可形成存储型脚本执行面。 | 统一内容策略只允许 PDF 与受支持位图 inline；**已修复**。 |
| SEC-010 | `backend/app/ai_service.py:78-151,369` | AI Provider SSRF 可访问主机回环、链路本地、保留网段或 DNS 混合解析目标。 | 配置与每次请求前复核全部解析地址，公网只允许 HTTPS，可信私网必须显式授权；**已修复**。 |
| SEC-011 | `backend/app/spreadsheet_security.py:9-24`、两个导出模块 | 事项、档案标题若以 `= + - @` 等开头，Excel/WPS 打开导出表时可能执行公式。 | 所有不可信单元格统一转义为文字；**已修复**。 |
| SEC-012 | `backend/app/main.py:170-224` | 缺少 Origin 校验与浏览器响应策略会扩大 CSRF、点击劫持和内容注入影响。 | Cookie 写请求校验 Origin；增加 CSP、frame deny、nosniff、权限策略、COOP/CORP 和 TLS 下 HSTS；**已修复，真实恶意 Origin 返回 403**。 |
| STAB-001 | `backend/app/client_agent.py:74-91`、更新执行器循环 | 认证失效与数据库失败若静默吞没，会造成协同机长期假在线或守护进程空转。 | 轮转日志、重新入网状态、有上限指数退避；**已修复**。 |
| STAB-002 | `backend/app/intake.py:38-40,200-240,300` | 超页数 PDF、超像素 OCR 或同步 CPU 解析会耗尽内存并阻塞 API。 | 500 页 PDF、20 页 OCR、像素限制、OCR 超时、并发信号量和 `asyncio.to_thread`；**已修复**。 |
| STAB-003 | `backend/app/setup_wizard.py:477-533` | 首次健康探测面对未信任自签名证书；若同时发送配对头，伪主机可窃取凭据。 | 健康探测只读取公开状态并明确忽略旧 token 参数；真正入网使用入网码内 CA 指纹固定；**已修复**。 |
| REL-004 | 当前源码 Python/SQLite 环境 | SQLite 3.50.4 低于项目安全冻结版本，源码 QA 不能替代正式冻结运行时。 | 正式构建内置 3.51.3 或更高并开启严格检查；**未关闭**。 |

### 低

| 编号 | 位置 | 风险 | 修复与状态 |
| --- | --- | --- | --- |
| UX-001 | `frontend/src/views/WorkspaceView.vue:914-925` | 文件操作按钮视觉有文字但缺可访问名称，读屏与稳定 UI 自动化只能看到空按钮。 | 为预览、下载、转发、关联、固化等补 `aria-label`；**已修复并用 Chromium 复验**。 |
| DEV-001 | 旧机器专用构建脚本/部署文档 | 开发者用户名和绝对路径造成隐私暴露且使外部贡献者无法复现。 | 删除机器专用脚本，文档改为变量和相对路径；**当前树已修复，历史与 SEC-001 一并清理**。 |
| OPS-001 | `.github/` 无远端 workflow | 外部 PR 不会自动获得 GitHub 侧测试反馈，维护成本和误合并风险较高；它本身不是运行时漏洞。 | 当前治理禁止远端 CI/CD，保留 `scripts/test.ps1` 本地硬门禁并在 PR 中附结果；若治理改变，再单独评审只读 CI。**已接受的治理限制**。 |

## 功能与运行态验证结果

| 验证项 | 2026-08-11 结果 | 判定 |
| --- | --- | --- |
| 后端全量 pytest | 213 passed，0 failed | 通过 |
| 后端分支覆盖 | 73.96%，仓库阈值 90% | **失败/阻断** |
| 前端 Vitest | 9 文件、41 passed | 通过 |
| 前端覆盖率 | 行/语句 5.47%，函数 55.55%，分支 71.62%，阈值 90% | **失败/阻断** |
| Sites 静态入口 | 4 passed | 通过 |
| Vue 类型检查 / Vite 生产构建 | 通过；存在 `vendor-ui` 788.15 kB 体积告警 | 功能通过，性能持续优化 |
| Python 依赖一致性 / CVE | `pip check` 通过；`pip-audit` 0 个已知漏洞 | 通过 |
| 前端生产依赖 CVE | `pnpm audit --prod` 0 个已知漏洞 | 通过 |
| Bandit | 0 High；剩余 Medium 为受固定指纹保护的首次 CA 获取、限定 scheme 的 urllib 调用和 `0.0.0.0` 防御性比较 | 无高危；人工复核通过 |
| gitleaks | 当前树与 Git 历史扫描通过配置门禁；定向隐私路径仍需历史重写 | 条件通过 |
| Chromium 真实回归 | 登录、主界面、目录纳管、59 文件索引、结构化阅读、浏览器下载、响应头与恶意 Origin 拒绝通过 | 通过（Windows 11 单机源码环境） |

浏览器下载的运行日志形成真实 `POST /workspace/downloads = 201` 与内容读取 `GET = 200`，不是仅检查按钮可见。重新加载生产构建后无新增控制台错误；登录页最初的 401 是匿名探测 `/auth/me` 的预期鉴权响应，不是未处理异常。

## 依赖、许可证与隐私

- 当前源代码许可证为 GPL-3.0；PyMuPDF 以 AGPL-3.0 使用，组合与对应源代码义务已在 `THIRD_PARTY_NOTICES.md` 明确说明。
- Python 与前端 CycloneDX 1.6 SBOM 位于 `docs/sbom-python.cdx.json`、`docs/sbom-frontend.cdx.json`。
- gitleaks 配置只排除 `.test-data` 测试 PKI、依赖、构建和运行产物；公开源码、文档与 Git 历史仍纳入扫描。
- 没有把更新私钥、模型包私钥、TLS 私钥或设备私钥加入仓库；发布私钥必须只存在于隔离签名环境。
- 日志、错误页和运行状态不应公开绝对业务路径、SQL 细节、令牌或正文；完整异常只写本机受限诊断日志并返回追踪号。

## 正式放行条件

只有以下条件全部满足，才能把结论改为 GO：

1. GitHub 远端所有相关分支和标签完成隐私历史重写，并重新验证旧 Blob 不可达。
2. 后端与前端全仓覆盖率达到 90%，不允许通过排除核心源码或降低阈值绕过。
3. Windows 10/11、UOS amd64/arm64 的主机和协同机矩阵全部通过。
4. 20GB、断点续传、主机重启、哈希失败、设备离线、传输中撤权和 24 小时长稳全部通过。
5. 三平台安装包、统一更新包、SHA-256、SBOM、许可证、签名、迁移与回滚证据齐全。
6. 由不同于实现者的发布审核人复核本报告、制品哈希和签名公钥指纹。

在这些条件完成前，允许继续发布明确标注的源代码候选和安全修复提交，但不得把候选安装包改名为稳定正式版，也不得承诺“无任何功能或安全问题”。

## 审计可复现命令

```powershell
& .\scripts\scan-secrets.ps1
& .\scripts\test.ps1
python -m bandit -r backend\app packaging\windows -x backend\.test-data -ll
python -m pip_audit -r backend\requirements-release.txt
corepack pnpm --dir frontend audit --prod --audit-level high
```

本轮在 Windows 上使用安全 PowerShell、`rg`、pytest、Vitest、Bandit、gitleaks 与真实 Playwright CLI；未使用不可用的 Sequential Thinking、Context7/Fetch 或远端 CI。Playwright 的 POSIX 包装脚本不适用于当前 Windows 终端，因此使用同一官方 CLI 的 `npx` 入口，影响仅为启动方式，不改变浏览器内核和观察结果。
