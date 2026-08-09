$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$staging = Join-Path $root ".enrollment-hotfix"
$archive = Join-Path $artifacts "PartyOps-1.1.3-enrollment-hotfix.zip"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)

$files = @(
  "backend\app\enrollment_codes.py",
  "backend\app\client_agent.py",
  "backend\app\routers\fleet.py",
  "backend\app\schemas.py",
  "packaging\uos\one-click-install.sh",
  "packaging\uos\build-portable.sh",
  "packaging\uos\build-and-install.sh",
  "docs\enrollment-hotfix-1.1.3.md"
)

if (Test-Path -LiteralPath $staging) {
  $resolved = (Resolve-Path -LiteralPath $staging).Path
  if (-not $resolved.StartsWith((Resolve-Path -LiteralPath $root).Path, [StringComparison]::OrdinalIgnoreCase)) {
    throw "暂存目录不在项目工作区内，拒绝清理。"
  }
  Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging, $artifacts | Out-Null

foreach ($relative in $files) {
  $source = Join-Path $root $relative
  if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "缺少修复文件：$relative"
  }
  $destination = Join-Path $staging $relative
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
  Copy-Item -LiteralPath $source -Destination $destination -Force
}

# 确保从 Windows 生成的 Shell 脚本在 UOS 上保持 LF。
Get-ChildItem -LiteralPath $staging -File -Recurse |
  Where-Object { $_.Extension -eq ".sh" } |
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
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archiveStream = [System.IO.File]::Open(
  $archive,
  [System.IO.FileMode]::CreateNew,
  [System.IO.FileAccess]::ReadWrite,
  [System.IO.FileShare]::None
)
$zip = New-Object System.IO.Compression.ZipArchive(
  $archiveStream,
  [System.IO.Compression.ZipArchiveMode]::Create,
  $false
)
try {
  Get-ChildItem -LiteralPath $staging -File -Recurse |
    Sort-Object FullName |
    ForEach-Object {
      $relative = $_.FullName.Substring($staging.Length).TrimStart("\", "/").Replace("\", "/")
      $entry = $zip.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
      $entryStream = $entry.Open()
      $sourceStream = [System.IO.File]::OpenRead($_.FullName)
      try {
        $sourceStream.CopyTo($entryStream)
      }
      finally {
        $sourceStream.Dispose()
        $entryStream.Dispose()
      }
    }
}
finally {
  $zip.Dispose()
  $archiveStream.Dispose()
}
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  "$archive.sha256",
  "$hash  $([IO.Path]::GetFileName($archive))`n",
  $utf8WithoutBom
)

Remove-Item -LiteralPath $staging -Recurse -Force
Write-Host "入网修复包已生成：$archive"
