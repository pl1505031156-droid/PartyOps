# PartyOps 1.4.3-rc.4 候选验收记录

最后更新：2026-08-17 16:02（北京时间，UTC+8）。结论：**七个真实产品安装包和七个签名更新包已完成本地放行；等待 CloudStudio、GitHub 与 EdgeOne 线上核验。**

## 本地验收结果

- 后端 892 项、前端 173 项测试全通过，行/分支覆盖率均达到本轮 90% 门槛；官网 rc.3 基线 33 项通过，rc.4 元数据落盘后再跑一次官网完整门禁。
- Windows 10/11 冻结运行时在当前 Win11 完成版本、SQLite 3.53.4、0019 schema 和关键模块烟雾测试。三个 Windows 安装器均包含完整前端资源并通过候选清单、MZ、位数和哈希核验。
- `D:\PartyOps-Custom-Test`、`E:\PartyOps-Custom-Test` 与 `D:\软件\PartyOps 自定义目录` 通过真实目录安全检查；普通用户确有删除子项权限的 `E:\codex` 祖先路径被正确拒绝，说明修复了误判但未拆除服务目录边界。
- Win7 x64/x86 由独立 Python 3.8 Legacy 链构建；安全回移、app-local UCRT、Tcl/Tk、SQLite/OCR、PE 子系统与导入 API 门禁通过。x86 按设计关闭语义重排和本地 LLM。
- Linux DEB/RPM 的 x86_64 与 ARM64 都是相应架构的真实原生制品；ARM64 RPM 在 AArch64 用户态构建，主程序为 AArch64 ELF，不是改标签包。包清单、同架构回滚包、wheelhouse 严格闭包和更新事务门禁通过。
- 七个 `.partyops-update` 均通过 Ed25519 签名、平台矩阵和载荷哈希验证；format v3 更新目录覆盖 `windows`、`windows7`、`linux-deb`、`linux-rpm` 及对应架构。
- 火绒扫描时间为 2026-08-17 15:53:04（北京时间），病毒库时间为 2026-08-16 18:37:36；7 文件、22735 对象、40 秒、威胁 0。

## 尚未冒充完成的事项

- 没有 Windows 7、麒麟、UOS、deepin、openEuler 真机，故发布页和官网持续显示“未真机验证”。当前 Win11 的运行烟雾不能替代这些系统的原生安装验收。
- 没有代码签名证书，rc.4 显示“未签名候选版”；内部自校验不能替代发布者身份签名。
- CloudStudio 现有直链会忽略 Range 请求，虽然本机实测约 27.4 MB/s，但慢网络不能续传或并行分段。rc.4 上线前必须用受信任的 EdgeOne 下载加速/分片回源能力或修复源站 Range，禁止第三方 GitHub 代理。

本轮按用户要求不使用 Codex Security 插件、不使用远端 CI/CD，也不强制 Docker。安全门禁由本机测试、依赖审计、静态分析、真实构建、二进制与归档检查、恶意软件扫描以及后续浏览器回归承担。
