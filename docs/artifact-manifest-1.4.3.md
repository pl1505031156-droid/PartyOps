# PartyOps 1.4.3-rc.7 冻结制品清单

状态：**待 rc.7 源码提交、七个平台构建和安全门禁全部通过后冻结。** 下表只定义预期文件名；字节数、SHA-256、源码提交和北京时间必须由最终制品重新生成，严禁沿用 rc.6 数值。

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.7_windows_amd64.exe` | Windows 10/11 x64 | 待构建 | 待计算 |
| `PartyOps_1.4.3-rc.7_windows7_amd64.exe` | Windows 7 SP1 x64 | 待构建 | 待计算 |
| `PartyOps_1.4.3-rc.7_windows7_x86.exe` | Windows 7 SP1 x86 | 待构建 | 待计算 |
| `PartyOps_1.4.3-rc.7_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 待构建 | 待计算 |
| `PartyOps_1.4.3-rc.7_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 待构建 | 待计算 |
| `PartyOps-1.4.3-0.rc.7.1.x86_64.rpm` | openEuler x86_64 | 待构建 | 待计算 |
| `PartyOps-1.4.3-0.rc.7.1.aarch64.rpm` | openEuler ARM64 | 待构建 | 待计算 |

普通用户只下载一个与系统和 CPU 架构匹配的安装包；`.sha256` 仅供自动化或手工复核。七个平台另有 Ed25519 签名的 `.partyops-update`，供系统内原位升级使用。

官网、CloudStudio 与 Gitee 只在 rc.7 新文件完整上传并逐项核验后切换当前下载；切换完成后官网不再链接旧制品。GitHub/Gitee 历史记录的保留或清理必须在远端资产核对后精确执行。

Windows 7、麒麟、UOS、deepin、openEuler 尚无对应真机运行验收；已完成依赖闭包、PE/ABI、资源、哈希、恶意软件与冻结运行时门禁。这些证据仍不等于目标商业系统真机通过。
