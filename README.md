<div align="center">

# 党建智办 PartyOps

### 面向基层党建工作的本地优先协同工作台

把事项办理、跨机文件、重要档案、迎检材料、通知评论和工作留痕，收进一套真正能落地的局域网协同闭环。

[![Release](https://img.shields.io/badge/release-v1.4.5--rc.4-b42318?style=for-the-badge)](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.5-rc.4)
[![Source](https://img.shields.io/badge/source-v1.4.5--rc.4-c58b3d?style=for-the-badge)](docs/release-notes-v1.4.5-rc.4.md)
[![License](https://img.shields.io/badge/license-GPL--3.0-292520?style=for-the-badge)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-c17b17?style=for-the-badge)](#安装教程)
[![Local first](https://img.shields.io/badge/data-local--first-2f7d57?style=for-the-badge)](#安全与隐私)
[![GitHub stars](https://img.shields.io/github/stars/pl1505031156-droid/PartyOps?style=for-the-badge&color=b42318)](https://github.com/pl1505031156-droid/PartyOps/stargazers)

[官方网站](https://www.partyops.cn/) · [下载安装](#下载) · [界面实景](#系统主界面实景) · [核心亮点](#partyops-的亮点) · [安装教程](#安装教程) · [更新记录](CHANGELOG.md) · [参与共建](#参与共建)

</div>

> [!IMPORTANT]
> 当前源码与发布目标为 `1.4.5-rc.4`，数据库模式仍为 `0024`；本版修复 rc.2/`0023` 原位升级时升级前备份过早使用新 ORM 导致的 Windows/Linux/macOS 共享启动故障，并为备份、迁移、校验和中断恢复建立模式无关启动事务。支持等级只以[rc.4 机器可读矩阵](docs/support-matrix-1.4.5-rc.4.json)和 Release 冻结清单为准，未通过原生构建与覆盖升级门禁的架构不得冒充可用。

## 当前公开发布

| 项目 | 当前状态 |
| --- | --- |
| 公开版本 | [`v1.4.5-rc.4`](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.5-rc.4)，GitHub Pre-release |
| 发布时间 | 以 GitHub Release 与官网显示的北京时间为准 |
| 冻结源码 | 不可变标签 [`v1.4.5-rc.4`](https://github.com/pl1505031156-droid/PartyOps/tree/v1.4.5-rc.4) |
| 官方网站 | [https://www.partyops.cn/](https://www.partyops.cn/) |
| 制品校验 | 当前九个 Windows/Linux/macOS 主安装包以同一 Release、官网和机器可读清单为准 |
| 发布边界 | 只有完成对应原生构建和目标系统门禁的制品才提供下载；缺少商业签名或用户真机交互验收的平台标为 preview，未构建架构标为 unavailable |

Release 当前提供九个 Windows/Linux/macOS 主安装包、可选 `.sha256`、构建证明、发布清单、SBOM、安全门禁和验收记录。rc.3 因 `0023 → 0024` 原位升级故障已撤回并仅保留审计记录；rc.4 重新构建全部平台，不能把 rc.3 制品改名复用。macOS 支持等级仍以当前发布清单为准，签名、公证和用户设备验证边界必须如实保留。历史版本变化仍在 `CHANGELOG.md` 中追溯。

## 30 秒了解 PartyOps

PartyOps 不是一套把表单搬到浏览器里的系统。它解决的是基层办公最常见的断点：事项散落在聊天记录里，材料留在不同电脑上，档案与办理过程彼此脱节，迎检时再临时拼接。

系统以一台单位主机保存权威数据库和受管附件，Windows 或 UOS 协同电脑通过受控 Agent 接入。团队成员可以发布自己电脑上的真实文件夹，在权限范围内互相浏览、阅读、下载和转发；事项、材料、档案、评论、通知与审计记录始终沿同一条责任链关联。

### 1.4.5-rc.4：让原位升级先保数据、再迁移

- **模式无关备份**：旧库仍在 `0023` 时不加载 `0024` ORM；SQLite 在线快照与附件归档完整校验后才进入迁移。
- **原子回滚**：迁移、FTS5、管理员不变量或健康检查失败时恢复已验证快照，并保留半迁移数据库用于诊断。
- **中断恢复**：迁移过程中断电或强退，下次启动根据持久化事务状态先回滚，再重新执行完整升级。
- **准确诊断**：缺表/缺列报告 `DATABASE_SCHEMA_FAILED`，备份失败报告 `UPGRADE_BACKUP_FAILED`；不再把结构问题误称 SQLite DLL 损坏。
- **全平台重发**：Windows、DEB、RPM、macOS 九个主包必须来自同一冻结源码，并分别通过全新安装与 rc.2 覆盖升级门禁。

### 1.4.5-rc.3：内嵌排版、通用台账与真实进度（已撤回）

- **内嵌公文排版**：无需另开助手窗口，页面内完成本机选文、诊断、确认、排版、复检和导出；文件内容不经过主机或 AI。
- **通用台账导入**：电子表格先剖析、再映射、全量校验后提交；低置信字段和重复记录必须人工确认，批次可安全撤销。
- **真实进度时间轴**：发展党员事实与未来计划统一显示；上游事实变化只重算尚未发生的节点，已有记录不被覆盖。
- **业务生命周期**：发展党员、会议、文档、学习计划、档案类别、目录、AI 策略、备份和模型包均有归档/停用、影响预检与恢复路径。
- **安装与数据盘**：修复 `[INSTALL_DIR_ACL_DENIED]` 递归误报，并支持普通账号创建、管理员安全接管的 D/E 盘空数据目录。
- **离线意图预览**：Needle 2 只生成结构化预览，低置信、提示注入、否定词和越权请求都会回退或要求确认。

### 1.4.5-rc.2：本机公文排版与跨平台可靠性

- **公文规范排版**：按 GB/T 9704-2012 在本机完成诊断、修改预览、排版、复核和 DOCX 导出；文件不进入主机、协同机、AI 或数据库。
- **协同地址事务**：本机浏览、监听、自动探测和对外公布地址分离；换址联动证书 SAN、健康检查、旧协同机迁移和失败回滚。
- **文件打开状态机**：一次性授权持久化记录兑换、打开、完成、过期和失败，PDF 同源预览与 WPS 本机打开不再共用笼统错误。
- **首轮节点预测**：发展党员全部后续节点直接显示法定边界或参考计划，预测日期不冒充实际发生日期。
- **安装启动诊断**：麒麟安装必需步骤快速返回，Windows/macOS/Linux 在进入运行时前输出机器可读探针，完整卸载预检失败自动保留数据。

### 1.4.5-rc.1：把党务业务真正做成闭环

- **三会一课**：支部党员大会、支委会、党小组会和党课都有独立模板、年度台账、出席记录、决议落实、材料检查与归档。
- **中心组学习**：从年度计划、专题安排、会前材料、集体研讨到调研成果和成果转化，使用专属台账推进。
- **发展党员**：输入申请书日期即可生成参考计划，同时清楚区分实际日期、法定边界和内部参考日期，系统推算不替代组织研究审批。
- **一事一档多文件**：一次选择多个资料，逐文件上传、失败重试；业务附件删除后保留 30 天，可按权限恢复。
- **本机智能**：先检测 CPU、内存、显存、磁盘和运行后端，再从 Needle、BGE 与 Qwen3 的 12 个档位中给出量化和资源建议；不安装模型也不影响全部核心业务。
- **身份重新配置**：已经配置过的电脑可从设置重新打开向导，在个人、主机和协同机之间重新选择，并保留业务数据。

## 系统主界面实景

下面全部来自 PartyOps 的真实浏览器流程和演示数据，不是设计稿。

<table>
  <tr>
    <td width="33%"><img src="docs/images/partyops-today-1.4.3.png" alt="PartyOps 1.4.3 今日工作台" width="100%"><br><strong>今日工作台</strong></td>
    <td width="33%"><img src="docs/images/partyops-memo-1.4.3.png" alt="PartyOps 1.4.3 本机私有备忘录" width="100%"><br><strong>本机私有备忘录</strong></td>
    <td width="33%"><img src="docs/images/partyops-party-development-1.4.3.png" alt="PartyOps 1.4.3 发展党员时间计算" width="100%"><br><strong>发展党员时间计算</strong></td>
  </tr>
</table>

### 今日工作台：一周工作，一处看清

<img src="docs/images/partyops-workbench-overview.png" alt="PartyOps 今日工作台，展示本周完成、下周计划、风险提醒和工作状态" width="100%">

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/partyops-task-collaboration.png" alt="PartyOps 事项与清单界面" width="100%"><br>
      <strong>事项与清单</strong><br>
      表格、看板、日历和时间轴共享同一份事项数据，主办、协办、审核、截止时间和材料完整度一起呈现。
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/partyops-my-work.png" alt="PartyOps 我的工作界面" width="100%"><br>
      <strong>我的工作</strong><br>
      只显示当前账号真正需要处理的主办、协办、审核和步骤分派，普通协同人员不会看到空白管理页。
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/partyops-file-center-overview.png" alt="PartyOps 原始文件中心界面" width="100%"><br>
      <strong>原始文件中心</strong><br>
      主机文件、协同机共享和本机共享统一显示，原文件保持原位，按权限完成索引、阅读、下载、转发和固化。
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/partyops-important-archives.png" alt="PartyOps 重要档案中心界面" width="100%"><br>
      <strong>重要档案中心</strong><br>
      年度目录、档案类别、扫描件、OCR、业务关联和权限贡献形成完整档案闭环。
    </td>
  </tr>
</table>

<details>
<summary><strong>展开查看：跨机器文件与离线文档阅读实景</strong></summary>
<br>
<table>
  <tr>
    <td width="50%"><img src="docs/images/file-center-office-preview.png" alt="PartyOps Office 文档结构化阅读" width="100%"><br><strong>Office 文档结构化阅读</strong></td>
    <td width="50%"><img src="docs/images/file-center-pdf-preview.png" alt="PartyOps PDF 版面阅读" width="100%"><br><strong>PDF 原始版面与结构检查</strong></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/images/cross-device-office-preview.png" alt="PartyOps 跨协同电脑文件阅读" width="100%"><br><strong>协同机文件经主机中转、哈希校验后直接阅读</strong></td>
  </tr>
</table>
</details>

## PartyOps 的亮点

| 今日工作一处看清 | 三会一课与中心组 | 发展党员时间轴 |
| --- | --- | --- |
| 本周完成、下周计划、临期风险、党务季度缺口、待归档会议和逾期决议汇总在同一工作台。 | 两个独立业务模块分别提供专属模板、流程、出席/发言记录、整改或成果转化、台账和导出。 | 只填申请书日期即可生成参考计划；实际、法定和参考日期分轨显示，规则版本随档案保存。 |
| **事项、汇总与多文件** | **真实文件与重要档案** | **本地协同与可选智能** |
| 主办、协办、审核、步骤、材料、评论和通知围绕同一事项推进；资料可批量上传、逐项重试并在删除后 30 天内恢复。 | 已授权用户可发布本机真实文件夹；常见办公文档可直接阅读，重要档案保留 OCR、版本、权限、业务关联和审计链。 | 数据默认留在本机或单位主机；设置会按硬件推荐本地模型，模型只增强搜索、草稿、提示和操作预览，核心业务不依赖模型。 |

## 六大工作域

| 工作域 | 面向谁 | 主要能力 |
| --- | --- | --- |
| **今日** | 所有人 | 一周总览、临期风险、必须办理、日历节点、周期汇总与本机私有备忘 |
| **党务** | 党务经办人、记录人、管理员 | 三会一课、中心组学习、发展党员和其他党建会议的专属工作台、流程、台账与归档 |
| **工作** | 主办、协办、审核人 | 事项与清单、我的工作、通知、日历、收件箱、报告、日志和专题空间 |
| **资料** | 档案与材料经办人 | 原始文件中心、跨机共享、重要档案、迎检归档、知识库、文档比较与查重 |
| **协同** | 主机与协同机用户 | 设备入网、共享目录、接收箱、三种传输、目录与人员授权 |
| **管理** | 有效能力对应的管理员 | 周期模板、自动归档、报告模板、本机智能、模型导入、更新、备份、诊断与帮助 |

## 主机与协同机的界面差异

前端不靠零散的 `role === admin` 决定按钮，而是读取后端返回的有效能力。用户只看见自己能完成的真实操作。

| 使用位置与账号 | 可以操作的内容 |
| --- | --- |
| 主机管理员 | 主机目录纳管、团队文件、设备/目录/人员授权、档案类别、更新、备份、诊断和 AI 管理 |
| 主机普通用户 | 团队文件浏览下载、业务关联、自己的事项和传输；隐藏系统管理入口 |
| 协同机管理员 | 发布本机目录、团队文件双通道下载，同时保留获授权的全局管理能力 |
| 协同机普通用户 | 发布和管理自己的本机目录、浏览团队目录、阅读下载文件、处理自己的传输与工作 |

## 版本演进

| 版本 | 这一阶段解决了什么 | 发布状态 |
| --- | --- | --- |
| `1.4.0` | 重要档案贡献权限、通知评论、我的工作、共享目录审批和跨平台更新适配 | 内部候选 |
| `1.4.1` | 普通用户发布共享目录、团队/指定成员授权、双通道下载和本地 AI 分能力激活 | 内部候选 |
| `1.4.2-rc.1` | AnyDoc / pdf-inspector 离线文档阅读、跨机文件直接阅读、权限撤销复核和发布链路收口 | 历史 Pre-release |
| `1.4.3-rc.1` | 本机私有备忘、2026 新版细则党员发展计算、专业中文 Word 导出与单位补充材料分层 | 历史 Pre-release，已被 rc.2 取代 |
| `1.4.3-rc.2` | 自定义数据盘、主机服务可诊断启动、单 EXE 下载、前端资源闭包及 UOS 双架构严格离线校验 | 历史候选，已被 rc.3 取代 |
| `1.4.3-rc.3` | 个人模式、安全卸载、中文策略诊断、系统内升级与国产 Linux 原生 DEB/RPM | 历史普通 Release；已被 rc.4 取代 |
| `1.4.3-rc.4` | 加入 Win7 x64/x86 完整主机、官方 UCRT 与冻结 GUI 实测门禁 | 历史候选；已被 rc.5 取代 |
| `1.4.3-rc.5` | 彻底修复自定义程序目录误拦截，收敛目标 ACL 与高完整性标签，补齐覆盖升级、保留数据卸载和空目录清理 | 历史候选；已由 rc.6 取代 |
| `1.4.3-rc.6` | 修复 Windows PowerShell 5.1 安装失败、Win7 误装、冻结向导缺包、麒麟 ARM64 共享库权限及全平台桌面入口静默失败 | 历史候选；已由 rc.7 取代 |
| `1.4.3-rc.7` | 修复个人模式连接拒绝、网页与服务版本不一致、Win7 Python 3.8 首页错误、Linux 安装后无响应和桌面启动静默失败 | 历史候选；已由 rc.8 取代 |
| `1.4.3-rc.8` | 修复国产 Linux 启动链并新增 macOS Apple Silicon/Intel 原生候选包 | 历史候选；已由 rc.9 取代 |
| `1.4.3-rc.9` | 修复跨平台安装/启动/端口/服务冲突、麒麟与 macOS 双击无响应，并补齐回退、目录操作和 DOCX 导出 | 历史候选；已由 1.4.4 取代 |
| `1.4.4` | 修复协同凭据升级、跨平台静默启动、文件授权和通知改期，增加会议筹备、在线文档、用户归档及发展党员全周期档案 | 历史版本；已由 1.4.5-rc.1 取代 |
| `1.4.5-rc.1` | 新增三会一课、中心组学习、发展党员三轨时间轴、多文件可恢复资料、本机硬件检测、12 档模型推荐和身份重新配置 | 历史版本；已由 rc.2 取代 |
| `1.4.5-rc.2` | 新增本机公文规范排版，修复协同地址、文件打开、发展党员预测、麒麟安装、跨平台启动与卸载保护 | 已验证回滚基线；rc.4 发布前官网暂时提供此版 |
| `1.4.5-rc.3` | 新增内嵌公文排版、可撤销通用台账导入、真实进度时间轴和 Needle 2，补齐生命周期、提醒、ACL 与非 C 盘路径 | 已撤回；旧库原位升级会在迁移前启动失败，仅保留审计 Release |
| `1.4.5-rc.4` | 修复 `0023 → 0024` 升级前备份缺列崩溃，新增模式无关备份、原子回滚、中断恢复和准确启动诊断 | 当前预发布；支持等级、签名和真机验证状态以发布清单为准 |

完整变更、修复与安全说明见 [CHANGELOG.md](CHANGELOG.md)。PartyOps 不会为了看起来“已发布”而隐藏未完成门禁，版本证据、制品哈希和已知限制都会随 Release 一起公开。

## 跨机器文件如何工作

```mermaid
flowchart LR
    A["协同机 A：用户选择并发布文件夹"] --> B["协同 Agent：只上报相对路径与索引"]
    B --> C["PartyOps 主机：统一目录、授权与审计"]
    C --> D["协同机 B：点击预览或下载"]
    D --> E["按需分块拉取、源变化检查、SHA-256 校验"]
    E --> F["浏览器本地解析阅读，或 Agent 保存到本机接收目录"]
```

主机不会保存协同机的绝对路径。目录停用、成员权限撤销或设备隔离后，正在进行的传输会在创建、分块和最终读取阶段重新校验并停止。结构化阅读默认限制为 64 MiB 和 200 万字符；超过限制的文件仍可下载，PDF、图片和文本在浏览器支持范围内可使用原始预览。

文件解析使用 [Firecrawl AnyDoc](https://github.com/firecrawl/anydoc) 与 [Firecrawl pdf-inspector](https://github.com/firecrawl/pdf-inspector) 的官方 WebAssembly 包，并在一次性 Web Worker 中离线执行。详细许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 下载

当前可下载版本为 [v1.4.5-rc.4](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.5-rc.4)：

1.4.5-rc.4 使用版本化文件名和不可变标签。Windows 安装器支持本机固定 D/E 盘、中文与空格目录，并对跨账号目录执行受控所有权和 ACL 收敛；Win7 使用独立 Python 3.8 Legacy 包并随附 UCRT/API-set；国产 Linux 四个入口共用固定运行时和 Bash 桌面启动链。请以 Release/官网显示的支持等级、上传时间、大小与 SHA-256 为准。

- [Windows 10/11 x64 单文件安装器](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_windows_amd64.exe)
- [Windows 7 SP1 x64 单文件安装器](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_windows7_amd64.exe)
- [Windows 7 SP1 x86 单文件安装器](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_windows7_x86.exe)
- [麒麟/UOS/deepin AMD64 DEB](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_linux_amd64.deb)
- [麒麟/UOS/deepin ARM64 DEB](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_linux_arm64.deb)
- [openEuler x86_64 RPM](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps-1.4.5-0.rc.4.1.x86_64.rpm)
- [openEuler ARM64 RPM](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps-1.4.5-0.rc.4.1.aarch64.rpm)
- [macOS 11+ Apple Silicon PKG](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_macos_arm64.pkg)
- [macOS 11+ Intel PKG](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.5-rc.4/PartyOps_1.4.5-rc.4_macos_x86_64.pkg)

普通 Windows 用户只需下载一个 EXE。安装器会校验其内部载荷；最终文件大小与 SHA-256 直接显示在 Release 和官网，无需再下载第二个“校验包”。同名 `.sha256` 仅为自动化工具提供，不是安装必需步骤。

国产 Linux 用户不再需要下载“构建套件 + 校验包”。每台电脑只下载一个与 CPU 架构匹配的 DEB 或 RPM：飞腾 D2000/FT-2000、麒麟 9000C/9006C/990、鲲鹏通常选 ARM64，海光/兆芯/Intel/AMD 通常选 AMD64；最准确的方法是运行 `uname -m`，`aarch64` 对应 ARM64、`x86_64` 对应 AMD64。银河麒麟桌面 V10 SP1 的 2107/2203/2303/2403/2503 使用同一套选包规则，不需要按小版本重复下载。龙芯 LoongArch 与 RISC-V 本轮没有对应包，不要强制安装其他架构。

Windows 7 x64 提供完整主机、协同、OCR 和语义重排；x86 提供核心主机、协同、数据库、文件、档案、备份和 OCR。Win7 不内置 `llama.cpp` 大模型运行时：上游当前 Windows 二进制直接依赖 Win8+ API 与新版 MSVC ABI，强行打包会造成启动或推理崩溃；需要本地大模型时应在受支持的 Windows 10/11 或 Linux 主机运行，再通过受控接口接入。x86 受 32 位地址空间限制也不启用语义重排。两者均使用独立 Python 3.8 Legacy 锁、经证据校验的安全回移组件、Microsoft 官方 app-local UCRT 与 VC142 运行库；由于没有 Win7 真机，仍不能把静态/冻结验证表述为真机通过。

macOS 1.4.5-rc.4 提供 Apple Silicon 与 Intel 两个原生 PKG。两者分别在 `macos-15` 与 `macos-15-intel` 原生主机完成安装、干净环境/污染环境重复自检、LaunchAgent 启动、真实 `open -na` LaunchServices 和 `0023 → 0024` 覆盖升级门禁；原生入口会在 Python 运行时前写入 `launch-probe.log`，清理 PyInstaller/Python/DYLD 继承环境，并记录脱敏 stderr 与退出码。安装包采用 ad-hoc 签名、未公证，首次安装须先核对 SHA-256，再按 macOS“隐私与安全”界面放行；不要全局关闭 Gatekeeper。

## 安装教程

### 安装前准备

1. 选定一台长期在线、磁盘可靠的电脑作为主机；SQLite 数据目录不得放在网络共享盘。
2. 为主机设置固定局域网地址或稳定主机名，并确认所有电脑使用“专用网络”。
3. Windows 普通用户从官网或同一个 GitHub Release 下载单个 EXE；不需要额外下载校验文件。
4. 安装前用下面命令计算 SHA-256，并与官网或 Release 页面直接显示的值逐字核对。

Windows PowerShell：

```powershell
Get-FileHash .\PartyOps_1.4.5-rc.4_windows_amd64.exe -Algorithm SHA256
Get-AuthenticodeSignature .\PartyOps_1.4.5-rc.4_windows_amd64.exe
```

Linux：

```bash
dpkg --print-architecture
sha256sum PartyOps_1.4.5-rc.4_linux_amd64.deb
```

macOS：

```bash
uname -m
shasum -a 256 PartyOps_1.4.5-rc.4_macos_arm64.pkg
```

### Windows 10/11 x64

1. 双击 `PartyOps_1.4.5-rc.4_windows_amd64.exe`。未签名候选出现 SmartScreen 时，先核对 SHA-256，再选择“更多信息 → 仍要运行”。
2. PartyOps 中文安装向导会分别询问程序安装目录和业务数据目录；两者都可自定义，升级时会保留原选择。数据目录建议使用 `D:\PartyOps-数据` 等本机固定磁盘目录，支持中文和空格，不支持磁盘根目录、系统目录、网络盘或移动盘。
3. 首次打开“党建智办”，明确选择角色：
   - **个人使用（新手推荐）**：无需管理员授权，只在本机使用，不安装服务、不开放局域网。
   - **主机**：再次确认数据目录；UAC 只用于写入系统配置和启动服务。数据库、附件、备份、证书、模型、缓存和日志全部进入所选目录；C 盘只保留程序和小型引导配置。
   - **协同机**：填写主机地址并完成配对；备份、接收文件和运行日志跟随所选本机数据目录，小型入网配置保留在 `%LOCALAPPDATA%\PartyOps`。
4. 主机管理员在“管理 → 设备协同”批准设备，并按用户、设备和目录授予能力。
5. 协同用户在“资料 → 原始文件”点击“共享本机文件夹”，用系统选择器选中目录，再设置团队或指定人员范围。
6. 从另一台电脑登录，确认可以浏览、打开阅读、浏览器另存为或下载到本机接收目录。

向导会依次显示“服务注册、SCM 启动、子进程启动、端口监听、健康检查、局域网地址就绪”。遇到失败先点击“复制诊断”或“打开日志”，不要先手动修改 `services.msc`。未就绪时 PartyOps 不会打开空白地址，也不会降级为普通用户进程。

### Windows 7 SP1 x64/x86

1. 仅在已停止系统级安全维护风险可控的局域网电脑使用，并先完成 SP1、KB2533623（或包含同等 Loader API 的后续汇总更新）和 Universal CRT 更新；安装器直接探测系统能力，不再只按补丁名称判断。
2. 64 位系统下载 `PartyOps_1.4.5-rc.4_windows7_amd64.exe`；32 位系统下载 `PartyOps_1.4.5-rc.4_windows7_x86.exe`。不要按 CPU 品牌猜测，先打开“控制面板 → 系统”查看系统类型。若出现 `api-ms-win-core-path-l1-1-0.dll` 缺失，说明误用了 Windows 10/11 通用包；不要下载单个 DLL，改下正确的 Win7 专用包。
3. 安装与首次配置同样支持受管理员保护的本机 D/E 盘、中文和空格目录。不要选择磁盘根目录、网络盘、移动盘、目录联接或允许普通用户替换文件的公共目录。
4. 1.4.5-rc.4 安装器在目标电脑真实启动刚释放的主程序，并分别检查全新 `0024` 空库与真实 `0023` 覆盖升级、RSA/Fernet、SQLite/FTS5、健康端点、运行版本和首页入口；失败会回滚并在安装日志中保留原因，不会显示安装成功后才让桌面图标静默失败。
5. Win7 x86 不提供语义重排与本地 LLM；这不会影响核心主机、文件协同、档案、备份和中文 OCR。Win7 不捆绑第三方浏览器，请使用单位安全策略允许的浏览器访问。若仍出现 `CHILD_EXITED`，请保留安装日志与提示路径中的 `launcher.log`；不要删除业务数据，下个候选版会按日志中的真实异常栈定点处理。

### 麒麟 / UOS / deepin / openEuler

银河麒麟桌面 V10 SP1 的系列版本、处理器示例与验收边界见[兼容说明](docs/kylin-v10-sp1-compatibility.md)。

先确认架构：

```bash
dpkg --print-architecture
```

海光、兆芯、Intel、AMD 通常使用 `amd64/x86_64`；飞腾 D2000/FT-2000、麒麟 9000C/9006C/990、鲲鹏等使用 `arm64/aarch64`。银河麒麟桌面 V10 SP1 2107—2503、UOS、deepin 下载 DEB，openEuler 下载 RPM。截图中 `D2000`、`HUAWEI Kirin 9000C` 且 `uname -m` 返回 `aarch64` 的电脑，都选择同一个 ARM64 DEB：

```bash
sudo install -m 0644 ./PartyOps_1.4.5-rc.4_linux_amd64.deb /var/tmp/partyops.deb
sudo apt install /var/tmp/partyops.deb
# ARM64 把第一行文件名改为 PartyOps_1.4.5-rc.4_linux_arm64.deb
```

```bash
sudo dnf install ./PartyOps-1.4.5-0.rc.1.1.x86_64.rpm
# ARM64 改用 PartyOps-1.4.5-0.rc.1.1.aarch64.rpm
```

安装后从应用菜单打开“党建智办”，按与 Windows 相同的向导选择个人、主机或协同机。启动器会先等待配置页或健康端点真正就绪，再打开系统默认浏览器；若浏览器关联失败会显示中文提示，诊断位于 `~/.config/partyops/desktop-launch.log`。Windows 桌面入口也会在默认浏览器关联损坏或协同页面准备超时时显示中文弹窗，不会静默退出。主机服务数据默认位于 `/var/lib/partyops`；日常用户的协同配置位于 `~/.config/partyops`，接收目录位于用户数据目录。无 sudo 的日常账号应由管理员安装，不要在 root 桌面完成普通用户配置。

### macOS 11+ Apple Silicon / Intel

1. 点击苹果菜单 → “关于本机”：Apple M 系列下载 `macos_arm64.pkg`，Intel 处理器下载 `macos_x86_64.pkg`；终端 `uname -m` 也会分别显示 `arm64` 或 `x86_64`。
2. 用 `shasum -a 256 <文件名>` 核对官网或 Release 显示的 SHA-256。当前候选包没有 Developer ID 和公证：在 Finder 按住 Control 点击下载的 PKG →“打开”；若仍被阻止，先尝试打开一次，再到“系统设置 → 隐私与安全性”点击对应的“仍要打开”，完成管理员授权并安装到 `/Applications`。
3. 安装完成后，在 Finder 的“应用程序”中按住 Control 点击“党建智办”并选择“打开”；如系统再次拦截，按同样方式在“隐私与安全性”确认。不要执行全局关闭 Gatekeeper 的命令。
4. 选择个人、主机或协同模式并等待页面就绪。未打开时依次查看 `~/Library/Logs/PartyOps/launch-probe.log`、`launcher.log` 与 `launch-stderr.log`；前置探针会在冻结向导启动前创建日志，并保留子进程 PID、架构、退出码和标准错误。反馈 Mac 型号、芯片、macOS 版本及脱敏后的日志末尾，不要上传真实业务数据。

### 首次组网与目录共享

1. 主机管理员创建普通协同账号。
2. 每台协同电脑安装相同版本，选择“协同机”，提交配对请求。
3. 管理员批准设备；普通用户登录协同机。
4. 点击“共享本机文件夹”，系统会签发与用户和设备绑定、60 秒内单次有效的本机操作令牌。
5. 选择真实文件夹，设置团队共享或指定成员及浏览/下载/发送权限，执行“立即同步”。
6. 文件中心会显示“主机文件”“某协同机共享”“本机共享”和在线/同步/权限状态。
7. 远端文件点击后先拉取并校验，再显示结构化阅读或原始预览；下载可选择浏览器另存为或当前协同机接收目录。

### 升级、备份与回滚

- rc.2 已执行一次显式信任根迁移：新公钥 SHA-256 指纹为 `7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3`。私钥仅保存在本机受限发布目录且不进入 Git、日志或制品；从 rc.1 及更早版本升级必须运行 rc.2 完整安装器，不能依赖旧信任根的系统内更新。
- 日常升级：管理员在“管理 → 系统更新”查看官方签名目录。系统每天至多自动检查一次，只在后台下载本机对应的 Windows、DEB 或 RPM 单平台签名更新包；弱网和关机中断后从已校验位置续传，已完整校验的包不会重复下载。
- 专业门禁：安装前再次确认版本、上传时间和中文更新内容，并逐层验证 Ed25519 目录签名、更新包签名、文件大小、SHA-256、平台和架构。主机升级成功并通过版本/数据库/健康检查后，协同电脑才分别读取官方目录并获取自己的平台制品。
- 升级前：系统自动创建一致性数据库、附件和档案快照；重要升级仍建议手工导出一份完整备份到独立介质。
- 失败回滚：安装、迁移或健康检查任一步失败都会尝试恢复上一版本程序和升级前数据；回滚未完成时服务保持停止并显示中文诊断编号，禁止带病继续运行。
- 不要通过复制正在运行的 SQLite 文件做备份，也不要混用不同版本的主机和协同 Agent。

当前版步骤见[1.4.5-rc.4 安装、升级与回滚](docs/upgrade-1.4.5-rc.4.md)；rc.3 的撤回原因见[撤回说明](docs/withdrawal-v1.4.5-rc.3.md)，上一条安全回滚基线见[1.4.5-rc.2 升级与回滚](docs/upgrade-1.4.5-rc.2.md)。通用说明另见[备份恢复手册](docs/backup-restore.md)和[长期运行手册](docs/operations-runbook.md)。

### 卸载

- Windows：在“设置 → 应用”卸载“党建智办 PartyOps”。选“仅删除程序”会保留业务数据；选“彻底卸载”会删除经安全预检证明属于 PartyOps 的本机数据。彻底卸载不可恢复，请先验证备份。
- UOS：`sudo apt remove partyops`。如需删除 `/var/lib/partyops`，必须先验证备份且由管理员明确执行，程序不会把删除业务数据作为普通升级步骤。

## 可选本地智能

主程序不强制捆绑模型权重。管理员先在“设置 → 本机智能能力”执行本机检测，系统按处理器、架构、当前可用内存、模型目录空间和 GPU 后端给出“流畅、可用、不建议”结果，并至少为系统和 PartyOps 保留 `max(2GB, 总内存的 25%)`。

- 受控意图：Needle 2 用于把中文指令整理为事项预览、字段和提醒建议；任何新增、修改、删除、发送、导出和身份切换都必须由用户明确确认。
- 中文检索：BGE Small、Base、Large 三档，用于事项、档案、知识和已授权共享目录的语义检索。
- 本地草稿：官网签名直导档包括 Qwen2.5 0.5B、Qwen3 0.6B 和 DeepSeek R1 Distill Qwen 1.5B；更大的 Qwen3 与 DeepSeek R1 Distill 7B 及以上模型从发布方官方仓库取得，通过本机 OpenAI 兼容服务接入。

体积适中的模型只有在来源、许可证、逐文件 SHA-256、Ed25519 签名和平台运行门禁全部完成后，才通过官网 `.partyops-modelpack` 直接下载。大模型不复制到官网：从 Qwen 或 DeepSeek 官方仓库取得权重后，使用官方 `llama.cpp` 的 `llama-server` 在本机提供 OpenAI 兼容接口，再在 PartyOps AI 配置中接入。未签名模型包会被拒绝；普通 GGUF 只能作为可信回环服务接入，不得绕过验签或把未认证端口开放到网络。

本地 LLM 只生成带来源的草稿，不自动修改事项、档案或文件权限；未获正文索引授权的共享内容不会进入提示词。模型不存在、资源不足或推理失败时，系统自动降级为规则推荐与普通检索。完整分档与新手接入说明见[官网本地模型页](https://www.partyops.cn/models)。

## 从源码运行

开发环境要求 Python 3.11—3.13、Node.js 22、Corepack 和 pnpm 11.9。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt -r backend\requirements-dev.txt
corepack enable
corepack pnpm --dir frontend install --frozen-lockfile
.\scripts\dev.ps1
```

开发页面默认为 `http://127.0.0.1:4173`。生产式同源构建：

```powershell
.\scripts\build.ps1
```

### 本地验证

```powershell
.\scripts\test.ps1
```

该脚本执行前端单元测试、覆盖率、类型检查、生产构建、Sites 兼容测试、Python 全量测试和依赖审计。项目正式门槛为前后端全仓行覆盖率至少 90%、分支覆盖率至少 80%；不要通过降低阈值或排除业务文件让发布“假绿”。

## 项目结构

- `frontend/`：Vue 3、TypeScript、Vite、Pinia、Arco Design Vue、Firecrawl WASM 阅读器。
- `backend/`：FastAPI、SQLAlchemy 2、Alembic、SQLite/FTS5、设备 Agent 和本地 AI 适配。
- `packaging/windows/`：PyInstaller 冻结运行时与 Inno Setup 安装器。
- `packaging/uos/`：UOS V20 amd64/arm64 离线构建、DEB 与统一更新包。
- `docs/`：安装、使用、备份、运行、验收和发布文档。
- `docs/images/`：经真实浏览器流程采集的项目截图。

## 安全与隐私

- 生产部署应使用 HTTPS；设备凭据、用户会话和一次性本机操作令牌都有独立边界。
- 浏览器文档解析禁用 Markdown HTML、外部图片和危险协议；HTML、XML、SVG 不提供可能联网的原始内联预览，预览关闭后销毁 Worker 和临时对象 URL。
- 文件权限在传输创建、每个分块和最终读取时复核，源文件 inode、修改时间、大小与最终 SHA-256 必须一致。
- 不要提交私钥、模型权重、生产备份、用户数据、QA 配置或未脱敏日志。
- 发现安全问题请先通过仓库维护者提供的私密渠道报告，不要在公开 Issue 附带真实数据或凭据。

## 参与共建

PartyOps 希望把“基层真正怎么办公”变成可以持续改进的开源产品。如果它对你有启发，欢迎点击右上角 **Star**，让更多需要本地协同、国产系统适配和党建业务闭环的团队看到它。

- **想直接体验**：从 [v1.4.5-rc.4 Release](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.5-rc.4) 下载支持矩阵中与本机系统和 CPU 架构匹配的单文件安装包，先阅读已知限制并核对页面显示的 SHA-256。
- **发现问题**：在 [Issues](https://github.com/pl1505031156-droid/PartyOps/issues) 提交版本、系统、主机/协同机角色、复现步骤、期望/实际结果和已脱敏日志。
- **有产品建议**：在 [Discussions](https://github.com/pl1505031156-droid/PartyOps/discussions) 讲清真实工作场景、现在怎么做、卡在哪里、哪些角色会受益。
- **愿意贡献代码**：先阅读[贡献指南](CONTRIBUTING.md)，从 `main` 创建短分支，为修复补充回归测试，并运行 `scripts/test.ps1`。
- **能提供真机环境**：Windows 10、UOS amd64/arm64、20GB 大文件和 24 小时长稳测试反馈最有价值。

建议不必宏大。一个按钮命名不清、一种档案字段不好录、一台协同电脑断线后不好恢复，都是值得解决的真实问题。请不要在公开 Issue 或 Discussion 上传真实档案、账号、密钥、内部文件或未脱敏日志。

## 许可证与致谢

PartyOps 自有代码采用 [GNU General Public License v3.0](LICENSE)，并组合使用 AGPL-3.0 的 PyMuPDF；网络交互与二进制再分发需要同时遵守相应源代码提供义务。第三方组件、许可证边界和完整依赖清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、[Python SBOM](docs/sbom-python.cdx.json) 与 [前端 SBOM](docs/sbom-frontend.cdx.json)。特别感谢 [Firecrawl AnyDoc](https://github.com/firecrawl/anydoc) 和 [pdf-inspector](https://github.com/firecrawl/pdf-inspector) 为离线文档阅读提供的开源能力。

更多资料：[完整文档索引](docs/README.md) · [更新记录](CHANGELOG.md) · [安全策略](SECURITY.md) · [使用说明](docs/user-guide.md) · [1.4.3 更新说明](docs/党建智办-1.4.3-更新说明.txt) · [2026 党员发展规则](docs/party-development-rules-2026.md) · [1.4.3 验收记录](docs/acceptance-1.4.3.md) · [需求追踪矩阵](docs/requirements-matrix.md)
