# PartyOps 1.4.3 制品清单

最后更新：2026-08-11。当前状态：**Windows 与 UOS 构建输入测试候选 / 稳定发布 NO-GO**。

| 制品 | 状态 | 大小与 SHA-256 |
| --- | --- | --- |
| `PartyOps_1.4.3_windows_amd64.exe` | Windows 11 冻结运行时、安装/卸载通过；未签名 | 136,846,051 字节；`9f57b7ba1798d5bde330548ec1be3f12661acf2e2515fcbf9d199f569e9d876e` |
| `PartyOps_1.4.3_windows_amd64.exe.sha256` | 已生成 | 与安装器实算一致 |
| `PartyOps_1.4.3_windows_amd64.candidate.json` | 已生成 | 记录版本、SQLite、覆盖率、安装验证和已知限制 |
| `PartyOps-UOS-1.4.3-build-kit.zip` | 已生成并核对 622 个条目；发布证据置于 ZIP 外，避免自身哈希循环；不是 DEB | 347,618,065 字节；`c9afca6bf0e5284e2871f3d47e2436b5cd8c7456528cd18ef3dbd10f0ad0094d` |
| `PartyOps-UOS-1.4.3-build-kit.zip.sha256` | 已生成 | 与构建套件实算一致 |
| `partyops_1.4.3_amd64.deb` | 未生成 | 需要 UOS amd64 真机原生构建与验收 |
| `partyops_1.4.3_arm64.deb` | 未生成 | 需要 UOS arm64 真机原生构建与验收 |
| `partyops_1.4.3.partyops-update` | 未生成 | 需要三平台安装制品和 Ed25519 发布密钥 |
| 三档 `.partyops-modelpack` | 未生成 | 需要正式权重来源、许可、运行时与签名密钥后独立交付 |

`artifacts/` 已按发布清洁原则处理：1.4.2 候选及旧构建套件已移入 Windows 回收站；构建中间目录也已移入回收站。当前目录只保留上表中已经生成的 1.4.3 五个文件。回收站操作可恢复，稳定版放行前不会删除当前候选。

两个 DEB、统一更新包和模型包缺失时，不能把该目录描述为正式三平台交付。正式签名完成后必须重新计算全部 SHA-256，并更新本清单和 Release 附件。
