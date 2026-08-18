# PartyOps 1.4.3-rc.6 冻结制品清单

最后更新：2026-08-18 20:05（北京时间，UTC+8）。七个安装器统一对应源码提交 `0ad3e7366d6d16e81af9495ffb10ec9d2deeb156`。版本、构建来源和下列哈希均不可覆盖。

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.6_windows_amd64.exe` | Windows 10/11 x64 | 129484831 | `4a19aab1b5d37e2286b5315cb26fe24bb2ace847135a33f06c7d2b6b12868eb2` |
| `PartyOps_1.4.3-rc.6_windows7_amd64.exe` | Windows 7 SP1 x64 | 109054209 | `16c8e6c190f98ce9ef9b0b0c45105d7a10204d53f4c9a0781139666e1edfda4f` |
| `PartyOps_1.4.3-rc.6_windows7_x86.exe` | Windows 7 SP1 x86 | 60884596 | `76baa27f51502dc0922eb160ee85943c7e64869007ddcd63eca4a9d2e6557b2c` |
| `PartyOps_1.4.3-rc.6_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 302606152 | `33c9c4a5c849ae7d55285feee780e4a724a69b6340458fddf95dc196183c6cc4` |
| `PartyOps_1.4.3-rc.6_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 285629088 | `84e36e441ca1d4e2dcfec2030e09699ac3bfe4f0eec0fe23a8fd56009f15d656` |
| `PartyOps-1.4.3-0.rc.6.1.x86_64.rpm` | openEuler x86_64 | 626319536 | `9e43b9bf879f630176e7b9ad954bb176441834295b9bedc919663be8b6509a24` |
| `PartyOps-1.4.3-0.rc.6.1.aarch64.rpm` | openEuler ARM64 | 591994472 | `bd3e9ea7b683fde7fd37ba7a8459ea96101fafe681f1c179a79ac3b75d7ab204` |

普通用户只下载一个与系统和 CPU 架构匹配的安装包；`.sha256` 仅供自动化或手工复核。七个平台另有 Ed25519 签名的 `.partyops-update`，供系统内原位升级使用。

官网和 CloudStudio 只展示、存储当前 `rc.6` 安装包；旧官网安装包必须在新文件完整上传并逐项核验后精确移除。GitHub 历史 Release 继续保留审计记录，但官网不再链接旧制品。

Windows 7、麒麟、UOS、deepin、openEuler 尚无对应真机运行验收；已完成依赖闭包、PE/ABI、资源、哈希、恶意软件与冻结运行时门禁。这些证据仍不等于目标商业系统真机通过。
