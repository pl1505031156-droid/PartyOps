# PartyOps 1.4.3-rc.7 冻结制品清单

冻结时间：2026-08-19 15:50（北京时间，UTC+8）
产品源码提交：`a35a522b5e62093934e81d9098bd1e25fccb68ec`

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.7_windows_amd64.exe` | Windows 10/11 x64 | 129544658 | `b1927283d292e028b2450e99273dbe157a7c7291e1cd5fb9878aea09b6e4cac3` |
| `PartyOps_1.4.3-rc.7_windows7_amd64.exe` | Windows 7 SP1 x64 | 109102573 | `1324e7bf1a867d497f0d782f29106b45bf9b8a0d8fba173f21bab0df794fc33b` |
| `PartyOps_1.4.3-rc.7_windows7_x86.exe` | Windows 7 SP1 x86 | 60858934 | `bca68deb15019d44cc2cac3a991655b2cf553ff1c43eeaddf3bd29872ac30eb5` |
| `PartyOps_1.4.3-rc.7_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 302622290 | `b8bb56388b421d636f3f0da9329f604a4b42ab2f3c4447bdc4021661b73443fd` |
| `PartyOps_1.4.3-rc.7_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 285640022 | `47156ce3e0772f2eb7300d8f7825867647dbe1b5fb4a9a61eaab3804479230d5` |
| `PartyOps-1.4.3-0.rc.7.1.x86_64.rpm` | openEuler x86_64 | 626329244 | `c838bd29cfa9c3a5391a5551ca7647219e8481dfdfe33ea01e130d7975c44899` |
| `PartyOps-1.4.3-0.rc.7.1.aarch64.rpm` | openEuler ARM64 | 592036412 | `ef2375fa4c5c6265aa67332152aa13e3edff779e1c8526deb3eb43d54549dc12` |

普通用户只需下载一个与系统及处理器匹配的安装包。每个平台另提供 Ed25519 签名的 `.partyops-update`，用于应用内原位升级；`.sha256` 仅供自动化或手工复核。

Windows 7、麒麟、UOS、deepin、openEuler 尚无对应真机运行验收。已完成 PE/ABI、依赖闭包、冻结资源、最终包反解运行、哈希和恶意软件门禁，但不得将这些证据表述为目标商业系统真机通过。
