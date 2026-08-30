param(
  [string]$Python = "python",
  [string]$SqliteDll = "",
  [string]$SqliteSha256 = "",
  [string]$InnoCompiler = "",
  [string]$OfficeRuntime = "",
  [ValidateSet("", "amd64", "x86")][string]$LegacyArchitecture = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "prepare-ocr-runtime.ps1")
$releaseVersion = "1.4.5-rc.6"
$releaseTag = "v1.4.5-rc.6"
& $Python (Join-Path $repoRoot "scripts\verify-full-function-gate.py") verify --root $repoRoot --scope package
if ($LASTEXITCODE -ne 0) { throw "全功能测试门禁失败，拒绝生成 Windows 安装包。" }
& $Python (Join-Path $repoRoot "scripts\verify-version-consistency.py") `
  --root $repoRoot --expected $releaseVersion
if ($LASTEXITCODE -ne 0) { throw "版本一致性门禁失败，拒绝冻结 Windows 制品。" }
$isLegacy = [bool]$LegacyArchitecture
$targetArchitecture = if ($isLegacy) { $LegacyArchitecture } else { "amd64" }
$runtimeProfile = if (-not $isLegacy) { "full" } elseif ($targetArchitecture -eq "amd64") { "legacy-smart" } else { "legacy-core" }
$platformFamily = if ($isLegacy) { "windows7" } else { "windows" }
$artifactSuffix = if ($isLegacy) { "windows7-$targetArchitecture" } else { "windows-amd64" }
$OfficeRuntime = if ($OfficeRuntime) { (Resolve-Path -LiteralPath $OfficeRuntime).Path } else {
  $runtimeName = if ($isLegacy -and $targetArchitecture -eq "amd64") {
    "libreoffice-headless-win7-amd64"
  } else {
    "libreoffice-headless-$targetArchitecture"
  }
  Join-Path $repoRoot "vendor\windows\$runtimeName"
}
$officeExecutable = Join-Path $OfficeRuntime "program\soffice.exe"
$officeSource = Join-Path $OfficeRuntime "SOURCE.json"
$officeLicenses = Join-Path $OfficeRuntime "licenses"
if (-not (Test-Path -LiteralPath $officeExecutable) -or
    -not (Test-Path -LiteralPath $officeSource) -or
    -not (Test-Path -LiteralPath $officeLicenses -PathType Container)) {
  throw "[OFFICE_RUNTIME_MISSING] 缺少 $targetArchitecture 经许可审计的 LibreOffice headless 运行时、来源清单或许可证。"
}
if (Get-ChildItem -LiteralPath $OfficeRuntime -Recurse -Force | Where-Object {
    $_.Attributes -band [IO.FileAttributes]::ReparsePoint
  } | Select-Object -First 1) {
  throw "[OFFICE_RUNTIME_REPARSE_POINT] LibreOffice 运行时不得包含联接、符号链接或其它重解析点。"
}
$officeBytes = [IO.File]::ReadAllBytes($officeExecutable)
if ($officeBytes.Length -lt 64 -or $officeBytes[0] -ne 0x4D -or $officeBytes[1] -ne 0x5A) {
  throw "[OFFICE_RUNTIME_PE_INVALID] LibreOffice soffice.exe 不是有效 PE 文件。"
}
$peOffset = [BitConverter]::ToInt32($officeBytes, 0x3C)
if ($peOffset -lt 0 -or $peOffset + 6 -gt $officeBytes.Length -or
    $officeBytes[$peOffset] -ne 0x50 -or $officeBytes[$peOffset + 1] -ne 0x45 -or
    $officeBytes[$peOffset + 2] -ne 0 -or $officeBytes[$peOffset + 3] -ne 0) {
  throw "[OFFICE_RUNTIME_PE_INVALID] LibreOffice soffice.exe 的 PE 头越界或签名无效。"
}
$machine = [BitConverter]::ToUInt16($officeBytes, $peOffset + 4)
$expectedMachine = if ($targetArchitecture -eq "amd64") { 0x8664 } else { 0x014C }
if ($machine -ne $expectedMachine) {
  throw "[OFFICE_RUNTIME_ARCH_MISMATCH] LibreOffice PE 架构与 $targetArchitecture 不一致。"
}
$ucrtArchitecture = if ($targetArchitecture -eq "amd64") { "x64" } else { "x86" }
$ucrtRoot = Join-Path $repoRoot "vendor\windows\ucrt-10.0.19041.0-$ucrtArchitecture"
$ucrtSource = Join-Path $ucrtRoot "SOURCE.json"
$vcRuntimeRoot = Join-Path $repoRoot "vendor\windows\vc142-14.29.30157-$ucrtArchitecture"
$vcRuntimeSource = Join-Path $vcRuntimeRoot "SOURCE.json"
$officialSqliteDll = if ($isLegacy -and $targetArchitecture -eq "x86") {
  Join-Path $repoRoot "vendor\windows\sqlite-3.53.4-x86\runtime\sqlite3.dll"
} else {
  Join-Path $repoRoot "vendor\windows\sqlite-3.53.4\runtime\sqlite3.dll"
}
$officialSqliteVersion = "3.53.4"
$officialSqliteSha256 = if ($isLegacy -and $targetArchitecture -eq "x86") {
  "1C2FCFA7632B6025829E3539142F1B7EBDBC5BB44D4FD6CC0F42F83715D2EB9F"
} else {
  "AB57D0437795ECC757CB693F32EA224173FA9856594D95CFA6B5033E645CD1EC"
}
if (-not $SqliteDll) { $SqliteDll = $officialSqliteDll }
if (-not $SqliteSha256) { $SqliteSha256 = $officialSqliteSha256 }
$SqliteSha256 = $SqliteSha256.ToUpperInvariant()
if (-not (Test-Path -LiteralPath $SqliteDll)) {
  throw "缺少经校验的 SQLite $officialSqliteVersion 运行时：$SqliteDll；请先执行 scripts/prepare-windows-build.ps1。"
}
$SqliteDll = (Resolve-Path -LiteralPath $SqliteDll).Path
$actualSqliteSha256 = (Get-FileHash -LiteralPath $SqliteDll -Algorithm SHA256).Hash
if ($actualSqliteSha256 -ne $SqliteSha256) {
  throw "SQLite DLL SHA-256 不匹配，拒绝把来源不明的数据库运行时写入正式安装包。"
}
$providedSqliteVersion = & $Python -c "import ctypes,sys; lib=ctypes.WinDLL(sys.argv[1]); lib.sqlite3_libversion.restype=ctypes.c_char_p; print(lib.sqlite3_libversion().decode())" $SqliteDll
if ($LASTEXITCODE -ne 0 -or $providedSqliteVersion -ne $officialSqliteVersion) {
  throw "SQLite DLL 版本应为 $officialSqliteVersion，实际为 $providedSqliteVersion。"
}
$buildRoot = if ($isLegacy) {
  Join-Path $repoRoot "artifacts\windows-runtime-$artifactSuffix"
} else {
  Join-Path $repoRoot "artifacts\windows-runtime"
}
$outputRoot = Join-Path $repoRoot "artifacts"
$frontendDist = Join-Path $repoRoot "frontend\dist"
$localAiRoot = Join-Path $repoRoot "vendor\windows\local-ai\llama-b10331"
$siteCustomizeRoot = Join-Path $repoRoot ".build-windows\sitecustomize"
$installPathValidator = Join-Path $PSScriptRoot "validate-install-path.ps1"
function Assert-NativeSuccess([string]$Stage) {
  if ($LASTEXITCODE -ne 0) { throw "$Stage 失败，退出码：$LASTEXITCODE" }
}

function Remove-LegacyOfficeInstallerArtifacts([string]$RuntimeRoot) {
  # LibreOffice 官方 MSI 会同时展开安装器专用的 System/System64 VC 运行库、
  # .NET UNO 桥接程序集、distutils 安装器和扫描仪兼容程序。这些文件不会被
  # PartyOps 的无界面文档转换链路加载；其中还包含与主程序不同架构的 PE，
  # 保留它们既扩大离线包，也会让 Win7 架构门禁无法证明实际运行闭包纯净。
  foreach ($installerDirectory in @("System", "System64")) {
    $path = Join-Path $RuntimeRoot $installerDirectory
    if (Test-Path -LiteralPath $path) {
      Remove-Item -LiteralPath $path -Recurse -Force
    }
  }
  $programRoot = Join-Path $RuntimeRoot "program"
  foreach ($pattern in @(
      "cli_*.config",
      "cli_*.dll",
      "policy.*.cli_*.dll",
      "spsupp_x86.dll",
      "twain32shim.exe"
    )) {
    Get-ChildItem -LiteralPath $programRoot -File -Filter $pattern |
      Remove-Item -Force
  }
  Get-ChildItem -LiteralPath $programRoot -Directory -Filter "python-core-*" |
    ForEach-Object {
      $distutilsCommand = Join-Path $_.FullName "lib\distutils\command"
      if (Test-Path -LiteralPath $distutilsCommand) {
        Get-ChildItem -LiteralPath $distutilsCommand -File -Filter "wininst-*.exe" |
          Remove-Item -Force
      }
    }
}
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
$validatorProbePath = Join-Path $env:TEMP "PartyOps-144-安装路径-$validatorProbeId\中文 空格"
$validatorProbeDiagnostic = Join-Path $env:TEMP "PartyOps-144-validator-$validatorProbeId.txt"
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
if ($isLegacy) {
  if (-not (Test-Path -LiteralPath $ucrtSource)) {
    throw "缺少 Win7 $targetArchitecture 的 Microsoft UCRT 本地可再发行运行时来源清单：$ucrtSource"
  }
  $ucrtEvidence = Get-Content -Raw -LiteralPath $ucrtSource | ConvertFrom-Json
  if ($ucrtEvidence.version -ne "10.0.19041.0" -or $ucrtEvidence.architecture -ne $ucrtArchitecture) {
    throw "Win7 UCRT 来源清单版本或架构不匹配。"
  }
  foreach ($property in $ucrtEvidence.files.PSObject.Properties) {
    $runtimePath = Join-Path $ucrtRoot $property.Name
    if (-not (Test-Path -LiteralPath $runtimePath)) {
      throw "Win7 UCRT 来源清单中的文件不存在：$($property.Name)"
    }
    $runtimeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtimePath).Hash.ToLowerInvariant()
    if ($runtimeHash -ne [string]$property.Value) {
      throw "Win7 UCRT 文件 SHA-256 与来源清单不一致：$($property.Name)"
    }
  }
  if (-not (Test-Path -LiteralPath $vcRuntimeSource)) {
    throw "缺少 Win7 $targetArchitecture 的 Microsoft VC142 运行时来源清单：$vcRuntimeSource"
  }
  $vcRuntimeEvidence = Get-Content -Raw -LiteralPath $vcRuntimeSource | ConvertFrom-Json
  if ($vcRuntimeEvidence.version -ne "14.29.30157.0" -or
      $vcRuntimeEvidence.architecture -ne $ucrtArchitecture) {
    throw "Win7 VC142 来源清单版本或架构不匹配。"
  }
  foreach ($property in $vcRuntimeEvidence.files.PSObject.Properties) {
    $runtimePath = Join-Path $vcRuntimeRoot $property.Name
    if (-not (Test-Path -LiteralPath $runtimePath)) {
      throw "Win7 VC142 来源清单中的文件不存在：$($property.Name)"
    }
    $runtimeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtimePath).Hash.ToLowerInvariant()
    if ($runtimeHash -ne [string]$property.Value) {
      throw "Win7 VC142 文件 SHA-256 与来源清单不一致：$($property.Name)"
    }
  }
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
if ($runtimeProfile -eq "full") {
  foreach ($runtimeFile in @("llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "ggml.dll", "LICENSE", "SOURCE.json")) {
    if (-not (Test-Path -LiteralPath (Join-Path $localAiRoot $runtimeFile))) {
      throw "缺少经固定版本校验的 Windows 本地 LLM 运行时：$runtimeFile"
    }
  }
}

Push-Location (Join-Path $repoRoot "frontend")
try {
  # 由 package.json 固定 pnpm 版本，避免系统全局 pnpm 与锁文件格式不一致。
  $corepackCommand = Get-Command corepack -ErrorAction SilentlyContinue
  if (-not $corepackCommand) { throw "未找到 Corepack，无法使用项目固定的 pnpm 构建前端。" }
  $corepack = $corepackCommand.Source
  $viteBin = Join-Path (Join-Path $repoRoot "frontend") "node_modules\.bin\vite.cmd"
  # pnpm.ps1 shim 会把子进程 stderr 写入错误流，在 $ErrorActionPreference=Stop
  # 下被当作 NativeCommandError 抛出，导致构建误判失败；这里临时放行并
  # 只以 $LASTEXITCODE 判定结果。
  $previousEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    if (-not (Test-Path -LiteralPath $viteBin)) {
      & $corepack pnpm install --frozen-lockfile
      Assert-NativeSuccess "前端依赖安装"
    }
    $existingFrontendDist = Join-Path $repoRoot "frontend\dist"
    if (Test-Path -LiteralPath $existingFrontendDist) {
      Remove-Item -LiteralPath $existingFrontendDist -Recurse -Force
    }
    & $corepack pnpm run typecheck
    Assert-NativeSuccess "前端类型检查"
    & $corepack pnpm run build
    Assert-NativeSuccess "前端生产构建"
  } finally {
    $ErrorActionPreference = $previousEap
  }
} finally { Pop-Location }
& $Python (Join-Path $repoRoot "scripts\validate-frontend-dist.py") (Join-Path $frontendDist "client")
Assert-NativeSuccess "前端静态资源闭包验证"

if (-not $isLegacy) {
  $previousEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Python -m pip install -r (Join-Path $repoRoot "backend\requirements.txt") -r (Join-Path $repoRoot "backend\requirements-local-ai.txt") -r (Join-Path $PSScriptRoot "requirements-build.txt")
  } finally {
    $ErrorActionPreference = $previousEap
  }
  Assert-NativeSuccess "Windows Python 构建依赖安装"
}
$sqliteVersion = & $Python -c "import sqlite3; print(sqlite3.sqlite_version)"
Assert-NativeSuccess "开发运行时 SQLite 版本读取"
Write-Host "开发 Python SQLite=$sqliteVersion；正式冻结运行时固定使用 SQLite $providedSqliteVersion。"

if (Test-Path -LiteralPath $buildRoot) { Remove-Item -LiteralPath $buildRoot -Recurse -Force }
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

# Alembic 迁移必须进入冻结运行时，但源码目录可能残留由不同 Python
# 版本生成的 __pycache__。直接递归收集会把无用 pyc 混入安装器，并使
# 发布清单依赖本机构建历史。先生成只含可审计 .py 源文件的干净暂存目录。
$migrationSourceRoot = Join-Path $repoRoot "backend\alembic"
$migrationDataRoot = Join-Path $repoRoot ".build-windows\alembic-data"
if (Test-Path -LiteralPath $migrationDataRoot) {
  Remove-Item -LiteralPath $migrationDataRoot -Recurse -Force
}
foreach ($migrationFile in Get-ChildItem -LiteralPath $migrationSourceRoot -Recurse -File -Filter "*.py") {
  if ($migrationFile.FullName -match "[\\/]__pycache__[\\/]") { continue }
  $relativeMigrationPath = $migrationFile.FullName.Substring($migrationSourceRoot.Length).TrimStart("\", "/")
  $stagedMigrationPath = Join-Path $migrationDataRoot $relativeMigrationPath
  New-Item -ItemType Directory -Path (Split-Path -Parent $stagedMigrationPath) -Force | Out-Null
  Copy-Item -LiteralPath $migrationFile.FullName -Destination $stagedMigrationPath -Force
}

# 所有入口统一使用 onedir，并在 bundleRoot 合并同版本的 _internal 运行时。
# onefile 会把约 80MB Python/原生依赖重复嵌入每个辅助程序，使安装器、应用内
# 更新包和弱网续传无谓膨胀；共享运行时仍由发布清单逐文件校验，不降低完整性。
$entries = @(
  @{ Name = "PartyOps"; Script = "packaging\uos\entrypoint.py"; Mode = "onedir"; Gui = $false },
  @{ Name = "PartyOpsAgent"; Script = "packaging\uos\client_entrypoint.py"; Mode = "onedir"; Gui = $false },
  @{ Name = "PartyOpsWizard"; Script = "packaging\uos\wizard_entrypoint.py"; Mode = "onedir"; Gui = $true },
  @{ Name = "PartyOpsUpdater"; Script = "packaging\uos\updater_entrypoint.py"; Mode = "onedir"; Gui = $false },
  @{ Name = "PartyOpsLauncher"; Script = "packaging\windows\windows_launcher.py"; Mode = "onedir"; Gui = $true },
  @{ Name = "PartyOpsDataCleanup"; Script = "packaging\windows\data_cleanup.py"; Mode = "onedir"; Gui = $true },
  @{ Name = "PartyOpsFileOpen"; Script = "packaging\windows\windows_file_open.py"; Mode = "onedir"; Gui = $true },
  @{ Name = "PartyOpsService"; Script = "packaging\windows\windows_service.py"; Mode = "onedir"; Gui = $false },
  @{ Name = "PartyOpsUpdaterService"; Script = "packaging\windows\windows_updater_service.py"; Mode = "onedir"; Gui = $false }
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
  if ($entry.Gui) { $arguments += "--noconsole" }
  if ($entry.Name -eq "PartyOps") {
    $arguments += @(
      "--add-data", "$frontendDist\client;frontend",
      "--add-data", "$migrationDataRoot;alembic",
      "--add-data", "$(Join-Path $repoRoot 'backend\alembic.ini');."
    )
  }
  $hiddenModules = @(
    # Windows 使用 CPython 自带 sqlite3 绑定并在冻结目录替换为经验证的
    # SQLite DLL；pysqlite3 仅用于 Linux 静态运行时，不能作为缺失隐藏模块。
    "sqlalchemy.dialects.sqlite.pysqlite",
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
    "cryptography", "cryptography.fernet", "httpx", "win32timezone"
  )
  # 新版 setuptools 的 pkg_resources 运行钩子会经 jaraco.context 导入
  # vendored backports.tarfile。部分入口的静态图较小，PyInstaller 不会
  # 自动发现该分支，最终会出现安装成功后向导/启动器立即退出；但 Win7
  # 锁定的旧版 setuptools 根本没有此模块，
  # 无条件添加又会产生可被忽略的 ERROR。先真实导入 vendored 模块，
  # 只有运行时确实需要时才触发官方 hook-backports 别名收集。
  # Legacy Python 3.8 固定的 setuptools 没有这个可选 vendored 模块。
  # 探测失败是预期分支，不能被全局 ErrorActionPreference=Stop 提前变成
  # NativeCommandError；否则 Win7 构建会在真正冻结之前被误阻断。
  $previousEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Python -c "import importlib; importlib.import_module('setuptools._vendor.backports.tarfile')" 2>$null
    $requiresBackportsTarfile = $LASTEXITCODE -eq 0
  } finally {
    $ErrorActionPreference = $previousEap
  }
  if ($requiresBackportsTarfile) {
    $hiddenModules += @("backports", "backports.tarfile")
  }
  if ($runtimeProfile -ne "legacy-core") {
    $hiddenModules += @("numpy", "onnxruntime", "tokenizers")
  } else {
    foreach ($excluded in @("numpy", "onnxruntime", "tokenizers")) {
      $arguments += @("--exclude-module", $excluded)
    }
  }
  foreach ($module in $hiddenModules) { $arguments += @("--hidden-import", $module) }
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

$wizardInternal = Join-Path $buildRoot "PartyOpsWizard\_internal"
foreach ($requiredGuiRuntime in @(
  (Join-Path $wizardInternal "_tkinter.pyd"),
  (Join-Path $wizardInternal "tcl86t.dll"),
  (Join-Path $wizardInternal "tk86t.dll"),
  (Join-Path $wizardInternal "_tcl_data\init.tcl"),
  (Join-Path $wizardInternal "_tk_data\tk.tcl")
)) {
  if (-not (Test-Path -LiteralPath $requiredGuiRuntime)) {
    throw "配置向导冻结运行时不完整，缺少 Tcl/Tk 文件：$requiredGuiRuntime"
  }
}

# 直接运行冻结后的向导自检：只有真正加载 _tkinter、Tcl/Tk 脚本并成功创建
# 隐藏窗口后才会返回 0。该过程不写配置、不申请提权，也不启动服务。
& (Join-Path $buildRoot "PartyOpsWizard\PartyOpsWizard.exe") --self-test
Assert-NativeSuccess "配置向导冻结图形运行时自检"

$bundleRoot = Join-Path $outputRoot "PartyOps-$releaseVersion-$artifactSuffix"
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
if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $internalRoot "sqlite3.dll")).Hash -ne $SqliteSha256) {
  throw "冻结运行时中的 SQLite DLL 与经校验输入不一致。"
}
if ($isLegacy) {
  # 在 Win10/11 上冻结时，PyInstaller 会从当前系统目录收集 API-set DLL。
  # 这些文件不能作为 Win7 制品输入。统一替换为 Windows SDK 10.0.19041
  # 官方 app-local UCRT 完整集合，并同时放在主程序与共享运行时目录。
  foreach ($destination in @($bundleRoot, $internalRoot)) {
    Get-ChildItem -LiteralPath $destination -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like "api-ms-win-*.dll" -or $_.Name -ieq "ucrtbase.dll" } |
      Remove-Item -Force
    foreach ($property in $ucrtEvidence.files.PSObject.Properties) {
      Copy-Item -LiteralPath (Join-Path $ucrtRoot $property.Name) `
        -Destination (Join-Path $destination $property.Name) -Force
    }
  }
  Copy-Item -LiteralPath $ucrtSource -Destination (Join-Path $bundleRoot "ucrt-source.json") -Force
  Copy-Item -LiteralPath (Join-Path $ucrtRoot "sdk_license.rtf") `
    -Destination (Join-Path $bundleRoot "ucrt-sdk-license.rtf") -Force
  Copy-Item -LiteralPath (Join-Path $ucrtRoot "sdk_third_party_notices.rtf") `
    -Destination (Join-Path $bundleRoot "ucrt-sdk-third-party-notices.rtf") -Force

  # PyInstaller 可能从当前 Windows 10/11 主机收集最新版 MSVC 运行库。新版
  # MSVCP140.dll 会直接导入 Win7 不存在的 API，即使 Python 与业务依赖都已
  # 锁定也会在进程入口前失败。必须用带哈希来源清单的 VC142 14.29 运行库
  # 同时覆盖程序根目录和共享运行时目录，禁止继承构建机的全局版本。
  foreach ($destination in @($bundleRoot, $internalRoot)) {
    foreach ($property in $vcRuntimeEvidence.files.PSObject.Properties) {
      Copy-Item -LiteralPath (Join-Path $vcRuntimeRoot $property.Name) `
        -Destination (Join-Path $destination $property.Name) -Force
      $copiedHash = (Get-FileHash -Algorithm SHA256 `
        -LiteralPath (Join-Path $destination $property.Name)).Hash.ToLowerInvariant()
      if ($copiedHash -ne [string]$property.Value) {
        throw "Win7 VC142 运行时写入后哈希不一致：$($property.Name)"
      }
    }
  }
  Copy-Item -LiteralPath $vcRuntimeSource `
    -Destination (Join-Path $bundleRoot "vc-runtime-source.json") -Force
}
Copy-Item -LiteralPath (Join-Path $repoRoot "packaging\uos\update-public-key.txt") -Destination $bundleRoot -Force
$bundledOfficeRuntime = Join-Path $bundleRoot "office-runtime"
Copy-Item -LiteralPath $OfficeRuntime -Destination $bundledOfficeRuntime -Recurse -Force
if ($isLegacy) {
  Remove-LegacyOfficeInstallerArtifacts -RuntimeRoot $bundledOfficeRuntime
}
Copy-Item -LiteralPath $brandIcon -Destination (Join-Path $bundleRoot "partyops.ico") -Force
Copy-Item -LiteralPath $brandImage -Destination (Join-Path $bundleRoot "partyops-1024.png") -Force
foreach ($notice in @("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
  $noticePath = Join-Path $repoRoot $notice
  if (-not (Test-Path -LiteralPath $noticePath)) { throw "发布包缺少开源声明文件：$notice" }
  Copy-Item -LiteralPath $noticePath -Destination $bundleRoot -Force
}
if ($runtimeProfile -eq "full") {
  Copy-Item -Path (Join-Path $localAiRoot "*") -Destination $bundleRoot -Force
  & (Join-Path $bundleRoot "llama-server.exe") --version | Out-Null
  Assert-NativeSuccess "llama.cpp Windows 运行时验证"
}
Expand-VerifiedPartyOpsOcrRuntime `
  -RepoRoot $repoRoot `
  -Destination (Join-Path $bundleRoot "ocr")
$sourceCommit = (& git -C $repoRoot rev-parse HEAD).Trim()
Assert-NativeSuccess "读取源码提交"
& $Python (Join-Path $repoRoot "scripts\generate-release-manifest.py") `
  --root $bundleRoot `
  --output (Join-Path $bundleRoot "release-manifest.json") `
  --version $releaseVersion `
  --tag $releaseTag `
  --commit $sourceCommit `
  --platform $platformFamily `
  --architecture $targetArchitecture `
  --runtime-profile $runtimeProfile
Assert-NativeSuccess "生成嵌入式发布清单"

if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw "未找到 Inno Setup 6：$InnoCompiler" }
$env:PARTYOPS_WINDOWS_BUILD_ROOT = $bundleRoot
$env:PARTYOPS_WINDOWS_OUTPUT_ROOT = $outputRoot
$installerBase = if ($isLegacy) { "PartyOps_1.4.5-rc.6_windows7_$targetArchitecture" } else { "PartyOps_1.4.5-rc.6_windows_amd64" }
$installer = Join-Path $outputRoot "$installerBase.exe"
$hashPath = "$installer.sha256"
$candidatePath = Join-Path $outputRoot "$installerBase.candidate.json"
$innoLog = Join-Path $buildRoot "$installerBase.inno.log"

# 每次封装前先让旧发布元数据失效。若 Inno 被中断，新 EXE 不会再与旧哈希、
# 旧 source_commit 组合成看似可发布的候选包。
foreach ($generatedPath in @($installer, $hashPath, $candidatePath)) {
  if (Test-Path -LiteralPath $generatedPath) {
    Remove-Item -LiteralPath $generatedPath -Force
  }
}

$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$innoExitCode = 1
try {
  $innoScript = if (-not $isLegacy) {
    Join-Path $PSScriptRoot "PartyOps.iss"
  } elseif ($targetArchitecture -eq "amd64") {
    Join-Path $PSScriptRoot "PartyOps-Win7-x64.iss"
  } else {
    Join-Path $PSScriptRoot "PartyOps-Win7-x86.iss"
  }
  # LibreOffice 运行时包含大量文件；完整输出写入可审计日志，避免宿主输出通道
  # 截断或断开后误以为构建已结束。
  & $InnoCompiler $innoScript *> $innoLog
  $innoExitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $previousEap
}
if ($innoExitCode -ne 0) {
  Get-Content -LiteralPath $innoLog -Tail 80 -ErrorAction SilentlyContinue | Write-Host
  throw "Inno Setup 安装器构建失败（退出码 $innoExitCode）；完整日志：$innoLog"
}
if (-not (Test-Path -LiteralPath $installer)) {
  throw "Inno 返回成功但未找到预期安装器：$installer"
}
$installerInfo = Get-Item -LiteralPath $installer
$installerStream = [IO.File]::OpenRead($installer)
try {
  $installerHeader0 = $installerStream.ReadByte()
  $installerHeader1 = $installerStream.ReadByte()
} finally {
  $installerStream.Dispose()
}
if ($installerInfo.Length -lt 1MB -or $installerHeader0 -ne 0x4D -or $installerHeader1 -ne 0x5A) {
  throw "Inno 返回成功但安装器长度或 PE 文件头无效：$installer"
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
$hashTemporary = "$hashPath.tmp"
[System.IO.File]::WriteAllText(
  $hashTemporary,
  "$hash  $(Split-Path -Leaf $installer)`n",
  (New-Object System.Text.UTF8Encoding($false))
)
$candidate = [ordered]@{
  schema_version = 1
  product = "PartyOps"
  version = $releaseVersion
  release_tag = $releaseTag
  source_commit = $sourceCommit
  platform = $platformFamily
  architecture = $targetArchitecture
  runtime_profile = $runtimeProfile
  signed = $false
  filename = (Split-Path -Leaf $installer)
  size = $installerInfo.Length
  sha256 = $hash
  sqlite_version = $officialSqliteVersion
  limitations = if (-not $isLegacy) {
    @("Windows 10 未实机验证", "未签名候选版")
  } elseif ($targetArchitecture -eq "amd64") {
    @(
      "Windows 7 未执行运行验收",
      "仅限受控局域网",
      "本地 llama.cpp 大模型不可用；保留规则与语义增强",
      "未签名候选版"
    )
  } else {
    @(
      "Windows 7 未执行运行验收",
      "仅限受控局域网",
      "语义重排与本地 llama.cpp 大模型不可用",
      "未签名候选版"
    )
  }
}
$candidateTemporary = "$candidatePath.tmp"
[System.IO.File]::WriteAllText(
  $candidateTemporary,
  ($candidate | ConvertTo-Json -Depth 5),
  (New-Object System.Text.UTF8Encoding($false))
)
Move-Item -LiteralPath $hashTemporary -Destination $hashPath -Force
Move-Item -LiteralPath $candidateTemporary -Destination $candidatePath -Force
Write-Host "Windows 安装器已生成：$installer"
