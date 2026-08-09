# PartyOps 1.3.3 东方四时长卷设计验收

- 验收日期：2026-08-03
- 验收状态：`passed`
- 视觉基准：用户提供的 13 张 GPT-image2 页面效果图
- 实际预览：`http://127.0.0.1:18769/`
- 浏览器基准视口：1280 × 720（小于目标 1366 宽度，用作更严格的横向空间检查）

## 验收结论

东方主题已从“全站复用同一底图”调整为“统一四季语言、页面独立场景”。每个页面使用独立场景资产，四季各自拥有对应变体；页头使用 `contain`，底部长卷使用 `100% auto` 覆盖主内容区整条宽度，二者均保持源图比例，不做横向或纵向拉伸。艺术层固定于视口、不可点击，不改变业务布局；高密度卡片、表格、按钮和输入区域保持清晰纸面底色。

本轮逐页对照未发现 P0、P1、P2 视觉问题：

- P0（阻断交互、白屏、布局破坏）：0
- P1（明显遮挡、裁切、变形、重复底图）：0
- P2（页面意象错误、视觉割裂、层级不清）：0

## 逐页对照

| 页面 | 场景意象 | 实际截图 | 对照画布 | 结果 |
| --- | --- | --- | --- | --- |
| 事项与清单 | 山径与芦岸 | `artifacts/design-qa/actual/tasks-1280x720.png` | `artifacts/design-qa/compare/tasks.png` | 通过 |
| 工作日历 | 日月四时与水岸 | `artifacts/design-qa/actual/calendar-1280x720.png` | `artifacts/design-qa/compare/calendar.png` | 通过 |
| 周期汇总 | 四时轮转与湖亭 | `artifacts/design-qa/actual/reports-1280x720.png` | `artifacts/design-qa/compare/reports.png` | 通过 |
| 工作日志 | 流水与石岸 | `artifacts/design-qa/actual/journal-1280x720.png` | `artifacts/design-qa/compare/journal.png` | 通过 |
| 快速收件箱 | 鸿雁传书与山径 | `artifacts/design-qa/actual/inbox-1280x720.png` | `artifacts/design-qa/compare/inbox.png` | 通过 |
| 专题工作空间 | 书斋、亭台与花枝 | `artifacts/design-qa/actual/topic-1280x720.png` | `artifacts/design-qa/compare/topic.png` | 通过 |
| 原始文件中心 | 卷宗与疏岸 | `artifacts/design-qa/actual/workspace-1280x720.png` | `artifacts/design-qa/compare/workspace.png` | 通过 |
| 重要档案 | 松石与水岸 | `artifacts/design-qa/actual/archives-1280x720.png` | `artifacts/design-qa/compare/archives.png` | 通过 |
| 迎检与归档 | 收卷与远亭 | `artifacts/design-qa/actual/inspection-1280x720.png` | `artifacts/design-qa/compare/inspection.png` | 通过 |
| 知识与联系人 | 竹柳与湖亭 | `artifacts/design-qa/actual/knowledge-1280x720.png` | `artifacts/design-qa/compare/knowledge.png` | 通过 |
| 文档比较与查重 | 双卷、疏岸与远山 | `artifacts/design-qa/actual/comparison-1280x720.png` | `artifacts/design-qa/compare/comparison.png` | 通过 |
| 设备协同 | 山川互联与驿道 | `artifacts/design-qa/actual/collaboration-1280x720.png` | `artifacts/design-qa/compare/collaboration.png` | 通过 |
| 文件接收箱 | 驿传飞雁与水岸 | `artifacts/design-qa/actual/transfer-1280x720.png` | `artifacts/design-qa/compare/transfer.png` | 通过 |

汇总画布：

- `artifacts/design-qa/sheets/comparison-1.png`
- `artifacts/design-qa/sheets/comparison-2.png`
- `artifacts/design-qa/sheets/comparison-3.png`
- `artifacts/design-qa/sheets/comparison-4.png`

## 浏览器运行时检查

- 13 个业务路由均解析到独立 `data-scene`，当前日期正确解析为夏季。
- 每页艺术层固定为 `position: fixed`，滚动 480px 前后画卷纵坐标保持一致。
- 艺术层与各槽位均为 `pointer-events: none`，不会截获按钮、表格、表单或滚动操作。
- 页头使用 `background-size: contain`；底部长卷使用 `background-size: 100% auto`，横向覆盖完整主内容区且保持源图宽高比。
- 全部页面 `scrollWidth <= clientWidth`，不存在横向溢出。
- 2026-08-03 继续使用“大暑”阶段画面，但页面不显示“大暑”文字；节气名称仅节气当天显示。
- 图片缺失时艺术层自动隐藏，不影响业务页面加载和操作。

## 底部长卷全宽回归（2026-08-03）

本轮针对“设备授权与状态”等页面底景缩成中央窄块的问题完成专项修复。根因是纵向构图素材被放入浅槽后继续使用 `contain`，浏览器按高度缩放，导致画面宽度显著小于主内容区；部分节气变量还会产生轻微横移，从而进一步放大断边感。

修复后的统一约束：

- 底景槽位与主内容区左右边界完全一致，`left/right` 均为 `0`。
- 底景使用 `100% auto` 等比铺满整条宽度，不使用 `100% 100%`，不发生横向或纵向压缩。
- 节气变量只保留纵向位移和等比缩放，不再横移底景。
- 页面仍保留独立场景素材，不以一张通用底图替代各页面意象。

专项证据：

- 修复前：`artifacts/design-qa/bottom-strip/00-before-fleet-grants.png`
- 修复后：`artifacts/design-qa/bottom-strip/03-after-fleet-grants-final.png`
- 24 路由汇总画布：`artifacts/design-qa/bottom-strip/routes-contact-sheet.png`
- 运行时几何：底景 `x=252`、`width=1028`、`right=1280`，与艺术画布完全一致；横向溢出为 `0`。
- 巡检范围：今日、事项、日历、收件箱、周期汇总、日志、专题、文件、档案、迎检、知识、文档比较、四个协同页面及八个管理页面，共 24 个路由。

专项回归未发现 P0、P1、P2 问题：

- P0：0
- P1：0
- P2：0

## 管理域页头画卷定位回归（2026-08-04）

本轮根据“自动归档规则”等管理页的最新参考框位，修正了前七个管理页面页头画卷过度靠右、与操作按钮争抢空间的问题。

统一后的桌面端安全槽位为：

- 横向范围约为主内容区 `31%—87%`，对应 2048×1118 参考画布的 `x≈804—1807`。
- 纵向起点为工具栏下方 `28px`，高度 `116px`，对应参考画布的 `y≈113—229`。
- 左右两端均使用渐隐蒙版，不形成矩形贴图边界。
- 最右侧独立保留“本页帮助 / 刷新 / 助手”操作区，画卷不进入按钮热区。
- 画面继续使用 `contain` 等比呈现，不做横向或纵向压缩。

验收证据：

- 实际页面：`artifacts/design-qa/oriental-edge-audit/templates.png`
- 修复前后并排画布：`artifacts/design-qa/oriental-edge-audit/compare-management.jpg`
- 覆盖路由：周期与模板、自动归档规则、报告模板、AI 草稿审批、系统更新、备份恢复、运行诊断。

管理域专项回归未发现 P0、P1、P2 问题：

- P0：0
- P1：0
- P2：0

## 自动化验证

- 前端组件与主题测试：34 项通过。
- 前端站点外壳测试：4 项通过。
- TypeScript 类型检查：通过。
- Vite 生产构建：通过。
- 后端测试：145 项通过。
- 四季及 32 份页面场景变体：清单、透明通道、绿色溢色和单季资源预算检查通过。

## 说明

本轮只修改主题映射、艺术组件、静态素材与视觉验收脚本，没有改变页面结构、业务流程或数据库模式。若需回滚，可恢复 `OrientalArtLayer.vue`、`oriental.ts`、`styles.css` 和 `frontend/src/assets/oriental/`；数据库无需回滚。

final result: passed
