# 需求追踪矩阵

输入来源：两份本地 Word 提示词、产品定位文档及用户确认的 1.2.0、1.3.0 实施计划；最后核对日期 2026-08-02。

| 需求域 | 实现 | 主要证据 | 状态 |
| --- | --- | --- | --- |
| 品牌启动页 | 党建智办 / 基层党建工作闭环协同系统 / PartyOps，方案 1 暖象牙与朱红文书风 | `frontend/src/views/LoginView.vue`、`design-qa.md` | passed |
| 单一数据源 | 主机保存 SQLite/附件；终端只保存地址、令牌和灾备副本 | `setup_wizard.py`、`client_agent.py` | 已实现 |
| 局域网协同 | 明确绑定本机 LAN IP；SSE 续传，10 秒轮询降级；最多 20 台设备 | `events.py`、`fleet.py`、前端设备中心 | 多设备接口与权限测试通过；UOS 现场待验 |
| 任务闭环 | 8 状态、审核退回、重新打开、主办/协办/审核 | `state_machine.py`、`task_service.py` | 自动化与 E2E 通过 |
| 三类事项 | 快速、标准、项目；项目含独立子任务 | 模型、任务详情、项目完成门槛测试 | 已实现 |
| 快速收件 | 粘贴、Word、WPS、PDF、图片/OCR、文本，本地候选、识别摘要、原文核对并人工确认；原件随任务归档 | `intake.py`、`InboxView.vue` | 已实现并完成浏览器交互验证 |
| 一事一档 | 材料项、多版本、单一终稿、SHA-256 去重、授权下载 | `storage.py`、材料 API/界面 | 自动化与 E2E 通过 |
| 搜索与导出 | FTS5，年度/类别/责任人/状态/终稿文件名；Word/Excel/迎检 ZIP | `support.py`、`exports.py` | 实测通过 |
| 模板与周期 | 月/季/半年/年/自定义周期，固定日、月末、季末、最后工作日、暂停/终止、单次跳过/改期，上期经验和联系人复用 | `recurrence.py`、`recurrence_extensions.py`、`TemplatesView.vue` | 自动化与生产构建通过 |
| 知识与联系人 | 增删改查、任务引用联系人 | `KnowledgeView.vue`、support API | 已实现 |
| 五域导航与今日工作台 | 今日、工作、资料、协同、管理；今日必须办、本周完成、下周计划、延续、风险、提醒、最近文件和系统异常 | `navigation.ts`、`TodayView.vue`、`today.py` | 19 路由 1366×768 浏览器巡检通过 |
| 统一工作日历 | 周/月/年度节点、正式/内部日期、周期、汇总、提醒、节假日/调休、人员/领域/专题筛选 | `calendar.py`、`calendar_service.py`、`CalendarView.vue` | API 自动化、浏览器布局和弹窗检查通过 |
| 统一对象关联 | 任务、文件、档案、日志、报告、知识、联系人和专题双向关联、反向链接、活动时间线 | `object_graph.py`、`relations.py`、`ObjectContextPanel.vue` | API 自动化通过；更多业务详情页接入持续补测 |
| 周/月/季/年报告 | 周期树、自动归集、发布快照、锁定、Word/Excel | `reports.py`、`operations.py`、`ReportsView.vue` | 自动化通过 |
| 专业日志与提醒 | 人工日志可修订、系统事件不可删除、持久化去重、免打扰、浏览器与终端伴随桌面提醒 | `work_journal.py`、`notifications.py`、`client_agent.py`、`JournalView.vue` | 自动化通过 |
| 原始文件中心 | 发现全部目录和普通文件后由管理员选择接入范围；所有类型仅索引名称与属性，不读正文/OCR、不展开压缩包；主机按 UOS 默认程序打开，支持任务关联与固化 | `workspace.py`、`client_agent.py`、`WorkspaceView.vue`、`open-local-file.sh` | 名称检索、正文不入库、一次性打开令牌、范围选择通过；URI 处理器自愈注册和内部 CA 显式校验已有回归契约，仍需 UOS 实机打开 WPS 复核 |
| 重要档案中心 | 任意四位年度、人事调动、事业编/公务员年度考核、自定义类别字段、受管扫描件、OCR、修订/作废、权限、全局搜索和年度校验包 | `archives.py`、`archive_service.py`、`archive_exporting.py`、`ArchivesView.vue` | API、导出和目录自动识别回归通过；UOS 浏览器现场待验 |
| AI 权限沙箱 | OpenAI 兼容接口、机器密钥、默认拒绝、敏感事项硬禁止、只读草稿 | `ai_service.py`、`AssistantView.vue` | 模拟接口自动化通过 |
| 东方美学皮肤 | 不改变业务布局；仅在今日工作台与工作日历的固定页头留白带显示四季横景、农历和二十四节气题签；支持管理员固定主题、个人减少装饰和减少动态 | `appearance.py`、`appearance` API、`appearance.ts`、`lunar.ts`、`OrientalArtLayer.vue`、`assets/oriental/` | 1366×768、1440×1024、1920×1080 浏览器视觉、固定定位、滚动稳定和路由白名单回归通过；UOS 字体仍待目标机复核 |
| 本地智能推荐 | 主机旁路运行规则推荐、可选 BGE 语义重排和可选 Qwen GGUF；资源不足、模型缺失或服务异常时不影响业务 | `recommendations.py`、`local_ai.py`、`model_packs.py`、AI API | 规则、权限、签名、资源降级和版本失效测试通过；双架构推理待 UOS 原生运行时与模型包验收 |
| 系统状态 | 架构、版本、业务/设备端口、SSE、数据库完整性、投影、存储、后台任务、备份、设备版本和 AI 脱敏状态 | `admin.py`、`SettingsView.vue` | API 自动化与前端构建通过 |
| 数据迁移 | 旧库一次性登记 0010，0011 起统一 Alembic；当前模式 0016 | `database.py`、`alembic/versions/0011*`—`0016*` | Windows `0015 → 0016 → 0015` 迁移自动化通过；真实旧库与 UOS 回滚待现场验收 |
| 原位升级 | 相同包名、升级前备份、模式 0016、平台级程序回滚、数据库/附件回滚、升级记录 | `upgrades.py`、`update_executor.py`、`build-deb.sh`、`build-windows.ps1` | 自动化迁移与清单兼容通过；Windows 11 安装/卸载通过，UOS 双架构回滚待实机验收 |
| 敏感事项 | 默认最小保存，显式授权正文/附件，越权隐藏 | 权限、存储和 API 测试 | 通过 |
| 并发冲突 | `If-Match`、409、冲突字段与可恢复草稿 | 任务 API、冲突比较界面 | 自动化通过 |
| 审计 | 写、删、审核、下载、恢复、权限变化追加记录 | admin API、设置页 | E2E 可见 |
| 备份恢复 | 在线快照、版本/模式/哈希校验、预恢复备份、原子切换和回滚 | `backups.py`、恢复测试 | 自动化与在线恢复通过 |
| 终端灾备 | 只读令牌、ETag/304、独立校验、重连补拉、Linux 自启动 | `client_agent.py`、`setup_wizard.py` | 实测通过 |
| 设备身份与安全通道 | 一次性入网码、CA 指纹固定、内部 CA、Agent 独立 mTLS 端口、证书轮换/撤销 | `pki.py`、`fleet.py`、`client_agent.py` | 自动化与静态回归通过；UOS 现场待验 |
| 联合文件中心 | 终端只读目录索引、联合搜索、主机中转、设备间传输、8MB 分块断点续传、审批和配额 | `fleet.py`、`workspace.py`、`client_agent.py`、`FleetView.vue` | 自动化接口通过；20GB/多终端现场待验 |
| 更新中心 | `.partyops-update` 第2版、Ed25519、双架构强校验、更新历史、主机健康检查、协同电脑强制版本一致、用户确认安装、浏览器续等、幂等安装和包级回滚 | `updates.py`、`update_executor.py`、`device_versions.py`、`RequiredUpdateView.vue` | 门禁、历史、拒绝旧格式与队列自动化通过；UOS 原位升级待验 |
| 日常效率 | 今日工作台、批量处理、Ctrl+K、保存视图、专题空间、日历、自动归档建议、交接包 | `productivity.py`、`TodayView.vue`、`TasksView.vue`、`AppShell.vue`、`HelpView.vue` | 后端 116 项测试、前端 16 项测试、构建和 19 路由浏览器实测通过 |
| 文档与智能辅助 | DOCX/PDF/图片比较、精确/近似重复检测、报告模板、AI 引用草稿与审批队列 | `productivity.py`、`ai.py`、`EfficiencyView.vue`、`ReportsView.vue`、`AssistantView.vue` | 后端 116 项测试、前端构建、浏览器实测通过 |
| 性能 | 10,000 条首页 p95 ≤500 ms；100,000 文件搜索 p95 ≤1 秒 | `performance_smoke.py`、`workspace_performance_smoke.py` | 443.69 ms；文件搜索 1.15 ms |
| UOS 双架构制品 | 静态 SQLite 3.51.3、自带 OCR、amd64/ARM64 便携包与 `.deb`、自动选包 | `packaging/uos/`、离线 build kit、release packager | 脚本与依赖已就绪；两种目标机待原生构建 |

说明：Windows 侧不能诚实替代 UOS V20 原生 PyInstaller/glibc/指令集验收。需求代码与双架构离线构建链已实现，最终四项 Linux 制品必须分别在实际 amd64、ARM64 目标机生成后才能标记“目标机通过”。

质量门槛现状以最新本地测试报告为准。1.3.3 在补齐 UOS 双架构原生构建、三机协同和连续运行验收前仍是功能集成候选，不得标记为正式发布。
