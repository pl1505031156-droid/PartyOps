# PartyOps 1.4.3-rc.6 候选验收记录

最后更新：2026-08-18 20:05（北京时间，UTC+8）。结论：**七个平台安装包与七个签名更新包已完成本地冻结门禁，可以进入 CloudStudio、GitHub 与 EdgeOne 逐级发布核验。rc.6 仍为未签名候选版。**

## 本地验收结果

- 最终制品统一对应源码提交 `0ad3e7366d6d16e81af9495ffb10ec9d2deeb156`，应用、Python 项目、Windows 安装器、Linux 包、更新包和官网显示版本均为 `1.4.3-rc.6`。
- 后端完整套件 912 项通过；追加三条真实分支探针后，后端行覆盖率 95.15%、分支覆盖率 90.01%。前端类型检查、覆盖率、静态入口测试与生产构建通过；生产依赖审计为 0 个高危漏洞。
- Windows 10/11 最终安装器在当前 Win11 对非 C 盘中文/空格目录 `E:\党建智办 PartyOps rc6 安装测试` 完成 rc.5→rc.6 覆盖安装。`PartyOpsHost` 与 `PartyOpsUpdateService` 均为“正在运行/自动”，健康端点返回 `version=1.4.3-rc.6`、`mode=host`、SQLite 3.53.4，原数据保留。
- 安装目录校验脚本以 UTF-8 BOM 封装，并从最终安装器重新提取后由 Windows PowerShell 5.1 在中文/空格路径执行成功。标准输出、错误输出和退出码全部进入 Inno 安装日志，解决受影响电脑的脚本解析型 `INSTALL_DIR_CHECK_FAILED`。
- Win7 x64/x86 使用隔离的 Python 3.8 Legacy 运行时；Tcl/Tk、SQLite/OCR、PE 子系统和导入 API 静态门禁通过，分别检查 262/158 个二进制。最终载荷拒绝导入 `api-ms-win-core-path-*`；两个冻结包均在当前 Win11 启动并通过健康端点，x86 明确使用不含语义重排和本地 LLM 的 `legacy-core` 配置。
- AMD64 的 DEB/RPM 均从最终安装包本体解开，在对应 Linux 用户态执行完整包自检、个人模式服务、健康端点与桌面启动器回归。ARM64 的 DEB/RPM 均从最终安装包本体解开，在 AArch64/glibc 2.17 用户态执行相同动态回归；完整智能运行时、中文 OCR、SQLite/FTS5 和页面启动均通过。
- 四个 Linux 原生包没有可执行共享库，`libgcc_s.so.1` 为普通数据权限；systemd 连续失败重试上限为 3，关闭麒麟安全中心无限弹窗链路。双架构严格依赖闭包不存在重复 `cryptography`，也不允许缺失 ARM64 智能运行时后降级出包。
- 七个 `.partyops-update` 均通过 Ed25519 签名、版本、平台矩阵和载荷哈希验证；format v3 更新目录覆盖 `windows`、`windows7`、`linux-deb`、`linux-rpm` 及对应架构。
- `pip check`、pip-audit、前端/官网生产依赖审计、Bandit 中/高危门禁和 gitleaks 均通过；已知高危/严重生产依赖漏洞为 0。本轮按用户要求未使用 Codex Security、Docker 或远端 CI/CD。
- 七个冻结安装包由 ClamAV 1.5.3、本地病毒库 28093 离线扫描，感染文件为 0。本机 Defender 被系统策略关闭，不冒充 Defender 已通过。

## 尚未冒充完成的事项

- 没有 Windows 7、麒麟、UOS、deepin、openEuler 真机，故发布页和官网持续显示“未真机验证”。当前 Win11 与 AArch64 用户态结果不能替代这些系统的原生安装验收。
- 没有 Authenticode 商业代码签名证书，Windows 安装器可能触发 SmartScreen；用户应核对官网/GitHub 显示的 SHA-256。
- Windows 7 已停止系统级安全维护，只能建议在受控局域网使用；不得从第三方网站单独下载缺失的系统 DLL。

发布后还需对 CloudStudio 完整下载、Content-Length、MZ、SHA-256 和实际速度，GitHub Release 资产，以及 EdgeOne 桌面/移动端、下载、更新日志、简历和控制台执行线上核验。任何线上哈希或静态资源错误均阻断后续步骤。
