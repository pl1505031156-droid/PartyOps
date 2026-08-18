# PartyOps 1.4.3-rc.5 发布就绪判定

最后核对：2026-08-18 11:58（北京时间，UTC+8）。当前结论：**rc.5 七平台不可变制品已完成本地冻结门禁，可以按 CloudStudio → GitHub 普通 Release → EdgeOne 官网的顺序发布；它仍是未签名候选版，不是稳定版。**

## 已通过门禁

- 后端完整套件 892/892、前端 173/173；行/分支覆盖率达到本轮 90% 门槛，类型检查、生产构建和静态资源闭包通过。
- 依赖锁一致性、`pip check`、pip-audit、前端/官网生产依赖审计、Bandit 高/中危和 gitleaks 通过；已知高危/严重漏洞为 0。
- Windows 10/11 rc.5 EXE 已在 Win11 自定义非 C 盘中文/空格目录执行真实安装、升级、保留数据卸载、重装、服务和健康检查。Win7 x64/x86 从同一源码冻结并通过 PE/架构/资源/API 静态门禁，但未真机运行。
- 四个 Linux 原生包通过包头、架构、资源模式、严格依赖闭包、同架构回滚载荷与 glibc 2.17 运行时门禁；ARM64 RPM 在 AArch64 用户态完成 rpmbuild。
- 七个安装包形成唯一清单，版本均为 rc.5；七个 Ed25519 更新包和 format v3 目录逐项验签。ClamAV 1.5.3/28094 扫描七个安装包感染 0。
- 官网候选元数据只允许这七个 rc.5 文件，历史下载计数继续使用稳定全局键和基数 92；更换域名、部署或制品 URL 不重置总数。

## 线上放行条件

1. CloudStudio 先上传只含当前 rc.5 的 Windows 下载目录，核验完整下载长度、MZ、SHA-256、Content-Type 与实际速度；记录 Range 是否为 206，不虚构支持。
2. 新 CloudStudio 部署核验后，只按精确 share link 取消旧 PartyOps CloudStudio 部署，不删除新部署或下载计数数据。
3. GitHub 推送 `main` 文档/工具更新，创建指向产品源码提交 `72007121b1b84f56508bd5c163857a01b39aee8e` 的不可变标签 `v1.4.3-rc.5`，并创建 `prerelease=false`、`make_latest=false` 的普通 Release；保留历史 Release 供审计。
4. EdgeOne 只部署到项目 `makers-gjuf8qcecmi3`。官网只展示七个 rc.5 安装包，发布时间统一为北京时间，并重新执行桌面/移动端、下载、更新日志、简历、二维码和全局计数回归。

## 公开限制与回滚

Windows 7 与国产 Linux 缺少对应真机，不宣称原生运行验收通过。Win7 仅建议在受控局域网使用，x86 不含语义重排和本地 LLM；所有 rc.5 Windows 安装器未做商业代码签名。

应用更新失败使用包内回滚事务和升级前数据快照。官网可回滚 EdgeOne 页面部署，但 CloudStudio 按“只存最新安装包”的要求不保留旧下载源；GitHub 历史 Release 保留为审计与应急人工取回渠道。
