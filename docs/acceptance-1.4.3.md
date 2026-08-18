# PartyOps 1.4.3-rc.5 候选验收记录

最后更新：2026-08-18 11:58（北京时间，UTC+8）。结论：**七个平台安装包与七个签名更新包已完成本地冻结门禁，可以进入 CloudStudio、GitHub 与 EdgeOne 逐级发布核验。rc.5 仍为未签名候选版。**

## 本地验收结果

- 后端完整套件 892 项、前端 173 项均通过；后端行/分支覆盖率达到 90% 门槛，前端行覆盖率 97.17%、分支覆盖率 90.07%。类型检查、生产构建和前端静态资源闭包通过。
- Windows 10/11 最终安装器在当前 Win11 真实安装到自定义 `E:\PartyOps rc5 自定义安装`：安装、覆盖升级、保留数据卸载、重装、服务自动启动、SQLite 3.53.4/FTS5、健康端点和代表性懒加载资源均通过。程序目录对 SYSTEM/Administrators 完全控制、普通用户只读执行；业务数据保留在所选非 C 盘目录。
- Windows 自定义路径策略不再因为父目录通用 ACL 误报 `INSTALL_DIR_PARENT_ACL_UNSAFE`；安装器负责创建并收敛目标目录权限，同时继续拒绝网络盘、移动盘、重解析点、系统目录、磁盘根和不归属 PartyOps 的非空目录。
- Win7 x64/x86 从同一 `7200712` 源码封装；Python 3.8 Legacy 锁、安全回移、app-local UCRT、Tcl/Tk、SQLite/OCR、PE 子系统和导入 API 静态门禁通过，分别检查 262/158 个二进制。x86 按设计关闭语义重排和本地 LLM。
- Linux DEB/RPM 的 x86_64 与 ARM64 均是对应架构制品；ARM64 RPM 在真实 AArch64 用户态构建。双架构严格 wheelhouse 闭包为 62 个包/66 个 wheel，不存在重复 `cryptography`、脏目录或缺失 ARM64 智能运行时降级；静态资源权限统一为 0644。
- 七个 `.partyops-update` 均通过 Ed25519 签名、版本、平台矩阵和载荷哈希验证；format v3 更新目录覆盖 `windows`、`windows7`、`linux-deb`、`linux-rpm` 及对应架构。
- `pip check`、pip-audit、前端/官网生产依赖审计、Bandit 高/中危门禁和 gitleaks 均通过；已知高危/严重漏洞为 0。本轮按用户要求未使用 Codex Security、Docker 或远端 CI/CD。
- ClamAV 1.5.3、病毒库 28094 对三个 Windows EXE 和四个 Linux 原生包执行离线扫描，七文件感染 0；AArch64 RPM 以解除默认归档大小限制的方式完整扫描 1.17 GiB 解包数据。本机 Defender 被系统策略关闭，不宣称 Defender 通过。

## 尚未冒充完成的事项

- 没有 Windows 7、麒麟、UOS、deepin、openEuler 真机，故发布页和官网持续显示“未真机验证”。当前 Win11 的运行结果不能替代这些系统的原生安装验收。
- 没有 Authenticode 商业代码签名证书，Windows 安装器可能触发 SmartScreen；用户应核对官网/GitHub 显示的 SHA-256。
- Windows 7 已停止系统级安全维护，只能建议在受控局域网使用；PartyOps 的制品门禁无法恢复操作系统安全。

发布后还需对 CloudStudio 完整下载、Content-Length、MZ、SHA-256 和实际速度，GitHub Release 资产，以及 EdgeOne 桌面/移动端、下载、更新日志、简历和控制台执行线上核验。任何线上哈希或静态资源错误均阻断后续步骤。
