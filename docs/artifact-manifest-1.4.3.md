# PartyOps 1.4.3 制品清单

最后更新：2026-08-13。当前状态：**v1.4.3-rc.2 未签名候选 / 稳定发布 NO-GO**。最终大小与 SHA-256 由同一源码提交构建后写入 GitHub Release、官网和同级候选 JSON；本文件不预填旧 rc.1 数值。

| 制品 | 状态 | 大小与 SHA-256 |
| --- | --- | --- |
| `PartyOps_1.4.3-rc.2_windows_amd64.exe` | 单文件 Windows 候选；未签名 | 见 Release 与 `candidate.json` |
| `PartyOps_1.4.3-rc.2_windows_amd64.exe.sha256` | 可选自动化附件 | 与安装器实算一致；普通用户无需下载 |
| `PartyOps_1.4.3-rc.2_windows_amd64.candidate.json` | 机器可读发布证据 | 记录标签、源码提交、大小、SHA-256、SQLite 与限制 |
| `PartyOps-UOS-1.4.3-rc.2-build-kit.zip` | amd64/arm64 严格离线构建套件；不是 DEB | 见 Release 与 `candidate.json` |
| `PartyOps-UOS-1.4.3-rc.2-build-kit.zip.sha256` | 可选自动化附件 | 与构建套件实算一致 |
| `PartyOps-UOS-1.4.3-rc.2-build-kit.candidate.json` | 机器可读发布证据 | 记录双架构严格闭包和未实机验证边界 |
| `partyops_1.4.3_amd64.deb` | 未生成 | 需要 UOS amd64 真机原生构建与验收 |
| `partyops_1.4.3_arm64.deb` | 未生成 | 需要 UOS arm64 真机原生构建与验收 |
| `partyops_1.4.3.partyops-update` | 未生成 | 需要三平台安装制品和 Ed25519 发布密钥 |
| 三档 `.partyops-modelpack` | 未生成 | 需要正式权重来源、许可、运行时与签名密钥后独立交付 |

Windows 安装器必须包含 `release-manifest.json`，记录版本、不可变标签、源码提交和内部文件清单。UOS ZIP 顶层目录必须为 `PartyOps-1.4.3-rc.2/`，不得覆盖解压到 rc.1 目录。

UOS ZIP 构建时必须独立复核：amd64/arm64 各 66 个 wheel，每个架构只有一个 `cryptography-50.0.0`；arm64 使用 `manylinux_2_28_aarch64` 轮子；禁止分发的私钥条目为 0。ZIP 内置 `BUILD-KIT-SHA256SUMS`，`install.sh` 自动完成安装前全量校验、DEB 校验和安装后核验；最终大小与外层 SHA-256 由同级候选 JSON 和 Release 记录，避免源码提交与制品哈希循环引用。

两个 DEB、统一更新包和模型包缺失时，不能把该目录描述为正式三平台交付。正式签名完成后必须重新计算全部 SHA-256，并更新本清单和 Release 附件。
