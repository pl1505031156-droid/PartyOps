# PartyOps 历史下载包上传与 EdgeOne 官网部署记录

最后核对：2026-08-20（北京时间，UTC+8）  
工作目录：`E:\codex\PartyOps\.publish-github`

## 前置说明

历史发布记录曾把安装包下载线路写成“CloudStudio 国内主线路”，但这些链接实际由 WorkBuddy 后台静态应用部署生成，域名均为 `app.workbuddy.link`。它们不会出现在腾讯 Cloud Studio 控制台中，也没有创建对应的 Cloud Studio 项目。

本文只记录此前实际采用的上传方式，以及 PartyOps 官网实际采用的 EdgeOne 部署方式。

## 一、此前安装包是如何上传的

### 1. 整理待发布文件

先将已冻结的安装包、对应 `.sha256` 文件和简易下载首页放入静态应用目录。

Windows 下载应用包含：

```text
index.html
PartyOps_1.4.3-rc.8_windows_amd64.exe
PartyOps_1.4.3-rc.8_windows_amd64.exe.sha256
PartyOps_1.4.3-rc.8_windows7_amd64.exe
PartyOps_1.4.3-rc.8_windows7_amd64.exe.sha256
PartyOps_1.4.3-rc.8_windows7_x86.exe
PartyOps_1.4.3-rc.8_windows7_x86.exe.sha256
```

四个 Linux 安装包体积较大，因此分别放进四个独立静态应用，每个应用只包含：

```text
index.html
对应架构的 DEB 或 RPM
对应的 .sha256 文件
```

### 2. 通过后台静态应用部署

上述目录通过 WorkBuddy 的后台静态应用部署功能分别发布。部署完成后，平台为每个应用生成一个随机的 `*.app.workbuddy.link` 地址。

此前生成的五个地址为：

| 内容 | 历史地址 |
| --- | --- |
| Windows 10/11 与 Win7 三个安装器 | `https://11023a158d7f4d4e95870bb1a7cf89de.app.workbuddy.link/` |
| AMD64 DEB | `https://2f253ae7b3b84b86873185036bb19261.app.workbuddy.link/` |
| ARM64 DEB | `https://2ed7c64568e34adc8e77da6919457dbc.app.workbuddy.link/` |
| x86_64 RPM | `https://e09083291a684ec0815fdcacb3422f83.app.workbuddy.link/` |
| aarch64 RPM | `https://a2358f8c63404f0bab766be04bc0b3be.app.workbuddy.link/` |

仓库没有保存当时使用的内部部署命令，因此能够确认的是“通过 WorkBuddy 后台静态应用部署”，不能把这一步写成可在腾讯 Cloud Studio 控制台复现的命令。

### 3. 将地址写入官网

部署完成后，把每个公开文件的完整 URL 写入官网的下载映射。例如：

```text
https://11023a158d7f4d4e95870bb1a7cf89de.app.workbuddy.link/PartyOps_1.4.3-rc.8_windows_amd64.exe
```

官网同时保留 GitHub Release 作为备用下载线路。

### 4. 上传后校验

每个文件都从公开 URL 重新完整下载，不使用本地原文件代替线上验证。校验内容包括：

1. HTTP 下载是否成功；
2. 实际下载字节数是否与冻结安装包一致；
3. SHA-256 是否完全一致；
4. EXE、DEB、RPM 文件头是否正确；
5. 下载首页是否只展示当前版本文件。

PowerShell 校验示例：

```powershell
curl.exe -L --fail --output .\PartyOps-download.tmp "<公开下载地址>"
Get-FileHash -LiteralPath .\PartyOps-download.tmp -Algorithm SHA256
```

截至 2026-08-20，上述五个历史站点首页仍返回 HTTP 200，但其中是旧 rc.8 文件。

## 二、EdgeOne 官网是如何部署的

### 1. EdgeOne 项目绑定

官网使用 EdgeOne Makers 的直接上传项目：

```text
项目名称：partyops-cn-overseas
项目 ID：makers-gjuf8qcecmi3
环境：production
区域：global
```

本地绑定文件为 `.edgeone/project.json`：

```json
{
  "Name": "partyops-cn-overseas",
  "ProjectId": "makers-gjuf8qcecmi3"
}
```

该文件不保存腾讯云登录令牌。

### 2. 安装官网依赖

```powershell
Set-Location E:\codex\PartyOps\.publish-github\website
npm ci
```

### 3. 运行官网测试和生产构建

```powershell
npm test
npm run build:edgeone
npm run test:sites
```

其中：

- `npm test` 验证下载助手、FAQ、更新日志、下载计数和页面交互；
- `npm run build:edgeone` 校验发布元数据，执行 Vite 生产构建，并生成 `website/dist/edgeone`；
- `npm run test:sites` 验证静态入口、SPA 回退、API 路由和部署目录结构。

任何一步失败都不能继续部署生产环境。

### 4. 登录并确认腾讯云账号

```powershell
npx -y edgeone@1.6.28 login
npx -y edgeone@1.6.28 whoami
```

登录在浏览器中完成。登录令牌、Cookie、验证码和账号凭据不写入仓库。

### 5. 部署预览环境

```powershell
Set-Location E:\codex\PartyOps\.publish-github
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone `
  -n partyops-cn-overseas `
  -e preview
```

预览环境用于检查首页、下载助手、更新日志、简历、FAQ、发布清单和移动端布局，不影响正式域名。

### 6. 部署生产环境

预览检查通过后执行：

```powershell
Set-Location E:\codex\PartyOps\.publish-github
npx -y edgeone@1.6.28 makers deploy website/dist/edgeone `
  -n partyops-cn-overseas `
  -e production
```

CLI 会执行以下操作：

1. 核对或复用项目 `partyops-cn-overseas`；
2. 将 `website/dist/edgeone` 上传到 EdgeOne；
3. 创建一条生产部署；
4. 等待部署状态变成成功；
5. 返回 Deployment ID、预览 URL 和控制台 URL。

一次实际成功记录为：

```text
Project:       partyops-cn-overseas
Project ID:    makers-gjuf8qcecmi3
Deployment ID: dpadd8yuobez
Environment:   production
Status:        success
```

### 7. 部署后线上核验

部署成功后检查：

```powershell
$urls = @(
  'https://partyops.cn/',
  'https://www.partyops.cn/',
  'https://partyops.cn/changelog',
  'https://partyops.cn/resume/',
  'https://partyops.cn/release-manifest.json'
)

foreach ($url in $urls) {
  $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 30
  [pscustomobject]@{
    Url = $url
    Status = [int]$response.StatusCode
    Length = $response.RawContentLength
    ContentType = $response.Headers['Content-Type']
  }
}
```

还需验证：

1. `partyops.cn` 与 `www.partyops.cn` 均返回 HTTPS 200；
2. 根路径和版本化发布清单内容一致；
3. 下载按钮指向正确系统和架构；
4. 页面显示正确的大小、SHA-256 和北京时间；
5. 真实浏览器页面没有产品级控制台错误；
6. 下载计数接口失败时不会阻断下载安装包。

### 8. EdgeOne 回滚方式

如果新官网存在问题：

1. 打开腾讯云 EdgeOne Makers 控制台；
2. 进入项目 `partyops-cn-overseas`；
3. 打开“构建部署”；
4. 选择上一条已经核验成功的生产部署；
5. 重新部署该版本；
6. 再次检查两个正式域名、发布清单和下载入口。

页面回滚不删除 GitHub Release、不删除安装包，也不清空累计下载次数。

## 三、两条流程的关系

```text
安装包目录
  └─ WorkBuddy 后台静态应用部署
       └─ app.workbuddy.link 下载地址
            └─ 写入官网下载映射

官网源码
  └─ npm 测试与 build:edgeone
       └─ website/dist/edgeone
            └─ EdgeOne CLI 生产部署
                 └─ partyops.cn / www.partyops.cn
```

安装包上传和 EdgeOne 官网部署是两次独立操作。此前的 EdgeOne 部署只上传官网静态文件、Edge Functions 和下载链接，没有把数百 MB 的安装包重新打进官网部署包。

## 参考

- [腾讯云 EdgeOne Makers 直接上传](https://cloud.tencent.com/document/product/1552/127371)
- [腾讯云 EdgeOne CLI](https://cloud.tencent.com/document/product/1552/127423)

资料核对日期：2026-08-20。
