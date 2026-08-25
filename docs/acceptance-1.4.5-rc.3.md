# PartyOps 1.4.5-rc.3 发布验收记录

最后验证日期：2026-08-25（北京时间）  
源码冻结提交：`4e1669d14b9b93375a7d4b3cd3854e61e80c2297`  
发布工具提交：`f1e9d17dda0e1ba4a2cd140bafe2015ca67e1a28`  
数据库迁移：`0024`  
正式 Ed25519 公钥指纹：`7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3`

## 源码与自动门禁

- 后端：1394 项通过、4 项平台跳过；行覆盖率 96.64%，分支覆盖率 92.79%。
- 前端：216 项通过；行覆盖率 96.45%，分支覆盖率 93.19%。
- 官网：86 项通过；行覆盖率 98.86%，分支覆盖率 93.29%。
- Ruff、类型检查、前后端与官网生产构建均无错误。
- Gitleaks 同时扫描完整 Git 历史和工作区，无密钥命中；Bandit 1.9.4 中高危为 0；Python、前端和官网生产依赖审计无已知高危漏洞。
- `0001 → 0024 → base` 与 `0023 → 0024 → 0023` 迁移/回滚链通过。
- 公文排版无外网、台账导入对抗样例、生命周期删除/恢复、提醒矩阵、模型提示注入与 Needle 真实推理均已通过自动测试。

## 原生制品门禁

| 平台 | 原生/动态门禁 | 结论 |
| --- | --- | --- |
| Windows 11 x64 | 当前 Windows 原生构建，最终冻结 EXE、安装事务和健康门禁 | 预览；无商业签名，Windows 10 未独立验收 |
| Windows 7 x64 | Python 3.8/UCRT 依赖闭包，267 个 PE 文件架构/导入门禁，XLSX 与 0024 空库初始化 | 预览；未在 Win7 真机启动 |
| Windows 7 x86 | 32 位依赖闭包，163 个 PE 文件架构/导入门禁，XLSX 与 0024 空库初始化 | 预览；未在 Win7 真机启动 |
| Linux amd64 DEB/RPM | 解包、冻结运行时、脚本与动态健康门禁 | 预览；未在麒麟/UOS/openEuler 图形真机验收 |
| Linux arm64 DEB/RPM | ARM64/QEMU 依赖闭包、脚本与动态健康门禁 | 预览；桌面 PID 归属仍需真实 ARM 内核复核 |
| macOS arm64/x86_64 | GitHub run `32853898809` 在 `macos-15`/`macos-15-intel` 原生完成安装、LaunchServices 与 `open -na` 自检 | 预览；ad-hoc 签名、未公证、无用户设备交互验收 |

## 安装包冻结哈希

| 文件 | 长度（字节） | SHA-256 |
| --- | ---: | --- |
| `PartyOps_1.4.5-rc.3_windows_amd64.exe` | 132771686 | `20c7eb4e4f4594e42a91cb34320502e65434131e59abc2e30b5bb9644bf7b29b` |
| `PartyOps_1.4.5-rc.3_windows7_amd64.exe` | 114298359 | `31333a3e617c9b8ae4c8e3bc22c323b61c92926e25cfa9f1fae87c006d069ef5` |
| `PartyOps_1.4.5-rc.3_windows7_x86.exe` | 66120178 | `49280666b95c48455589d4d216c276ce21a5ca61d20ab52569a3a9347cd85650` |
| `PartyOps_1.4.5-rc.3_linux_amd64.deb` | 164598070 | `0366d91bf56a913bbe6e67071f7b89e6437ec4fc3189d12119734594b737a2b0` |
| `PartyOps_1.4.5-rc.3_linux_arm64.deb` | 153688848 | `c73e95586b24bac217193b7b49e39ef18de28c5607aa19c5d69249c63c39258b` |
| `PartyOps-1.4.5-0.rc.3.1.x86_64.rpm` | 349707572 | `9ca01c655750d173a3c1369cb696f9462f8cc11119a2dc0b52b03bb03e703bcb` |
| `PartyOps-1.4.5-0.rc.3.1.aarch64.rpm` | 328531480 | `d75c50ecd7f74b5fa828ad26dd4f3c26615cb7641158997849874696559828e7` |
| `PartyOps_1.4.5-rc.3_macos_arm64.pkg` | 200485765 | `fe095c0198f598aed64966e7b1dd2ded6d8fa2dc7598ffbf33af5b8bd7f8cbac` |
| `PartyOps_1.4.5-rc.3_macos_x86_64.pkg` | 221836207 | `3acadbd4a185a1d596c5f67900ff3392507ba8bf626c486c7f1e42b6e459d58b` |

## 模型包

`needle2-intent-2.0.3-windows-amd64.partyops-modelpack` 长度 13303857 字节，SHA-256 为 `2d3dc4d8b4e455a4d7d53f442f14f3de9a03cb0902769d83d734cf70ec609794`。已使用与客户端内置信任根匹配的正式密钥签名，并通过导入、激活、原生 DLL 推理、安全回退和卸载 8 项生命周期测试。当前仅发布 Windows AMD64，其他平台不得把它当作通用模型包。

## 发布边界

Cloud Studio 公网回读、GitHub Release、EdgeOne Preview/Production 与 Deployment ID 在实际部署后写入同批发布记录；任何一步哈希、文件头、HTTPS 或页面冒烟检查失败都停止后续切换并保留 rc.2。

