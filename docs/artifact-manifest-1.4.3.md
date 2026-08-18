# PartyOps 1.4.3-rc.5 冻结制品清单

最后更新：2026-08-18 11:58（北京时间，UTC+8）。七个安装包统一来自产品源码提交 `72007121b1b84f56508bd5c163857a01b39aee8e`，版本化文件不可覆盖。

| 制品 | 平台 | 字节 | SHA-256 |
| --- | --- | ---: | --- |
| `PartyOps_1.4.3-rc.5_windows_amd64.exe` | Windows 10/11 x64 | 131127996 | `97a5b52b211e1d1cbbd16b1fd04763bf18775059e81a6b4f03f2e80331674b15` |
| `PartyOps_1.4.3-rc.5_windows7_amd64.exe` | Windows 7 SP1 x64 | 109060256 | `55c041746ef46e3aab1678b4c3bcdd33abd57ac459028e1508f32fb12bbbca1e` |
| `PartyOps_1.4.3-rc.5_windows7_x86.exe` | Windows 7 SP1 x86 | 60833652 | `b779239fd257951692af77999193d117a250690b9782e630bdea4f5e6e29f493` |
| `PartyOps_1.4.3-rc.5_linux_amd64.deb` | 麒麟/UOS/deepin x86_64 | 302619368 | `a92d7d52b861af9c9da22ce8a69f611161fb45bd0351144add4e68b5a0ed3d1b` |
| `PartyOps_1.4.3-rc.5_linux_arm64.deb` | 麒麟/UOS/deepin ARM64 | 285575054 | `9704ad43871a048f3fa9f72072dfc900c854ca18adf59b04bab02936b0c4ab9f` |
| `PartyOps-1.4.3-0.rc.5.1.x86_64.rpm` | openEuler x86_64 | 626372908 | `a90d4ce44aae6a3379af2c63a841a8828b9430b6cf85b469b6c0b24a8656e860` |
| `PartyOps-1.4.3-0.rc.5.1.aarch64.rpm` | openEuler ARM64 | 591970368 | `cd6d9d31d0c4debbb147f893f2e5821bd683482f031a5c9c90c60dd0bd781815` |

普通用户只下载一个与系统和 CPU 架构匹配的安装包；`.sha256` 仅供自动化或手工复核。七个平台另有 Ed25519 签名的 `.partyops-update`，供系统内原位升级使用。

官网和 CloudStudio 只展示、存储当前 `rc.5` 安装包；旧官网安装包必须在新文件完整上传并逐项核验后精确移除。GitHub 历史 Release 继续保留审计记录，但官网不再链接旧制品。

Windows 7、麒麟、UOS、deepin、openEuler 尚无对应真机运行验收；已完成依赖闭包、PE/ABI、资源、哈希、恶意软件与冻结运行时门禁，但这些静态证据不等于稳定版或真机通过。
