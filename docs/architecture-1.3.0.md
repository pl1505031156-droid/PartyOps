# PartyOps 1.3.0 东方皮肤与本地智能模块边界

最后更新：2026-08-02

## 目标

1.3.0 不重排任何业务页面。东方皮肤是可关闭的表现层，本地智能是可失败的旁路能力；二者均不得成为事项、文件、档案、协同、备份和更新的启动前置条件。

## 前端边界

```text
appearance store
  → 根节点 data-season / data-art-level / data-reduce-motion
  → AppShell 唯一季节页头层
  → 全局空状态位图

lunar.ts
  → 今日 / 日历 / 登录完整题签
  → AppShell 既有副标题中的简化题签
```

- `styles.css` 是四季静态资源变量的唯一入口。
- 页面不得自行判断季节或直接引用季节图片。
- 素材 `pointer-events:none`，不改变组件坐标，不覆盖表格正文。
- `lunar-typescript` 锁定离线版本；失败不得阻断路由或登录。

## 后端边界

```text
appearance.py / routers/appearance.py
  → 只维护主题偏好，不依赖业务对象

recommendations.py
  → 可解释规则建议（始终可用）
  → 后台语义检查点（可选）
  → 只重排既有 FTS 命中，不扩大数据范围

model_packs.py
  → 签名、哈希、路径、许可和安装校验

local_ai.py
  → ONNX 按需加载
  → llama.cpp 单并发进程
  → 内存/系统繁忙降级
```

- 原始文件中心仍只索引文件属性，不进入语义索引。
- 敏感事项在候选入口即排除；本地和外部模型都不能绕过既有 AI 策略。
- 搜索请求不会冷启动 ONNX，只复用后台已加载会话，避免拖慢 Ctrl+K。
- LLM 只生成草稿，业务变更仍走原接口、权限、`If-Match` 和审计。
- AI Worker 崩溃、模型缺失、签名错误、可用内存不足 3GB 或系统繁忙时返回中文降级状态，主服务继续运行。

## 数据迁移

- `0014`：`user_appearance_preferences`。
- `0015`：模型包、语义检查点、可追溯推荐，并允许本地调用不绑定外部供应商。
- 旧库严格执行 `alembic upgrade head`；回退到 `0013` 时仅清理 1.3.0 新增的本地调用记录，再恢复旧非空约束。

## 交付边界

- 主程序 `.partyops-update` 不携带约 2GB 模型。
- `.partyops-modelpack` 只接受清单内模型、分词器和许可文件，不执行包内脚本。
- ONNX/Tokenizer/NumPy 和 `llama-server` 属于对应架构主程序运行时，必须在 UOS V20 amd64、ARM64 分别原生验收。
