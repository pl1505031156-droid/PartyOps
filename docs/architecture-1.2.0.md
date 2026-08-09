# PartyOps 1.2.0 模块边界与维护约定

最后更新：2026-07-31

## 目标

1.2.0 的首要工程约束是低耦合、模块化和可回滚。业务数据库仍只有主机一份，但任务、日历、周期、文件、设备、更新等领域不能依靠互相直接修改内部表来联动。

## 后端边界

```text
routers（HTTP 契约、认证、If-Match）
  ↓
domain services（业务规则和幂等操作）
  ↓
models / SQLAlchemy transaction
  ↓
event_outbox
  ↓
projection services（周期汇总、通知、搜索）
```

- `routers/today.py`：今日工作台聚合，只读查询。
- `routers/calendar.py` 与 `calendar_service.py`：日期投影和工作日规则。
- `routers/relations.py` 与 `object_graph.py`：统一对象关联和反向链接。
- `routers/recurrence_extensions.py` 与 `recurrence.py`：周期预览、例外、生成。
- `routers/guidance.py` 与 `projections.py`：用户引导和可重建投影。
- `routers/integration.py`：只负责组合领域路由，不承载业务逻辑。
- 设备、文件传输、更新执行继续是独立边界；不得从页面路由直接执行系统命令。

新增模块必须遵守：

- 一个领域路由不能导入另一个领域路由。
- 跨领域联动优先写入同事务事件，再由幂等投影处理。
- 可修改对象使用整数 `version` 与 `If-Match`。
- 长任务进入持久化后台任务，不在请求中长时间占用数据库写锁。
- API 只返回稳定错误码、中文说明和追踪编号。

## 前端边界

- `navigation.ts` 是五域导航的唯一配置来源。
- 页面通过路由懒加载，不把所有工具的数据请求集中到首页。
- `ObjectContextPanel.vue` 统一承载关联和活动时间线。
- `PageHelp.vue` 统一承载上下文帮助。
- 状态中文由 `utils/labels.ts` 管理，时间由 `utils/datetime.ts` 管理。
- 弹窗统一使用全局居中、固定标题和底部、正文滚动规范。

## 数据迁移

- `0011`：登记 Alembic 正式基线。
- `0012`：对象关联与活动事件。
- `0013`：周期例外、日历偏好、投影检查点、用户引导进度。

旧库先由一次性兼容适配器登记到 `0010`，随后只允许 `alembic upgrade head`。禁止在日常启动中继续增加手写 `ALTER TABLE`。

## 回滚原则

- 程序升级前创建版本化备份。
- 数据迁移失败时不允许新版连接旧模式数据库。
- 发布报告快照不被投影重建改写。
- 原始文件不移动、不删除、不重命名。
- 系统内更新只执行签名清单列出的 Debian 包，不执行更新包自带脚本。
