param(
  [string]$Python = "",
  [switch]$InstallInnoSetup
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) { $Python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "未找到项目 Python：$Python" }

$vendorRoot = Join-Path $root "vendor\windows\sqlite-3.53.3-x64"
$archive = Join-Path $vendorRoot "sqlite-dll-win-x64-3530300.zip"
$dll = Join-Path $vendorRoot "sqlite3.dll"
$expectedSha3 = "3a494861ce24d1f330efbc6c3fb58ce4972f2cf8df4e43122246ed987109dc8a"
$downloadUrl = "https://sqlite.org/2026/sqlite-dll-win-x64-3530300.zip"
New-Item -ItemType Directory -Path $vendorRoot -Force | Out-Null

if (-not (Test-Path -LiteralPath $archive)) {
  Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -UseBasicParsing
}
$actualSha3 = & $Python -c "import hashlib,sys; print(hashlib.sha3_256(open(sys.argv[1],'rb').read()).hexdigest())" $archive
if ($actualSha3 -ne $expectedSha3) {
  throw "SQLite 官方归档 SHA3-256 不匹配，拒绝用于发布构建。"
}
if (-not (Test-Path -LiteralPath $dll)) {
  Expand-Archive -LiteralPath $archive -DestinationPath $vendorRoot -Force
}
$sqliteVersion = & $Python -c "import ctypes,sys; lib=ctypes.WinDLL(sys.argv[1]); lib.sqlite3_libversion.restype=ctypes.c_char_p; print(lib.sqlite3_libversion().decode())" $dll
if ([version]$sqliteVersion -lt [version]"3.51.3") {
  throw "SQLite DLL 版本为 $sqliteVersion，低于发布门槛 3.51.3。"
}

& $Python -m pip install -r (Join-Path $root "packaging\windows\requirements-build.txt")

$innoCandidates = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$inno = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($InstallInnoSetup -and -not $inno) {
  winget install --id JRSoftware.InnoSetup -e -s winget --silent --accept-package-agreements --accept-source-agreements
  $inno = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $inno) {
  Write-Warning "尚未安装 Inno Setup 6；请重跑并添加 -InstallInnoSetup。"
}

Write-Host "Windows 构建依赖已准备：SQLite $sqliteVersion；DLL=$dll；Inno=$inno"
