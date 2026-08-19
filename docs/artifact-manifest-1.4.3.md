# PartyOps 1.4.3-rc.8 冻结制品清单

冻结时间：2026-08-19 20:31（北京时间，UTC+8）
产品源码提交：`bc0ad0c0e6eb4f272bcda731e52894dc4a282c48`

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.8_windows_amd64.exe` | Windows 10/11 x64 | 131309292 | `f2878c630eabb56de7f7d26dcd7aaf9effcac2daedd91260cebc0398e2ebb679` |
| `PartyOps_1.4.3-rc.8_windows7_amd64.exe` | Windows 7 SP1 x64 | 109172276 | `963aaab350f5dcb8d100fdde6d41a645355b1c6737dd104fb01e7d2407cdf0f4` |
| `PartyOps_1.4.3-rc.8_windows7_x86.exe` | Windows 7 SP1 x86 | 60850436 | `9b019ba3c3c34305a8b960d5b3fa0d9029b406ac6d16c3c74ddb9db5bc44d514` |
| `PartyOps_1.4.3-rc.8_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 162866234 | `46af3ce4d437d638f5bc51a54b9eb60a3a6f37d5fc1687a1f6978dfd95444235` |
| `PartyOps_1.4.3-rc.8_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 152364058 | `aa52fa6ba4b4fd827b4da7b91a8493ccd5d81d328221b417354324a13dcd321a` |
| `PartyOps-1.4.3-0.rc.8.1.x86_64.rpm` | openEuler x86_64 | 346707944 | `acf7b7fa06a51dfc55eecd236c019c7b93106d280beb1db7fdadfed0e6f430bd` |
| `PartyOps-1.4.3-0.rc.8.1.aarch64.rpm` | openEuler ARM64 | 325726456 | `6aa31e4129f880454d84c84471bb4c03f2532b130b48291ad7104d0427caa837` |

普通用户只需下载一个与系统及处理器匹配的安装包。每个平台另提供 Ed25519 签名的 `.partyops-update`，用于应用内原位升级；`.sha256` 仅供自动化或手工复核。

Windows 7、麒麟、UOS、deepin、openEuler 尚无对应真机运行验收。已完成 PE/ABI、依赖闭包、冻结资源、最终包反解运行、哈希和恶意软件门禁，但不得将这些证据表述为目标商业系统真机通过。
