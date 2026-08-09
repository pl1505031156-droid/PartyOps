# 党建智办 PartyOps

面向基层党建工作的本地优先、局域网协同办公系统。PartyOps 将事项办理、重要档案、原始文件、迎检材料、通知评论和工作留痕放在同一套可审计闭环中；业务数据库和受管附件保留在单位主机，协同电脑通过受控 Agent 发布目录、浏览团队文件、阅读和下载内容。

> 当前源码版本：`1.4.2`；数据库模式：`0017`。代码与 Windows 11 浏览器/协同 Agent 候选验证已经完成，但覆盖率、Windows 10、UOS 双架构、20GB、24 小时长稳和正式签名门禁尚未全部关闭，因此本仓库暂不把任何安装器标注为“稳定正式版”。详见[发布就绪判定](docs/release-readiness-1.4.2.md)。

## 界面预览

### Office 文档结构化阅读

![PartyOps 文件中心 Office 文档预览](docs/images/file-center-office-preview.png)

### PDF 版面阅读

![PartyOps 文件中心 PDF 文档预览](docs/images/file-center-pdf-preview.png)

### 跨协同电脑文件阅读

![PartyOps 跨机器 Office 文档预览](docs/images/cross-device-office-preview.png)

## 为什么选择 PartyOps

- **真正的局域网协同**：已入网用户可以发布本机真实文件夹，按团队或指定人员授权浏览、下载和转发；设备间文件继续由主机中转，不开放 SMB、匿名共享或设备直连。
- **文件可以直接读**：PDF、Word、Excel、PowerPoint、OpenDocument、RTF、EPUB、CSV 和常见文本可在文件中心打开；主机文件直接读取，协同机文件先经过分块传输、源变化检查、SHA-256 校验和最终权限复核。
- **党建业务闭环**：事项、审核、评论提及、通知、“我的工作”、重要档案、知识、工作日志、报告和迎检包相互关联。
- **本地数据边界**：浏览器中的文档解析不会把正文上传给 Firecrawl 或外部服务；本地 AI 也是管理员离线导入、按能力启停。
- **跨平台部署目标**：同一业务能力面向 Windows 10/11 x64、UOS V20 amd64 和 arm64，主机或协同机角色由首次向导明确选择。
- **可恢复、可追溯**：SQLite 在线快照、版本化备份、哈希清单、原子恢复、更新前备份、审计记录和设备隔离共同保护数据。

## 核心能力

| 工作域 | 能力 |
| --- | --- |
| 今日与工作 | 快速/标准/项目事项，主办、协办、审核、步骤分派，表格/看板/月历/时间轴，周期任务与工作日历 |
| 资料 | 原始文件中心、跨机共享、结构化阅读、重要档案、知识库、文档比较、重复检测、年度归档包 |
| 协同 | 本机目录发布、团队/指定成员授权、三种设备传输、通知评论、提及回复、接收箱和“我的工作” |
| 管理 | 用户与设备、目录审批、备份恢复、更新编排、审计、运行诊断、本地 AI 模型包 |
| 稳定性 | 乐观锁冲突草稿、SSE 续传与轮询降级、传输断点续传、哈希失败隔离、磁盘与临时数据保留策略 |

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

## 参与贡献

欢迎下载试用、提交问题、补充测试和提出更适合基层工作的创新方案：

1. 先阅读[贡献指南](CONTRIBUTING.md)和现有 [Issues](https://github.com/pl1505031156-droid/PartyOps/issues)。
2. 新功能请说明使用场景、账号/设备角色、数据边界和验收步骤。
3. 缺陷请提供版本、系统、复现步骤、期望/实际结果和已脱敏日志。
4. 提交代码前运行 `scripts/test.ps1`，并为修复补充可重复的回归测试。

## 许可证与致谢

PartyOps 采用 [GNU General Public License v3.0](LICENSE)。第三方组件及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。特别感谢 [Firecrawl AnyDoc](https://github.com/firecrawl/anydoc) 和 [pdf-inspector](https://github.com/firecrawl/pdf-inspector) 为离线文档阅读提供的开源能力。

更多资料：[完整文档索引](docs/README.md) · [更新记录](CHANGELOG.md) · [安全策略](SECURITY.md) · [使用说明](docs/user-guide.md) · [1.4.2 更新说明](docs/党建智办-1.4.2-更新说明.txt) · [1.4.2 验收记录](docs/acceptance-1.4.2.md) · [需求追踪矩阵](docs/requirements-matrix.md)
