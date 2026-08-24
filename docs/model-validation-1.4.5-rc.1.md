# PartyOps 1.4.5-rc.1 本地模型验收记录

最后验证：2026-08-24（北京时间）

## BGE Small 中文语义检索

- 上游：`BAAI/bge-small-zh-v1.5`
- 固定提交：`7999e1d3359715c523056ef9478215996d62a620`
- 上游模型卡声明许可证：MIT
- 导出格式：ONNX FP32，opset 17，动态批次与动态序列长度
- ONNX SHA-256：`3f24c0667e723a6ba012aa1dd4a77e2e898464cc5192988837786826ff405b43`
- tokenizer SHA-256：`258135af5b19e1c7f25ea95432e699e275053b381b04c5915054467cca8abab3`
- 输出维度：512
- PyTorch 与 ONNX Runtime 测试输入最大绝对误差：`3.9637088775634766e-06`

本机使用隔离临时 Ed25519 信任根执行了完整发布路径：构建 format v2
模型包、正式导入接口验签、受管目录安装、启用 embedding 能力，以及三条
中文文本的真实推理。输出均为有限数、512 维且归一化；相同文本余弦相似度
大于 `0.99999`。测试命令：

```powershell
$env:PARTYOPS_REAL_BGE_ONNX='<固定提交导出的 ONNX>'
$env:PARTYOPS_REAL_BGE_TOKENIZER='<固定提交导出的 tokenizer.json>'
$env:PARTYOPS_REAL_BGE_LICENSE='<审核后的 MIT 许可说明>'
uv run --project backend pytest backend/tests/test_real_bge_model_pack_e2e.py -q
```

动态 INT8 的两次候选量化没有通过预设的 `0.99` 单句向量保真门禁，最低
余弦分别约为 `0.9604` 和 `0.9809`，因此不进入官网候选资产。

## 正式签名状态

仓库和本机构建环境均未挂载与客户端既有信任公钥
`fEbQCm6VLHYv7f8pKYIeGGD+gkW6EHz/W/ODs5DoGkc=` 匹配的 Ed25519 私钥。
PartyOps 验证器只信任客户端外部配置的公钥，不信任模型包自带公钥，因此：

- 不使用临时密钥生成或上传冒充正式的模型包；
- 不静默轮换客户端信任根；
- 只有挂载原私钥并在隔离环境确认导出公钥完全匹配后，才能生成官网正式包；
- 若原私钥已遗失，必须通过新版本客户端完成可审计的信任根轮换，不能仅替换官网文件。

临时测试私钥和临时签名包由 pytest 临时目录托管，测试结束即清理，不进入
Git、GitHub Release、Cloud Studio 或官网。
