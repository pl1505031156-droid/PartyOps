param(
  [string]$Python = "python",
  [string]$SqliteDll = "",
  [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$releaseVersion = "1.4.3-rc.2"
$releaseTag = "v1.4.3-rc.2"
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
$siteCustomizeRoot = Join-Path $repoRoot ".build-windows\sitecustomize"
function Assert-NativeSuccess([string]$Stage) {
  if ($LASTEXITCODE -ne 0) { throw "$Stage 失败，退出码：$LASTEXITCODE" }
}
# PyInstaller 6.16 会无条件读取 site.getusersitepackages()，即使 Python 已
# 禁用用户目录。发布构建必须只扫描虚拟环境；这里用构建期 sitecustomize
# 返回空路径，既避免无权限用户目录，也防止未锁定包混入制品。
New-Item -ItemType Directory -Force -Path $siteCustomizeRoot | Out-Null
$siteCustomize = @'
"""PartyOps Windows 发布构建隔离。"""
import site
if not site.ENABLE_USER_SITE:
    site.getusersitepackages = lambda: ""
'@
[System.IO.File]::WriteAllText(
  (Join-Path $siteCustomizeRoot "sitecustomize.py"),
  $siteCustomize,
  (New-Object System.Text.UTF8Encoding($false))
)
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONPATH = if ($env:PYTHONPATH) {
  "$siteCustomizeRoot$([IO.Path]::PathSeparator)$env:PYTHONPATH"
} else {
  $siteCustomizeRoot
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
  # 使用系统 pnpm（corepack 在当前构建环境不可用）。
  # 依赖已完整存在时跳过 install，避免在受控环境中触发多余的网络与清理操作。
  $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
  if (-not $pnpm) { throw "未找到 pnpm，无法构建前端。请先安装 pnpm 或启用 corepack。" }
  $viteBin = Join-Path (Join-Path $repoRoot "frontend") "node_modules\.bin\vite.cmd"
  # pnpm.ps1 shim 会把子进程 stderr 写入错误流，在 $ErrorActionPreference=Stop
  # 下被当作 NativeCommandError 抛出，导致构建误判失败；这里临时放行并
  # 只以 $LASTEXITCODE 判定结果。
  $previousEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    if (-not (Test-Path -LiteralPath $viteBin)) {
      & pnpm install --frozen-lockfile
      Assert-NativeSuccess "前端依赖安装"
    }
    $existingFrontendDist = Join-Path $repoRoot "frontend\dist"
    if (Test-Path -LiteralPath $existingFrontendDist) {
      Remove-Item -LiteralPath $existingFrontendDist -Recurse -Force
    }
    & pnpm run typecheck
    Assert-NativeSuccess "前端类型检查"
    & pnpm run build
    Assert-NativeSuccess "前端生产构建"
  } finally {
    $ErrorActionPreference = $previousEap
  }
} finally { Pop-Location }
& $Python (Join-Path $repoRoot "scripts\validate-frontend-dist.py") (Join-Path $frontendDist "client")
Assert-NativeSuccess "前端静态资源闭包验证"

$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  & $Python -m pip install -r (Join-Path $repoRoot "backend\requirements.txt") -r (Join-Path $repoRoot "backend\requirements-local-ai.txt") -r (Join-Path $PSScriptRoot "requirements-build.txt")
} finally {
  $ErrorActionPreference = $previousEap
}
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

# 统一的 PartyOps 品牌图标：嵌入所有可执行文件，桌面/开始菜单/资源管理器
# 均显示自定义图标，而不是 PyInstaller 默认图标。
$brandIcon = Join-Path $PSScriptRoot "partyops.ico"
$brandImage = Join-Path $PSScriptRoot "partyops-1024.png"
if (-not (Test-Path -LiteralPath $brandIcon)) {
  throw "缺少品牌图标 partyops.ico，无法为可执行文件嵌入自定义图标。"
}
if (-not (Test-Path -LiteralPath $brandImage)) {
  throw "缺少品牌图片 partyops-1024.png，无法生成 PartyOps 专属中文安装界面。"
}

foreach ($entry in $entries) {
  $arguments = @(
    "-m", "PyInstaller", "--noconfirm", "--clean",
    $(if ($entry.Mode -eq "onedir") { "--onedir" } else { "--onefile" }),
    "--name", $entry.Name,
    "--icon", $brandIcon,
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
  # PyInstaller 会把进度/警告写入 stderr，在 $ErrorActionPreference=Stop 下
  # 会被当作 NativeCommandError 抛出；临时放行并仅以 $LASTEXITCODE 判定。
  $previousEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Python @arguments
  } finally {
    $ErrorActionPreference = $previousEap
  }
  Assert-NativeSuccess "$($entry.Name) 冻结构建"
}

$bundleRoot = Join-Path $outputRoot "PartyOps-$releaseVersion-windows-amd64"
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
Copy-Item -LiteralPath $brandIcon -Destination (Join-Path $bundleRoot "partyops.ico") -Force
Copy-Item -LiteralPath $brandImage -Destination (Join-Path $bundleRoot "partyops-1024.png") -Force
foreach ($notice in @("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
  $noticePath = Join-Path $repoRoot $notice
  if (-not (Test-Path -LiteralPath $noticePath)) { throw "发布包缺少开源声明文件：$notice" }
  Copy-Item -LiteralPath $noticePath -Destination $bundleRoot -Force
}
Copy-Item -Path (Join-Path $localAiRoot "*") -Destination $bundleRoot -Force
& (Join-Path $bundleRoot "llama-server.exe") --version | Out-Null
Assert-NativeSuccess "llama.cpp Windows 运行时验证"
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
Assert-NativeSuccess "读取源码提交"
& $Python (Join-Path $repoRoot "scripts\generate-release-manifest.py") `
  --root $bundleRoot `
  --output (Join-Path $bundleRoot "release-manifest.json") `
  --version $releaseVersion `
  --tag $releaseTag `
  --commit $sourceCommit
Assert-NativeSuccess "生成嵌入式发布清单"

if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw "未找到 Inno Setup 6：$InnoCompiler" }
$env:PARTYOPS_WINDOWS_BUILD_ROOT = $bundleRoot
$env:PARTYOPS_WINDOWS_OUTPUT_ROOT = $outputRoot
$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  & $InnoCompiler (Join-Path $PSScriptRoot "PartyOps.iss")
} finally {
  $ErrorActionPreference = $previousEap
}
Assert-NativeSuccess "Inno Setup 安装器构建"

$installer = Join-Path $outputRoot "PartyOps_1.4.3-rc.2_windows_amd64.exe"
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
  platform = "windows-amd64"
  signed = $false
  filename = (Split-Path -Leaf $installer)
  size = (Get-Item -LiteralPath $installer).Length
  sha256 = $hash
  sqlite_version = $officialSqliteVersion
  limitations = @("Windows 10 未实机验证", "UOS 未实机验证", "未签名测试候选")
}
[System.IO.File]::WriteAllText(
  (Join-Path $outputRoot "PartyOps_1.4.3-rc.2_windows_amd64.candidate.json"),
  ($candidate | ConvertTo-Json -Depth 5),
  (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "Windows 安装器已生成：$installer"
