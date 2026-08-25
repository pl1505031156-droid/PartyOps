# PartyOps 1.4.5-rc.3 Needle 2 模型验收

最后验证日期：2026-08-25（北京时间）

- 模型 ID：`needle2-intent`
- 运行时版本：`2.0.3`
- 平台：Windows AMD64
- 文件：`needle2-intent-2.0.3-windows-amd64.partyops-modelpack`
- 长度：13303857 字节
- SHA-256：`2d3dc4d8b4e455a4d7d53f442f14f3de9a03cb0902769d83d734cf70ec609794`
- 签名信任根指纹：`7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3`
- 上游来源：[cactus-compute/needle](https://github.com/cactus-compute/needle)
- 许可证：Apache-2.0；包内保留来源和许可元数据。

## 已完成测试

正式签名成品已通过 8 项生命周期测试：签名导入、清单与架构校验、激活、原生 DLL 离线意图推理、结构化输出校验、否定词/提示注入安全回退、规则引擎回退以及卸载清理。模型只生成结构化意图预览，不直接执行新建、修改、删除、权限或其他业务写操作。

## 限制

本次只有 Windows AMD64 原生运行时完成制品级验证。Windows ARM64/x86、Linux 和 macOS 目录显示“不可用”，不得改名或复制 Windows 包伪装兼容。低置信度、未知参数、缺少证据字段、越权工具或提示注入命中时必须回到规则引擎或人工确认。

