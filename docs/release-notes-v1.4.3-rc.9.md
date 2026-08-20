# 党建智办 PartyOps 1.4.3-rc.9 发布说明

`1.4.3-rc.9` 是未签名候选版，集中修复 Windows 7/10/11、统信 UOS 与其他国产 Linux 的安装、启动、端口和跨版本冲突，并补齐一事一档回退、文档目录操作与党员发展 Word 导出。每台电脑仍只需下载一个与系统、包格式和处理器架构匹配的安装包。

## 安装与启动修复

- 修复旧 UOS 配置把已失效的局域网 IP 同时用于服务监听，导致 Agent 端口 `18766` 绑定失败、主程序提前退出。升级时会把监听地址与对外地址分离：监听使用回环或所有本机地址，对外地址保留可访问的局域网地址。
- 个人模式端口 `18775` 被未知程序占用时不结束未知进程；新版原子选择 `18775—18875` 内的空闲回环端口并同步保存配置。只有监听 PID、数据目录实例锁和当前可执行文件三项一致时，才恢复缺失的旧 PartyOps 进程标记。
- Windows 同名“PartyOps主机服务”采用规范化 SCM 路径、安装目录和 Inno 卸载元数据三方证明归属；覆盖安装可识别旧 32 位卸载记录及带空格的未加引号路径，无法证明归属时仍停止安装且绝不修改第三方服务。
- Linux 打包阶段统一清除桌面入口 CRLF，并对三个 `.desktop` 文件执行格式、`TryExec` 和动态启动自检；安装失败时把 `desktop-file-validate` 详情写入 `/var/log/partyops-desktop-file-validate.log`，修复 `PACKAGE_DESKTOP_ENTRY_INVALID` 导致的 `dpkg` 半配置状态。
- 启动器会核对端口健康端点的实际版本。属于当前安装的旧 PartyOps 进程会安全重启；未知进程不会被终止，也不会打开新旧版本混合页面。
- Windows 10/11 安装器不再把机器级 `HKA\\Software\\Classes` 协议注册作为安装门禁。桌面启动后改在原始用户的 `HKCU` 内事务注册；拒绝写入或检测到第三方协议时只禁用该协议功能，不回滚主程序、不覆盖第三方值。
- Windows 7 不再只按补丁登记名判断 `KB2533623`，而是探测 `AddDllDirectory`/`SetDefaultDllDirectories` 安全加载 API；安装了等效后续累积更新的系统可正常通过，缺少真实能力时给出明确补丁提示。
- Windows 11 前端增加启动看门狗，脚本、样式、模块加载或挂载超时会显示可复制诊断，不再“闪一下后永久空白”。
- UOS 桌面入口改为非阻塞事务锁。重复双击复用同一配置页；真正卡住的向导超过 180 秒后只终止本入口创建且路径明确的子进程，释放锁并输出 `WIZARD_PAGE_TIMEOUT`，不再永久残留 `LAUNCH_LOCK_TIMEOUT`。
- 麒麟/UOS 桌面入口不再依赖文件管理器直接执行 `/opt` 下的 ELF：统一由 `/bin/bash` 进入受控启动器，并在环境缺少 `HOME` 时从账号数据库恢复；安装器在原桌面用户的 DBus 会话中设置快捷方式可信元数据，失败时仍保留可执行位和启动日志。
- macOS Finder 主入口改为原生 C 启动探针，不再依赖 shell 脚本被 LaunchServices 直接执行；双击后先写入 `~/Library/Logs/PartyOps/launch-probe.log`，再启动冻结向导，确保入口级错误也有日志。原生 Intel 与 Apple Silicon 构建均执行真实 `open -na` LaunchServices 自检。

## 业务修复

- 一事一档材料增加“回退到此版”。回退不覆盖、不删除历史文件，而是引用目标内容创建新的最终版本；要求并发版本匹配、回退原因和权限校验，归档事项需先重开，审计、工作日志和事件同步留痕。
- 文档管理中心始终显示“操作目录”。主机可纳管主机目录，协同机可共享本机文件夹；当前浏览器无桌面能力时显示明确入口说明，不再静默隐藏按钮。
- 党员发展时间节点 Word 导出改为稳定的 DOM 下载流程：先挂载下载节点，触发后延迟清理对象 URL，并拒绝空文件，兼容旧 WebView/Chromium。

## 选择安装包

| 系统 | 架构 | 安装包 |
| --- | --- | --- |
| Windows 10/11 | x64 | `PartyOps_1.4.3-rc.9_windows_amd64.exe` |
| Windows 7 SP1 | x64 | `PartyOps_1.4.3-rc.9_windows7_amd64.exe` |
| Windows 7 SP1 | x86 | `PartyOps_1.4.3-rc.9_windows7_x86.exe` |
| 麒麟 / UOS / deepin | AMD64 | `PartyOps_1.4.3-rc.9_linux_amd64.deb` |
| 麒麟 / UOS / deepin | ARM64 | `PartyOps_1.4.3-rc.9_linux_arm64.deb` |
| openEuler | x86_64 | `PartyOps-1.4.3-0.rc.9.1.x86_64.rpm` |
| openEuler | aarch64 | `PartyOps-1.4.3-0.rc.9.1.aarch64.rpm` |
| macOS 11+ | Apple Silicon / ARM64 | `PartyOps_1.4.3-rc.9_macos_arm64.pkg` |
| macOS 11+ | Intel / x86_64 | `PartyOps_1.4.3-rc.9_macos_x86_64.pkg` |

macOS 两个 PKG 分别由 GitHub 托管的 `macos-15`（Apple Silicon）和 `macos-15-intel`（Intel）原生 Darwin 主机生成，不是 Linux 交叉构建。构建已通过 Bundle、嵌套运行时、PKG 安装、LaunchServices 双击入口、启动探针日志与回读门禁；仍不等价于用户真机交互验收。

## 验收边界

- 后端、前端、官网、版本一致性、Windows 安装脚本真实编译、Linux 双架构动态运行和 macOS 双架构原生构建门禁已通过；最终安装包的大小、SHA-256、平台格式及线上全量回读以发布就绪记录为准。
- Windows 7、Windows 10/11 与国产 Linux 的自动回归不等价于用户真机交互验收；未取得的证据会继续明确列为限制。
- Windows 安装器仍无 Authenticode 商业证书，可能触发 SmartScreen；安装前必须核对官网或 GitHub Release 显示的 SHA-256。
- macOS 当前包没有 Developer ID 与公证，首次打开会出现 Apple 无法验证提示。先核对 SHA-256，再用 Finder `Control` 点按 PKG →“打开”，或在“系统设置 → 隐私与安全性”选择对应的“仍要打开”；不得关闭 Gatekeeper。永久消除此提示仍需 Developer ID Installer/Application 证书与 Apple 公证、staple，本候选包不能虚假声明已完成。
- rc.3 及后续版本的 Ed25519 在线更新信任根保持不变；若发布机未挂载匹配的离线私钥，rc.9 只发布完整安装器，不生成或伪造新的 `.partyops-update` 与更新目录。

完整变化见[更新日志](../CHANGELOG.md)，安装、升级与回滚见[升级指南](upgrade-1.4.3.md)。
