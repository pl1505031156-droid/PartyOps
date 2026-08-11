param(
  [string]$Python = "",
  [switch]$InstallInnoSetup
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) { $Python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $Python)) { throw "未找到项目 Python：$Python" }

$vendorRoot = Join-Path $root "vendor\windows\sqlite-3.53.4"
$archive = Join-Path $vendorRoot "sqlite-dll-win-x64-3530400.zip"
$runtimeRoot = Join-Path $vendorRoot "runtime"
$dll = Join-Path $runtimeRoot "sqlite3.dll"
$expectedVersion = "3.53.4"
$expectedSha3 = "deddee963c810d1eeac3ce5e15c7c41da21a1c54d7a39cf54fbf577d2f50de3a"
$expectedDllSha256 = "AB57D0437795ECC757CB693F32EA224173FA9856594D95CFA6B5033E645CD1EC"
$downloadUrl = "https://sqlite.org/2026/sqlite-dll-win-x64-3530400.zip"
New-Item -ItemType Directory -Path $vendorRoot -Force | Out-Null

if (-not (Test-Path -LiteralPath $archive)) {
  Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -UseBasicParsing
}
$actualSha3 = & $Python -c "import hashlib,sys; print(hashlib.sha3_256(open(sys.argv[1],'rb').read()).hexdigest())" $archive
if ($actualSha3 -ne $expectedSha3) {
  throw "SQLite 官方归档 SHA3-256 不匹配，拒绝用于发布构建。"
}
if (-not (Test-Path -LiteralPath $dll)) {
  New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
  Expand-Archive -LiteralPath $archive -DestinationPath $runtimeRoot -Force
}
$sqliteVersion = & $Python -c "import ctypes,sys; lib=ctypes.WinDLL(sys.argv[1]); lib.sqlite3_libversion.restype=ctypes.c_char_p; print(lib.sqlite3_libversion().decode())" $dll
if ($sqliteVersion -ne $expectedVersion) {
  throw "SQLite DLL 版本应为 $expectedVersion，实际为 $sqliteVersion。"
}
$actualDllSha256 = (Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash
if ($actualDllSha256 -ne $expectedDllSha256) {
  throw "SQLite DLL SHA-256 不匹配，拒绝用于发布构建。"
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

Write-Host "Windows 构建依赖已准备：SQLite $sqliteVersion；SHA-256=$($actualDllSha256.ToLowerInvariant())；DLL=$dll；Inno=$inno"
