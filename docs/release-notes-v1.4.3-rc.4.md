# 党建智办 PartyOps 1.4.3-rc.4 发布说明

发布时间：2026-08-17 18:50:48（北京时间，UTC+8）

`1.4.3-rc.4` 是面向多系统交付的未签名候选版。本版重点修复 Windows 自定义安装目录误报，并在独立 Legacy 构建、安全回移和二进制门禁完成后加入 Windows 7 SP1 x64/x86 制品。

## 下载选择

| 系统 | CPU / 系统类型 | 单文件安装包 | 能力边界 |
| --- | --- | --- | --- |
| Windows 10/11 | x64 | `PartyOps_1.4.3-rc.4_windows_amd64.exe` | 完整功能；当前 Win11 构建机执行冻结运行验收 |
| Windows 7 SP1 | 64 位 | `PartyOps_1.4.3-rc.4_windows7_amd64.exe` | 完整主机、协同、OCR、语义重排、本地 LLM；未真机验证 |
| Windows 7 SP1 | 32 位 | `PartyOps_1.4.3-rc.4_windows7_x86.exe` | 核心主机、协同、数据库、文件、档案、备份、OCR；无语义重排/本地 LLM；未真机验证 |
| 麒麟桌面 V10/V10 SP1、UOS V20、deepin | 海光/兆芯/英特尔/AMD x86_64 | `PartyOps_1.4.3-rc.4_linux_amd64.deb` | 未对应商业系统真机验证 |
| 麒麟桌面 V10/V10 SP1、UOS V20、deepin | 飞腾/鲲鹏 ARM64 | `PartyOps_1.4.3-rc.4_linux_arm64.deb` | 未对应商业系统真机验证 |
| openEuler 22.03/24.03 LTS | x86_64 | `PartyOps-1.4.3-0.rc.4.1.x86_64.rpm` | 未目标真机验证 |
| openEuler 22.03/24.03 LTS | aarch64 | `PartyOps-1.4.3-0.rc.4.1.aarch64.rpm` | 未目标真机验证 |

普通用户只下载表中与本机匹配的一个文件。`.sha256`、SBOM、VEX 和机器可读发布清单用于审计与自动化，不是第二个必装包。文件大小和 SHA-256 以 GitHub Release、官网及发布清单三方一致值为准。

## 本版解决的问题

- Windows 10/11 的自定义安装目录继续支持本机固定磁盘、中文、空格和非 C 盘路径。检查器现在把当前提升管理员和 `OWNER RIGHTS` 视为合法所有者，不再误把管理员本人创建的目录当作普通用户可劫持目录。
- 安全边界没有取消：网络盘、移动盘、磁盘根、重解析点、系统目录，以及确实允许其他普通用户删除/替换服务文件的父目录仍会拒绝，并给出精确中文诊断码。
- Windows 配置向导的冻结制品会在构建阶段真实加载 `_tkinter`、创建隐藏窗口并销毁；静态资源、Tcl/Tk DLL 或 Tcl 脚本缺失会直接阻断封装。
- Windows 7 不再复用主线 Python 运行时。x64/x86 分别拥有 Python 3.8 离线锁、架构匹配 wheelhouse、官方 UCRT、SQLite 和 OCR 运行时；PE 门禁逐项检查架构、Win7 API、文件哈希和来源证据。

## Windows 7 安装前提

1. 必须是 Windows 7 SP1；安装 KB2533623 和 Universal CRT 后重启。
2. 在“控制面板 → 系统”确认 32/64 位；64 位选 `amd64`，32 位选 `x86`。
3. Windows 7 已停止系统级安全维护，只能在受控局域网使用，不应直接暴露到互联网，也不应把 PartyOps 自身门禁理解为操作系统安全恢复。
4. 本版不捆绑旧版第三方浏览器。请按单位安全策略使用可用浏览器。

## 已知边界

- 所有 Windows 安装器当前没有 Authenticode 代码签名证书，会出现 SmartScreen 提示；先核对官网 SHA-256，再决定是否运行。
- Windows 7、麒麟、UOS、deepin、openEuler 没有对应真机运行结论。“普通 Release”只表示官网和 GitHub 可直接下载，不等于稳定版或真机已通过。
- Windows 7 x86 因 32 位地址空间主动关闭语义重排和本地 LLM，不会在运行中静默降级。
- LoongArch64、RISC-V、macOS、Win7 ARM 不在本轮支持范围。

升级、回滚和自定义数据目录说明见[安装、升级与回滚](upgrade-1.4.3.md)，完整变更见[更新日志](../CHANGELOG.md)。
