# PartyOps 1.4.5-rc.6 模型验收记录

最后验证日期：2026-08-27（北京时间，UTC+08:00）  
状态：Windows AMD64 本地闭环通过；跨平台原生运行与公开发布门禁尚未完成。

## 锁定组合

| 组件 | 用途 | 当前状态 |
| --- | --- | --- |
| DeepSeek-R1-Distill-Qwen-1.5B Q4_K_M | 主编排和中文多步骤规划 | 正式签名包已恢复并在 rc.6 Windows AMD64 运行时真实推理通过 |
| Needle 2 | 意图路由、安全白名单和提示注入回退 | Windows AMD64 正式签名包已完成导入、激活、原生推理、回退和卸载复验 |
| BGE-small-zh-v1.5 | 中文语义检索、表头匹配和重复检测 | 正式签名包已恢复，512 维归一化向量与相似度探针通过 |
| Qwen3-0.6B | 低配回退 | 正式签名包已恢复并在 rc.6 Windows AMD64 llama.cpp 运行时真实推理通过 |
| 规则编排引擎 | 最终确定性兜底 | 随应用提供，不依赖模型包 |

## 安全边界

编排器仅生成固定工具白名单内的结构化计划；所有写入、删除、归档、权限、设备、网络
和模型操作仍需服务端权限检查、并发版本校验和用户确认。外部模型默认关闭，只能在逐次
同意后接收最小脱敏上下文作为第二意见。

本机从既有受控制品目录恢复了与 rc.2/rc.4 发布记录完全一致的四个正式包；恢复过程只按
既有长度与 SHA-256 接受文件，没有重新签名或接触私钥：

| 包 | 长度（字节） | SHA-256 |
| --- | ---: | --- |
| `PartyOps_DeepSeek_R1_Distill_Qwen_1.5B_Q4_K_M.partyops-modelpack` | 1,117,326,656 | `4da1d7c23f481f753746c0e5d5048f8b12a50f0cc6ae9cce435b66d6f58b81f6` |
| `needle2-intent-2.0.3-windows-amd64.partyops-modelpack` | 13,303,857 | `2d3dc4d8b4e455a4d7d53f442f14f3de9a03cb0902769d83d734cf70ec609794` |
| `PartyOps_BGE_Small_ZH_1.5.0.partyops-modelpack` | 94,965,776 | `57df3669e368497ad6d428dab309fb7665229a57edfe3a4fe1f2afd6a0f1a155` |
| `PartyOps_Qwen3_0.6B_Q8_0.partyops-modelpack` | 639,451,854 | `deb85abe5c9d9c79fddb943424144770ed35499a4f5b9e15047812297f448d63` |

rc.6 客户端使用 `packaging/uos/update-public-key.txt` 作为外部信任根，重新走生产上传接口完成
验签、安装和激活。BGE 生成三条 512 维有限、归一化向量；DeepSeek 与 Qwen3 使用本次冻结的
`llama-server b10331` 生成非空中文结果；Needle 完成原生离线意图推理、只读预览、停用和卸载。
规则引擎在模型不可用或输出未通过 Schema/权限/提示注入门禁时仍可独立工作。

## 待完成门禁

- 在 Windows AMD64/ARM64、Linux AMD64/ARM64、macOS Intel/Apple Silicon 对真实模型运行时
  做原生构建和资源上限测试；不支持的平台明确标为 `unavailable`。
- 通过提示注入、敏感字段、否定词、越权工具名、低置信度和两模型分歧测试后，才能更新
  官网模型目录和下载清单。
- 生成 rc.6 模型目录时只列上述锁定组合，不把测试辅助使用的 Qwen2.5 误列为编排器组件；
  Cloud Studio 公网回读通过前，官网仍不得宣称 rc.6 模型已公开挂载。
