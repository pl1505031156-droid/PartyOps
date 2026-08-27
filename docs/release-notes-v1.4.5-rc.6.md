# PartyOps 1.4.5-rc.6 预发布说明

发布日期：2026-08-27（北京时间，UTC+08:00）  
状态：源码与迁移已进入本地验证，尚未完成九平台原生制品发布。

## 本版重点

- 从未公开发布的 rc.5 内部主线继续开发，数据库增量迁移为 `0026`；rc.4 仍是生产回滚点。
- 引入全系统智能编排器：DeepSeek-R1-Distill-Qwen-1.5B 量化版负责多步骤规划，Needle 2 负责意图路由和安全白名单，BGE-small-zh-v1.5 负责中文语义检索，Qwen3-0.6B 作为低配回退，规则引擎作为最终兜底。
- 模型只能生成结构化计划，不能生成任意函数、SQL、Shell、网络地址或文件路径。写入、导入、归档、删除、权限、设备和网络变更均需服务端校验、版本校验和用户确认。
- 新增编排会话、步骤级确认、最小上下文授权和脱敏审计；不保存原始提示、文档正文、凭据或外部模型完整响应。
- 保留旧版 Needle 意图预览和本地 AI 接口，模型包仍必须通过 Ed25519 正式信任根验签。
- Windows 启动修复：启动前验证运行时依赖和清单哈希；旧快捷方式通过受信安装根标记定位
  当前 Launcher；自启动恢复失败降级为 `AUTOSTART_RESTORE_DEFERRED`，不再误报
  `MODE_SWITCH_ROLLBACK_FAILED`。

## 模型分工与支持边界

| 组件 | 职责 | rc.6 状态 |
| --- | --- | --- |
| DeepSeek-R1-Distill-Qwen-1.5B Q4_K_M | 主编排与中文多步骤规划 | 已登记目录；逐架构制品、签名和真机推理门禁未完成前不标记稳定 |
| Needle 2 | 安全路由、固定工具白名单、低置信度回退 | 既有原生能力保留；无匹配平台包时安全回退规则 |
| BGE-small-zh-v1.5 | 中文向量检索、表头匹配、重复检测 | 作为内置检索能力候选；只保存向量与哈希，不保存原文 |
| Qwen3-0.6B | 低配置规划回退 | 仅在资源和签名门禁通过的平台启用 |
| 规则编排引擎 | 无模型时的确定性计划 | 所有支持平台始终可用 |

DeepSeek 模型来源为[官方 R1 仓库](https://github.com/deepseek-ai/DeepSeek-R1)及[1.5B 模型卡](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B)，BGE 来源为[官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)，Needle 来源为[官方仓库](https://github.com/cactus-compute/needle)。许可文件随模型包提供，未通过正式签名或架构验证的包不得进入官网稳定下载。

## 接口

- `GET /api/v1/ai/capabilities`
- `POST /api/v1/ai/orchestrations`
- `GET /api/v1/ai/orchestrations/{id}`
- `POST /api/v1/ai/orchestrations/{id}/replan`
- `POST /api/v1/ai/orchestrations/{id}/steps/{step_id}/approve`
- `POST /api/v1/ai/orchestrations/{id}/execute`
- `POST /api/v1/ai/orchestrations/{id}/cancel`
- `GET /api/v1/ai/orchestrations/{id}/audit`
- `POST /api/v1/ai/orchestrations/{id}/external-consent`

外部模型默认关闭。单独授权只允许脱敏目标、字段候选或用户选定文本片段作为第二意见，不能直接执行 PartyOps 操作。

## 发布门禁

rc.6 仍未发布安装包。必须依次完成 `0025 → 0026` 回滚演练、全量测试、模型正式签名、九平台原生构建、Cloud Studio `/downloads/` 回读、GitHub 预发布和 EdgeOne Preview；当前后端 mypy 存量错误和 Win7 安全回移证据缺失仍阻断发布，任一门禁失败都保留 rc.4 生产版本。
