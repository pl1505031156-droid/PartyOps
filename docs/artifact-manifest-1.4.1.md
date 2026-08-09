# PartyOps 1.4.1 候选制品清单

生成/复核日期：2026-08-09

| 制品 | 大小 | SHA-256 | 状态 |
| --- | ---: | --- | --- |
| `artifacts/PartyOps_1.4.1_windows_amd64.exe` | 133,370,070 | `352dca19f70e4ca7097bff642f2fd000af87648a5669e7edbe9934c8e5256222` | Windows 11 x64 未签名候选 |
| `artifacts/PartyOps-UOS-build-kit.zip` | 337,983,535 | `897139590050672f20c519dd5ed4eb3510cbb8f553dae8590ceef9a638673dcd` | UOS 双架构原生构建输入，不是 DEB |
| `artifacts/qa-evidence-1.4.1/协同机共享材料-浏览器下载.txt` | 96 | `cbbffd43883a855c579ec3f9cbd95f3fcf5c0fffadde470c560f4122711b7d4a` | Chrome 真下载证据 |

校验文件：

- `artifacts/PartyOps_1.4.1_windows_amd64.exe.sha256`
- `artifacts/PartyOps-UOS-build-kit.zip.sha256`

构建与验收日志：

- `artifacts/windows-build-1.4.1.stdout.log`
- `artifacts/windows-build-1.4.1.stderr.log`
- `artifacts/windows-uninstall-1.4.1.log`
- `artifacts/qa-evidence-1.4.1/windows-protocol-download-install-uninstall.txt`

UOS 构建套件自检：547 个文件条目；amd64/arm64 均包含 cryptography 49.0.0 轮子和 llama.cpp b10331 运行时；私钥条目 0、QA/运行目录条目 0；外层 SHA-256 与校验文件一致。

## 未生成的正式制品

以下制品未满足发布门禁，因而没有生成占位文件：

- `partyops_1.4.1_amd64.deb`
- `partyops_1.4.1_arm64.deb`
- `partyops_1.4.1.partyops-update`
- 中文向量、轻量 LLM、增强 LLM `.partyops-modelpack`

缺少项分别依赖 UOS 两种架构原生构建/验收、正式 Ed25519 与 Authenticode 签名环境、合法可再分发权重和模型包签名密钥。禁止把构建套件或未签名 EXE 改名后当作正式更新包分发。
