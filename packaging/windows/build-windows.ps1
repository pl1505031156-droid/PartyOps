param(
  [string]$Python = "python",
  [string]$SqliteDll = "",
  [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$officialSqliteDll = Join-Path $repoRoot "vendor\windows\sqlite-3.53.4\runtime\sqlite3.dll"
$officialSqliteVersion = "3.53.4"
$officialSqliteSha256 = "AB57D0437795ECC757CB693F32EA224173FA9856594D95CFA6B5033E645CD1EC"
if (-not $SqliteDll) { $SqliteDll = $officialSqliteDll }
if (-not (Test-Path -LiteralPath $SqliteDll)) {
  throw "缺少经校验的 SQLite $officialSqliteVersion 运行时：$SqliteDll；请先执行 scripts/prepare-windows-build.ps1。"
}
$SqliteDll = (Resolve-Path -LiteralPath $SqliteDll).Path
$actualSqliteSha256 = (Get-FileHash -LiteralPath $SqliteDll -Algorithm SHA256).Hash
if ($actualSqliteSha256 -ne $officialSqliteSha256) {
  throw "SQLite DLL SHA-256 不匹配，拒绝把来源不明的数据库运行时写入正式安装包。"
}
$providedSqliteVersion = & $Python -c "import ctypes,sys; lib=ctypes.WinDLL(sys.argv[1]); lib.sqlite3_libversion.restype=ctypes.c_char_p; print(lib.sqlite3_libversion().decode())" $SqliteDll
if ($LASTEXITCODE -ne 0 -or $providedSqliteVersion -ne $officialSqliteVersion) {
  throw "SQLite DLL 版本应为 $officialSqliteVersion，实际为 $providedSqliteVersion。"
}
$buildRoot = Join-Path $repoRoot "artifacts\windows-runtime"
$outputRoot = Join-Path $repoRoot "artifacts"
$frontendDist = Join-Path $repoRoot "frontend\dist"
$localAiRoot = Join-Path $repoRoot "vendor\windows\local-ai\llama-b10331"
function Assert-NativeSuccess([string]$Stage) {
  if ($LASTEXITCODE -ne 0) { throw "$Stage 失败，退出码：$LASTEXITCODE" }
}
if (-not $InnoCompiler) {
  $InnoCompiler = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
  ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
foreach ($runtimeFile in @("llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "ggml.dll", "LICENSE", "SOURCE.json")) {
  if (-not (Test-Path -LiteralPath (Join-Path $localAiRoot $runtimeFile))) {
    throw "缺少经固定版本校验的 Windows 本地 LLM 运行时：$runtimeFile"
  }
}

Push-Location (Join-Path $repoRoot "frontend")
try {
  corepack pnpm install --frozen-lockfile
  Assert-NativeSuccess "前端依赖安装"
  corepack pnpm run typecheck
  Assert-NativeSuccess "前端类型检查"
  corepack pnpm run build
  Assert-NativeSuccess "前端生产构建"
} finally { Pop-Location }

& $Python -m pip install -r (Join-Path $repoRoot "backend\requirements.txt") -r (Join-Path $repoRoot "backend\requirements-local-ai.txt") -r (Join-Path $PSScriptRoot "requirements-build.txt")
Assert-NativeSuccess "Windows Python 构建依赖安装"
$sqliteVersion = & $Python -c "import sqlite3; print(sqlite3.sqlite_version)"
Assert-NativeSuccess "开发运行时 SQLite 版本读取"
Write-Host "开发 Python SQLite=$sqliteVersion；正式冻结运行时固定使用 SQLite $providedSqliteVersion。"

if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

$entries = @(
  @{ Name = "PartyOps"; Script = "packaging\uos\entrypoint.py"; Mode = "onedir" },
  @{ Name = "PartyOpsAgent"; Script = "packaging\uos\client_entrypoint.py" },
  @{ Name = "PartyOpsWizard"; Script = "packaging\uos\wizard_entrypoint.py" },
  @{ Name = "PartyOpsUpdater"; Script = "packaging\uos\updater_entrypoint.py" },
  @{ Name = "PartyOpsLauncher"; Script = "packaging\windows\windows_launcher.py" },
  @{ Name = "PartyOpsFileOpen"; Script = "packaging\windows\windows_file_open.py" },
  @{ Name = "PartyOpsService"; Script = "packaging\windows\windows_service.py" },
  @{ Name = "PartyOpsUpdaterService"; Script = "packaging\windows\windows_updater_service.py" }
)

foreach ($entry in $entries) {
  $arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean",
    $(if ($entry.Mode -eq "onedir") { "--onedir" } else { "--onefile" }),
    "--name", $entry.Name,
    "--paths", (Join-Path $repoRoot "backend"),
    "--distpath", $buildRoot,
    "--workpath", (Join-Path $repoRoot ".build-windows\work"),
    "--specpath", (Join-Path $repoRoot ".build-windows\spec")
  )
  if ($entry.Name -eq "PartyOps") {
    $arguments += @(
      "--add-data", "$frontendDist\client;frontend",
      "--add-data", "$(Join-Path $repoRoot 'backend\alembic');alembic",
      "--add-data", "$(Join-Path $repoRoot 'backend\alembic.ini');."
    )
  }
  foreach ($module in @(
    "pysqlite3", "pysqlite3.dbapi2", "sqlalchemy.dialects.sqlite.pysqlite",
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
    "cryptography", "cryptography.fernet", "httpx", "win32timezone",
    "numpy", "onnxruntime", "tokenizers"
  )) { $arguments += @("--hidden-import", $module) }
  $arguments += @("--add-binary", "$SqliteDll;.")
  $arguments += (Join-Path $repoRoot $entry.Script)
  & $Python @arguments
  Assert-NativeSuccess "$($entry.Name) 冻结构建"
}

$bundleRoot = Join-Path $outputRoot "PartyOps-1.4.3-windows-amd64"
if (Test-Path -LiteralPath $bundleRoot) { Remove-Item -LiteralPath $bundleRoot -Recurse -Force }
New-Item -ItemType Directory -Path $bundleRoot | Out-Null
foreach ($entry in $entries) {
  if ($entry.Mode -eq "onedir") {
    Copy-Item -Path (Join-Path $buildRoot "$($entry.Name)\*") -Destination $bundleRoot -Recurse -Force
  } else {
    Copy-Item -LiteralPath (Join-Path $buildRoot "$($entry.Name).exe") -Destination $bundleRoot -Force
  }
}
Copy-Item -LiteralPath $SqliteDll -Destination (Join-Path $bundleRoot "sqlite3.dll") -Force
$internalRoot = Join-Path $bundleRoot "_internal"
New-Item -ItemType Directory -Path $internalRoot -Force | Out-Null
Copy-Item -LiteralPath $SqliteDll -Destination (Join-Path $internalRoot "sqlite3.dll") -Force
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $internalRoot "sqlite3.dll")).Hash -ne $officialSqliteSha256) {
  throw "冻结运行时中的 SQLite DLL 与经校验输入不一致。"
}
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\uos\update-public-key.txt") -Destination $bundleRoot -Force
foreach ($notice in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
  $noticePath = Join-Path $repoRoot $notice
  if (-not (Test-Path -LiteralPath $noticePath)) { throw "发布包缺少开源声明文件：$notice" }
  Copy-Item -LiteralPath $noticePath -Destination $bundleRoot -Force
}
Copy-Item -Path (Join-Path $localAiRoot "*") -Destination $bundleRoot -Force
& (Join-Path $bundleRoot "llama-server.exe") --version | Out-Null
Assert-NativeSuccess "llama.cpp Windows 运行时验证"

if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw "未找到 Inno Setup 6：$InnoCompiler" }
$env:PARTYOPS_WINDOWS_BUILD_ROOT = $bundleRoot
$env:PARTYOPS_WINDOWS_OUTPUT_ROOT = $outputRoot
& $InnoCompiler (Join-Path $PSScriptRoot "PartyOps.iss")
Assert-NativeSuccess "Inno Setup 安装器构建"

$installer = Join-Path $outputRoot "PartyOps_1.4.3_windows_amd64.exe"
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  "$installer.sha256",
  "$hash  $(Split-Path -Leaf $installer)`n",
  (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "Windows 安装器已生成：$installer"
