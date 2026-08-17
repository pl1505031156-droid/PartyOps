# PartyOps 1.4.3-rc.4 预期制品清单

最后更新：2026-08-17（北京时间，UTC+8）。当前状态：**七个产品安装包正在执行源码冻结前门禁；大小、SHA-256 和上传时间在最终文件关闭并完成核验前保持空缺。**

| 预期制品 | 面向平台 | 当前状态 |
| --- | --- | --- |
| `PartyOps_1.4.3-rc.4_windows_amd64.exe` | Windows 10/11 x64 | 待冻结与逐件验收 |
| `PartyOps_1.4.3-rc.4_windows7_amd64.exe` | Windows 7 SP1 x64 | Legacy 闭包、安全回移、GUI 与 PE 门禁已通过预检；待从冻结标签重建 |
| `PartyOps_1.4.3-rc.4_windows7_x86.exe` | Windows 7 SP1 x86 | Legacy 闭包、安全回移、GUI、OCR 与 PE 门禁已通过预检；待从冻结标签重建 |
| `PartyOps_1.4.3-rc.4_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 待冻结与逐件验收；未真机验证 |
| `PartyOps_1.4.3-rc.4_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 待冻结与逐件验收；未真机验证 |
| `PartyOps-1.4.3-0.rc.4.1.x86_64.rpm` | openEuler x86_64 | 待冻结与逐件验收；未真机验证 |
| `PartyOps-1.4.3-0.rc.4.1.aarch64.rpm` | openEuler ARM64 | 待冻结与逐件验收；未真机验证 |

每个产品包发布时还必须附带可选 `.sha256`、机器可读总发布清单、源码提交、制品级 SBOM、VEX 和验收记录。普通用户只需下载一个与系统/架构匹配的安装包；`.sha256` 不得成为安装必需步骤。

历史语法探针只用于证明 Inno 脚本可编译，不含正式 PartyOps 运行时，禁止上传、散发或写入官网。rc.4 只接受从不可变标签生成、包含完整冻结运行时并通过逐件门禁的最终 EXE。

rc.4 使用新的版本化 URL，禁止覆盖 rc.3 文件。依据用户的发布决策，rc.2 Release/资产在 rc.4 在线核验完成后移除；rc.3 继续作为应用内升级信任基线保留。

依据用户 2026-08-14 的发布决定，缺少 Win7、麒麟/UOS/deepin/openEuler 真机本身不再单独阻断对应制品；它会作为每个新增平台制品的发布限制写入清单、GitHub Release 和官网。任何制品只要依赖闭包、PE/ABI、冻结自检、哈希、漏洞或安装事务门禁失败，仍单独阻断，不能用“未真机验证”替代失败。
