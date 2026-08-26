# PartyOps 1.4.5-rc.4 最终验收记录

最后验证日期：2026-08-26（北京时间，UTC+8）

## 1. 冻结对象

- 分支：`release/1.4.5-rc.4`
- 修复提交：`7f776ecf58a311b6781bf5dc86f659332bf83d01`
- 文档冻结提交：`2f61d50c51294f3dc5ca2b8d4d8eb452ea8f8a43`
- 发布标签：`v1.4.5-rc.4`（GitHub 预发布，标签不可变）
- 数据库迁移：`0024_ledger_imports_and_progress`
- 签名信任根指纹：`7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3`

## 2. 根因与修复

rc.3 在旧数据库仍为 `0023` 时，迁移前先通过 `0024` ORM 的 `BackupRun` 模型写入备份，访问旧库不存在的列，导致 Windows、Linux、macOS 共用启动链在迁移前抛出 `SQLITE_RUNTIME_FAILED`。数据库 `quick_check` 正常，故障不是 SQLite 文件损坏。

rc.4 将升级前备份改为模式无关的 SQLite 在线备份，并把备份校验、迁移、迁移后不变量检查、失败恢复和状态登记纳入受控事务。迁移完成前禁止使用新模型写旧库。新增 `UPGRADE_BACKUP_FAILED`、`DATABASE_SCHEMA_FAILED`，保留 `SQLITE_RUNTIME_FAILED` 仅表示驱动、版本或 FTS5 能力故障。

Windows 旧版卸载后残留的 `PartyOps 主机服务` 也已修复：只按官方 rc.1/rc.2/已撤回 rc.3 可验证 EXE 哈希，或按完整 PartyOps 服务元数据受控接管；未知同名服务仍拒绝修改，避免误杀其他软件。

## 3. 自动测试与安全门禁

| 门禁 | 结果 | 证据 |
| --- | --- | --- |
| 后端 | 通过 | 1420 tests；行 95.775978%，分支 92.786805%，语句 96.681457% |
| 前端 | 通过 | 216 tests；行 96.45%，分支 93.19% |
| 官网 | 通过 | 86 tests；行 98.82%，分支 93.06%；EdgeOne 专项 9/9 |
| 迁移回归 | 通过 | 真实 `0023 → 0024` 覆盖升级、备份恢复、WAL/SHM 清理、管理员和健康不变量 |
| 安装启动 | 通过 | Windows 新装/升级、Win7 x64/x86 包门禁、Linux amd64/ARM64 QEMU、macOS 双架构原生 Darwin |
| 安全扫描 | 通过 | Ruff、类型检查、Gitleaks、Bandit、依赖审计、SBOM/VEX；高危漏洞 0 |
| Needle 2 | 通过 | 正式 Ed25519 签名、Windows AMD64 导入/启用/离线推理/预览/停用/卸载 |

Windows、Linux、macOS 的实际制品均执行了全新安装和覆盖升级路径；Win7、Linux ARM64 目标真机和 macOS 用户设备仍按支持矩阵标记为 `preview`，不冒充 `stable`。

## 4. 制品与公网回读

- Cloud Studio 固化 `/downloads/` 流程：19 个安装包、更新包和 Needle 包全部上传并从公开地址回读到 EOF，长度、SHA-256、文件头、版本和架构一致。证据：`artifacts/cloudstudio-readback-1.4.5-rc.4.json`（`verified=true`，`asset_count=19`，15:38:49）。
- GitHub Release：61 个资产逐项验证摘要和大小一致，预发布地址为 [v1.4.5-rc.4](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.5-rc.4)。证据：`artifacts/github-release-view-1.4.5-rc.4.json`。
- 官网正式域名 `https://partyops.cn` 已切换 rc.4；根清单、更新清单和模型清单均返回真实 JSON（HTTP 200，不回退 SPA HTML），二维码、rc.4 更新诊断截图和 WebP 缩略图均 HTTP 200。

主要部署记录：

- EdgeOne Preview（首轮）：`dpy0payiambi`；补拍 rc.4 实景后 Preview：`dpys59kia3cr`。
- EdgeOne Production：`dpusd04ko3s8`。
- Preview 域名被当前 Chrome 扩展的客户端规则拦截，已使用公开回读验证静态资源；Production 通过 Chrome 完成桌面端、移动端、赞助弹层和控制台无错误验收。

## 5. 官网验收

- 首页版本、更新撤回说明、涉密提醒、安装指引和九个平台下载选项均显示 rc.4。
- “今日工作台”为 Hero 和画廊第一张；实景画廊保留 37 个稳定功能条目，分类数量为今日 2、工作 9、党务 5、资料 7、协同 4、管理 10。
- 系统更新实景已由 Chrome 打开 rc.4 原生运行时真实拍摄，画面中显示 `应用版本 1.4.5-rc.4`、数据库模式 `0024` 和健康状态正常；文件为 `website/public/screenshots/partyops-update-diagnostics-1.4.5-rc.4.png`。
- 赞助弹层支持微信、支付宝、爱发电；爱发电使用用户提供的二维码图片 `/support/afdian-partyops-support-20260825.jpg`，无 iframe，移动端为底部弹层。
- Chrome 控制台错误/警告为 0；390×844 移动视口下版本、涉密提醒、37 张画廊、赞助入口和安装提示词均可见。

## 6. 回滚与限制

- PartyOps 回滚点：rc.3 GitHub Release 保留“已撤回”审计记录；官网 Production 上一稳定部署为 `dprnxy3qyn05`。任一制品或官网门禁失败时，回到该部署和 rc.2/上一生产清单，不覆盖历史标签。
- Windows/macOS 未商业签名；macOS 为 ad-hoc 签名且未公证，SmartScreen/Gatekeeper 提示不能由程序逻辑消除，官网保留 SHA-256 和安全放行说明。
- Linux ARM64 通过 QEMU 动态门禁，尚无对应国产桌面真机 GUI 证据；Needle 2 仅验证 Windows AMD64，其他平台不提供伪通用包。
- 当前会话没有可调用的 Cloud Studio 删除接口，因此没有对历史 rc.3 审计应用做不可逆删除；官网和在线更新已不再引用 rc.3，历史 GitHub Release 保留审计。

## 7. 证据索引

- `docs/upgrade-1.4.5-rc.4.md`
- `docs/support-matrix-1.4.5-rc.4.json`
- `artifacts/cloudstudio-readback-1.4.5-rc.4.json`
- `artifacts/cloudstudio-upload-results-1.4.5-rc.4.json`
- `artifacts/github-release-view-1.4.5-rc.4.json`
- `website/public/release-manifest.json`
- `website/public/releases/update-v3.json`
- `website/public/releases/model-catalog-v1.json`
