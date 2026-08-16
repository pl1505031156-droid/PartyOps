# PartyOps 1.4.3-rc.3 预期制品清单

最后更新：2026-08-16。当前状态：**五个允许发布的产品安装包正在从已通过源码门禁的干净提交冻结；大小、SHA-256 和上传时间在文件关闭并完成核验前保持空缺。**

| 预期制品 | 面向平台 | 当前状态 |
| --- | --- | --- |
| `PartyOps_1.4.3-rc.3_windows_amd64.exe` | Windows 10/11 x64 | 待冻结与逐件验收 |
| `PartyOps_1.4.3-rc.3_windows7_amd64.exe` | Windows 7 SP1 x64 | 阻断；缺安全回移轮子和证据 |
| `PartyOps_1.4.3-rc.3_windows7_x86.exe` | Windows 7 SP1 x86 | 阻断；缺安全回移轮子和证据 |
| `PartyOps_1.4.3-rc.3_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 待冻结与逐件验收；未真机验证 |
| `PartyOps_1.4.3-rc.3_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 待冻结与逐件验收；未真机验证 |
| `PartyOps-1.4.3-0.rc.3.1.x86_64.rpm` | openEuler x86_64 | 待冻结与逐件验收；未真机验证 |
| `PartyOps-1.4.3-0.rc.3.1.aarch64.rpm` | openEuler ARM64 | 待冻结与逐件验收；未真机验证 |

每个产品包发布时还必须附带可选 `.sha256`、机器可读总发布清单、源码提交、制品级 SBOM、VEX 和验收记录。普通用户只需下载一个与系统/架构匹配的安装包；`.sha256` 不得成为安装必需步骤。

`artifacts/inno-syntax-rc3-20260813/output/` 内约 2.8 MB 的 EXE 仅用于证明 Inno 脚本可编译，不含正式 PartyOps 运行时，禁止上传、散发或写入官网。

rc.2 资产继续保留。rc.3 最终清单冻结前，官网仍展示已核验的 rc.2 文件、真实上传时间和哈希，禁止以 rc.3 名称覆盖旧 URL。

依据用户 2026-08-14 的发布决定，缺少麒麟/UOS/deepin/openEuler 真机本身不再阻断上述四个 Linux 文件；它会作为每个新增平台制品的发布限制写入清单、GitHub Release 和官网。Win7 不适用这项放行：其安全回移证据未达到零高危门禁，本次继续阻断。
