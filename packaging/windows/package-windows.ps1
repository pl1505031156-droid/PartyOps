param(
  [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRoot = Join-Path $repoRoot "artifacts\windows-runtime"
$artifactRoot = Join-Path $repoRoot "artifacts"
$bundleRoot = Join-Path $artifactRoot "PartyOps-1.4.3-windows-amd64"
$expectedBundleRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "artifacts\PartyOps-1.4.3-windows-amd64"))
$sqliteDll = Join-Path $repoRoot "vendor\windows\sqlite-3.53.4\runtime\sqlite3.dll"
$expectedSqliteVersion = "3.53.4"
$expectedSqliteSha256 = "AB57D0437795ECC757CB693F32EA224173FA9856594D95CFA6B5033E645CD1EC"
$localAiRoot = Join-Path $repoRoot "vendor\windows\local-ai\llama-b10331"

if ([System.IO.Path]::GetFullPath($bundleRoot) -ne $expectedBundleRoot) {
  throw "拒绝清理未验证的 Windows 组装目录：$bundleRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "PartyOps\PartyOps.exe"))) {
  throw "缺少已冻结的 PartyOps 主程序，请先执行 build-windows.ps1。"
}
if (-not (Test-Path -LiteralPath $sqliteDll)) {
  throw "缺少经校验的 SQLite 运行时：$sqliteDll"
}
$actualSqliteVersion = & (Join-Path $repoRoot ".venv\Scripts\python.exe") -c "import ctypes,sys; lib=ctypes.WinDLL(sys.argv[1]); lib.sqlite3_libversion.restype=ctypes.c_char_p; print(lib.sqlite3_libversion().decode())" $sqliteDll
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

$oneFileEntries = @(
  "PartyOpsAgent",
  "PartyOpsWizard",
  "PartyOpsUpdater",
  "PartyOpsLauncher",
  "PartyOpsFileOpen",
  "PartyOpsService",
  "PartyOpsUpdaterService"
)
foreach ($entry in $oneFileEntries) {
  if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "$entry.exe"))) {
    throw "缺少已冻结的 Windows 组件：$entry.exe"
  }
}

if (Test-Path -LiteralPath $bundleRoot) {
  Remove-Item -LiteralPath $bundleRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
Copy-Item -Path (Join-Path $runtimeRoot "PartyOps\*") -Destination $bundleRoot -Recurse -Force
foreach ($entry in $oneFileEntries) {
  Copy-Item -LiteralPath (Join-Path $runtimeRoot "$entry.exe") -Destination $bundleRoot -Force
}

Copy-Item -LiteralPath $sqliteDll -Destination (Join-Path $bundleRoot "sqlite3.dll") -Force
$internalRoot = Join-Path $bundleRoot "_internal"
New-Item -ItemType Directory -Path $internalRoot -Force | Out-Null
Copy-Item -LiteralPath $sqliteDll -Destination (Join-Path $internalRoot "sqlite3.dll") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\uos\update-public-key.txt") -Destination $bundleRoot -Force
foreach ($notice in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
  $noticePath = Join-Path $repoRoot $notice
  if (-not (Test-Path -LiteralPath $noticePath)) { throw "发布包缺少开源声明文件：$notice" }
  Copy-Item -LiteralPath $noticePath -Destination $bundleRoot -Force
}
Copy-Item -Path (Join-Path $localAiRoot "*") -Destination $bundleRoot -Force
& (Join-Path $bundleRoot "llama-server.exe") --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "llama.cpp Windows 运行时验证失败，退出码：$LASTEXITCODE" }

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

$installer = Join-Path $artifactRoot "PartyOps_1.4.3_windows_amd64.exe"
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  "$installer.sha256",
  "$hash  $(Split-Path -Leaf $installer)`n",
  (New-Object System.Text.UTF8Encoding($false))
)

[pscustomobject]@{
  installer = $installer
  size = (Get-Item -LiteralPath $installer).Length
  sha256 = $hash
  sqlite_sha256 = $expectedSqliteHash.ToLowerInvariant()
} | ConvertTo-Json -Compress
