# PartyOps 1.4.5-rc.1 冻结与验收记录

最后验证时间：2026-08-24 02:40（北京时间，UTC+8）

## 结论

- 业务源码冻结提交：`6c1168f88617396ab518f9261c6d7d1668a3a263`。
- Windows 三个与 Linux 双格式双架构四个候选安装包已完成本地冻结。
- macOS Apple Silicon/Intel 只能在对应原生 macOS 环境构建并执行 LaunchServices 门禁；当前发布机不具备该条件，本版不提供 PKG，也不复用或改名历史制品。
- 这是未签名候选版。Windows 7、国产 Linux 发行版真机和 ARM64 桌面 PID 归属仍需用户设备复核，不能把冻结运行时或 QEMU 门禁表述为真机通过。

## 冻结制品

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| `PartyOps_1.4.5-rc.1_windows_amd64.exe` | 131738163 | `e3033fb334131aadf1b7e05f03793055f8c47f6d4cd94b2f48140b2915bc6365` |
| `PartyOps_1.4.5-rc.1_windows7_amd64.exe` | 111178955 | `9de3bc5dd2066505006188452beb3d0cb1e9f3fb3c96535aab473ddf211c6376` |
| `PartyOps_1.4.5-rc.1_windows7_x86.exe` | 63360925 | `9baba23bdb7f4f0e0d4c2f3186fdfad7658077ba6ec1e883f38a6043f131c565` |
| `PartyOps_1.4.5-rc.1_linux_amd64.deb` | 163420352 | `a0dc7e7442f872f12d728e0c8cb8c7116bd95c453c8d49368a2afea039be9191` |
| `PartyOps_1.4.5-rc.1_linux_arm64.deb` | 152688768 | `cf7d14376aa501de5025ffa71c6c93754e0280e64287d7b922231391fe9ea5fe` |
| `PartyOps-1.4.5-0.rc.1.1.x86_64.rpm` | 347428872 | `011c684c4098b17eed4be001ae38ab399d71200824cbde79474d6a2bb8cc7c9b` |
| `PartyOps-1.4.5-0.rc.1.1.aarch64.rpm` | 326437064 | `7708caac01017f8efeac176c9f8d81e441dd87e57ec9f2e2f609abb5da2d053a` |

## 自动化与安全门禁

- 后端：1126 项通过；行覆盖率 95.19%，分支覆盖率 90.03%。
- 前端：195 项通过；行覆盖率 95.43%，分支覆盖率 90.02%。
- 官网：48 项通过；行覆盖率 98.42%，分支覆盖率 90.02%，函数覆盖率 95.18%。
- 前端和官网生产依赖高危漏洞为零；Python 锁定依赖审计为零。
- Git 历史与工作区凭据扫描未发现泄露；Bandit 高危和中危结果为零。
- CycloneDX 1.6 Python、前端和官网 SBOM 已重新生成并完成 JSON 解析校验。

## 平台门禁

- Windows 10/11：冻结运行时冷启动到 `1.4.5-rc.1`，SQLite 3.53.4，数据库迁移到 `0022`。
- Windows 7 x64/x86：真实 Python 3.8 完整应用导入与空库迁移到 `0022`；PE/API、冻结资源和冷启动门禁通过。
- Linux amd64：glibc 2.17 基线、62 个运行/构建依赖与 66 个 wheel 文件严格闭包通过；DEB/RPM 文件头与元数据通过。
- Linux ARM64：在 aarch64 QEMU 根文件系统中通过 OCR `chi_sim`、llama、本地智能、SQLite 3.51.3/FTS5、配置向导共享运行时和动态健康检查。
- 所有模型官方入口已逐个公开回读为 HTTP 200；大型模型不转存官网，使用发布方官方页和本机 OpenAI 兼容服务接入。

## 发布边界与回滚

- 只发布上表七个制品；缺失的 macOS 目标保持 `unavailable`。
- 未挂载与既有信任根匹配的离线 Ed25519 私钥，因此不生成临时签名更新包或模型包，也不轮换客户端信任根。
- 发布前先上传国内线路并完整回读哈希，再创建 GitHub/Gitee 标签与说明，最后切换 EdgeOne 官网元数据。
- 回滚只恢复上一条已核验的官网部署与下载清单；不移动标签、不覆盖已发布同名资产、不删除用户数据。
