# PartyOps Cloud Studio 安装包与 EdgeOne 官网部署

最后核对：2026-08-21（北京时间，UTC+8）
工作目录：`E:\codex\PartyOps\.publish-github`

## 当前结论

- 安装包上传：项目方 Cloud Studio 已挂载的受控后台 `/downloads/`。
- 官网部署：本地构建后直传 EdgeOne Makers 项目 `partyops-cn-overseas`（`makers-gjuf8qcecmi3`）。
- GitHub Release：同一批冻结制品的公开审计与海外备用线路。
- 禁止把第三方后台静态应用、聊天附件、临时网盘或 GitHub 代理当作国内主线路。

完整的不可变顺序、回读脚本、失败策略和完成判定见[国内下载、GitHub 与官网固化发布流程](domestic-release-workflow.md)。

## 安装包上传摘要

1. 先冻结版本、源码提交、文件名、字节数、SHA-256、文件头和支持等级。
2. 在 Cloud Studio 官方工作区打开现有安装包后台，把本次通过门禁的文件上传到 `/downloads/`。
3. 记录后台返回的对象标识、公开 HTTPS 地址和北京时间；不得记录令牌、Cookie 或验证码。
4. 从公开 `/downloads/<文件名>` 完整回读，用 `scripts/verify-domestic-download.ps1` 校验大小、SHA-256、文件头和 Range 行为。
5. 只有完整回读成功的 URL 才能进入官网。

## EdgeOne 摘要

```powershell
Set-Location E:\codex\PartyOps\.publish-github\website
npm ci
npm test
npm run build:edgeone
npm run test:sites

Set-Location E:\codex\PartyOps\.publish-github
npx -y edgeone@1.6.28 whoami
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone -n partyops-cn-overseas -e preview
# preview 验收通过后：
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone -n partyops-cn-overseas -e production
```

官网上线后检查 `partyops.cn`、`www.partyops.cn`、`/changelog`、`/resume/`、`/release-manifest.json`、移动端、FAQ、控制台和所有下载入口。异常时恢复上一条已核验的 EdgeOne production 部署。

## 历史审计

`v1.4.3-rc.9` 的国内下载曾使用第三方后台静态应用。其具体旧链接不再保留在当前说明和官网源码中；GitHub 历史 Release 继续承担版本审计和应急取回。自 `v1.4.4` 起一律执行 Cloud Studio `/downloads/` 固化流程。
