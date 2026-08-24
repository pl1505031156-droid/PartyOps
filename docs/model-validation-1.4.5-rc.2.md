# PartyOps 1.4.5-rc.2 本地模型发布验收记录

最后验证：2026-08-24（北京时间）

## 1. 信任根与发布边界

- 四个直导包均使用与 `packaging/uos/update-public-key.txt` 匹配的正式 Ed25519 私钥签发。
- 公钥 SHA-256 指纹：`7d9d69a006ab26add736a16d0f9eb4f3667343c63da18d7672daf5d6fa2de2a3`。
- 私钥只以发布机本地路径传入构建脚本，不进入模型包、源码、日志、聊天或网站。
- `.partyops-modelpack` 内的公钥只是声明；导入时必须由客户端外部信任根验签，不能自签自信。
- 模型只增强检索和草稿，未安装或运行失败不影响事项、档案、协同、备份和权限。

## 2. 正式签名直导包

| 模型 | 来源与固定提交 | 权重/量化 | 包长度（字节） | 包 SHA-256 | 实测峰值 |
| --- | --- | --- | ---: | --- | ---: |
| BGE Small 中文语义检索 | `BAAI/bge-small-zh-v1.5@7999e1d3359715c523056ef9478215996d62a620` | ONNX FP32 | 94,965,776 | `57df3669e368497ad6d428dab309fb7665229a57edfe3a4fe1f2afd6a0f1a155` | 640 MiB |
| Qwen2.5 0.5B 中文轻量草稿 | `Qwen/Qwen2.5-0.5B-Instruct-GGUF@9217f5db79a29953eb74d5343926648285ec7e67` | 官方 GGUF Q4_K_M | 491,405,222 | `d150d18982dd877ad3e7f10cc005a68a5402d61948eed8c90e19465b4ae1456a` | 527 MiB |
| Qwen3 0.6B 中文增强草稿 | `Qwen/Qwen3-0.6B-GGUF@23749fefcc72300e3a2ad315e1317431b06b590a` | 官方 GGUF Q8_0 | 639,451,854 | `deb85abe5c9d9c79fddb943424144770ed35499a4f5b9e15047812297f448d63` | 1,130 MiB |
| DeepSeek R1 Distill Qwen 1.5B 推理草稿 | `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B@ad9f0ae0864d7fbcd1cd905e3c6c5b069cc8b562` | PartyOps 使用 llama.cpp `b10331@7ba604f1c` 从官方 BF16 转换为 GGUF Q4_K_M | 1,117,326,656 | `4da1d7c23f481f753746c0e5d5048f8b12a50f0cc6ae9cce435b66d6f58b81f6` | 1,721 MiB |

BGE 原始 ONNX SHA-256 为
`3f24c0667e723a6ba012aa1dd4a77e2e898464cc5192988837786826ff405b43`，
tokenizer SHA-256 为
`258135af5b19e1c7f25ea95432e699e275053b381b04c5915054467cca8abab3`。
DeepSeek 官方 `model.safetensors` SHA-256 为
`58858233513d76b8703e72eed6ce16807b523328188e13329257fb9594462945`，
本地量化 GGUF SHA-256 为
`20e37d8fce6493c8c21ec270f7d7b2b29ba78705cd43ccd729a2588779dce3ca`。

许可证随包封装：BGE 为 MIT；两档 Qwen 为 Apache-2.0；DeepSeek R1 权重为 MIT，
并附带 Qwen2.5 基座 Apache-2.0 许可。模型源、许可、逐文件长度和 SHA-256
都写入签名清单。

## 3. 真实导入与运行结果

`backend/tests/test_real_release_model_packs.py` 对最终四个包执行以下闭环：

1. 通过 `/api/v1/admin/ai/model-packs` 上传，不调用测试专用安装捷径；
2. 使用客户端外部公钥验签、核对每个成员长度和 SHA-256，再安装到隔离受管目录；
3. 启用 `embedding` 或 `llm` 能力；
4. BGE 生成三条 512 维归一化向量，并验证相同文本相似度；
5. 三个 GGUF 由安装包同版 `llama-server b10331` 启动，调用正式本地草稿路径并确认中文正文非空；
6. 每档结束后精确停止其运行进程，业务数据库与权限不受影响。

最终结果：`1 passed`。独立运行探针结果如下：

| 模型 | 启动就绪 | 64/1200 token 验收响应 | 结果 |
| --- | ---: | ---: | --- |
| Qwen2.5 0.5B Q4_K_M | 1.739 秒 | 0.593 秒 | 中文正文非空 |
| Qwen3 0.6B Q8_0 | 2.293 秒 | 1.069 秒 | 中文正文非空 |
| DeepSeek R1 Distill 1.5B Q4_K_M | 2.188 秒 | 8.419 秒 | 中文正文非空 |

实测还发现 Qwen3 默认思考模式可能在短请求中耗尽 token 后返回空正文；rc.2
已在正式请求中设置 `chat_template_kwargs.enable_thinking=false` 并补充回归测试。
DeepSeek R1 的模板保留推理过程，但 1,200 token 正式预算已产生非空最终正文。

## 4. 新手选择建议

- 只需要资料搜索：选 BGE Small，体积和内存最低，不生成正文。
- 8 GB 左右内存、优先速度：选 Qwen2.5 0.5B。
- 希望小体积下有更自然中文：选 Qwen3 0.6B。
- 8 GB 以上内存、重视材料逻辑梳理：选 DeepSeek R1 Distill 1.5B；它更慢，也更容易过度推理。
- DeepSeek R1 Distill 7B、Qwen3 4B 及以上只给官方来源和本机服务教程，不在官网重复存储大文件。

任何模型都可能出现事实错误；输出只能作为草稿，法规、时间节点、人员和金额必须由用户核对。

## 5. 未签名模型如何安全接入

未签名 `.partyops-modelpack` 会被拒绝，不提供关闭验签、替换公钥或改名导入教程。
管理员若已从发布方官方仓库取得普通 GGUF，可把它作为外部本机服务接入：

```powershell
llama-server --model <模型.gguf> --host 127.0.0.1 --port 18767
```

然后在“设置 → AI 配置”新增 OpenAI 兼容服务，地址填写
`http://127.0.0.1:18767/v1`，只在确认进程、模型来源、许可和 SHA-256 后标记
“可信本机”。不得监听 `0.0.0.0`，不得把未经 TLS 和认证的端口开放到局域网或公网。

原始 ONNX 权重不能直接导入。单位如需自有模型包，应在自有客户端版本中以
可审计方式配置单位信任根并自行签发；公开 PartyOps 客户端只接受官方信任根。

## 6. 尚待发布门禁

本记录证明本机签名、导入和运行闭环，不等同于公开下载已经可用。模型包只有在
Cloud Studio `/downloads/models/` 上传、公开地址完整回读、长度/SHA-256/文件头
复核及官网链接测试通过后，才从“已签名待发布”改为“可直接下载”。
