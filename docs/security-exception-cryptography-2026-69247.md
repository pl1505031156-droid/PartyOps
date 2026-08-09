# cryptography CVE-2026-69247 适用性审查与候选例外

最后审查：2026-08-09

## 结论

PartyOps 已从 `cryptography 48.0.1` 升级到当前官方稳定版 `49.0.0`，从而修复 CVE-2026-69248 与 CVE-2026-69249。CVE-2026-69247 的官方修复版本为尚未稳定发布的 `50.0.0`，故不能用不存在的稳定制品完成常规升级。

该公告只影响 `pkcs7_decrypt_der`、`pkcs7_decrypt_pem`、`pkcs7_decrypt_smime` 对攻击者可控 PKCS#7 EnvelopedData 的自动解密。对 `backend/`、`scripts/`、`packaging/` 的代码检索结果为零调用；PartyOps 使用的是 Fernet、本机 CA/X.509 证书生成、RSA、Ed25519 签名验证，不提供 PKCS#7 解密接口。因此当前产品代码没有形成公告要求的解密预言机入口。

官方公告还说明，cryptography 官方轮子使用 OpenSSL 3.2+ 时无效 RSA 填充会返回合成明文，特定错误通道不会区分符合格式的密文。PartyOps 的候选构建使用官方 Windows 轮子，不自行链接旧 OpenSSL。

## 门禁处理

- 候选验证命令：`pip_audit -r backend/requirements.txt --ignore-vuln PYSEC-2026-3552`。
- 该命令只允许忽略 `PYSEC-2026-3552 / CVE-2026-69247`，其余依赖公告必须为零。
- 例外仅用于 1.4.1 隔离候选构建，不等于正式风险接受；正式签名发布仍被阻断。
- 上游发布 `cryptography 50.0.0` 稳定版后，升级源码、锁文件、Windows 环境和 UOS 双架构离线轮子，删除忽略参数并复跑全量回归。
- 如果 PartyOps 未来引入 S/MIME/PKCS#7 解密，本例外立即失效，在完成上游修复升级前禁止上线该能力。

## 证据

- 官方 PyPI 当前稳定版本：49.0.0，支持 Python 3.9+、Windows x64 与 Linux x86_64/aarch64 官方轮子。
- GitHub Reviewed 公告：CVE-2026-69247，影响 `>=44.0.0,<50.0.0`，CVSS 8.2；利用条件包含对攻击者输入执行 PKCS#7 解密并高频反射不同结果。
- 本地验证：全量后端 170 项通过；`pip check` 无依赖冲突；带单项例外的依赖审计为“无已知漏洞，忽略 1 项”。
