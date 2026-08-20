# PartyOps 国内下载与官网发布工作流

最后修订：2026-08-20（北京时间，UTC+8）

适用范围：PartyOps 后续所有候选版与稳定版

## 目标与硬门禁

本流程把“制品冻结 → 国内安装包托管 → GitHub 普通 Release → EdgeOne 官网”固化为唯一发布顺序。官网生产环境只有在以下条件全部满足后才能切换：

1. 发布清单中的版本、文件名、字节数和 SHA-256 已冻结；
2. 每个主安装包都已部署到国内可访问的 `app.workbuddy.link` 独立静态应用；
3. 每个国内链接均已重新完整下载，并与冻结制品逐字节等价；
4. GitHub Release 与国内链接指向同一批制品；
5. 官网测试、生产构建和预览核验全部通过。

任何安装包出现哈希不一致、下载内容为 HTML、文件头不符、静态应用未公开或完整下载失败时，只阻断该平台，不得把未核验链接写入生产官网。

## 一、冻结发布输入

### 1. 确认仓库状态

```powershell
Set-Location E:\codex\PartyOps\.publish-github
git status --short
git rev-parse HEAD
git tag --points-at HEAD
```

发布标签创建后不得移动。官网源码位于 `website/`，仍按项目约定独立部署，不提交到 GitHub；产品源码、README、发布说明与本工作流文档正常提交。

### 2. 生成冻结表

每个公开安装包至少记录：

- 版本与平台；
- 文件名；
- 精确字节数；
- SHA-256；
- 源码提交；
- 构建与验收记录；
- 是否签名、是否完成真机验证。

必须先冻结清单，再开始任何远端上传。上传后不得在原 URL 上静默替换文件；需要修改制品时发布新版本。

## 二、准备 WorkBuddy 国内静态应用

### 1. 每个安装包使用独立目录

在被 Git 忽略的发布暂存目录下，为每个平台建立独立子目录。每个目录只包含一个主安装包和一个最小 `index.html`：

```text
artifacts/workbuddy-<版本>/
├─ windows-amd64/
├─ windows7-amd64/
├─ windows7-x86/
├─ linux-deb-amd64/
├─ linux-deb-arm64/
├─ linux-rpm-x86_64/
├─ linux-rpm-aarch64/
├─ macos-arm64/
└─ macos-x86_64/
```

使用同卷硬链接复用冻结安装包可以避免重复占用磁盘；创建后仍须重新计算硬链接路径的 SHA-256。不得把旧版本、调试包、临时归档或多个架构混入同一目录。

`index.html` 只提供当前文件的下载入口、版本、字节数和 SHA-256，不加载会阻断下载的第三方脚本。

### 2. 使用 WorkBuddy 后台连接器直接部署

此处使用 Codex 本机会话中已运行的 WorkBuddy 后台连接器，不进入腾讯 Cloud Studio 控制台，也不向 WorkBuddy 聊天窗口发送发布消息。连接器工具名为 `workbuddy_cloudstudio_deploy`，每个目录分别调用一次：

```json
{
  "action": "deploy",
  "directory": "<平台独立目录的绝对路径>",
  "port": 3000,
  "entry": "index.html",
  "shareLink": true
}
```

连接器返回 `shareLink`、`sandboxId` 和 `verified`。令牌、临时端口、Cookie 及连接器命令行只能在内存中使用，禁止写入仓库、发布记录或终端截图。

`verified=true` 只证明平台完成部署，不代替下一节的外部完整下载校验。

## 三、逐文件核验国内直链

国内文件 URL 由 `shareLink + "/" + 文件名` 组成。每个文件按以下顺序检查：

1. HTTPS 请求成功，最终 URL 仍为预期静态应用；
2. `Content-Type` 不是 `text/html`；
3. `Content-Length` 与冻结字节数一致；
4. 探测并记录 Range 能力；返回 206 时核对分段范围，静态层忽略 Range 并返回 200 时不得伪造为支持断点续传；
5. 文件头正确：EXE 为 `MZ`，DEB 为 `!<arch>`，RPM 为 `ED AB EE DB`，PKG 为 `xar!`；
6. 从公开 URL 完整下载到独立临时目录；
7. 完整文件大小和 SHA-256 与冻结表完全一致。

仓库已提供流式校验脚本，不把大文件重复落盘：

```powershell
.\scripts\verify-domestic-download.ps1 `
  -Url '<公开文件 URL>' `
  -ExpectedBytes <冻结字节数> `
  -ExpectedSha256 '<冻结 SHA-256>' `
  -PackageType exe
```

脚本会先探测 Range 状态和文件头，再以流式方式读取完整公开文件并核对字节数及 SHA-256。`range_supported=false` 不会被伪装成 206；它表示该静态线路可以完整下载，但不具备断点续传能力。若使用落盘方式做复核，临时验证文件必须逐个删除；删除前先解析绝对路径并确认目标仍位于专用验证目录内。不得对工作区根目录、`artifacts` 根目录或未解析变量执行递归删除。

验证结果记录北京时间。HTTP 429 等待 20 秒；HTTP 5xx 或超时等待 2 秒后最多重试一次；仍失败则阻断该文件。

## 四、创建 GitHub 普通 Release

1. 推送干净提交到 `main`；
2. 创建不可变标签；
3. 创建 `draft=false`、`prerelease=false` 的普通 Release；
4. 候选版设置 `make_latest=false`，避免冒充最新稳定版；
5. 上传安装包、可选 `.sha256`、发布清单、SBOM、VEX 和验收记录；
6. 逐项比对 GitHub 资产的名称、字节数和摘要。

GitHub 是公开审计与海外备用线路。国内用户的官网主按钮必须指向已经完成第三节校验的 WorkBuddy 国内 URL；GitHub 只作为次级备用入口。

## 五、更新官网并部署 EdgeOne

### 1. 更新下载映射

在 `website/src/siteContent.js` 中：

- `downloadFiles` 写入九个已核验的国内完整 URL；
- `links.*` 保留 GitHub 官方备用 URL；
- 页面显示冻结的字节数、SHA-256、上传时间和国内镜像完整校验时间；
- 官网只展示当前版本，不再展示旧安装包的主下载入口。

不得使用 GitHub 代理、临时网盘、需要登录的链接或未经完整下载校验的地址。

### 2. 本地门禁

```powershell
Set-Location E:\codex\PartyOps\.publish-github\website
npm ci
npm test
npm run build:edgeone
npm run test:sites
```

任何命令失败都不能部署生产环境。

### 3. EdgeOne 预览与生产部署

官网绑定项目固定为：

```text
项目名称：partyops-cn-overseas
项目 ID：makers-gjuf8qcecmi3
```

```powershell
Set-Location E:\codex\PartyOps\.publish-github
npx -y edgeone@1.6.28 whoami
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone -n partyops-cn-overseas -e preview
# 预览核验通过后才执行：
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone -n partyops-cn-overseas -e production
```

## 六、生产上线后核验

至少核验：

- `https://partyops.cn/`；
- `https://www.partyops.cn/`；
- `/changelog`、`/resume/` 和发布清单；
- 桌面端与移动端下载助手；
- 九个主下载按钮与 GitHub 备用按钮；
- 每个国内 URL 的文件名、大小、SHA-256 和文件头；
- 浏览器产品级控制台错误为零；
- 下载计数接口或第三方脚本失败不会阻断下载。

线上核验记录必须包含版本、源码提交、GitHub Release、九个 WorkBuddy `sandboxId/shareLink`、EdgeOne Deployment ID、北京时间和验证结论。

## 七、失败回滚

- 国内某个平台失败：官网该平台继续使用上一条已核验线路或 GitHub 官方链接，不切换错误 URL；
- GitHub 上传失败：不部署 EdgeOne，保留本地冻结制品；
- 官网预览失败：不进入 production；
- 生产官网异常：在 EdgeOne Makers 中恢复上一条已核验生产部署；
- 回滚官网时不删除 GitHub Release、WorkBuddy 新应用、历史数据或累计下载数。

发布完成后，可在 WorkBuddy 的「设置 - 数据管理 - 我发布的应用」中查看和管理本次九个静态应用。删除前必须先确认官网已不再引用相应链接。

## 八、发布完成判定

只有以下证据同时存在，任务才能标记完成：

1. 冻结清单与源码提交；
2. 九个国内 URL 的完整回下载校验记录；
3. GitHub 普通 Release 的资产一致性记录；
4. 官网测试与 EdgeOne 构建记录；
5. EdgeOne 生产 Deployment ID；
6. 正式域名、移动端和全部下载入口的线上核验记录。

本流程不依赖远端 CI/CD；所有构建、测试、上传、校验与部署均从本地受控环境执行并留存证据。

## 九、v1.4.3-rc.9 实际执行记录

执行日期：2026-08-20（北京时间，UTC+8）

冻结产品提交：`5cb136ad1b79d6d4e1d44bb0570a4777f3ca3945`

GitHub 普通 Release：<https://github.com/pl1505031156-droid/PartyOps/releases/tag/v1.4.3-rc.9>

| 平台 | WorkBuddy `sandboxId` | 国内主下载地址 | 完整校验时间 |
| --- | --- | --- | --- |
| Windows 10/11 x64 | `e17d366baa364266b29da1051f725740` | <https://e17d366baa364266b29da1051f725740.app.workbuddy.link/PartyOps_1.4.3-rc.9_windows_amd64.exe> | 21:00:54 |
| Windows 7 x64 | `e58d9ed8378b42f7b0ac5cf092c6c5c8` | <https://e58d9ed8378b42f7b0ac5cf092c6c5c8.app.workbuddy.link/PartyOps_1.4.3-rc.9_windows7_amd64.exe> | 21:01:02 |
| Windows 7 x86 | `9cbc96e690b24814b661e312d1b2ba2a` | <https://9cbc96e690b24814b661e312d1b2ba2a.app.workbuddy.link/PartyOps_1.4.3-rc.9_windows7_x86.exe> | 20:58:43 |
| Linux DEB AMD64 | `0671032b239e42b397e52db431ab340f` | <https://0671032b239e42b397e52db431ab340f.app.workbuddy.link/PartyOps_1.4.3-rc.9_linux_amd64.deb> | 21:01:11 |
| Linux DEB ARM64 | `c9dc2523e636494ea9bf199f94ba3f5d` | <https://c9dc2523e636494ea9bf199f94ba3f5d.app.workbuddy.link/PartyOps_1.4.3-rc.9_linux_arm64.deb> | 21:00:52 |
| Linux RPM x86_64 | `0267a794af4341dd9319d5decf887a89` | <https://0267a794af4341dd9319d5decf887a89.app.workbuddy.link/PartyOps-1.4.3-0.rc.9.1.x86_64.rpm> | 21:01:13 |
| Linux RPM aarch64 | `e14ee35ad2c64f34a87898485a66cc2a` | <https://e14ee35ad2c64f34a87898485a66cc2a.app.workbuddy.link/PartyOps-1.4.3-0.rc.9.1.aarch64.rpm> | 21:00:55 |
| macOS Apple 芯片 | `2619faf159604762aedf540cf91c0228` | <https://2619faf159604762aedf540cf91c0228.app.workbuddy.link/PartyOps_1.4.3-rc.9_macos_arm64.pkg> | 21:01:10 |
| macOS Intel | `2ebb2e460ec149bb964b29a08f94adb8` | <https://2ebb2e460ec149bb964b29a08f94adb8.app.workbuddy.link/PartyOps_1.4.3-rc.9_macos_x86_64.pkg> | 21:01:18 |

九个地址均返回 HTTP 200 和 `application/octet-stream`，完整流式回读的字节数、SHA-256 与 EXE/DEB/RPM/PKG 文件头全部匹配冻结清单。该静态层忽略 Range 并以分块传输返回 200，故本次明确记录 `range_supported=false`，不宣称支持断点续传。

官网门禁结果：Vitest 40/40、EdgeOne/Sites 8/8、4,975 个生产模块构建通过。预览部署 `dpi3d81ebqp8` 验证了 rc.9 清单与九个国内域名；生产部署 `dp2ror0i8deq` 状态为成功。`partyops.cn`、`www.partyops.cn`、`/changelog`、`/resume/` 与 `/release-manifest.json` 均返回 HTTP 200，线上 JS 包含全部九个国内节点。
