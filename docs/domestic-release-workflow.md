# PartyOps 国内下载、GitHub 与官网固化发布流程

最后修订：2026-08-21（北京时间，UTC+8）
适用范围：PartyOps `v1.4.4` 及后续版本

## 前置说明

安装包只允许通过 WorkBuddy 内置的 `workbuddy_cloudstudio_deploy` Cloud Studio 后台发布程序上传，公开路径必须位于 `/downloads/`。这里是本机后台程序调用，不向 WorkBuddy 会话发送部署消息，也不使用浏览器项目上传页。聊天附件、临时网盘、GitHub 代理或未经完整回读的镜像不得进入官网主下载链。

本文只描述发布顺序和门禁，不保存 Cloud Studio、GitHub 或 EdgeOne 的令牌、Cookie、验证码和账号信息。上传界面返回的公开域名、对象标识和时间写入当次发布记录，不硬编码到本流程。

## 一、不可变发布顺序

1. 冻结源码提交、版本、支持矩阵和制品清单；
2. 将通过门禁的安装包上传到 Cloud Studio 受控 `/downloads/`；
3. 从公开 HTTPS 地址完整回读每个安装包；
4. 核对字节数、SHA-256、文件头、文件名和版本；
5. 把同一批冻结制品上传 GitHub Release；
6. 更新官网版本、清单、FAQ 和下载映射；
7. 本地测试并构建 EdgeOne 目录；
8. 部署 EdgeOne preview，浏览器验收通过后再部署 production；
9. 记录源码提交、制品摘要、回读结果、GitHub Release、EdgeOne Deployment ID、验证平台、未验证项和北京时间。

任一步失败均不得越过。某个架构未通过原生构建和目标系统验证时，将其标为 `preview` 或 `unavailable`，不生成伪兼容包。

## 二、冻结源码与制品

```powershell
Set-Location E:\codex\PartyOps\.publish-github
git status --short
git rev-parse HEAD
git tag --points-at HEAD
```

每个公开制品至少冻结以下字段：

- `version`、`release_tag`、平台、系统最低版本和架构；
- `filename`、精确字节数和 SHA-256；
- EXE/DEB/RPM/PKG 文件头；
- 源码提交、构建环境、测试记录和支持等级；
- Windows/macOS 签名状态、macOS 公证状态和目标机验证状态。

标签发布后不得移动，公开 URL 上不得静默覆盖同名文件。发现制品需要修改时重新构建并发布新版本。

## 三、Cloud Studio `/downloads/` 上传

1. 确认本机 WorkBuddy 后台连接可用；如登录态确需验证码，只在官方登录页完成，不把验证码或会话凭据写入仓库。
2. 为每个冻结制品建立最小静态目录，只包含必要首页和 `/downloads/<冻结文件名>`，然后直接调用 `workbuddy_cloudstudio_deploy`。
3. 不通过 WorkBuddy 聊天消息触发发布，也不依赖 Cloud Studio 网页普通项目列表；后台返回的受管应用在 WorkBuddy「设置 - 数据管理 - 我发布的应用」中管理。
4. 每次只选择冻结清单中的文件；不得上传调试目录、旧版安装包、临时归档或私钥。
5. 上传结束后记录公开 HTTPS 地址和北京时间。临时令牌只能保存在本地发布暂存目录，验证结束后不得写入仓库或公开日志。
6. 普通制品独立发布。单个文件超过后台单请求上限时，可先通过同一后台程序发布顺序分块接收器；接收器必须限制固定文件名、最大分块、连续偏移和一次性令牌，完成后在服务器端核对长度、文件头、SHA-256，原子封存并关闭上传路由。
7. 公开 URL 的路径必须精确为 `/downloads/<冻结文件名>`；若后台返回不同文件名、登录页、临时签名 URL 或 HTML 中转页，该文件不合格。
8. 官网只挂载当前公开版本；历史版本由 GitHub Release 保留审计记录。

Cloud Studio 只承载安装包，官网源码仍从本地构建后直接部署到 EdgeOne Makers 项目 `partyops-cn-overseas`，两条上传链路不得混用。

## 四、公开地址完整回读

每个文件必须使用仓库脚本独立验证：

```powershell
.\scripts\verify-domestic-download.ps1 `
  -Url 'https://<受控下载域名>/downloads/PartyOps_1.4.4_windows_amd64.exe' `
  -ExpectedBytes <冻结字节数> `
  -ExpectedSha256 '<冻结 SHA-256>' `
  -PackageType exe `
  -ExpectedFileName 'PartyOps_1.4.4_windows_amd64.exe' `
  -ExpectedVersion '1.4.4'
```

验证项：

1. HTTPS 成功且最终 URL 仍为受控域名的 `/downloads/`；
2. 响应不是 HTML；
3. Content-Length（若存在）和完整回读字节数与清单一致；
4. EXE 为 `MZ`、DEB 为 `!<arch>`、RPM 为 `ED AB EE DB`、PKG 为 `xar!`；
5. 完整流式 SHA-256 与冻结清单一致；
6. Range 返回 206 时校验范围，静态层忽略 Range 返回 200 时如实记录 `range_supported=false`；
7. 从包内元数据或配套验收记录确认版本为本次发布版本。

脚本对 HTTP 429 固定等待 20 秒；HTTP 5xx/超时等待 2 秒后最多重试一次。仍失败则阻断该文件。验证结果保存到当次发布记录，时间统一为北京时间。

## 五、GitHub Release

1. 产品源码和发布文档形成干净提交；官网源码按项目约定不提交；
2. 创建并推送不可变标签 `v<版本>`；
3. 创建 GitHub 普通 Release；稳定版 `prerelease=false`，预览版按真实状态标记；
4. 上传与 Cloud Studio 完整回读一致的冻结制品、`.sha256`、发布清单、SBOM、VEX 和验收记录；
5. 核对 GitHub 资产名称、字节数和摘要；
6. GitHub 只作公开审计和海外备用线路，国内主按钮指向已核验的 Cloud Studio `/downloads/`。

## 六、官网与 EdgeOne

在 `website/src/siteContent.js` 中集中维护版本、支持矩阵、国内 URL、GitHub 备用 URL、字节数、SHA-256、上传时间、签名状态和真机验证状态。官网只展示当前版本主安装包；`preview` 和 `unavailable` 必须清楚标注，且 `unavailable` 不提供下载按钮。

本地硬门禁：

```powershell
Set-Location E:\codex\PartyOps\.publish-github\website
npm ci
npm test
npm run build:edgeone
npm run test:sites
```

预览和生产部署：

```powershell
Set-Location E:\codex\PartyOps\.publish-github
npx -y edgeone@1.6.28 whoami
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone -n partyops-cn-overseas -e preview
# 预览验收通过后才能执行：
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone -n partyops-cn-overseas -e production
```

固定项目：

```text
项目名称：partyops-cn-overseas
项目 ID：makers-gjuf8qcecmi3
输出目录：website/dist/edgeone
```

预览至少检查 HTTPS、下载助手、FAQ、更新日志、移动端布局、控制台、发布清单和全部可用下载链接。生产部署失败或线上回归失败时，在 EdgeOne Makers 恢复上一条已核验生产部署。

## 七、生产上线验收

至少核验：

- `https://partyops.cn/` 与 `https://www.partyops.cn/`；
- `/changelog`、`/resume/` 和 `/release-manifest.json`；
- 所有 `stable`/`preview` 下载按钮及 GitHub 备用按钮；
- 每个 Cloud Studio `/downloads/` 地址的文件名、大小、SHA-256 和文件头；
- FAQ 中 Windows 未签名、macOS 未签名/未公证的安全放行步骤；
- 浏览器产品级控制台错误为零；
- 下载计数或非关键脚本失败不会阻断下载。

## 八、回滚与完成判定

- Cloud Studio 某文件失败：不切换该平台链接；修复后重新完整回读。
- GitHub 上传失败：不部署 EdgeOne production。
- EdgeOne preview 失败：不进入 production。
- EdgeOne production 异常：恢复上一条已核验生产部署，不删除 GitHub Release、冻结制品或累计下载数据。

只有以下证据同时存在，才能宣布发布完成：

1. 冻结源码提交与机器可读制品/支持清单；
2. 每个公开安装包的 Cloud Studio `/downloads/` 完整回读记录；
3. GitHub Release 同批资产一致性记录；
4. 官网测试、构建与 preview 验收记录；
5. EdgeOne production Deployment ID；
6. 正式域名、移动端、FAQ、清单和下载入口线上验收记录。

## 九、后台路径判定

`workbuddy_cloudstudio_deploy` 是本项目已验证的 Cloud Studio 后台发布路径。它与“在 WorkBuddy 对话中发送部署消息”不是一回事，也不会必然在 Cloud Studio 网页普通项目列表中创建可见项目。以后发布必须复用这一后台程序、保留每个公开根地址和完整回读报告；未经验证的新上传方式不能替代它。
