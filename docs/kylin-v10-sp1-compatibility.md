# 银河麒麟桌面 V10 SP1 兼容说明

PartyOps `1.4.4` 按“包格式 + 处理器架构”交付，不按 CPU 型号或银河麒麟小版本重复制作安装包。银河麒麟桌面 V10 SP1 的 2107、2203、2303、2403、2503 使用同一套选包和安装后自检规则。

## 一分钟选包

打开终端执行：

```bash
uname -m
```

| 输出 | 常见处理器 | 下载文件 |
| --- | --- | --- |
| `aarch64` | 飞腾 D2000/FT-2000、麒麟 9000C/9006C/990、鲲鹏 | `PartyOps_1.4.4_linux_arm64.deb` |
| `x86_64` | 海光 C86、兆芯、Intel、AMD | `PartyOps_1.4.4_linux_amd64.deb` |
| `loongarch64`、`sw64`、`riscv64` | 龙芯、申威、RISC-V | 1.4.4 暂无对应包，不得强制安装 AMD64/ARM64 包 |

CPU 名称只用于帮助新手理解，最终以 `uname -m` 为准。截图所示的飞腾 D2000 与 HUAWEI Kirin 9000C 设备应在 `uname -m` 返回 `aarch64` 时选择同一个 ARM64 DEB。

## 系列版本边界

| 银河麒麟桌面版本 | 1.4.4 处理方式 | 结论边界 |
| --- | --- | --- |
| V10 SP1 2107 / 2203 | 使用同架构 DEB，并由 APT 依赖门禁与安装后自检决定是否提交安装 | 未取得对应真机，不能写成真机通过 |
| V10 SP1 2303 / 2403 | 官方组件矩阵为 glibc 2.31、systemd 245；1.4.4 DEB 仅要求 glibc 2.17 | 静态兼容与 QEMU 动态门禁通过，未取得商业系统真机 |
| V10 SP1 2503 | 官方说明与 2403 保持核心组件一致、应用生态兼容并支持平滑升级 | 沿用同一 DEB，未取得商业系统真机 |

ARM64 DEB 的冻结 SHA-256 为 `16661a42aa106bf44328ede383c4f7427b0168b6905ccab2078eefa7a1595a89`。包元数据显示 `Architecture: arm64`、`Depends: libc6 (>= 2.17)`；安装脚本会再次核对架构、运行时、桌面入口、前端资源、服务与健康端点，失败时中止配置而不是留下“安装成功但打不开”的半成品。

## 安装

```bash
sudo install -m 0644 ./PartyOps_1.4.4_linux_arm64.deb /var/tmp/partyops.deb
sudo apt install /var/tmp/partyops.deb
```

不要使用 `dpkg --force-architecture`。若安装失败，请保留终端完整输出和 `~/.config/partyops/desktop-launch.log`；这些证据用于区分架构不符、麒麟安全策略拦截、依赖配置失败与桌面浏览器关联问题。

## rc.8 双击无反应的 1.4.4 复核

rc.8 在部分麒麟桌面安装后只创建了图标，但快捷方式可信元数据的写入发生在 root 的非桌面 DBus 会话中，`gio set metadata::trusted true` 可能静默失败；同时部分桌面环境从图标启动时不提供完整 `HOME`/`PATH`。这两点都可能在程序主体运行前阻断入口，因此没有业务日志。

1.4.4 延续并加固 `.desktop` 的固定 `/bin/bash` 入口，由脚本进入 `/opt/partyops` 运行时；启动器在写任何配置前使用 `getent passwd` 恢复当前账号的 `HOME`，然后立即创建 `~/.config/partyops/desktop-launch.log` 和启动诊断。安装器把 `HOME`、`XDG_CONFIG_HOME`、`XDG_RUNTIME_DIR`、`DBUS_SESSION_BUS_ADDRESS` 传给真实桌面用户后再设置可信元数据，并保留桌面文件可执行位兜底。

AMD64 包已在 glibc 2.17 环境、ARM64 包已在 QEMU aarch64 环境执行剥离环境变量的桌面入口动态门禁。由于当前没有银河麒麟图形真机，结论是“已修复已知入口链并通过动态仿真”，不能替代目标用户设备验收；安装后仍无反应时先运行：

```bash
/bin/bash /opt/partyops/desktop-launcher.sh
tail -n 120 ~/.config/partyops/desktop-launch.log
cat ~/.config/partyops/startup-diagnostic.txt
```
