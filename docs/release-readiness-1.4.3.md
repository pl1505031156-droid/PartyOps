# PartyOps 1.4.3-rc.9 发布就绪判定

最后本地核对：2026-08-20（北京时间，UTC+8）。

当前结论：九个主安装包已冻结并通过本地/原生构建门禁；业务修复与跨平台启动修复已完成。Windows 7、国产 Linux 与 macOS 仍缺少用户真机交互验收，Windows/macOS 未获商业签名，macOS 未公证，因此 rc.9 继续作为明确标注限制的候选版。

## 已通过

1. Windows 10/11、Win7 x64/x86、DEB/RPM 双架构和 macOS Apple Silicon/Intel 九个安装包的名称、长度与 SHA-256 已冻结，旁路 `.sha256` 一致。
2. 后端从零执行全量回归：行覆盖率 `95.14%`、分支覆盖率 `90.00%`；新增 macOS 更新事务、Linux/麒麟桌面启动、端口身份、服务所有权和 CLI 特权路由对抗测试。
3. 前端 180 项测试、类型检查、静态检查和生产构建通过，行覆盖率 `97.10%`、分支覆盖率 `90.00%`；一事一档回退、目录操作与 DOCX 导出均有回归。
4. 官网 40 项测试与生产依赖审计通过，行覆盖率 `98.51%`、分支覆盖率 `94.15%`；Gatekeeper 常见问题明确给出 Control 点按和“隐私与安全性 → 仍要打开”，禁止关闭系统安全机制。
5. Win7 x64/x86 对最终 PE、UCRT、API-set、Python 3.8 与 OpenSSL 做成品门禁；安装器在每台目标电脑继续真实启动最终 `PartyOps.exe`，核验 RSA/Fernet、SQLite/FTS5、数据库迁移、同版本健康端点和首页。rc.8 `CHILD_EXITED` 反馈缺少 `launcher.log`，因此不宣称已还原唯一异常栈，但同类提前退出不再能形成“安装成功”的 rc.9 安装。
6. Linux 四个原生包在 glibc 2.17 x86_64 与 QEMU aarch64 环境完成固定运行时、SQLite 3.51.3/FTS5、OCR、AI、前端、桌面入口、权限与启动事务动态门禁。
7. macOS 两个 PKG 分别由 `macos-15` Apple Silicon 和 `macos-15-intel` 原生 Darwin 主机构建，执行 Bundle/嵌套代码、安装、`open -na` LaunchServices、启动探针日志和版本回读门禁。
8. 生产 Python、前端与官网依赖审计未发现已知漏洞；最终秘密扫描、SAST、机器可读发布清单和线上回读在发布动作前再次执行并记录。

## 发布顺序与阻断条件

1. 提交并推送最终源码与文档，冻结不可变标签 `v1.4.3-rc.9`。
2. 生成机器可读清单，并逐项复算九个安装包；离线 Ed25519 私钥未挂载时不生成 `.partyops-update` 或更新目录，不以临时密钥代替。
3. 上传 Cloud Studio 受控 `/downloads/` 后逐文件核对 HTTP 状态、长度和完整 SHA-256；不使用 WorkBuddy 上传。
4. 创建 GitHub 普通 Release，上传同一组冻结资产并回读；历史 Release 保留审计，不删除。
5. 部署 EdgeOne Makers 项目 `makers-gjuf8qcecmi3`，再核验官网、HTTPS、下载、更新日志、常见问题、版本化清单及移动端。

任一资产出现哈希不一致、静态资源缺失、依赖闭包不完整或安装后门禁失败时，只阻断该资产，不用警告替代失败。

## 公开限制与回滚

Windows 7、国产 Linux 与 macOS 缺少用户真机交互验收，不宣称已获真实使用验证。Win7 `CHILD_EXITED` 的反馈未附原始启动日志，rc.9 按可预见故障类别增加目标机门禁后继续发布；后续日志若证明存在新的独立原因，在下一候选版定点修复。Windows 安装器没有 Authenticode 商业证书，可能触发 SmartScreen。macOS PKG 只有 ad-hoc 签名，没有 Developer ID Application/Installer 与 Apple 公证；核对哈希后按官网“仍要打开”步骤操作，不得关闭 Gatekeeper。永久消除提示需要正式证书、公证与 staple。

Windows/Linux/macOS 更新代码均使用升级前快照、真实健康检查和失败回滚；但新的在线更新包必须由现有离线 Ed25519 私钥签发。官网可回滚 EdgeOne 页面部署，GitHub 历史 Release 用于审计与应急取回，官网只展示最新 rc.9 主安装包。
