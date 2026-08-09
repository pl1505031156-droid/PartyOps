# 党建智办 1.0.0 → 1.1.1 原位升级与回滚

最后更新：2026-07-29

## 1. 升级原则

- 不卸载 1.0.0，不删除旧数据，不另建第二个应用。
- 包名继续使用 `partyops`，桌面仍只有一个“党建智办”图标。
- 处理器架构必须一致：海光/x86 使用 `amd64`，飞腾 D2000/8 使用 `arm64`。
- 升级前先在系统内执行一次手动备份，并把 `.partyops-backup` 与 `.sha256` 复制到另一台电脑。
- 不使用 `dpkg --force-architecture` 或 `--force-all`。

## 2. 正常升级

按电脑架构双击相应文件：

```text
partyops_1.1.1_amd64.deb
partyops_1.1.1_arm64.deb
```

统信安装器应显示“升级”，而不是创建新应用。也可在离线安装目录中执行：

```bash
bash install.sh
```

脚本自动识别 `dpkg --print-architecture`，只校验并安装匹配包。升级会：

1. 停止正在运行的 `partyops` 系统服务；
2. 保留 `/var/lib/partyops`、`/etc/partyops`、用户配置和终端配对；
3. 替换 `/opt/partyops` 程序；
4. 首次启动时创建带清单和哈希的 `pre-upgrade` 备份；
5. 把数据库模式迁移到 `0008`；
6. 迁移成功后写入升级记录并恢复服务。

早期用户目录数据不会被空数据库覆盖；首次启动向导会继续使用原配置的数据目录。

## 2.1 此后在应用内更新

每台 1.0.0 电脑只需最后手动安装一次 1.1.1 桥接包。此后主机管理员进入“系统设置 → 系统更新”，导入发布方签名的 `.partyops-update`，可选择主机、指定设备或全部设备：

1. 系统验证 Ed25519 签名、清单、文件大小和 SHA-256；
2. 主机 root 更新服务只提取与本机架构匹配的 `.deb`，不执行包内任意脚本；
3. 主机先做 SQLite 在线一致快照，再安装、迁移并检查健康状态；
4. 终端通过 Agent 下载更新包，并调用固定路径的系统更新 helper；
5. 离线设备下次上线继续，失败记录原因；主机失败自动恢复旧程序和数据库快照。

生产配置必须设置受信发布公钥 `PARTYOPS_UPDATE_PUBLIC_KEY`，未签名或签名不匹配的包会被拒绝。

发布密钥只生成一次。安装包和 UOS 构建套件只携带
`packaging/uos/update-public-key.txt`；私钥必须离线保管，禁止复制到单位终端、
安装目录、备份包或代码仓库。生成后续签名更新包时，在隔离发布机执行：

```bash
export PARTYOPS_UPDATE_PRIVATE_KEY_FILE=/安全介质/partyops-update-private-key.pem
bash packaging/uos/build-update-package.sh
```

脚本会同时校验私钥与安装包内置公钥是否匹配，防止误用另一套密钥。

## 3. 升级后检查

进入“系统设置 → 运行状态”，确认：

- 应用版本为 `1.1.1`；
- 数据库模式为 `0008`；
- 原有用户、事项、附件、模板、备份和配对记录仍在；
- 文件索引、OCR、通知、备份和 SSE 状态正常；
- 系统只有一个 `partyops` 软件包、一个桌面图标。

命令行可辅助检查：

```bash
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' partyops
systemctl status partyops --no-pager
```

## 4. 失败回滚

迁移失败时，程序会保留失败数据库并恢复升级前数据库，服务保持停止，避免继续写入。处理顺序：

1. 保留 `/var/lib/partyops`、安装日志和升级前备份，不要删除数据目录。
2. 记录“系统设置 → 运行状态”的诊断信息，或执行 `journalctl -u partyops -n 200 --no-pager`。
3. 重新安装同架构的上一版包。
4. 若数据库仍不可用，从升级前 `.partyops-backup` 恢复。
5. 只允许一台主机恢复上线，避免两个主机同时写入。

卸载包默认保留 `/var/lib/partyops`；任何彻底清理都必须在另存完整备份后人工执行。

## 5. 升级或重新安装后页面空白

如果左侧菜单和顶部栏正常、中央内容空白，通常不是数据库丢失，而是旧版
PartyOps 进程仍在运行，浏览器却已经读取了新版页面文件；另一种情况是浏览器
保留了升级前的分包文件名。

先关闭所有“党建智办”浏览器页签，再根据实际安装模式执行其中一组命令：

```bash
# 系统服务模式
sudo systemctl restart partyops
sudo systemctl status partyops --no-pager

# 日常桌面账号模式：不要在 root 身份下执行
/opt/partyops/stop.sh
/opt/partyops/start.sh
```

重新双击桌面“党建智办”图标。如果仍显示旧页面，再按一次
`Ctrl+Shift+R` 强制刷新。不要删除 `/var/lib/partyops`、
`~/.local/share/partyops` 或数据库文件。

如仍未恢复，保留现场并执行：

```bash
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' partyops
ps -ef | grep '[p]artyops'
sudo ss -lntp | grep -E '18765|18766'
sudo journalctl -u partyops -n 200 --no-pager
```

修复后的 1.1.1 会在加载页面前核对前后端版本，自动重载一次失效分包，并在
仍不兼容时显示中文诊断页，不再无说明地白屏。安装程序也会在替换文件前停止
系统服务和早期用户模式进程；旧进程 30 秒内无法退出时会中止安装，避免形成
新旧程序混用。若电脑已经登记为 1.1.1，一键安装器会自动执行同版本修复性
重装，不会因“已经是最新版本”而跳过程序文件替换。
