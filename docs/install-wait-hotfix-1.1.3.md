# PartyOps 1.1.3：UOS 首次启动等待误判修复

## 适用现象

安装日志显示 PyInstaller 已经完成构建，随后连续出现本机健康检查“拒绝连接”，
并在服务刚输出“Application startup complete”时报告：

```text
安装未完成，失败阶段：2/5 构建 amd64 离线便携运行时
```

这不是架构、依赖、磁盘、内存或 OCR 缺失。原因是部分 UOS 国产电脑首次启动需要
30 秒以上，而旧安装脚本只等待 30 秒，导致把正常慢启动误判成失败。

## 修复内容

- 便携运行时和安装后服务的默认等待时间由 30 秒提升到 180 秒。
- 支持通过 `PARTYOPS_SMOKE_TIMEOUT_SECONDS` 和
  `PARTYOPS_HEALTH_TIMEOUT_SECONDS` 在 30—900 秒范围内调整。
- 等待期间监控服务进程，进程真正退出时立即失败，不盲目等满超时。
- 正常等待不再反复打印 `curl: (7) 拒绝连接`。
- 真正失败时显示最后 120 行服务日志，并保存
  `artifacts/portable-smoke-failure-<架构>.log`。

## 使用方法

将热修复 ZIP 放到当前 `PartyOps` 目录并覆盖解压：

```bash
cd '/data/home/XHX-CXZ-0010/2026年/其他/PartyOps/PartyOps'
sha256sum -c PartyOps-1.1.3-install-wait-hotfix.zip.sha256
unzip -o PartyOps-1.1.3-install-wait-hotfix.zip
```

确认脚本已经更新：

```bash
grep -n 'PARTYOPS_SMOKE_TIMEOUT_SECONDS:-180' \
  packaging/uos/build-portable.sh
```

然后继续原位安装：

```bash
bash install.sh --desktop-user XHX-CXZ-0010
```

旧版本程序、`/var/lib/partyops`、附件、账号和配置均不需要删除。

如果 180 秒仍未完成，可在保留诊断日志的前提下临时延长到 300 秒：

```bash
PARTYOPS_SMOKE_TIMEOUT_SECONDS=300 \
PARTYOPS_HEALTH_TIMEOUT_SECONDS=300 \
bash install.sh --desktop-user XHX-CXZ-0010
```

不要使用 `--force-architecture`，也不要删除 `/var/lib/partyops`。
