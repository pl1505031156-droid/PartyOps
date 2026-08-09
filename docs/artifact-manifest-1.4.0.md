# PartyOps 1.4.0 制品清单

最后更新：2026-08-09

| 制品 | 状态 | SHA-256/说明 |
| --- | --- | --- |
| `artifacts/PartyOps_1.4.0_windows_amd64.exe` | Windows 11 x64 受控试装通过；未签名 | `f5fdf8cd249b62a95b1a35658939b075fa7b09a99e67a90522d711f5b2fee754`；85,490,512 字节 |
| `artifacts/PartyOps-UOS-build-kit.zip` | 已重建并通过关键文件及私钥泄露扫描；双架构原生构建输入，不是 DEB | `d6a18928fa7881b837eefb330be565a27c37abb16aea4c1a4cbf79e68eae0fc7`；280,452,536 字节；499 项 |
| `partyops_1.4.0_amd64.deb` | 未生成 | 需要 UOS amd64 构建机 |
| `partyops_1.4.0_arm64.deb` | 未生成 | 需要 UOS arm64 构建机 |
| `partyops_1.4.0.partyops-update` | 未生成 | 需要两个 DEB、Windows EXE 和隔离发布签名密钥 |

严禁使用工作区历史私钥目录生成正式更新签名；必须由发布负责人在隔离环境提供与部署公钥匹配的密钥。
