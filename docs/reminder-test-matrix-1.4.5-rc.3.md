# PartyOps 1.4.5-rc.3 提醒与通知验收矩阵

最后验证日期：2026-08-25（Asia/Hong_Kong）

## 验收原则

提醒不是单独的弹窗功能，而是“业务事实变化 → 立即重算 → 唯一活动提醒 → 历史审计保留 → 桌面端按偏好展示”的一致性链路。周期调度器只补偿漏算，不能成为改期后生效的必要条件。

## 已自动验证

| 领域 | 场景 | 预期结果 | 证据 |
| --- | --- | --- | --- |
| 事项截止 | 同日改时、跨日改期 | 更新同一活动提醒的正文与时间；旧未读提醒撤销 | `test_task_due_change_updates_then_revokes_stale_notification` |
| 事项逾期 | 多次周期刷新 | 始终收敛为一条活动提醒，不按天无限增长 | `test_overdue_notification_refreshes_single_row` |
| 发展党员 | 节点日期改变、完成、作废或档案归档 | 更新或撤销节点提醒；实际完成后不继续催办 | `test_party_development_notification_converges_after_date_change` |
| 偏好 | 总开关关闭、非法旧静默时段 | 禁止桌面弹窗；非法旧值安全回退且接口不返回 500 | `test_notification_disabled_preference_and_daily_refresh_branches`、`test_invalid_legacy_quiet_hours_fall_back_safely` |
| 周期汇总 | 周报/月报状态变化 | 只向有权用户生成对应提醒，已读与未读生命周期正确 | `test_period_report_full_maintenance_and_notifications` |
| 协同桌面 | 配对设备读取摘要、重复轮询、其他用户提醒 | 只返回当前账号摘要；同一 revision 不重复弹出 | `test_paired_desktop_notification_is_private_and_deduplicated` |
| Agent 退化 | 主机不可达、响应缺字段、系统通知 API 不可用 | 保留旧状态并安全回退，不泄露正文或阻断业务页面 | `test_command_notification_and_network_fallbacks`、`test_notifications_reachability_and_desktop_revision` |

专项筛选命令 `pytest tests -k "notification or reminder" -q` 共 13 项通过；相关用例同时包含在 1306 项后端全量覆盖率基线中。前端通知中心、今日工作台、设置提醒偏好、任务详情和发展党员时间轴包含在 203 项前端全量回归中。

## 原生制品阶段仍需复核

- Windows 10/11：系统通知允许/拒绝、静默时段、睡眠唤醒后的补偿刷新。
- Windows 7：气泡通知 API 不可用时保持页面内提醒，不能导致个人进程退出。
- UOS/麒麟 AMD64/ARM64：桌面通知服务存在/不存在时均可启动，协同账号仅看到自己的计数。
- macOS Intel/Apple Silicon：通知权限拒绝不影响页面启动，LaunchServices 重启后 revision 去重仍有效。

未完成对应原生复核的平台不得在支持矩阵中标为 `stable`。
