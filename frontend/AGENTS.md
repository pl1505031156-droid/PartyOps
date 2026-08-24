# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## PartyOps 1.2.0 持久设计决策

- 保持暖象牙纸张、朱红强调、炭黑正文的文书式视觉语言，不改为通用蓝紫 SaaS 风格。
- 一级导航收敛为“今日、工作、资料、协同、管理”五个工作域；低频能力进入域内二级入口。
- 合并“工作首页”和“今日工作台”，根路由默认展示必须处理、近期风险和本周进展。
- 工作日历使用“周视图＋当日日程”为默认视图，月视图和年度节点为辅助视图。
- 取消杂项式“效率工具中心”，专题、日历、比较、查重、自动化和 AI 审批分别回到所属业务域。
- 所有业务弹窗必须居中，正文独立滚动，标题和操作区固定；1366×768 下不得裁切主要操作。
- 主画布沿用“一周工作，一处看清”的文书式布局；左侧按五域分组，工作域标题支持展开和收纳，首次只展开当前域并记住用户选择，避免功能过多造成拥挤。
- 每个业务页面只保留一个“本页帮助”入口；帮助内容随页面变化，不与顶部全局指令或使用帮助重复。

## PartyOps 1.3.3 东方四时长卷持久设计决策

- 本节替代 1.3.2 的“仅今日与日历展示”限制。现有导航、卡片、表格、按钮、页面尺寸和业务流程仍保持不变；东方美学只作为不占位、不拦截交互的独立艺术皮肤层。
- 全部业务路由必须解析到明确的 `OrientalScene`；今日、日历、登录使用完整长卷，普通业务页使用页头与安全底景，高密度设置、审批和详情页只显示克制页头与空状态。
- 四季页头和底部长卷必须使用真实透明 WebP/PNG；不得使用 CSS、SVG、字符或渐变模拟山水。业务页面不得直接引用素材路径。
- 每个页面只选择一幅页头和一幅底景：四季决定色候，页面语义决定卷宗、鸿雁、文房、流水等构图；禁止在同一槽位叠加多张图。
- 画卷使用固定视口画布，顶部工具栏下方和页面底部均不随业务正文滚动；所有艺术层必须位于业务内容后方，表格、表单、按钮、分页和敏感正文保持纯净可读。
- 主题路由映射、场景插槽、季节资源、节气属性和固定坐标统一由 `theme/oriental.ts` 与 `OrientalArtLayer.vue` 管理，业务页面只提供路由，不编写季节判断。
- 默认标准装饰应达到视觉真值中的清晰程度，透明层次由素材 Alpha 控制；“减少装饰”隐藏所有图片，只保留农历、节气和题签。
- 只允许 180ms 以内淡入，不使用视频、视差、粒子、持续动画或模糊滤镜；所有艺术图层必须 `pointer-events: none`、`user-select: none`，素材失败不得影响业务请求。
- 视觉验收必须把参考图与同尺寸浏览器截图放在同一比较输入中，确认位置、裁切、遮挡、透明度和业务组件坐标后才能将 `design-qa.md` 标记为通过。
- 页头山水必须使用标题右侧整段横向留白，不能缩成右上角小景；底部长卷必须位于业务层后方，卡片和表格使用可读的半透明纸张底，让画卷自然透出而不穿过文字。
- 四季与二十四节气自动更迭必须保留：季节决定画卷色候，`activeSolarTerm` 在下一节气前持续驱动重心与舒展度；节气文字只在交节当天显示，不改变页面结构或叠加新的底图。
- 全部装饰素材必须按原始宽高比等比缩放；只能使用 `contain` 或等价的等比规则，禁止横向/纵向拉伸、`100% 100%` 填充和非等比变形。画面不能填满时，空余处保留为宣纸留白。
- 任务、收件、周期汇总、工作日志、专题、知识、设备协同和文件传输分别使用独立页面画面，并且每个页面画面必须提供春夏秋冬四个色候版本；页面之间不得复用同一专属底景来伪装差异。
- 页面专属画面只能进入经过视觉核对的安全留白。页面有文字、按钮、表格、表单或分页时优先保证业务内容，禁止以提高透明度或层级的方式把装饰压到正文上。
- 发布前必须逐页对照 13 张已选效果图，保存实际截图和并排对照图，检查构图融合、缩放比例、遮挡、裁切、透明边缘、横向溢出和固定画布；未逐页通过不得标记设计验收完成。
- 原始文件中心、重要档案以及管理域的八个页面（含使用帮助）禁止把竖幅空状态、档案物件或角落植物按全宽放大；这些页面的底部统一使用同季横向水岸长卷并控制在约 `190—320px` 的克制高度，页头继续保持页面语义差异。
- 所有页头与底景槽位必须在主题组件内统一做左右羽化，羽化范围覆盖源图 Alpha 收口位置；任何页面不得出现可辨认的垂直断边、矩形边框或“贴上去”的画片感。迎检页固定使用横向水岸长卷，不再把 `content-center` 中幅插画当底景。
- 靠右或靠左的画面主体必须贴合主内容区对应边界；源图透明装裱边移出视口，不能在边界前留下空白竖条，中央构图才使用双侧渐隐。
- 管理域前七个页面的页头画卷例外采用中央安全槽：固定在标题右侧、帮助与刷新操作区左侧，宽屏约占主内容区 `31%—87%`，两端自然渐隐；不得再推到最右边与操作按钮争抢空间。

## PartyOps 1.4.5-rc.2 党务工作域持久决策

- 用户明确要求在既有五域之外新增“党务”一级工作域；本节覆盖 1.2.0 的“五域”限制，一级导航为“今日、工作、党务、资料、协同、管理”。
- “三会一课”“党委（党组）理论学习中心组学习”“发展党员”必须是独立可见入口；原通用会议仅承载其他党建会议。
- 发展党员使用“快速测算、人员台账、材料清单、单位口径”统一入口；旧地址保留重定向，避免书签失效。
- 设置页为管理员提供“重新配置运行角色”入口，可重新打开配置向导选择个人、主机或协同机；角色切换失败必须回滚原模式，不能影响现有业务数据。
