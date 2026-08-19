param(
  [string]$InnoCompiler = "",
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "prepare-ocr-runtime.ps1")
$releaseVersion = "1.4.3-rc.7"
$releaseTag = "v1.4.3-rc.7"
$runtimeRoot = Join-Path $repoRoot "artifacts\windows-runtime"
$artifactRoot = Join-Path $repoRoot "artifacts"
$bundleRoot = Join-Path $artifactRoot "PartyOps-$releaseVersion-windows-amd64"
$expectedBundleRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "artifacts\PartyOps-$releaseVersion-windows-amd64"))
$sqliteDll = Join-Path $repoRoot "vendor\windows\sqlite-3.53.4\runtime\sqlite3.dll"
$expectedSqliteVersion = "3.53.4"
$expectedSqliteSha256 = "AB57D0437795ECC757CB693F32EA224173FA9856594D95CFA6B5033E645CD1EC"
$localAiRoot = Join-Path $repoRoot "vendor\windows\local-ai\llama-b10331"
$installPathValidator = Join-Path $PSScriptRoot "validate-install-path.ps1"

$validatorBytes = [IO.File]::ReadAllBytes($installPathValidator)
if ($validatorBytes.Length -lt 3 -or
    $validatorBytes[0] -ne 0xEF -or
    $validatorBytes[1] -ne 0xBB -or
    $validatorBytes[2] -ne 0xBF) {
  throw "安装期 PowerShell 脚本必须使用 UTF-8 BOM；否则 Windows PowerShell 5.1 会在传统 ANSI/GBK 系统上误解码中文并导致安装失败。"
}
$windowsPowerShell51 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $windowsPowerShell51)) {
  throw "构建机缺少 Windows PowerShell 5.1，无法验证安装目录脚本的真实兼容性。"
}
$validatorProbeId = [guid]::NewGuid().ToString("N")
$validatorProbePath = Join-Path $env:TEMP "PartyOps-rc7-安装路径-$validatorProbeId\中文 空格"
$validatorProbeDiagnostic = Join-Path $env:TEMP "PartyOps-rc7-validator-$validatorProbeId.txt"
try {
  & $windowsPowerShell51 -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File $installPathValidator -Path $validatorProbePath `
    -DiagnosticFile $validatorProbeDiagnostic
  if ($LASTEXITCODE -ne 0) {
    $validatorProbeMessage = if (Test-Path -LiteralPath $validatorProbeDiagnostic) {
      Get-Content -Raw -LiteralPath $validatorProbeDiagnostic -Encoding UTF8
    } else {
      "未生成诊断文件"
    }
    throw "安装目录脚本未通过 Windows PowerShell 5.1 真实执行门禁：$validatorProbeMessage"
  }
} finally {
  Remove-Item -LiteralPath $validatorProbeDiagnostic -Force -ErrorAction SilentlyContinue
}

if (-not $Python) {
  $Python = if ($env:PARTYOPS_PYTHON) {
    $env:PARTYOPS_PYTHON
  } else {
    Join-Path $repoRoot ".venv\Scripts\python.exe"
  }
}
if (-not (Test-Path -LiteralPath $Python)) {
  throw "未找到用于生成发布清单的 Python：$Python"
}

if ([System.IO.Path]::GetFullPath($bundleRoot) -ne $expectedBundleRoot) {
  throw "拒绝清理未验证的 Windows 组装目录：$bundleRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "PartyOps\PartyOps.exe"))) {
  throw "缺少已冻结的 PartyOps 主程序，请先执行 build-windows.ps1。"
}
if (-not (Test-Path -LiteralPath $sqliteDll)) {
  throw "缺少经校验的 SQLite 运行时：$sqliteDll"
}
$actualSqliteVersion = & $Python -c "import ctypes,sys; lib=ctypes.WinDLL(sys.argv[1]); lib.sqlite3_libversion.restype=ctypes.c_char_p; print(lib.sqlite3_libversion().decode())" $sqliteDll
if ($LASTEXITCODE -ne 0 -or $actualSqliteVersion -ne $expectedSqliteVersion) {
  throw "SQLite DLL 版本应为 $expectedSqliteVersion，实际为 $actualSqliteVersion。"
}
$actualSqliteHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sqliteDll).Hash
if ($actualSqliteHash -ne $expectedSqliteSha256) {
  throw "SQLite DLL SHA-256 不匹配，拒绝组装正式安装包。"
}
foreach ($runtimeFile in @("llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "ggml.dll", "LICENSE", "SOURCE.json")) {
  if (-not (Test-Path -LiteralPath (Join-Path $localAiRoot $runtimeFile))) {
    throw "缺少经固定版本校验的 Windows 本地 LLM 运行时：$runtimeFile"
  }
}

$onedirEntries = @(
  "PartyOps",
  "PartyOpsAgent",
  "PartyOpsWizard",
  "PartyOpsUpdater",
  "PartyOpsLauncher",
  "PartyOpsDataCleanup",
  "PartyOpsFileOpen",
  "PartyOpsService",
  "PartyOpsUpdaterService"
)
foreach ($entry in $onedirEntries) {
  if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "$entry\$entry.exe"))) {
    throw "缺少已冻结的 Windows 组件：$entry.exe"
  }
}

if (Test-Path -LiteralPath $bundleRoot) {
  Remove-Item -LiteralPath $bundleRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
foreach ($entry in $onedirEntries) {
  # 当前冻结布局为 onedir；按与 build-windows.ps1 相同的顺序合并共享
  # _internal，避免旧 package 脚本漏掉卸载器或读取已淘汰的根目录 EXE。
  Copy-Item -Path (Join-Path $runtimeRoot "$entry\*") -Destination $bundleRoot -Recurse -Force
}

Copy-Item -LiteralPath $sqliteDll -Destination (Join-Path $bundleRoot "sqlite3.dll") -Force
$installerIcon = Join-Path $repoRoot "packaging\windows\partyops.ico"
$installerImage = Join-Path $repoRoot "packaging\windows\partyops-1024.png"
if (-not (Test-Path -LiteralPath $installerIcon)) {
  throw "缺少 Windows 安装器品牌图标：$installerIcon"
}
if (-not (Test-Path -LiteralPath $installerImage)) {
  throw "缺少 Windows 安装器品牌图片：$installerImage"
}
Copy-Item -LiteralPath $installerIcon -Destination (Join-Path $bundleRoot "partyops.ico") -Force
Copy-Item -LiteralPath $installerImage -Destination (Join-Path $bundleRoot "partyops-1024.png") -Force
$internalRoot = Join-Path $bundleRoot "_internal"
New-Item -ItemType Directory -Path $internalRoot -Force | Out-Null
Copy-Item -LiteralPath $sqliteDll -Destination (Join-Path $internalRoot "sqlite3.dll") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\uos\update-public-key.txt") -Destination $bundleRoot -Force
foreach ($notice in @("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
  $noticePath = Join-Path $repoRoot $notice
  if (-not (Test-Path -LiteralPath $noticePath)) { throw "发布包缺少开源声明文件：$notice" }
  Copy-Item -LiteralPath $noticePath -Destination $bundleRoot -Force
}
Copy-Item -Path (Join-Path $localAiRoot "*") -Destination $bundleRoot -Force
& (Join-Path $bundleRoot "llama-server.exe") --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "llama.cpp Windows 运行时验证失败，退出码：$LASTEXITCODE" }
Expand-VerifiedPartyOpsOcrRuntime `
  -RepoRoot $repoRoot `
  -Destination (Join-Path $bundleRoot "ocr")
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "读取源码提交失败" }
& $Python `
  (Join-Path $repoRoot "scripts\generate-release-manifest.py") `
  --root $bundleRoot `
  --output (Join-Path $bundleRoot "release-manifest.json") `
  --version $releaseVersion `
  --tag $releaseTag `
  --commit $sourceCommit
if ($LASTEXITCODE -ne 0) { throw "嵌入式发布清单生成失败" }

$expectedSqliteHash = $expectedSqliteSha256
$bundledSqliteHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $internalRoot "sqlite3.dll")).Hash
if ($expectedSqliteHash -ne $bundledSqliteHash) {
  throw "冻结运行时中的 SQLite DLL 与经校验输入不一致。"
}

if (-not $InnoCompiler) {
  $InnoCompiler = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
  ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not (Test-Path -LiteralPath $InnoCompiler)) {
  throw "未找到 Inno Setup 6：$InnoCompiler"
}

$env:PARTYOPS_WINDOWS_BUILD_ROOT = $bundleRoot
$env:PARTYOPS_WINDOWS_OUTPUT_ROOT = $artifactRoot
& $InnoCompiler (Join-Path $PSScriptRoot "PartyOps.iss")
if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup 安装器构建失败，退出码：$LASTEXITCODE"
}

$installer = Join-Path $artifactRoot "PartyOps_1.4.3-rc.7_windows_amd64.exe"
if (-not (Test-Path -LiteralPath $installer)) {
  throw "Inno 返回成功但未找到预期安装器：$installer"
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  "$installer.sha256",
  "$hash  $(Split-Path -Leaf $installer)`n",
  (New-Object System.Text.UTF8Encoding($false))
)
$candidate = [ordered]@{
  schema_version = 1
  product = "PartyOps"
  version = $releaseVersion
  release_tag = $releaseTag
  source_commit = $sourceCommit
  platform = "windows"
  architecture = "amd64"
  runtime_profile = "full"
  signed = $false
  filename = (Split-Path -Leaf $installer)
  size = (Get-Item -LiteralPath $installer).Length
  sha256 = $hash
  sqlite_version = $expectedSqliteVersion
  limitations = @("Windows 10 未实机验证", "未签名候选版")
}
[System.IO.File]::WriteAllText(
  (Join-Path $artifactRoot "PartyOps_1.4.3-rc.7_windows_amd64.candidate.json"),
  ($candidate | ConvertTo-Json -Depth 5),
  (New-Object System.Text.UTF8Encoding($false))
)

[pscustomobject]@{
  installer = $installer
  size = (Get-Item -LiteralPath $installer).Length
  sha256 = $hash
  sqlite_sha256 = $expectedSqliteHash.ToLowerInvariant()
} | ConvertTo-Json -Compress
