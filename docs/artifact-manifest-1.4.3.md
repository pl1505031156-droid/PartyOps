# PartyOps 1.4.3-rc.4 冻结制品清单

最后更新：2026-08-17 16:02（北京时间，UTC+8）。源码提交：`e2445d000797c4f5401f25074fd018a5d34e9d2e`。

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.4_windows_amd64.exe` | Windows 10/11 x64 | 131108262 | `5b4c595a45c2132a9aaeb3bd1bd663f0c1742893f8fb8f6fe0c2b088cd716f27` |
| `PartyOps_1.4.3-rc.4_windows7_amd64.exe` | Windows 7 SP1 x64 | 109036354 | `660e555c2c912891235846a9a5a7019f83410e83d705d1fa70da8a2df7993bd6` |
| `PartyOps_1.4.3-rc.4_windows7_x86.exe` | Windows 7 SP1 x86 | 60912478 | `a2981f48edf337fbcd5614fb8b821fc247dd2457bc0bdaf6fdb13d097459e235` |
| `PartyOps_1.4.3-rc.4_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 304193762 | `4e6430d38afa8b6e5566a2ad776a507f5ea300c183ac2ab08f502691101bfc1c` |
| `PartyOps_1.4.3-rc.4_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 287193414 | `3e3d2e5333ff695d89e7d712349f435e68d8e4bd9763b37e204b4b70311e58a7` |
| `PartyOps-1.4.3-0.rc.4.1.x86_64.rpm` | openEuler x86_64 | 629524488 | `9be78687a82a8d5374a4ccc10e41845196417d17e224af8fa5ccdba517cc00d9` |
| `PartyOps-1.4.3-0.rc.4.1.aarch64.rpm` | openEuler ARM64 | 595204928 | `8fe0bf22262ee7d1553583db18ba0348d779e8540da354edc8f2fbe4a95422dc` |

普通用户只需下载一个与系统和 CPU 架构匹配的安装包；`.sha256` 仅供自动化或手工复核。七个平台另有 Ed25519 签名的 `.partyops-update`，供系统内原位升级使用。

官网只展示和存储当前 `rc.4` 安装包。旧官网安装包在 `rc.4` 完整上传并逐项核验后精确移除；GitHub 的历史 Release 与安装包存档按独立发布策略处理。任何本地构建中间包、语法探针或标记为 `invalid` 的归档均不得上传。

Windows 7、麒麟、UOS、deepin、openEuler 尚无对应真机运行验收；这不替代已经完成的依赖闭包、PE/ABI、资源、哈希、安全和冻结运行时门禁，也不代表稳定版承诺。
