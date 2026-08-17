# PartyOps 1.4.3-rc.3 发布就绪判定

最后核对：2026-08-17 09:25（北京时间，UTC+8）。当前结论：**Windows 10/11 x64 与四个国产 Linux 制品已经完成冻结、扫描、普通 Release 和在线核验；Win7 x64/x86 仍为 NO-GO。rc.3 不是稳定版。**

## 放行矩阵

| 门禁 | 当前状态 | 放行条件 |
| --- | --- | --- |
| 后端测试 | 888/888 通过 | 已满足 |
| 后端覆盖率 | 行 95.23%、分支 90.04% | 已满足 |
| 前端/官网 | 前端 173/173；官网 33/33；生产构建通过 | 已满足 |
| 高危漏洞/泄密 | 已发布五个制品为零；秘密扫描与恶意软件扫描通过 | 已满足 |
| Windows 10/11 | 131,106,847 字节单文件安装器已发布并完成 MZ/SHA-256/在线下载核验 | 已满足；仍标注未签名候选版 |
| Win7 x64/x86 | Legacy 锁与静态门禁存在，但依赖审计分别记录 54/45 条漏洞；正式 wheelhouse 门禁均以退出码 2 拒绝 | 不上传；取得可审计安全回移组件后以新版本发布 |
| Linux DEB/RPM | 四个原生包已发布；glibc 2.17 双架构闭包、安装后自检契约、扫描和哈希冻结通过 | 已满足；仍标注未真机验证 |
| 发布一致性 | GitHub 五个安装包与冻结目录哈希一致；CloudStudio Windows 镜像哈希一致；EdgeOne 已部署 | 已满足 |

## Win7 发布门禁复核

2026-08-17 使用正式校验器分别检查现有 x64/x86 wheelhouse，两个架构都因
`cryptography` 只有上游 `47.0.0`、缺少要求的
`47.0.0+partyops.1` 安全回移与证据而退出，返回码均为 2。

| 架构 | 有漏洞依赖 | 审计记录 | 去重公告 | 结论 |
| --- | ---: | ---: | ---: | --- |
| x64 | 9 | 54 | 45 | NO-GO |
| x86 | 6 | 45 | 36 | NO-GO |

直接升级也不能解决 Python 3.8 兼容性。官方 PyPI 元数据显示，当前修复版本的最低
Python 要求如下：

| 组件修复版本 | 官方最低 Python |
| --- | --- |
| [`cryptography 50.0.0`](https://pypi.org/project/cryptography/50.0.0/) | `>=3.9`（且无 CPython 3.8 Windows wheel） |
| [`Pillow 12.3.0`](https://pypi.org/project/pillow/12.3.0/) | `>=3.10` |
| [`click 8.3.3`](https://pypi.org/project/click/8.3.3/) | `>=3.10` |
| [`filelock 3.20.3`](https://pypi.org/project/filelock/3.20.3/) | `>=3.10` |
| [`python-dotenv 1.2.2`](https://pypi.org/project/python-dotenv/1.2.2/) | `>=3.10` |
| [`python-multipart 0.0.32`](https://pypi.org/project/python-multipart/0.0.32/) | `>=3.10` |
| [`requests 2.33.0`](https://pypi.org/project/requests/2.33.0/) | `>=3.10` |
| [`starlette 1.3.1`](https://pypi.org/project/starlette/1.3.1/) | `>=3.10` |
| [`urllib3 2.7.0`](https://pypi.org/project/urllib3/2.7.0/) | `>=3.10` |

`artifacts/inno-syntax-rc3-20260813` 下两个约 2.78 MB 的 Win7 EXE 只包含品牌资源、
路径校验脚本和占位 Updater 文件，用于证明 Inno Setup 脚本可编译；它们不含 PartyOps
运行时，不能上传或提供给用户。

Win7 放行必须同时满足：安全补丁逐项回移、漏洞复现先失败后通过、x64/x86 原生 wheel、
SBOM、VEX、PE 子系统/API 门禁、完整安装器自检和零高危/严重漏洞。由于 rc.3 标签与资产
已经冻结，满足条件后的 Win7 制品必须随新版本标签发布，不能覆盖 rc.3。

## 不可降级的阻断项

1. 不得把分支覆盖率失败改成警告或降低阈值。
2. 不得用“重复 cryptography 已解决”替代 Linux 完整 ABI/依赖闭包结论。
3. 不得以旧 Win7 依赖加忽略规则代替安全回移补丁、复现测试和 VEX。
4. 不得把 Inno 语法探针当成产品安装包；探针不含正式运行时。
5. 不得在没有最终制品时预填大小、SHA-256 或上传时间。
6. GitHub `prerelease=false` 只是发布属性，不代表真机验证或稳定承诺。
7. rc.3 是应用内升级信任基线；rc.2 → rc.3 需一次手工原位桥接，后续平台升级必须通过受保护公钥、精确平台选包、一致性备份和失败回滚。

## 用户接受的发布限制

无真机不再是独立硬阻断项。依据用户 2026-08-14 的明确指令，新增平台可以在真实构建、静态兼容、自检、依赖闭包、覆盖率、安全和哈希门禁全部通过后发布；官网、GitHub Release、发布清单与安装器必须持续标注“未真机验证”。后续使用者反馈不能倒填本次验收记录，只能形成新的现场验证记录或修复版本。

## 发布顺序

五个已放行制品已经按以下顺序完成：冻结干净提交与清单 → 构建五包 → 逐件本地验收/扫描 → CloudStudio 上传 Windows 镜像并验证 Magic/SHA → GitHub 普通 Release → EdgeOne 项目 `makers-gjuf8qcecmi3` → 桌面与移动端回归。Win7 不得跳过上述独立门禁，也不得补传语法探针。

回滚只恢复已保留的旧官网部署；不移动 rc.3 标签、不覆盖 rc.3 资产、不删除用户数据。
