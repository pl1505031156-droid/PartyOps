# PartyOps 1.4.5-rc.4 部署与独立搭建

最后核对：2026-08-26（北京时间，UTC+8）。本文只描述当前 `1.4.5-rc.4` / 数据库 `0024`；旧版本文档仅用于迁移追溯。

> `v1.4.5-rc.4` 按平台标记 `preview` 或 `unavailable`，不是全平台稳定生产版。Windows 7、国产 Linux 与 macOS 制品仍有明确的真机、商业签名或公证限制；Windows 7 已停止系统级安全维护，只能在受控局域网使用。

## 1. 部署结构

- 一台主机运行后端、SQLite、受管附件、备份、审计与更新服务。
- Windows 10/11 x64、麒麟/UOS/deepin 或 openEuler amd64/arm64 协同机通过 Agent 接入。
- 浏览器业务端口默认 `18765`；设备 Agent 双向 TLS 端口默认 `18766`。
- 只允许可信局域网，不支持也不应配置公网端口映射、SMB 匿名共享或设备直连。
- 主机数据库必须位于本机可靠磁盘，不能放在 NAS、云盘同步目录或网络共享盘。

## 2. 使用 Release 安装

### 2.1 下载与真实性校验

Windows 普通用户只下载版本化 EXE，不需要第二个校验包。官网与 GitHub Release 直接显示最终 SHA-256；安装前计算并逐字核对：

```powershell
Get-FileHash .\PartyOps_1.4.5-rc.4_windows_amd64.exe -Algorithm SHA256
Get-AuthenticodeSignature .\PartyOps_1.4.5-rc.4_windows_amd64.exe
```

```bash
sha256sum PartyOps_1.4.5-rc.4_linux_amd64.deb
dpkg-deb --info PartyOps_1.4.5-rc.4_linux_amd64.deb
```

哈希不一致时停止安装。当前 `1.4.5-rc.4` Windows 文件未做 Authenticode 正式签名，SmartScreen 显示未知发布者属于已知限制，不应误称为正式签名版本。

### 2.2 Windows 10/11 x64

1. Windows 10/11 运行 `PartyOps_1.4.5-rc.4_windows_amd64.exe`，程序目录与业务数据目录都可选择本机固定磁盘路径，例如 `E:\PartyOps` 与 `D:\PartyOps-数据`。Win7 必须改用文件名带 `windows7_amd64` 或 `windows7_x86` 的专用包。
2. rc.2 卸载后如 SCM 仍残留同名服务，rc.4 只会在旧二进制哈希命中正式 rc.1/rc.2/已撤回 rc.3 白名单，或缺失二进制的停止服务同时满足完整 PartyOps 服务元数据时接管；未知同名服务仍以 `LEGACY_SERVICE_CONFLICT` 停止安装。
3. 首次打开桌面“党建智办”，明确选择“个人使用（新手推荐）”“主机”或“协同机”。个人使用不申请管理员权限且只监听回环地址。
4. 主机模式再次确认数据目录；数据库、附件、备份、证书、模型、缓存和日志位于该目录，服务在确认主机角色后设为随系统启动。
5. 协同机模式使用主机生成的限时入网码；小型用户配置位于 `%LOCALAPPDATA%\PartyOps`，备份、接收文件和日志位于向导所选目录。
6. 主机管理员在“管理 → 设备协同”确认设备、目录与成员权限。
7. 防火墙只对“专用网络”和单位可信网段开放所需端口，不开放“公用网络”。

### 2.3 麒麟 / UOS / deepin DEB

确认架构后安装匹配包：

```bash
uname -m
sudo apt install ./PartyOps_1.4.5-rc.4_linux_amd64.deb
# aarch64 / ARM64 使用 PartyOps_1.4.5-rc.4_linux_arm64.deb
```

每台电脑只下载一个原生包，不再下载构建套件或额外校验包。包管理器配置阶段会执行架构、文件清单、前端资源、SQLite/FTS5、中文 OCR、本地智能、更新服务和回环健康端点自检；失败时服务保持停止并返回中文诊断。

### 2.4 openEuler RPM

```bash
uname -m
sudo dnf install ./PartyOps-1.4.5-0.rc.4.1.x86_64.rpm
# aarch64 使用 PartyOps-1.4.5-0.rc.4.1.aarch64.rpm
```

不要使用 `--force-architecture`，也不要关闭 SELinux、防火墙或系统安全策略。安装器只生成最小必要规则。

主机系统配置位于 `/etc/partyops/partyops.env`，数据默认位于 `/var/lib/partyops`。服务检查：

```bash
systemctl status partyops partyops-updater --no-pager
journalctl -u partyops -n 100 --no-pager
curl --cacert /var/lib/partyops/secrets/ca-cert.pem https://主机地址:18765/api/v1/health
```

### 2.5 macOS 11+ Apple Silicon / Intel

Apple 芯片选择 `PartyOps_1.4.5-rc.4_macos_arm64.pkg`，Intel 芯片选择 `PartyOps_1.4.5-rc.4_macos_x86_64.pkg`。当前 PKG 只有 ad-hoc 签名，没有 Developer ID Installer/Application 与 Apple 公证；先核对 SHA-256，再在 Finder 中 `Control` 点按 PKG →“打开”，或在“系统设置 → 隐私与安全性”选择对应的“仍要打开”。不得关闭 Gatekeeper。

安装后从“应用程序”打开党建智办。Finder 主入口会先写 `~/Library/Logs/PartyOps/launch-probe.log`，再启动冻结向导；如完全无反应，先检查该文件，再检查同目录 `launcher.log`。永久消除 Gatekeeper 提示需要正式 Developer ID 签名、公证与 staple，不得把本候选包描述为已公证。

## 3. 从源码独立搭建（仅本机开发/审计）

源码模式默认只绑定 `127.0.0.1`，不能代替带 TLS、服务管理和回滚的正式安装器。

前置条件：Git、CPython 3.11–3.13、Node.js 22、Corepack。Windows PowerShell：

```powershell
git clone https://github.com/pl1505031156-droid/PartyOps.git
Set-Location PartyOps
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
corepack pnpm --dir frontend install --frozen-lockfile
.\scripts\dev.ps1
```

开发前端为 `http://127.0.0.1:4173`，API 健康检查为 `http://127.0.0.1:18765/api/v1/health`。开发脚本会显式启用开发环境和随机口令演示数据；它不会创建可用于外部部署的服务或 TLS 边界。

生产前端本机构建：

```powershell
.\scripts\build.ps1
```

生产模式如果绑定非回环地址，程序会拒绝通配/公网地址和明文 HTTP。请优先使用安装向导生成的 CA、主机证书、设备证书与服务配置，不要手工关闭校验。

## 4. 生产配置基线

关键环境变量如下，敏感值不得提交到 Git：

```dotenv
PARTYOPS_MODE=host
PARTYOPS_ENVIRONMENT=production
PARTYOPS_HOST=192.168.1.20
PARTYOPS_BIND_HOST=0.0.0.0
PARTYOPS_ADVERTISE_HOST=192.168.1.20
PARTYOPS_PORT=18765
PARTYOPS_AGENT_PORT=18766
PARTYOPS_DATA_DIR=/var/lib/partyops
PARTYOPS_STRICT_SQLITE=true
PARTYOPS_SEED_DEMO=false
PARTYOPS_TLS_ENABLED=true
PARTYOPS_TLS_CERT_FILE=/var/lib/partyops/secrets/host-cert.pem
PARTYOPS_TLS_KEY_FILE=/var/lib/partyops/secrets/host-key.pem
PARTYOPS_TLS_CLIENT_CA_FILE=/var/lib/partyops/secrets/ca-cert.pem
PARTYOPS_TLS_REQUIRE_CLIENT_CERT=true
PARTYOPS_UPDATE_PUBLIC_KEY=<部署端预置的 Ed25519 公钥>
PARTYOPS_MODEL_PACK_PUBLIC_KEY=<模型包发布公钥；可与更新公钥分离>
```

- 私钥文件权限限定为服务账号；不得放进源码、更新包、模型包或诊断包。
- 更新包/模型包不能用包内自带公钥给自己签名，必须由上述外部受信公钥验证。
- `allowed_origins` 只加入实际受信前端来源；浏览器写操作同时验证 Origin。
- AI 外部服务必须使用 HTTPS；内网模型需要管理员显式标记受信，链路本地与保留地址始终拒绝。

## 5. 首次验收

至少用主机管理员、主机普通用户、协同机管理员、协同机普通用户各走一次：

1. 登录、退出、密码重置和会话撤销。
2. 新建事项、协办/审核、评论/提及、材料版本和归档。
3. 普通协同用户选择本机真实目录，设置团队/指定成员并立即同步。
4. 另一台电脑浏览、预览、单文件下载、多选/文件夹 ZIP、下载到本机接收目录。
5. 传输中撤销目录/成员权限，确认下一分块和最终读取均停止。
6. 档案获授权用户新建、编辑、上传扫描件；字段错误显示在具体字段。
7. 创建、下载、校验备份；在隔离副本上完成恢复演练。
8. 检查浏览器控制台、服务日志、Agent 轮转日志和磁盘空间告警。

健康接口必须显示程序版本、SQLite 能力和模式修订均正确。候选发布还必须满足官网与 GitHub Release 同步公开的 1.4.3 发布就绪门禁。

## 6. 备份、升级与回滚

- rc.2/数据库 `0023` 原位升级时，先使用 SQLite 在线备份 API 创建与 ORM 模型无关的快照；ZIP 边界、长度、SHA-256 与 `quick_check` 全部通过后才执行 `0024` 迁移，迁移成功后才写入新结构的升级记录。
- 迁移或启动健康门禁失败时，恢复已验证快照并清理失败事务的 WAL/SHM；不要手工删除原数据库。
- 备份导入限制上传体积、成员数、解压体积与压缩比，并拒绝路径逃逸、符号链接、未登记文件和哈希不一致。
- 统一 `.partyops-update` 只接受外部受信 Ed25519 公钥验证通过的包，按平台/架构选取制品。
- 升级失败由平台执行器停止新版本、恢复程序与升级前数据库，再运行健康检查。
- `0024 → 0023` 会移除台账导入、发展党员真实进度及相关新增字段；已有新业务写入时优先恢复完整升级前备份，不要长期使用结构降级库。

详见 [rc.4 升级与回滚](upgrade-1.4.5-rc.4.md)、[备份恢复](backup-restore.md) 和 [长期运行手册](operations-runbook.md)。

## 7. 故障定位

- 主机日志：数据目录 `logs/partyops.log`（JSON 单行、按日轮转）。
- Windows 主机监督日志：数据目录 `logs/partyops-host-service.log`（5 MiB × 6 份）；状态诊断为 `logs/partyops-host-status.json`。
- 协同机日志：配置目录 `logs/partyops-agent.log`（5 MiB × 6 份）。
- `AGENT_MTLS_REQUIRED`：设备令牌走错端口或未启用正式双向 TLS。
- `ORIGIN_DENIED`：Cookie 写请求来自非当前服务/未允许来源。
- `DEVICE_UPDATE_REQUIRED`：协同 Agent 与主机版本不一致，先完成签名更新。
- `reauth_required`：设备凭据失效，需要备份本机共享配置后重新入网。
- `SERVICE_MISSING` / `SERVICE_STOPPED`：安装器未正确注册服务或服务未运行，优先使用安装器“修复安装”。
- `RUNTIME_PERMISSION_DENIED`：诊断摘要会标明“个人模式配置”“PartyOps 主程序”或“个人数据目录”。优先使用同一安装包执行修复安装；数据目录阶段失败时，在配置向导选择当前桌面账号可写的本机固定磁盘目录。不要关闭单位安全策略、不要给 Everyone 完全控制，也不要删除原数据。
- `LEGACY_SERVICE_CONFLICT`：rc.4 能安全识别 rc.1/rc.2/已撤回 rc.3 的正式服务残留；如果 rc.4 仍报此码，说明路径、哈希和完整服务元数据都无法证明归属，不应手工强删，请保留安装日志供核验。
- `UPGRADE_BACKUP_FAILED` / `DATABASE_SCHEMA_FAILED` / `SQLITE_RUNTIME_FAILED`：分别表示升级前备份失败、结构/迁移失败和 SQLite 驱动/版本/FTS5 故障；rc.4 不再把含有 SQLite 字样的迁移异常误报为运行时损坏。
- `CHILD_EXITED` / `PORT_IN_USE` / `DATA_DIR_DENIED` / `TLS_INIT_FAILED` / `HEALTH_TIMEOUT`：分别检查主进程日志、端口、所选目录 ACL、内部 CA/TLS 与启动阶段；向导可直接复制诊断。
- 恢复前先复制原始日志和备份，不要删除数据目录或覆盖数据库。

## 8. 开源与许可证

PartyOps 自有代码按 GPL-3.0 发布，并组合使用 AGPL-3.0 的 PyMuPDF。发布二进制必须保留源码获取入口、[第三方声明](../THIRD_PARTY_NOTICES.md)；Python、前端和官网三份 CycloneDX SBOM 随官网与 GitHub Release 单独交付。许可证选择和再分发义务应由发布责任人做最终法律复核。
