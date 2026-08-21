# PartyOps Cloud Studio 安装包与 EdgeOne 官网部署

最后核对：2026-08-21（北京时间，UTC+8）
工作目录：`E:\codex\PartyOps\.publish-github`

## 当前结论

- 安装包上传：调用 WorkBuddy 内置的 `workbuddy_cloudstudio_deploy` Cloud Studio 后台发布程序；不向 WorkBuddy 会话发送部署消息，也不走浏览器项目上传页。
- 官网部署：本地构建后直传 EdgeOne Makers 项目 `partyops-cn-overseas`（`makers-gjuf8qcecmi3`）。
- GitHub Release：同一批冻结制品的公开审计与海外备用线路。
- 禁止把聊天附件、临时网盘、GitHub 代理或未经完整回读的地址当作国内主线路。

完整的不可变顺序、回读脚本、失败策略和完成判定见[国内下载、GitHub 与官网固化发布流程](domestic-release-workflow.md)。

## 安装包上传摘要

1. 先冻结版本、源码提交、文件名、字节数、SHA-256、文件头和支持等级。
2. 通过本机 WorkBuddy MCP 后台直接调用 `workbuddy_cloudstudio_deploy`，把仅含 `index.html` 与 `/downloads/<冻结文件名>` 的静态目录发布为受管应用。这里调用的是后台程序，不是在聊天中请 WorkBuddy 代为部署。
3. 普通制品按文件独立发布，避免一个超大归档触发上传网关限制；若单个制品仍超过后台单请求上限，只允许先用同一后台程序发布受控分块接收器，再按顺序上传、在服务器端核对字节数、文件头和 SHA-256 后原子封存，并立即关闭上传入口。
4. 记录后台返回的公开 HTTPS 地址和北京时间；不得记录令牌、Cookie、验证码或临时上传凭据。
5. 从公开 `/downloads/<文件名>` 完整回读，用 `scripts/verify-domestic-download.ps1` 校验大小、SHA-256、文件头和 Range 行为。
6. 只有完整回读成功的 URL 才能进入官网。

后台发布的受管应用不一定出现在 Cloud Studio 网页的普通项目列表中，这是预期行为。应在 WorkBuddy 的「设置 - 数据管理 - 我发布的应用」中查看或删除；公开文件仍由 Cloud Studio 后台实例承载。

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

## 1.4.4 实际执行记录

- 后台发布程序：`workbuddy_cloudstudio_deploy`；未发送 WorkBuddy 会话消息。
- Cloud Studio 公网完整回读：9/9 通过，完成时间 `2026-08-21 22:57:22（北京时间，UTC+8）`。
- GitHub 普通 Release：`v1.4.4`，35/35 资产同名同大小，发布时间 `2026-08-21 23:03:48（北京时间，UTC+8）`。
- EdgeOne preview：`dprm7eyjnivb`。
- EdgeOne production：`dpgtm3zx9352`。
- EdgeOne 项目：`partyops-cn-overseas`（`makers-gjuf8qcecmi3`）。
- 正式域名、更新日志、简历、发布清单、移动端与九个国内下载入口均完成线上验收。
