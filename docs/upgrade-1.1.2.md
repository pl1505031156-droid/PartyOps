# 党建智办 1.1.2 应用内更新说明

最后核对：2026-07-29

## 交付文件

日常使用人员只接收以下文件，不接收源码或 Build Kit：

```text
partyops_1.1.2.partyops-update
partyops_1.1.2.partyops-update.sha256
党建智办-1.1.2-更新说明.txt
```

`.partyops-update` 内同时包含 UOS V20 amd64 与 ARM64 安装程序，并带有
Ed25519 发布签名、文件大小和 SHA-256 清单。主机只提取本机架构制品，
不会执行更新包中的脚本。

## 已安装 1.1.1 或更高版本

1. 在主机进入“系统设置 → 备份与灾备”，先创建一次手动备份，确认状态为“已完成”。
2. 进入“系统设置 → 系统更新”，点击“导入 .partyops-update”。
3. 选择 `partyops_1.1.2.partyops-update`，等待签名、版本、架构、磁盘和哈希校验通过。
4. 从 1.1.1 第一次更新到 1.1.2 时先选择“仅主机”。这是因为旧版更新编排器尚不具备主机优先队列。
5. 页面在主机重启期间会自动等待；看到应用版本 `1.1.2`、数据库模式 `0009` 后，回到同一更新记录，选择需要升级的终端。
6. 从 1.1.2 升级后续版本时，可以直接选择“主机和全部终端”：系统先升级主机，健康检查通过后才向终端下发；离线终端下次上线继续。

不要在升级过程中关闭主机、拔电或重复点击“开始升级”。浏览器可以短暂断开，
更新任务编号已经保存在本机浏览器中，服务恢复后会自动继续显示进度。

## 仍是 1.0.0

1.0.0 没有更新助手，必须最后手动安装一次与架构匹配的 1.1.2 `.deb`：

```bash
dpkg --print-architecture
sudo apt install ./partyops_1.1.2_amd64.deb
# 或 ARM64：
sudo apt install ./partyops_1.1.2_arm64.deb
```

不要卸载 1.0.0，不要使用 `--force-architecture`。包名仍为 `partyops`，
安装器会原位升级并保留数据、配置和唯一桌面图标。

## 更新执行与失败保护

- 更新包格式必须为第 2 版，且必须同时包含 amd64 和 ARM64 制品。
- 生产环境只接受安装时内置公钥对应的 Ed25519 签名。
- 更新前至少保留 512 MB 且不少于更新包三倍的可用空间。
- 主机先创建 SQLite 在线快照、附件和档案快照，并重建当前已安装版本的 Debian 回滚包。
- 新版安装、数据库迁移和健康检查全部通过后，才下发终端更新。
- 更新命令具有幂等性；已经安装目标版本的终端直接报告成功。
- 失败时通过 Debian 包管理器恢复上一程序版本，再恢复数据库、附件和档案快照。
- 如果程序包回滚本身失败，服务保持停止并显示中文诊断编号，避免继续写坏数据。

## 升级后检查

在“系统设置 → 运行诊断”确认：

- 应用版本 `1.1.2`，数据库模式 `0009`；
- 原有用户、事项、材料、重要档案、文件根和设备仍存在；
- 工作日志显示北京时间；
- 原始文件中心扫描单个损坏文件时只提示“正文识别失败，属性已保留”；
- 主机和终端版本一致，离线终端有明确的等待状态；
- 桌面仍只有一个“党建智办”图标。

命令行辅助检查：

```bash
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' partyops
systemctl status partyops partyops-updater --no-pager
journalctl -u partyops -u partyops-updater -n 200 --no-pager
```

## 发布方生成更新包

两种 `.deb` 必须分别在同架构 UOS V20 目标机原生构建。汇总到
`artifacts/` 后，在隔离发布机执行：

```bash
export PARTYOPS_VERSION=1.1.2
export PARTYOPS_UPDATE_PRIVATE_KEY_FILE=/安全介质/partyops-update-private-key.pem
bash packaging/uos/build-update-package.sh
```

脚本拒绝无签名包，并生成更新包及独立 SHA-256 文件。发布私钥不得进入安装包、
业务备份、源代码或单位终端。
