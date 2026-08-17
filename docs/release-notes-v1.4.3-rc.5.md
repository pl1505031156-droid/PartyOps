# 党建智办 PartyOps 1.4.3-rc.5 发布说明

`1.4.3-rc.5` 是面向多系统交付的未签名候选版。本版使用全新的版本号和不可变制品，不覆盖 rc.4，重点彻底解决 Windows 自定义程序目录被错误拦截的问题。

## Windows 安装修复

- 支持本机固定 D/E 盘、中文和空格程序目录；不再因为上级日常文件目录允许普通用户写入，就误报 `INSTALL_DIR_PARENT_ACL_UNSAFE` 或 `INSTALL_DIR_CHECK_FAILED`。
- 仍拒绝磁盘根、网络/移动盘、重解析点、系统目录、非 PartyOps 非空目录等真实危险目标。
- 安装器创建目标后将 DACL 收敛为 Administrators/SYSTEM 完全控制、普通用户只读执行，并设置高完整性标签，兼顾自定义路径与 LocalSystem 服务文件防替换。
- 覆盖升级保留主机与更新服务原有自动启动状态；保留业务数据卸载会清理服务、自启动、程序载荷和空程序根目录，不删除用户选择的数据目录。
- Windows 文件版本、应用版本、安装器文件名、更新清单和健康接口统一为 `1.4.3-rc.5`。

## 下载选择

| 系统 | 架构 | 单文件安装包 | 验收边界 |
| --- | --- | --- | --- |
| Windows 10/11 | x64 | `PartyOps_1.4.3-rc.5_windows_amd64.exe` | 当前 Win11 构建机执行真实自定义路径安装、覆盖升级、健康与卸载回归 |
| Windows 7 SP1 | x64 | `PartyOps_1.4.3-rc.5_windows7_amd64.exe` | 静态/冻结/PE 门禁；未真机验证 |
| Windows 7 SP1 | x86 | `PartyOps_1.4.3-rc.5_windows7_x86.exe` | 无语义重排和本地 LLM；未真机验证 |
| 麒麟/UOS/deepin | amd64 / arm64 | 对应 `PartyOps_1.4.3-rc.5_linux_*.deb` | 包格式、架构和依赖闭包门禁；未对应商业系统真机验证 |
| openEuler | x86_64 / aarch64 | 对应 `PartyOps-1.4.3-0.rc.5.1.*.rpm` | 包格式、架构和依赖闭包门禁；未目标真机验证 |

普通用户只下载与本机匹配的一个 EXE、DEB 或 RPM。`.sha256`、SBOM、VEX 和机器可读清单用于审计与自动化，不是第二个必装包。

## 已知边界

- Windows 安装器没有 Authenticode 商业代码签名证书，首次运行可能出现 SmartScreen；请先核对官网或 GitHub Release 显示的 SHA-256。
- Windows 7 已停止系统级安全维护，只能在受控局域网使用；PartyOps 自身门禁不能恢复操作系统安全。
- Windows 7、麒麟、UOS、deepin 和 openEuler 缺少对应真机运行结论。“GitHub 普通 Release”只表示可以直接下载，不等于稳定版或真机已通过。

完整变更见[更新日志](../CHANGELOG.md)，安装、升级和回滚见[升级指南](upgrade-1.4.3.md)。
