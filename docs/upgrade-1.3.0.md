# 党建智办 PartyOps 1.3.0 安装升级与模型包

最后更新：2026-08-02

## 已安装电脑升级

1. 主机管理员进入“管理 → 系统设置 → 系统更新”。
2. 导入签名文件 `partyops_1.3.0.partyops-update`，核对版本、数据库模式 `0015` 和更新内容。
3. 点击“开始升级”。系统自动校验签名、磁盘和数据库，建立升级前备份，再升级主机。
4. 主机健康检查通过后向在线协同机下发匹配架构程序；离线设备下次上线继续。
5. 未完成 1.3.0 更新的协同机不能进入业务页面，更新完成后自动恢复原配对和登录入口。

只有更新助手损坏时，才使用同架构 `partyops_1.3.0_amd64.deb` 或 `partyops_1.3.0_arm64.deb` 做一次修复性原位安装。不得卸载旧版，不得删除 `/var/lib/partyops` 或用户数据目录。

## 新电脑安装

- 海光、兆芯等 x86_64 电脑使用 amd64 包。
- 飞腾 D2000/8 等 `aarch64` 电脑使用 ARM64 包。
- 双击对应 `.deb`，输入管理员密码；首次启动选择主机或协同电脑。
- 主机完成诊断后生成一次性入网信息；协同电脑粘贴完整信息，等待主机批准。

## 本地模型包

模型不随主程序安装。管理员在主机“系统设置 → AI 与权限”导入签名 `.partyops-modelpack` 并点击启用，协同机只读取主机的模型状态。

模型包至少包含：

- Qwen3 1.7B GGUF 文件。
- BGE Small 中文 ONNX 文件和 `tokenizer.json`。
- 两个模型的许可文件。
- 文件大小、SHA-256、架构、版本和 Ed25519 发布签名。

离线构建示例：

```bash
python3 packaging/uos/build-model-pack.py \
  --llm /safe/models/Qwen3-1.7B-Q8_0.gguf \
  --embedding /safe/models/bge-small-zh-v1.5.onnx \
  --tokenizer /safe/models/tokenizer.json \
  --license /safe/models/QWEN-LICENSE \
  --license /safe/models/BGE-LICENSE \
  --private-key /safe/keys/partyops-ed25519-private.key \
  --architecture universal \
  --output artifacts/partyops-local-ai-1.0.0.partyops-modelpack
```

私钥不得进入源码、安装包、模型包或业务电脑。正式环境只接受安装包内固定公钥对应的签名。

## 资源与降级

- 上下文 4096、单并发、最多 4 线程、内存上限约 3.5GB。
- 可用内存不足 3GB、备份/恢复/更新/扫描正在运行或文件正在传输时暂停本地智能。
- 空闲 5 分钟卸载 LLM；没有模型时仍保留规则建议。
- 模型损坏或推理失败只影响草稿与语义重排，不影响事项、协同、文件和备份。

## 发布前必须完成

- UOS V20 amd64 主机和 D2000 ARM64 灾备主机分别验证 `llama-server`、ONNX、导入、推理、暂停、恢复和卸载。
- 三机验证版本一致、任务共享、断线重连和系统内更新。
- 运行完整自动化、数据库升级/回退、签名伪造、低内存和进程崩溃测试。
