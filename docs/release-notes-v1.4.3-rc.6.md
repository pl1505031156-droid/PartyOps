# 党建智办 PartyOps 1.4.3-rc.6 发布说明

`1.4.3-rc.6` 是面向多系统交付的未签名候选版。本版使用全新的版本号和不可变制品，不覆盖 rc.5，重点解决 Windows PowerShell 5.1 编码导致的安装目录检查失败，以及 Win7 误装 Windows 10/11 通用包后缺少系统 DLL 的问题。

## Windows 安装修复

- 安装目录校验脚本使用 Windows PowerShell 5.1 可确定识别的 UTF-8 BOM。简体中文系统不会再把 UTF-8 中文注释按 GBK/ANSI 解码，从而产生“逗号后缺少表达式”或“缺少右大括号”等假语法错误。
- 安装器把校验脚本的标准输出、错误输出和退出码写入安装日志。即使诊断文件尚未来得及创建，也能定位真实原因。
- 自定义程序目录继续支持本机固定 D/E 盘、中文和空格。磁盘根目录、网络盘、重解析点、非 PartyOps 非空目录和确实可被普通用户替换服务文件的目标仍会被拒绝。
- 构建门禁要求源脚本保留 UTF-8 BOM；最终安装器还要重新提取脚本并在 Windows PowerShell 5.1 下完成中文/空格路径校验。

## Windows 7 专用包

- Windows 10/11 通用包现在设置最低系统版本为 Windows 10。在 Win7 上运行会直接出现中文提示，要求下载文件名带 `windows7_amd64` 或 `windows7_x86` 的专用包。
- Win7 专用包使用隔离的 Python 3.8 Legacy 运行时。最终载荷全量拒绝 `api-ms-win-core-path-*` 以及其他 Win8/10 专属 DLL/API 导入，从源头避免 `api-ms-win-core-path-l1-1-0.dll` 缺失。
- 不要从第三方网站单独下载并复制系统 DLL；这既不能修复错误运行时，也会引入来源不明的系统级风险。
- Win7 仍要求 SP1、KB2533623 与 Universal CRT；Windows 7 已停止系统级安全维护，只建议在受控局域网使用。

## 下载选择

| 系统 | 架构 | 单文件安装包 | 验收边界 |
| --- | --- | --- | --- |
| Windows 10/11 | x64 | `PartyOps_1.4.3-rc.6_windows_amd64.exe` | 当前 Win11 构建机执行真实自定义路径安装、覆盖升级、健康与卸载回归 |
| Windows 7 SP1 | x64 | `PartyOps_1.4.3-rc.6_windows7_amd64.exe` | 冻结、PE 架构/子系统/导入与安装器静态门禁；未真机验证 |
| Windows 7 SP1 | x86 | `PartyOps_1.4.3-rc.6_windows7_x86.exe` | 无语义重排和本地 LLM；未真机验证 |
| 麒麟/UOS/deepin | amd64 / arm64 | 对应 `PartyOps_1.4.3-rc.6_linux_*.deb` | 包格式、架构和依赖闭包门禁；未对应商业系统真机验证 |
| openEuler | x86_64 / aarch64 | 对应 `PartyOps-1.4.3-0.rc.6.1.*.rpm` | 包格式、架构和依赖闭包门禁；未目标真机验证 |

普通用户只下载与本机匹配的一个 EXE、DEB 或 RPM。`.sha256`、SBOM、VEX 和机器可读清单用于审计与自动化，不是第二个必装包。

## 已知边界

- Windows 安装器没有 Authenticode 商业代码签名证书，首次运行可能出现 SmartScreen；请先核对官网或 GitHub Release 显示的 SHA-256。
- Windows 7、麒麟、UOS、deepin 和 openEuler缺少对应真机运行结论。“GitHub 普通 Release”只表示可以直接下载，不等于稳定版或真机已通过。

完整变更见[更新日志](../CHANGELOG.md)，安装、升级和回滚见[升级指南](upgrade-1.4.3.md)。
