# PartyOps 1.4.0 验收记录

最后验证：2026-08-09

## 自动化结果

| 项目 | 命令/证据 | 结果 |
| --- | --- | --- |
| 后端功能 | `.venv\Scripts\python.exe -m pytest -q` | 164 通过，0 失败 |
| 前端功能 | `corepack pnpm run test` | 35 通过，0 失败 |
| 前端类型/构建 | `pnpm run typecheck`、`pnpm run build` | 通过；仅保留 UI 大块提示 |
| 后端依赖 | `python -m pip_audit -r backend\requirements.txt` | 无已知漏洞 |
| 前端依赖 | `pnpm audit --prod` | 无已知漏洞 |
| 覆盖率 | `coverage-backend-1.4.0.json`、Vitest V8 | 后端语句 78%/行 73%；前端 4.87%，未达 90% |
| 冻结运行时 | `scripts\smoke-windows-runtime.ps1` | 1.4.0 / SQLite 3.53.3 / schema 0016 |

## Windows 安装器实机验收

- 环境：Windows 11 x64（当前构建号 26200），本机管理员模式。
- 安装：退出码 0，安装日志无 `exception/error/失败`。
- 服务：`PartyOpsUpdateService` 自动启动；`PartyOpsHost` 注册为自动启动并可手工进入 `RUNNING`，显式角色未配置时不启动业务主机。
- 恢复策略：两项服务均配置 5 秒、15 秒两次重启。
- 网络：只创建专用网络入站规则，开放 TCP 18765、18766。
- 桌面：开始菜单、桌面入口、`partyops-file` 协议和当前用户 Agent 自启均存在。
- 卸载：退出码 0；两项服务、PartyOps 进程、防火墙规则、协议、自启和安装文件均清理；业务数据目录保留。
- 证据日志：`artifacts/windows-install-final-1.4.0.log`、`artifacts/windows-uninstall-final-1.4.0.log`。

## Chrome 协同闭环与视觉验收

- 管理员账号：档案类别管理、档案新建、字段级中文错误、首个错误自动聚焦、通知面板和通知跳转通过。
- 普通协同账号：档案新建、编辑、扫描件入口可见；类别管理和档案作废入口不可见；已实际创建档案。
- 评论闭环：普通协同账号追加说明并提及管理员，管理员收到去重后的提及通知，跳回事项并完成层级回复。
- 协同页：普通协同账号可见“本机协同状态、我的共享目录、我的传输、权限说明”，不再出现空白页。
- “我的工作”：我主办、我协办、我审核和“步骤分派给我”四类入口均存在，“我审核”只展示当前账号待审核事项。
- 1280×720 CSS 视口无页面级横向溢出、无浏览器控制台警告/错误；1.3.4/1.4.0 同状态对照图位于 `output/chrome-qa-1.4.0/`。

## 候选制品

- `PartyOps_1.4.0_windows_amd64.exe`
- 大小：85,490,512 字节
- SHA-256：`f5fdf8cd249b62a95b1a35658939b075fa7b09a99e67a90522d711f5b2fee754`
- 内置 SQLite DLL SHA-256：`79fd9ec89dba3f8bd64529a2ca8e9dde6ae6edc486c55a1d3f1ce77975a8375c`（与经校验输入一致）
- Authenticode：未签名，只允许受控试装。
- UOS 双架构构建套件：`PartyOps-UOS-build-kit.zip`，280,452,536 字节，SHA-256 `d6a18928fa7881b837eefb330be565a27c37abb16aea4c1a4cbf79e68eae0fc7`；499 项，包含迁移 `0016` 与 1.4.0 更新说明，禁止分发私钥计数为 0。

## 未完成矩阵

- Windows 10 x64 主机/协同机。
- Windows 11 x64 已完成管理员/普通账号 Chrome 业务闭环；真实协同 Agent 绑定后的四角色设备组合仍需两台 Windows 真机补齐。
- UOS V20 amd64/arm64 主机/协同机、原位升级、回滚和连续 24 小时运行。
- 20GB 大文件、三种跨机传输的真实异构设备断线恢复。
