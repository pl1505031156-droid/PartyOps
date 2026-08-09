<div align="center">

# 党建智办 PartyOps

### 面向基层党建工作的本地优先协同工作台

把事项办理、跨机文件、重要档案、迎检材料、通知评论和工作留痕，收进一套真正能落地的局域网协同闭环。

[![Release](https://img.shields.io/badge/release-v1.4.2--rc.1-b42318?style=for-the-badge)](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.2-rc.1)
[![License](https://img.shields.io/badge/license-GPL--3.0-292520?style=for-the-badge)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20UOS-c17b17?style=for-the-badge)](#安装教程)
[![Local first](https://img.shields.io/badge/data-local--first-2f7d57?style=for-the-badge)](#安全与隐私)
[![GitHub stars](https://img.shields.io/github/stars/pl1505031156-droid/PartyOps?style=for-the-badge&color=b42318)](https://github.com/pl1505031156-droid/PartyOps/stargazers)

[下载安装](#下载) · [界面实景](#系统主界面实景) · [核心亮点](#partyops-的亮点) · [安装教程](#安装教程) · [更新记录](CHANGELOG.md) · [参与共建](#参与共建)

</div>

> [!IMPORTANT]
> 当前源码版本为 `1.4.2`，数据库模式为 `0017`。最新可下载版本是 **v1.4.2-rc.1 测试候选**。Windows 11 浏览器与真实协同 Agent 候选验证已完成，但覆盖率、Windows 10、UOS 双架构、20GB、24 小时长稳和正式签名门禁尚未全部关闭，因此当前安装器不能标注为稳定正式版。详见[发布就绪判定](docs/release-readiness-1.4.2.md)。

## 30 秒了解 PartyOps

PartyOps 不是一套把表单搬到浏览器里的系统。它解决的是基层办公最常见的断点：事项散落在聊天记录里，材料留在不同电脑上，档案与办理过程彼此脱节，迎检时再临时拼接。

系统以一台单位主机保存权威数据库和受管附件，Windows 或 UOS 协同电脑通过受控 Agent 接入。团队成员可以发布自己电脑上的真实文件夹，在权限范围内互相浏览、阅读、下载和转发；事项、材料、档案、评论、通知与审计记录始终沿同一条责任链关联。

## 系统主界面实景

下面全部来自 PartyOps 1.4.2 的真实浏览器流程和演示数据，不是设计稿。

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

| 真协同文件网 | 一条责任链 | 档案不是孤岛 |
| --- | --- | --- |
| 已入网用户可以发布本机真实文件夹，按团队或指定人员授权浏览、下载和转发。设备间仍由主机中转，不开放 SMB、匿名共享或设备直连。 | 事项的主办、协办、审核、步骤、材料、评论、提及和通知围绕同一业务对象推进，减少重复建表和版本漂移。 | 重要档案支持年度目录、类别权限、协同贡献、扫描件、OCR、版本历史、业务关联、作废与恢复。 |
| **文件打开就能读** | **本地数据边界** | **失败可恢复、过程可追溯** |
| PDF、Word、Excel、PowerPoint、OpenDocument、RTF、EPUB、CSV 和常见文本可在文件中心打开，远端内容先完成分块传输与 SHA-256 校验。 | 文档正文不上传 Firecrawl 或外部服务；本地 AI 由管理员离线导入并按向量、LLM 两种能力启停。 | SQLite 在线快照、版本化备份、更新前备份、原子恢复、断点续传、哈希失败隔离、审计记录和设备隔离共同守住数据。 |

## 五大工作域

| 工作域 | 面向谁 | 主要能力 |
| --- | --- | --- |
| **今日** | 所有人 | 一周总览、临期风险、必须办理、日历节点和周期汇总 |
| **工作** | 主办、协办、审核人 | 事项与清单、我的工作、通知、日历、收件箱、报告、日志和专题空间 |
| **资料** | 档案与材料经办人 | 原始文件中心、跨机共享、重要档案、迎检归档、知识库、文档比较与查重 |
| **协同** | 主机与协同机用户 | 设备入网、共享目录、接收箱、三种传输、目录与人员授权 |
| **管理** | 有效能力对应的管理员 | 周期模板、自动归档、报告模板、本地 AI、更新、备份、诊断与帮助 |

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
| `1.4.2-rc.1` | AnyDoc / pdf-inspector 离线文档阅读、跨机文件直接阅读、权限撤销复核和发布链路收口 | 当前 Pre-release |

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

当前可下载版本为 [v1.4.2-rc.1 测试候选](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.2-rc.1)：

- [Windows 10/11 x64 安装器](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.2-rc.1/PartyOps_1.4.2_windows_amd64.exe)
- [Windows SHA-256](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.2-rc.1/PartyOps_1.4.2_windows_amd64.exe.sha256)
- [Windows 候选验证清单](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.2-rc.1/PartyOps_1.4.2_windows_amd64.candidate.json)
- [UOS 1.4.2 原生构建套件](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.2-rc.1/PartyOps-UOS-build-kit.zip)
- [UOS 构建套件 SHA-256](https://github.com/pl1505031156-droid/PartyOps/releases/download/v1.4.2-rc.1/PartyOps-UOS-build-kit.zip.sha256)

UOS ZIP 是供 UOS V20 amd64/arm64 目标机原生构建的离线套件，不是可直接安装的 DEB。稳定正式版的目标制品为：

- `PartyOps_1.4.2_windows_amd64.exe`
- `partyops_1.4.2_amd64.deb`
- `partyops_1.4.2_arm64.deb`
- `partyops_1.4.2.partyops-update`
- 可选的中文向量、轻量 LLM、增强 LLM `.partyops-modelpack`

只有同时附带 SHA-256、发布签名、迁移/回滚说明和真机验收记录的 Release 才是正式版。当前门禁为 **NO-GO**；`v1.4.2-rc.1` 未做 Windows Authenticode 正式签名，只能在隔离测试电脑试用，不得用于正式全量生产部署。

## 安装教程

### 安装前准备

1. 选定一台长期在线、磁盘可靠的电脑作为主机；SQLite 数据目录不得放在网络共享盘。
2. 为主机设置固定局域网地址或稳定主机名，并确认所有电脑使用“专用网络”。
3. 从同一个 GitHub Release 下载制品和 `.sha256`；正式部署还必须下载签名说明。
4. 先校验文件，再安装。以下示例中的哈希值必须与对应 Release 清单完全一致。

Windows PowerShell：

```powershell
Get-FileHash .\PartyOps_1.4.2_windows_amd64.exe -Algorithm SHA256
Get-AuthenticodeSignature .\PartyOps_1.4.2_windows_amd64.exe
```

UOS：

```bash
dpkg --print-architecture
sha256sum partyops_1.4.2_amd64.deb
```

### Windows 10/11 x64

1. 使用管理员权限运行 `PartyOps_1.4.2_windows_amd64.exe`。
2. 安装器创建开始菜单/桌面入口、本机共享管理协议和仅限专用网络的 `18765`、`18766` 入站规则。
3. 首次打开“党建智办”，明确选择角色：
   - **主机**：创建系统管理员，数据写入 `%PROGRAMDATA%\PartyOps`，主机服务随 Windows 启动。
   - **协同机**：填写主机地址并完成配对，配置、接收文件和个人状态写入 `%LOCALAPPDATA%\PartyOps`，Agent 随当前用户登录启动。
4. 主机管理员在“管理 → 设备协同”批准设备，并按用户、设备和目录授予能力。
5. 协同用户在“资料 → 原始文件”点击“共享本机文件夹”，用系统选择器选中目录，再设置团队或指定人员范围。
6. 从另一台电脑登录，确认可以浏览、打开阅读、浏览器另存为或下载到本机接收目录。

`v1.4.2-rc.1` 尚未签名，Windows SmartScreen 可能阻止启动。只有在隔离测试电脑上、且实算 SHA-256 与 Release 完全一致时才可继续；正式部署遇到无签名或发布者不一致时不要安装。

### UOS V20 amd64 / arm64

先确认架构：

```bash
dpkg --print-architecture
```

海光、兆芯、Intel、AMD 通常使用 `amd64`；飞腾等 ARM 机器使用 `arm64`。当前候选 Release 只提供原生构建套件，须在对应 UOS 目标机解压并构建：

```bash
sha256sum PartyOps-UOS-build-kit.zip
unzip PartyOps-UOS-build-kit.zip
cd PartyOps
sudo bash packaging/uos/build-and-install.sh
```

正式 Release 形成 DEB 后，可直接安装对应包：

```bash
sudo apt install ./partyops_1.4.2_amd64.deb
# ARM64 机器改用：sudo apt install ./partyops_1.4.2_arm64.deb
```

安装后从应用菜单打开“党建智办”，按与 Windows 相同的向导选择主机或协同机。主机服务数据默认位于 `/var/lib/partyops`；日常用户的协同配置位于 `~/.config/partyops`，接收目录位于用户数据目录。无 sudo 的日常账号应由管理员安装，不要在 root 桌面完成普通用户的协同配置。

### 首次组网与目录共享

1. 主机管理员创建普通协同账号。
2. 每台协同电脑安装相同版本，选择“协同机”，提交配对请求。
3. 管理员批准设备；普通用户登录协同机。
4. 点击“共享本机文件夹”，系统会签发与用户和设备绑定、60 秒内单次有效的本机操作令牌。
5. 选择真实文件夹，设置团队共享或指定成员及浏览/下载/发送权限，执行“立即同步”。
6. 文件中心会显示“主机文件”“某协同机共享”“本机共享”和在线/同步/权限状态。
7. 远端文件点击后先拉取并校验，再显示结构化阅读或原始预览；下载可选择浏览器另存为或当前协同机接收目录。

### 升级、备份与回滚

- 日常升级：主机管理员进入“管理 → 系统更新”，导入已签名的 `partyops_1.4.2.partyops-update`。主机先升级并健康检查，协同机随后按平台选择包。
- 升级前：执行一次手工备份并下载到独立介质；系统还会自动创建升级前快照。
- 失败回滚：更新执行器恢复程序与升级前数据库；`0017` 降级到 `0016` 会丢失 1.4.1/1.4.2 的共享成员等新字段，正式环境优先恢复完整备份。
- 不要通过复制正在运行的 SQLite 文件做备份，也不要混用不同版本的主机和协同 Agent。

完整步骤见[安装、升级与回滚](docs/upgrade-1.4.2.md)、[备份恢复手册](docs/backup-restore.md)和[长期运行手册](docs/operations-runbook.md)。

### 卸载

- Windows：在“设置 → 应用”卸载“党建智办 PartyOps”。卸载前先导出备份；业务数据是否保留以卸载确认页为准。
- UOS：`sudo apt remove partyops`。如需删除 `/var/lib/partyops`，必须先验证备份且由管理员明确执行，程序不会把删除业务数据作为普通升级步骤。

## 可选本地智能

主程序不强制捆绑模型权重。管理员可以离线导入独立签名模型包：

- 中文向量：BGE 小型中文模型，用于事项、档案、知识和已授权共享目录的语义检索。
- 轻量 LLM：面向内存 8 GB 以上主机的本地草稿生成。
- 增强 LLM：面向内存 16 GB 以上主机的本地草稿生成。

本地 LLM 只生成带来源的草稿，不自动修改事项、档案或文件权限；未获正文索引授权的共享内容不会进入提示词。模型不存在、资源不足或推理失败时，系统自动降级为规则推荐与普通检索。

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

该脚本执行前端单元测试、覆盖率、类型检查、生产构建、Sites 兼容测试、Python 全量测试和依赖审计。项目正式门槛为前后端全仓行覆盖率至少 90%；不要通过降低阈值或排除业务文件让发布“假绿”。

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

- **想直接体验**：从 [v1.4.2-rc.1 Release](https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.2-rc.1) 下载候选安装器，先阅读已知限制并校验 SHA-256。
- **发现问题**：在 [Issues](https://github.com/pl1505031156-droid/PartyOps/issues) 提交版本、系统、主机/协同机角色、复现步骤、期望/实际结果和已脱敏日志。
- **有产品建议**：在 [Discussions](https://github.com/pl1505031156-droid/PartyOps/discussions) 讲清真实工作场景、现在怎么做、卡在哪里、哪些角色会受益。
- **愿意贡献代码**：先阅读[贡献指南](CONTRIBUTING.md)，从 `main` 创建短分支，为修复补充回归测试，并运行 `scripts/test.ps1`。
- **能提供真机环境**：Windows 10、UOS amd64/arm64、20GB 大文件和 24 小时长稳测试反馈最有价值。

建议不必宏大。一个按钮命名不清、一种档案字段不好录、一台协同电脑断线后不好恢复，都是值得解决的真实问题。请不要在公开 Issue 或 Discussion 上传真实档案、账号、密钥、内部文件或未脱敏日志。

## 许可证与致谢

PartyOps 采用 [GNU General Public License v3.0](LICENSE)。第三方组件及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。特别感谢 [Firecrawl AnyDoc](https://github.com/firecrawl/anydoc) 和 [pdf-inspector](https://github.com/firecrawl/pdf-inspector) 为离线文档阅读提供的开源能力。

更多资料：[完整文档索引](docs/README.md) · [更新记录](CHANGELOG.md) · [安全策略](SECURITY.md) · [使用说明](docs/user-guide.md) · [1.4.2 更新说明](docs/党建智办-1.4.2-更新说明.txt) · [1.4.2 验收记录](docs/acceptance-1.4.2.md) · [需求追踪矩阵](docs/requirements-matrix.md)
