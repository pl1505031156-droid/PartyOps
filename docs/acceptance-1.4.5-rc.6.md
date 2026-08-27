# PartyOps 1.4.5-rc.6 本地验收记录

最后验证日期：2026-08-27（北京时间，UTC+08:00）  
状态：开发候选，未发布；生产回滚点为 `v1.4.5-rc.4`。

## 1. 本轮修复

- 修复 Windows 10/11、Windows 7 启动前依赖检查缺失的问题：个人入口在配置前核验
  主程序、向导、Launcher、Python、SQLite/FTS5、UCRT、VC、Tcl/Tk、前端资源及清单
  SHA-256；发现缺失或混装时返回 `RUNTIME_DEPENDENCY_MISSING`，不再等子进程退出后
  才显示笼统错误。
- 修复 `RUNTIME_DEPENDENCY_MISSING` 之后关闭并重新配置又出现
  `MODE_SWITCH_ROLLBACK_FAILED / 自启动：未找到运行程序：PartyOpsLauncher` 的错误分类。
  自启动恢复现在是辅助步骤：入口被安全软件隔离或策略拒绝时只记录
  `AUTOSTART_RESTORE_DEFERRED`，保留个人/主机/协同核心模式；真实的运行时缺失统一返回
  `RUNTIME_EXECUTABLE_MISSING` 并给出同版本修复安装建议。
- 安装器和桌面 Launcher 在用户配置目录及 `ProgramData\\PartyOps` 写入当前安装根标记。
  标记只接受绝对目录，并且候选 Launcher 必须与 PartyOps 发布清单中的 SHA-256 完全一致，
  防止旧快捷方式或被篡改的标记执行非 PartyOps 程序。
- Win7 专用包继续强制 CPython 3.8、目标位数和 KB2533623/等效 Loader API；Win10/11
  通用包拒绝在 Win7 或错误架构上运行。

## 2. 自动验证结果

| 门禁 | 结果 | 证据 |
| --- | --- | --- |
| 后端全量回归 | 通过 | `1477 passed, 4 skipped, 10 warnings` |
| 后端覆盖率 | 通过 | `coverage-rc6-windows-fix-final.json`：行 96.50%，分支 92.43% |
| 前端全量回归 | 通过 | 19 个测试文件、218 个测试通过 |
| 前端覆盖率 | 通过 | 行 96.51%，分支 93.08% |
| 前端类型检查与生产构建 | 通过 | `pnpm run build`；Vite 1296 modules transformed |
| 官网 Sites worker | 通过 | 4/4 测试通过 |
| Ruff | 通过 | 后端 `ruff check app tests`；Windows 修复专项测试通过 |
| Windows 依赖探针专项 | 通过 | `test_175_rc3_runtime_permission_preflight.py`、`test_setup_wizard_rc3_branch_matrix.py` |
| 后端 mypy | 未通过，阻断发布 | 当前全仓库仍有 299 项既有跨平台类型错误；未使用全局忽略掩盖 |
| Win7 wheelhouse 门禁 | 未通过，阻断发布 | 缺少 `cryptography.json` 安全回移证据，未构建伪兼容包 |

## 3. Windows 候选包状态

已在本机使用主线 Python 3.11 完成一次通用 Windows AMD64 候选构建及冻结前置探针；由于
本轮修复发生在候选生成后，需在源码冻结提交后重新构建，并重新执行新装、覆盖升级、
非 C 盘数据目录、普通账号、自启动和卸载保留数据门禁。当前候选不得上传或作为正式下载。

Win7 x64/x86 构建被安全回移证据门禁阻断，不能通过复制旧版制品或改名宣称支持。Linux、
UOS、麒麟和 macOS rc.6 也尚未在对应原生环境完成构建与启动验证。

## 4. 发布判定与回滚

rc.6 不能进入 Cloud Studio、GitHub Release 或 EdgeOne Production。必须先补齐 mypy 存量错误
治理、Win7 安全回移证据、Linux/macOS 原生构建及模型签名/推理门禁，再按固化顺序发布。
任一门禁失败均保留 rc.4 生产版本，不替换官网清单，也不上传部分平台制品。
