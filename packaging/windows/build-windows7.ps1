param(
  [Parameter(Mandatory = $true)][ValidateSet("amd64", "x86")][string]$Architecture,
  [Parameter(Mandatory = $true)][string]$Python,
  [Parameter(Mandatory = $true)][string]$Wheelhouse,
  [Parameter(Mandatory = $true)][string]$EvidenceRoot,
  [string]$SqliteDll = "",
  [string]$SqliteSha256 = "",
  [string]$InnoCompiler = "",
  [string]$OfficeRuntime = "",
  [ValidateSet("bundled", "external")][string]$OfficeRuntimeMode = "external"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$releaseVersion = "1.4.5-rc.6"
$wheelhousePath = (Resolve-Path -LiteralPath $Wheelhouse).Path
$evidencePath = (Resolve-Path -LiteralPath $EvidenceRoot).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$pythonInfo = & $pythonPath -c "import json,platform,struct,sys; print(json.dumps({'version':list(sys.version_info[:3]),'bits':struct.calcsize('P')*8,'impl':platform.python_implementation()}))"
if ($LASTEXITCODE -ne 0) { throw "无法读取 Win7 Python 信息。" }
$runtime = $pythonInfo | ConvertFrom-Json
$expectedBits = if ($Architecture -eq "amd64") { 64 } else { 32 }
if ($runtime.impl -ne "CPython" -or $runtime.version[0] -ne 3 -or $runtime.version[1] -ne 8 -or $runtime.bits -ne $expectedBits) {
  throw "Win7 $Architecture 必须使用 $expectedBits 位 CPython 3.8，实际：$pythonInfo"
}
$tkInfo = & $pythonPath -c "import json,tkinter; print(json.dumps({'tcl':tkinter.Tcl().eval('info patchlevel')}))"
if ($LASTEXITCODE -ne 0 -or -not $tkInfo) {
  throw "Win7 $Architecture 必须使用包含 Tcl/Tk 的官方完整 CPython 3.8；缺少图形运行时会导致配置向导无法打开。"
}
& $pythonPath (Join-Path $repoRoot "scripts\validate-win7-wheelhouse.py") `
  --wheelhouse $wheelhousePath `
  --architecture $Architecture `
  --config (Join-Path $repoRoot "backend\legacy\security-backports.json") `
  --evidence-root $evidencePath
if ($LASTEXITCODE -ne 0) { throw "Win7 wheelhouse 或安全回移证据未通过，拒绝构建。" }

$requiredLock = Join-Path $repoRoot "backend\legacy\requirements-windows7-$Architecture.lock"
& $pythonPath -m pip install --no-index --only-binary=:all: --require-hashes `
  --find-links $wheelhousePath `
  -r $requiredLock
if ($LASTEXITCODE -ne 0) { throw "Win7 离线依赖安装失败。" }
& $pythonPath (Join-Path $repoRoot "scripts\validate-win7-cryptography-runtime.py")
if ($LASTEXITCODE -ne 0) {
  throw "Win7 cryptography 安全回移运行时门禁失败，拒绝继续冻结安装包。"
}
& $pythonPath -m pip install --no-index --only-binary=:all: `
  --find-links $wheelhousePath `
  -r (Join-Path $PSScriptRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Win7 冻结工具链安装失败。" }

# FastAPI 路由装饰器会在导入时立即求值，主线 Python 3.11 测试无法发现
# `list[dict]` 等 Python 3.9+ 写法。冻结前必须用真实 3.8 运行时导入完整应用。
$backendRoot = Join-Path $repoRoot "backend"
$legacyImport = & $pythonPath -c "import pathlib,sys; sys.path.insert(0, str(pathlib.Path(sys.argv[1]))); import app.main; print(app.main.app.version)" $backendRoot
if ($LASTEXITCODE -ne 0 -or $legacyImport -notcontains $releaseVersion) {
  throw "Win7 $Architecture Python 3.8 完整应用导入门禁失败，拒绝继续冻结安装包。"
}

# 仅导入应用不会执行 Alembic 历史迁移。使用隔离数据目录真实初始化一次，
# 防止 Python 3.8 不支持的迁移注解在用户首次启动时才暴露。
$legacySchemaRoot = Join-Path $repoRoot ".py38-schema-smoke\$Architecture-$PID"
New-Item -ItemType Directory -Path $legacySchemaRoot -Force | Out-Null
$savedDataDir = $env:PARTYOPS_DATA_DIR
try {
  $env:PARTYOPS_DATA_DIR = $legacySchemaRoot
  $legacySchema = & $pythonPath -c "import pathlib,sqlite3,sys; sys.path.insert(0, str(pathlib.Path(sys.argv[1]))); from app.database import DatabaseRuntime; DatabaseRuntime().create_schema(); db=sqlite3.connect(str(pathlib.Path(sys.argv[2])/'partyops.db')); print(db.execute('select version_num from alembic_version').fetchone()[0])" $backendRoot $legacySchemaRoot
  if ($LASTEXITCODE -ne 0 -or $legacySchema -notcontains "0026") {
    throw "Win7 $Architecture Python 3.8 数据库迁移链初始化门禁失败，拒绝继续冻结安装包。"
  }
} finally {
  $env:PARTYOPS_DATA_DIR = $savedDataDir
  if (Test-Path -LiteralPath $legacySchemaRoot) {
    Remove-Item -LiteralPath $legacySchemaRoot -Recurse -Force
  }
}

# PyInstaller bootloader 也必须来自已验证 wheelhouse，禁止临时下载主线构建工具。
& $pythonPath -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Win7 wheelhouse 缺少已验证的 PyInstaller/bootloader。" }

$env:PARTYOPS_LEGACY_ARCH = $Architecture
$env:PARTYOPS_LEGACY_PROFILE = if ($Architecture -eq "amd64") { "legacy-smart" } else { "legacy-core" }
$env:PARTYOPS_LEGACY_WHEELHOUSE = $wheelhousePath
$env:PARTYOPS_LEGACY_EVIDENCE_ROOT = $evidencePath

# 共享冻结器必须显式识别 Legacy 环境，避免误用 Python 3.11 主线；产出后再由
# PE 门禁确认每一个 EXE/DLL/PYD 均满足 Win7 6.1 与目标架构。
& (Join-Path $PSScriptRoot "build-windows.ps1") `
  -Python $pythonPath `
  -SqliteDll $SqliteDll `
  -SqliteSha256 $SqliteSha256 `
  -InnoCompiler $InnoCompiler `
  -OfficeRuntime $OfficeRuntime `
  -OfficeRuntimeMode $OfficeRuntimeMode `
  -LegacyArchitecture $Architecture
if ($LASTEXITCODE -ne 0) { throw "Win7 $Architecture 冻结或安装器构建失败。" }

$bundleRoot = Join-Path $repoRoot "artifacts\PartyOps-$releaseVersion-windows7-$Architecture"
& $pythonPath (Join-Path $repoRoot "scripts\validate-win7-pe.py") `
  --root $bundleRoot `
  --architecture $Architecture
if ($LASTEXITCODE -ne 0) { throw "Win7 $Architecture PE 门禁未通过。" }
