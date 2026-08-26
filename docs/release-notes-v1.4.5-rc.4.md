# 党建智办 PartyOps 1.4.5-rc.4 发布说明

`1.4.5-rc.4` 是针对 rc.3 原位升级启动故障的全平台重发版本，数据库迁移仍为 `0024`。rc.3 在旧数据库仍处于 `0023` 时，升级前备份先通过 `0024` 的 `BackupRun` 模型写表，因旧表没有 `deleted_at` 等新列而在 Alembic 迁移开始前退出。真实故障数据库的 `PRAGMA quick_check=ok`，因此这不是数据库损坏或 SQLite DLL 故障。

## 本版修复

- 升级前数据库快照完全绕过当前 ORM，使用生产 SQLite 驱动在线备份；迁移成功后才使用 `0024` 模型登记 `BackupRun` 与 `UpgradeRecord`。
- 备份包必须通过路径边界、成员闭包、长度、SHA-256、SQLite `quick_check` 和 `integrity_check`，任一校验失败都不执行迁移。
- 备份、迁移、迁移后校验、管理员不变量、SQLite 版本/FTS5、业务健康检查和状态写入构成一个受控启动事务。
- 迁移失败时恢复已验证的旧快照，清除生产路径上的 WAL/SHM；半迁移数据库与伴随文件改名保留，方便诊断且不会覆盖前一次证据。
- 新增持久化 `upgrade-transaction.json`。如果进程在迁移或校验阶段被强退，下次启动先恢复升级前快照，再重新执行完整事务。
- `UPGRADE_BACKUP_FAILED` 表示备份或事务日志未完成，旧库没有迁移；`DATABASE_SCHEMA_FAILED` 表示表结构迁移失败并已尝试回滚；`SQLITE_RUNTIME_FAILED` 仅表示驱动、版本或 FTS5 能力问题。

## 升级边界

- rc.2/`0023` 可直接使用 rc.4 完整安装包原位升级到 `0024`。
- 已安装 rc.3 但无法启动的电脑也应使用 rc.4 完整安装包覆盖安装；不要删除数据目录，不要手工删除 WAL/SHM。
- 全新安装仍直接创建 `0024` 当前结构，不执行历史 ORM 写入。
- rc.3 已撤回，不再作为官网主下载或在线更新目标；GitHub Release 仅为审计保留。

## 安全与签名

- PartyOps 不建议安装在涉密、敏感电脑上，也不得用于处理涉密文件。
- Windows 安装器没有商业代码签名；macOS 使用 ad-hoc 签名、没有 Developer ID 公证。首次放行前必须核对官网清单的文件名、长度和 SHA-256，禁止全局关闭 SmartScreen、Gatekeeper 或单位安全软件。
- 更新目录、更新包和模型包继续使用公钥指纹 `7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3` 对应的正式 Ed25519 私钥签名；私钥不进入仓库、日志、聊天或制品。

## 验收说明

源码、覆盖率、安全扫描、九平台原生构建、安装/升级门禁、制品哈希、Cloud Studio 回读、GitHub Release 和 EdgeOne Deployment ID 统一记录在 `docs/acceptance-1.4.5-rc.4.md`。任一发布门禁失败时官网继续保留 rc.2。
