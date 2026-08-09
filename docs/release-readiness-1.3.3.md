# PartyOps 1.3.3 发布就绪清单

最后验证日期：2026-08-03

## 本地已经通过

- 后端 144 项测试通过。
- 前端 29 项测试、TypeScript 类型检查和生产构建通过。
- amd64、ARM64 离线 wheelhouse 各 65 个文件，38 个实际依赖闭包通过。
- 两种架构均包含 NumPy 2.2.6、ONNX Runtime 1.22.1、tokenizers 0.21.4。
- `vendor/SHA256SUMS` 对全部 134 个离线输入校验通过。
- 更新执行器对签名、架构、大小、哈希、非法路径和损坏包执行拒绝策略。
- 发布脚本生成 `.partyops-update` 后会再次运行独立自校验器。
- 东方四时长卷前端生产资产已经生成，数据库模式保持 `0015`。

## 尚须在 UOS 目标机完成

Windows 不能可靠生成或验证 UOS 原生 `.deb`、PyInstaller 运行时和系统服务。正式发布必须完成：

1. 在 UOS V20 amd64 电脑解压 Build Kit，以日常桌面账号运行：

   ```bash
   bash install.sh --desktop-user "$(id -un)" --rebuild
   bash packaging/uos/target-acceptance.sh "https://本机局域网IP:18765"
   ```

2. 在 UOS V20 ARM64 电脑执行相同命令。
3. 将两台电脑生成的下列文件汇总到同一份源码目录的 `artifacts/`：

   ```text
   partyops_1.3.3_amd64.deb
   SHA256SUMS.amd64
   PartyOps-uos-amd64.tar.zst
   partyops_1.3.3_arm64.deb
   SHA256SUMS.arm64
   PartyOps-uos-arm64.tar.zst
   ```

4. 在隔离发布机连接保存既有 Ed25519 私钥的安全介质，生成正式更新包：

   ```bash
   export PARTYOPS_UPDATE_PRIVATE_KEY_FILE=/安全介质/partyops-update-private-key.pem
   bash packaging/uos/build-update-package.sh
   ```

   发布脚本会拒绝与 `packaging/uos/update-public-key.txt` 不匹配的私钥。不要生成新密钥替换既有信任链，不要把私钥复制进源码、Build Kit 或单位业务电脑。

5. 最终执行双架构安装、原位升级、失败回滚、重启、三机协同和至少 24 小时连续运行验收。

## 用户后续更新方式

正式 `partyops_1.3.3.partyops-update` 生成后，日常用户不需要终端命令：主机管理员进入“管理 → 系统更新”导入更新包并点击开始升级。主机完成备份、安装、迁移和健康检查后，在线协同电脑按本机架构接收更新；未更新到主机版本前不能进入业务页面，更新完成后自动恢复进入系统。

只有更新助手损坏时，才使用对应架构 `.deb` 做一次修复性原位升级；不会创建第二份数据库或第二个桌面应用。

## 当前发布阻断项

- 后端总体覆盖率 74%，前端总体覆盖率 4.81%，未达到计划中的 90%。
- UOS V20 amd64/ARM64 原生构建、真实安装和连续运行验收尚未在本机完成。
- 正式签名 `.partyops-update` 只能在两种目标架构制品汇总且私钥可用后生成。

因此当前 Build Kit 是“目标机原生构建输入”，不是已经完成双架构实机签收的正式安装包。
