$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$staging = Join-Path $root ".comprehensive-hotfix"
$archive = Join-Path $artifacts "PartyOps-1.1.3-comprehensive-hotfix.zip"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path -LiteralPath (Join-Path $root "frontend\dist\client\index.html"))) {
  throw "缺少最新前端生产构建，请先运行 pnpm --dir frontend run build。"
}

$items = @(
  "backend",
  "frontend\src",
  "frontend\dist",
  "frontend\tests",
  "frontend\package.json",
  "frontend\pnpm-lock.yaml",
  "frontend\pnpm-workspace.yaml",
  "frontend\tsconfig.json",
  "frontend\vite.config.mjs",
  "frontend\vitest.config.ts",
  "packaging",
  "scripts",
  "docs",
  "README.md",
  "install.sh",
  "apply-hotfix.sh",
  "extract-and-apply-hotfix.sh",
  "一键安装党建智办.sh",
  "一键解压并应用修复.sh",
  "应用本次修复.sh",
  "安装说明.txt",
  "design-qa.md"
)

if (Test-Path -LiteralPath $staging) {
  $resolved = (Resolve-Path -LiteralPath $staging).Path
  if (-not $resolved.StartsWith((Resolve-Path -LiteralPath $root).Path, [StringComparison]::OrdinalIgnoreCase)) {
    throw "暂存目录不在项目工作区内，拒绝清理。"
  }
  Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging, $artifacts | Out-Null

foreach ($relative in $items) {
  $source = Join-Path $root $relative
  if (-not (Test-Path -LiteralPath $source)) {
    throw "缺少综合修复文件：$relative"
  }
  $destination = Join-Path $staging $relative
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
  Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
}

Get-ChildItem -LiteralPath $staging -Directory -Recurse |
  Where-Object {
    $_.Name -in @("__pycache__", "coverage", "htmlcov", ".pytest_cache", ".mypy_cache", ".test-data", "node_modules") -or
    $_.Name -like ".pytest-tmp-*" -or
    $_.Name -like ".test-tmp*" -or
    $_.Name -like ".qa-*" -or
    $_.Name -like ".smoke-*"
  } |
  Sort-Object FullName -Descending |
  ForEach-Object {
    $resolved = (Resolve-Path -LiteralPath $_.FullName).Path
    if ($resolved.StartsWith((Resolve-Path -LiteralPath $staging).Path, [StringComparison]::OrdinalIgnoreCase)) {
      Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
  }
Get-ChildItem -LiteralPath $staging -File -Recurse |
  Where-Object { $_.Name -like ".coverage*" -or $_.Name -like "coverage*.json" -or $_.Extension -eq ".pyc" } |
  ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

# Windows 生成的覆盖包必须保持 Linux 脚本 LF 行尾。
Get-ChildItem -LiteralPath $staging -File -Recurse |
  Where-Object {
    $_.Extension -in @(".sh", ".desktop", ".service", ".env") -or
    $_.Name -eq "SHA256SUMS"
  } |
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
  Get-ChildItem -LiteralPath $staging -File -Recurse -Force |
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
Copy-Item -LiteralPath (Join-Path $root "extract-and-apply-hotfix.sh") `
  -Destination (Join-Path $artifacts "extract-and-apply-hotfix.sh") -Force
Copy-Item -LiteralPath (Join-Path $root "一键解压并应用修复.sh") `
  -Destination (Join-Path $artifacts "一键解压并应用修复.sh") -Force

$zipReader = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
  $required = @(
    "backend/app/enrollment_codes.py",
    "backend/app/material_categories.py",
    "backend/app/workspace.py",
    "frontend/dist/client/index.html",
    "packaging/uos/open-local-file.sh",
    "packaging/uos/partyops-file.desktop",
    "apply-hotfix.sh",
    "extract-and-apply-hotfix.sh",
    "一键解压并应用修复.sh",
    "应用本次修复.sh"
  )
  $names = @($zipReader.Entries | ForEach-Object { $_.FullName })
  foreach ($name in $required) {
    if ($name -notin $names) {
      throw "综合修复包缺少关键文件：$name"
    }
  }
}
finally {
  $zipReader.Dispose()
}

Remove-Item -LiteralPath $staging -Recurse -Force
Write-Host "综合覆盖修复包已生成：$archive"
