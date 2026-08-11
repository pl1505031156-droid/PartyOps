$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$version = if ($env:PARTYOPS_VERSION) { $env:PARTYOPS_VERSION } else { "1.4.3" }
$stagingRoot = Join-Path $root ".release-kit"
$releaseName = "PartyOps-$version-UOS-amd64-arm64-offline"
$staging = Join-Path $stagingRoot $releaseName
$archive = Join-Path $artifacts "$releaseName.zip"
$required = @(
  "partyops_${version}_amd64.deb",
  "partyops_${version}_arm64.deb",
  "PartyOps-uos-amd64.tar.zst",
  "PartyOps-uos-arm64.tar.zst",
  "partyops_${version}.partyops-update",
  "partyops_${version}.partyops-update.sha256",
  "党建智办-${version}-更新说明.txt",
  "SHA256SUMS.amd64",
  "SHA256SUMS.arm64"
)

foreach ($name in $required) {
  if (-not (Test-Path -LiteralPath (Join-Path $artifacts $name))) {
    throw "缺少双架构原生制品：artifacts\$name。请分别在 UOS V20 amd64 和 ARM64 目标机完成构建后汇总。"
  }
}

if (Test-Path -LiteralPath $stagingRoot) {
  $resolved = (Resolve-Path -LiteralPath $stagingRoot).Path
  $project = (Resolve-Path -LiteralPath $root).Path
  if (-not $resolved.StartsWith($project, [StringComparison]::OrdinalIgnoreCase)) {
    throw "发布暂存目录不在工作区内，拒绝清理。"
  }
  Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path `
  (Join-Path $staging "artifacts"), `
  (Join-Path $staging "packaging\uos"), `
  (Join-Path $staging "docs") | Out-Null

Copy-Item -LiteralPath (Join-Path $root "install.sh") -Destination $staging
Copy-Item -LiteralPath (Join-Path $root "一键安装党建智办.sh") -Destination $staging
Copy-Item -LiteralPath (Join-Path $root "安装说明.txt") -Destination $staging
Copy-Item -LiteralPath (Join-Path $root "packaging\uos\one-click-install.sh") `
  -Destination (Join-Path $staging "packaging\uos")
foreach ($document in @(
  "one-click-install.md",
  "upgrade-1.4.3.md",
  "党建智办-1.4.3-更新说明.txt",
  "upgrade-1.4.2.md",
  "党建智办-1.4.2-更新说明.txt",
  "upgrade-1.4.1.md",
  "党建智办-1.4.1-更新说明.txt",
  "upgrade-1.4.0.md",
  "党建智办-1.4.0-更新说明.txt",
  "upgrade-1.3.4.md",
  "党建智办-1.3.4-更新说明.txt",
  "upgrade-1.3.3.md",
  "党建智办-1.3.3-更新说明.txt",
  "upgrade-1.3.2.md",
  "党建智办-1.3.2-更新说明.txt",
  "upgrade-1.3.1.md",
  "党建智办-1.3.1-更新说明.txt",
  "upgrade-1.3.0.md",
  "党建智办-1.3.0-更新说明.txt",
  "architecture-1.3.0.md",
  "upgrade-1.2.0.md",
  "architecture-1.2.0.md",
  "upgrade-1.1.3.md",
  "党建智办-1.1.3-更新说明.txt",
  "upgrade-1.1.2.md",
  "upgrade-1.1.md",
  "installation-checklist.md",
  "operations-runbook.md",
  "backup-restore.md"
)) {
  Copy-Item -LiteralPath (Join-Path $root "docs\$document") `
    -Destination (Join-Path $staging "docs")
}
foreach ($name in $required) {
  Copy-Item -LiteralPath (Join-Path $artifacts $name) `
    -Destination (Join-Path $staging "artifacts")
}

$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
Get-ChildItem -LiteralPath $staging -File -Recurse |
  Where-Object { $_.Extension -eq ".sh" -or $_.Name -like "SHA256SUMS.*" } |
  ForEach-Object {
    $content = [System.IO.File]::ReadAllText($_.FullName)
    [System.IO.File]::WriteAllText(
      $_.FullName,
      $content.Replace("`r`n", "`n").Replace("`r", "`n"),
      $utf8WithoutBom
    )
  }

if (Test-Path -LiteralPath $archive) {
  Remove-Item -LiteralPath $archive -Force
}
Compress-Archive -LiteralPath $staging -DestinationPath $archive -CompressionLevel Optimal

# 双架构安装目录仅允许公开安装制品和说明，禁止混入发布签名私钥。
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
  $forbiddenEntries = @(
    $zip.Entries | Where-Object {
      $_.FullName -match '(^|/)(release-keys|private-keys?)(/|$)' -or
      $_.FullName -match '(?i)(private[-_]?key|secret[-_]?key|update-private-key).*\.pem$'
    }
  )
  if ($forbiddenEntries.Count -gt 0) {
    throw "一键安装目录检测到禁止分发的私钥文件：$($forbiddenEntries[0].FullName)"
  }
}
finally {
  $zip.Dispose()
}

$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  "$archive.sha256",
  "$hash  $([IO.Path]::GetFileName($archive))`n",
  $utf8WithoutBom
)
Write-Host "双架构一键安装目录已打包：$archive"

$resolvedStaging = (Resolve-Path -LiteralPath $stagingRoot).Path
$resolvedRoot = (Resolve-Path -LiteralPath $root).Path
if (-not $resolvedStaging.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "发布暂存目录不在工作区内，拒绝清理。"
}
Remove-Item -LiteralPath $stagingRoot -Recurse -Force
