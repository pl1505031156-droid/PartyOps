param(
  [ValidateSet("amd64", "arm64", "all")]
  [string]$Architecture = "all"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$vendor = Join-Path $root "vendor"
$wheelsRoot = Join-Path $vendor "wheels"
$sqliteArchive = Join-Path $vendor "sqlite-amalgamation-3510300.zip"
$pysqliteArchive = Join-Path $vendor "pysqlite3-0.5.4.tar.gz"
$targets = @{
  amd64 = @{
    PipPlatforms = @("manylinux_2_28_x86_64", "manylinux2014_x86_64")
    PythonArchive = "cpython-3.11.15+20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
    PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20260623/cpython-3.11.15%2B20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
    PythonSha256 = "0604cd029b142dc223e131f17f5941c0c8d2d5074997c8178b515b19eea2a6c2"
  }
  arm64 = @{
    PipPlatforms = @("manylinux_2_28_aarch64", "manylinux2014_aarch64")
    PythonArchive = "cpython-3.11.15+20260623-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
    PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20260623/cpython-3.11.15%2B20260623-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
    PythonSha256 = "9ac18c9a761e91e6c6452bc0ef0082922a00a3fdec734555635d57c3169309b7"
  }
}

if (-not (Test-Path -LiteralPath $python)) {
  throw "缺少项目 Python 虚拟环境：$python"
}
New-Item -ItemType Directory -Force -Path $vendor, $wheelsRoot | Out-Null

function Prepare-Architecture([string]$TargetArchitecture) {
  $target = $targets[$TargetArchitecture]
  $wheelhouse = Join-Path $wheelsRoot $TargetArchitecture
  if (Test-Path -LiteralPath $wheelhouse) {
    $resolvedWheelhouse = (Resolve-Path -LiteralPath $wheelhouse).Path
    $resolvedVendor = (Resolve-Path -LiteralPath $vendor).Path
    if (-not $resolvedWheelhouse.StartsWith($resolvedVendor, [StringComparison]::OrdinalIgnoreCase)) {
      throw "轮子目录不在项目 vendor 内，拒绝清理。"
    }
    Remove-Item -LiteralPath $wheelhouse -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

  $pipArgs = @(
    "-m", "pip", "download",
    "--dest", $wheelhouse,
    "--python-version", "311",
    "--implementation", "cp",
    "--abi", "cp311",
    "--only-binary=:all:"
  )
  foreach ($platform in $target.PipPlatforms) {
    $pipArgs += @("--platform", $platform)
  }
  $pipArgs += @(
    "-r", (Join-Path $root "backend\requirements.txt"),
    "-r", (Join-Path $root "backend\requirements-local-ai.txt"),
    "-r", (Join-Path $root "packaging\uos\requirements-build.txt")
  )
  & $python @pipArgs
  if ($LASTEXITCODE -ne 0) {
    throw "下载 UOS $TargetArchitecture 轮子失败。"
  }

  & $python (Join-Path $root "scripts\validate-uos-wheelhouse.py") `
    --architecture $TargetArchitecture `
    --wheelhouse $wheelhouse `
    --requirements `
    (Join-Path $root "backend\requirements.txt") `
    (Join-Path $root "backend\requirements-local-ai.txt") `
    (Join-Path $root "packaging\uos\requirements-build.txt")
  if ($LASTEXITCODE -ne 0) {
    throw "UOS/Linux $TargetArchitecture 离线依赖闭包不完整。"
  }

  $pythonArchive = Join-Path $vendor $target.PythonArchive
  if (-not (Test-Path -LiteralPath $pythonArchive)) {
    Invoke-WebRequest -Uri $target.PythonUrl -OutFile $pythonArchive
  }
  $actual = (Get-FileHash -LiteralPath $pythonArchive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $target.PythonSha256) {
    throw "$TargetArchitecture 独立 Python 3.11 制品 SHA-256 不匹配。"
  }
}

$requested = if ($Architecture -eq "all") { @("amd64", "arm64") } else { @($Architecture) }
foreach ($item in $requested) {
  Prepare-Architecture $item
}
if ($requested -contains "amd64") {
  # 1.0 套件把 amd64 轮子直接放在 wheels 根目录；1.1 改为按架构隔离。
  # 新目录校验通过后移除旧的平铺副本，避免发布套件重复携带或 ARM64 误选。
  Get-ChildItem -LiteralPath $wheelsRoot -File |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
}

$sourceTemp = Join-Path $env:TEMP "partyops-pysqlite-source"
if (Test-Path -LiteralPath $sourceTemp) {
  $resolvedSourceTemp = (Resolve-Path -LiteralPath $sourceTemp).Path
  $resolvedSystemTemp = (Resolve-Path -LiteralPath $env:TEMP).Path
  if (-not $resolvedSourceTemp.StartsWith($resolvedSystemTemp, [StringComparison]::OrdinalIgnoreCase)) {
    throw "源码暂存目录不在系统临时目录内，拒绝清理。"
  }
  Remove-Item -LiteralPath $sourceTemp -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $sourceTemp | Out-Null
& $python -m pip download --dest $sourceTemp --no-deps --no-binary=pysqlite3 "pysqlite3==0.5.4"
if ($LASTEXITCODE -ne 0) {
  throw "下载 pysqlite3 源码包失败。"
}
$downloadedSource = Get-ChildItem -LiteralPath $sourceTemp -Filter "pysqlite3-0.5.4.tar.gz" | Select-Object -First 1
if (-not $downloadedSource) {
  throw "未下载到 pysqlite3-0.5.4 源码包。"
}
Copy-Item -LiteralPath $downloadedSource.FullName -Destination $pysqliteArchive -Force
Remove-Item -LiteralPath $sourceTemp -Recurse -Force

if (-not (Test-Path -LiteralPath $sqliteArchive)) {
  Invoke-WebRequest -Uri "https://www.sqlite.org/2026/sqlite-amalgamation-3510300.zip" -OutFile $sqliteArchive
}

$hashFile = Join-Path $vendor "SHA256SUMS"
$paths = Get-ChildItem -LiteralPath $vendor -File -Recurse |
  Where-Object { $_.FullName -ne $hashFile } |
  Sort-Object FullName
$lines = foreach ($path in $paths) {
  $hash = (Get-FileHash -LiteralPath $path.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  $relative = $path.FullName.Substring($vendor.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
  "$hash  $relative"
}
[System.IO.File]::WriteAllText(
  $hashFile,
  (([string[]]$lines -join "`n") + "`n"),
  (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "UOS amd64/arm64 离线构建依赖已准备：$vendor"
