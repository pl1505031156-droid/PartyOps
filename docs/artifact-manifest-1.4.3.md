# PartyOps 1.4.3-rc.9 冻结制品清单

最后本地核对：2026-08-20（北京时间，UTC+8）。机器可读清单在最终源码提交和线上上传时间确定后生成；下表是安装包本体的不可变长度与 SHA-256。

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.9_windows_amd64.exe` | Windows 10/11 x64 | 129563904 | `19140c35545b958125f5ec049a7265cfa64e4871eae4d208808a2f635a239f0d` |
| `PartyOps_1.4.3-rc.9_windows7_amd64.exe` | Windows 7 SP1 x64 | 109278890 | `9cff6ccb6dec5c0e53011d545370793bef2bfb80014758a0fe1cd3e4c81f5d55` |
| `PartyOps_1.4.3-rc.9_windows7_x86.exe` | Windows 7 SP1 x86 | 61005175 | `0072252da8ccccb7f5f1f45f47ffb8dde40aa6c6dfdf167ecaba8434bb8e4468` |
| `PartyOps_1.4.3-rc.9_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 163009588 | `b120994f0830720f8d1477a1148b7b9a5eb921d7c00b0daa435d215be7c83522` |
| `PartyOps_1.4.3-rc.9_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 152475392 | `16661a42aa106bf44328ede383c4f7427b0168b6905ccab2078eefa7a1595a89` |
| `PartyOps-1.4.3-0.rc.9.1.x86_64.rpm` | openEuler x86_64 | 346957464 | `d6de6b468fb25c88033ad7c39d5f2143911e9cac37fb3067c11462339cd0a4f3` |
| `PartyOps-1.4.3-0.rc.9.1.aarch64.rpm` | openEuler ARM64 | 325973644 | `1e45e415062fd14cc180ff4d1f0f6f42993d192e97fab6d1cfa8d443b0f7bf10` |
| `PartyOps_1.4.3-rc.9_macos_arm64.pkg` | macOS 11+ Apple Silicon | 196907127 | `f59b0aa4734a058114e0205c2e8795a391bd60e664c80c89207de0320e89889a` |
| `PartyOps_1.4.3-rc.9_macos_x86_64.pkg` | macOS 11+ Intel | 207055418 | `19a0bf0110b307480653354a179ebd4201740e600d7f0dffcc919cac8a215f50` |

普通用户只需下载一个与系统及处理器匹配的安装包，并同时核对官网或 GitHub Release 的 SHA-256。`.sha256` 是同一值的自动化旁路文件。

Windows 7、麒麟、UOS、deepin、openEuler 与 macOS 尚无对应用户真机交互验收。Windows 已执行冻结运行时与安装器门禁，Linux 已执行 glibc 2.17/x86_64 和 QEMU/aarch64 动态门禁，macOS 已在对应原生 Darwin 构建机执行 PKG 安装和 LaunchServices 双击自检；这些证据均不得表述为目标用户设备真机通过。

在线 `.partyops-update` 只能由与 `packaging/uos/update-public-key.txt` 匹配的离线 Ed25519 私钥签发。私钥未挂载时，本发布只冻结完整安装包，不生成替代签名、不轮换信任根。
