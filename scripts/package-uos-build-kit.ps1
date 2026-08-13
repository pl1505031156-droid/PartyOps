param(
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$stagingRoot = Join-Path $root ".build-kit"
$releaseVersion = "1.4.3-rc.2"
$releaseTag = "v1.4.3-rc.2"
$staging = Join-Path $stagingRoot "PartyOps-$releaseVersion"
$archive = Join-Path $artifacts "PartyOps-UOS-$releaseVersion-build-kit.zip"
if (-not $Python) {
  $Python = if ($env:PARTYOPS_PYTHON) {
    $env:PARTYOPS_PYTHON
  } else {
    Join-Path $root ".venv\Scripts\python.exe"
  }
}
if (-not (Test-Path -LiteralPath $Python)) {
  throw "未找到用于校验 UOS 离线依赖的 Python：$Python"
}

if (-not (Test-Path -LiteralPath (Join-Path $root "frontend\dist\client\index.html"))) {
  throw "缺少前端生产构建，请先运行 scripts\build.ps1 或 pnpm build。"
}
if (-not (Test-Path -LiteralPath (Join-Path $root "vendor\SHA256SUMS"))) {
  throw "缺少 UOS 离线依赖，请先运行 scripts\prepare-uos-offline.ps1。"
}
$requiredInputs = @(
  "vendor\wheels\amd64",
  "vendor\wheels\arm64",
  "vendor\cpython-3.11.15+20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
  "vendor\cpython-3.11.15+20260623-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
)
foreach ($input in $requiredInputs) {
  if (-not (Test-Path -LiteralPath (Join-Path $root $input))) {
    throw "缺少双架构离线输入：$input。请运行 scripts\prepare-uos-offline.ps1 -Architecture all。"
  }
}

# rc.2 候选套件把本地语义重排和 llama.cpp 作为强制离线输入。
# 任一架构缺少原生组件、依赖闭包不完整或存在重复规范化包名时，
# 都必须拒绝打包，不能再生成会在目标机上静默降级的候选套件。
$optionalEmbeddingMissing = [System.Collections.Generic.List[string]]::new()
$optionalLlmMissing = [System.Collections.Generic.List[string]]::new()
$requiredWheelPrefixes = @("numpy-2.2.6-", "onnxruntime-1.22.1-", "tokenizers-0.21.4-")
foreach ($architecture in @("amd64", "arm64")) {
  $wheelhouse = Join-Path $root "vendor\wheels\$architecture"
  foreach ($prefix in $requiredWheelPrefixes) {
    $match = Get-ChildItem -LiteralPath $wheelhouse -File -Filter "$prefix*.whl" | Select-Object -First 1
    if (-not $match) {
      $optionalEmbeddingMissing.Add("vendor/wheels/$architecture/$prefix*.whl")
    }
  }
  foreach ($runtimeFile in @("llama-runtime.tar.gz", "LICENSE", "SOURCE.json")) {
    $relativePath = "vendor/local-ai/$architecture/$runtimeFile"
    if (-not (Test-Path -LiteralPath (Join-Path $root $relativePath))) {
      $optionalLlmMissing.Add($relativePath)
    }
  }
  & $Python (Join-Path $root "scripts\validate-uos-wheelhouse.py") `
    --architecture $architecture `
    --wheelhouse $wheelhouse `
    --requirements `
    (Join-Path $root "backend\requirements.txt") `
    (Join-Path $root "backend\requirements-local-ai.txt") `
    (Join-Path $root "packaging\uos\requirements-build.txt")
  if ($LASTEXITCODE -ne 0) {
    throw "UOS $architecture 离线依赖闭包或唯一性校验失败，拒绝生成候选构建套件。"
  }
}

if (Test-Path -LiteralPath $stagingRoot) {
  $resolved = (Resolve-Path -LiteralPath $stagingRoot).Path
  $expectedParent = (Resolve-Path -LiteralPath $root).Path
  if (-not $resolved.StartsWith($expectedParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "暂存目录不在项目工作区内，拒绝清理。"
  }
  Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging, $artifacts | Out-Null

$include = @(
  "backend",
  "frontend\dist",
  "frontend\package.json",
  "frontend\pnpm-lock.yaml",
  "frontend\pnpm-workspace.yaml",
  "packaging",
  "scripts",
  "docs",
  "design",
  "README.md",
  "QUICK-INSTALL.txt",
  "install.sh",
  "apply-hotfix.sh",
  "extract-and-apply-hotfix.sh",
  "一键安装党建智办.sh",
  "安装说明.txt",
  "design-qa.md",
  ".gitignore",
  "vendor"
)
foreach ($item in $include) {
  $source = Join-Path $root $item
  if (Test-Path -LiteralPath $source) {
    $destination = Join-Path $staging $item
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
  }
}
if ($optionalEmbeddingMissing.Count -gt 0 -or $optionalLlmMissing.Count -gt 0) {
  $allMissing = @($optionalEmbeddingMissing) + @($optionalLlmMissing)
  throw "rc.2 严格模式要求完整双架构本地智能运行时，缺少：$($allMissing -join ', ')"
}

# 发布清单、候选验收和 Release 正文都要记录这个 ZIP 自身的最终哈希或条目数，
# 若把它们再次放入 ZIP 会形成不可收敛的自引用。构建套件只保留安装、运维、
# 迁移和用户文档；发布证据以 GitHub 源码与 Release 同级附件为准。
foreach ($releaseEvidence in @(
  "docs\README.md",
  "docs\release-notes-v1.4.3-rc.2.md",
  "docs\release-readiness-1.4.3.md",
  "docs\acceptance-1.4.3.md",
  "docs\artifact-manifest-1.4.3.md"
)) {
  $evidencePath = Join-Path $staging $releaseEvidence
  if (Test-Path -LiteralPath $evidencePath) {
    Remove-Item -LiteralPath $evidencePath -Force
  }
}

$capabilityLines = @(
  "党建智办 PartyOps UOS 原生构建套件能力说明",
  "版本：$releaseVersion",
  "标签：$releaseTag",
  "生成时间：$([DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz'))",
  "",
  "已包含：",
  "- PartyOps 1.4.3 后端源码、前端生产构建、UOS/Windows 安装与统一升级脚本。",
  "- amd64 与 ARM64 的 Python 3.11.15 基础运行时和核心离线轮子。",
  "- SQLite 3.53.4、FTS5 构建输入、OCR 集成脚本及双架构打包入口。",
  "- 规则推荐与已获批外部 AI 接口不依赖本地模型运行时。",
  "",
  "本地智能说明："
)
$capabilityLines += "- 双架构 numpy、ONNX Runtime 和 tokenizers 离线依赖已包含，并已通过严格闭包与重复包校验。"
$capabilityLines += "- 双架构官方 llama.cpp b10331 CPU 运行时、来源清单和许可文件已包含，可启用本地 LLM。"
$capabilityLines += @(
  "",
  "重要边界：",
  "- 本文件是目标机原生构建套件说明，不代表双架构 .deb 已完成实机验收。",
  "- 不携带本地大模型文件；模型须通过签名 .partyops-modelpack 在主机系统内导入。"
)
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines(
  (Join-Path $staging "BUILD-KIT-CAPABILITIES.txt"),
  $capabilityLines,
  $utf8WithoutBom
)

Get-ChildItem -LiteralPath $staging -Directory -Recurse |
  Where-Object {
    $_.Name -in @("__pycache__", "coverage", "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".test-data", ".venv", "venv", "node_modules", ".run-e2e") -or
    $_.Name -like "*.egg-info" -or
    $_.Name -like ".pytest-tmp-*" -or
    $_.Name -like ".test-tmp*" -or
    $_.Name -like ".smoke-*" -or
    $_.Name -like ".qa-*"
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
  ForEach-Object {
    $resolved = (Resolve-Path -LiteralPath $_.FullName).Path
    if ($resolved.StartsWith((Resolve-Path -LiteralPath $staging).Path, [StringComparison]::OrdinalIgnoreCase)) {
      Remove-Item -LiteralPath $_.FullName -Force
    }
  }

# Linux 会把 CR 当作文件名的一部分。打包前统一关键脚本与清单为 LF，
# 即使套件从 Windows 文件系统生成，也能在 UOS 上直接执行和校验。
Get-ChildItem -LiteralPath $staging -File -Recurse |
  Where-Object {
    $_.Extension -in @(".sh", ".desktop", ".service", ".env") -or
    $_.Name -eq "SHA256SUMS"
  } |
  ForEach-Object {
    $content = [System.IO.File]::ReadAllText($_.FullName)
    $linuxContent = $content.Replace("`r`n", "`n").Replace("`r", "`n")
    [System.IO.File]::WriteAllText($_.FullName, $linuxContent, $utf8WithoutBom)
  }

if (Test-Path -LiteralPath $archive) {
  Remove-Item -LiteralPath $archive -Force
}
$zipMinTime = [DateTime]"1980-01-01T00:00:00"
$zipMaxTime = [DateTime]"2107-12-31T23:59:59"
$normalizedTime = [DateTime]"2000-01-01T00:00:00"
Get-ChildItem -LiteralPath $staging -File -Recurse -Force |
  Where-Object { $_.LastWriteTime -lt $zipMinTime -or $_.LastWriteTime -gt $zipMaxTime } |
  ForEach-Object { $_.LastWriteTime = $normalizedTime }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archiveStream = [System.IO.File]::Open(
  $archive,
  [System.IO.FileMode]::CreateNew,
  [System.IO.FileAccess]::ReadWrite,
  [System.IO.FileShare]::None
)
$zipWriter = New-Object System.IO.Compression.ZipArchive(
  $archiveStream,
  [System.IO.Compression.ZipArchiveMode]::Create,
  $false
)
try {
  Get-ChildItem -LiteralPath $staging -File -Recurse -Force |
    Sort-Object FullName |
    ForEach-Object {
      $relative = $_.FullName.Substring($stagingRoot.Length).TrimStart("\", "/").Replace("\", "/")
      $entry = $zipWriter.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
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
  $zipWriter.Dispose()
  $archiveStream.Dispose()
}
if (-not (Test-Path -LiteralPath $archive)) {
  throw "UOS 离线构建套件压缩失败。"
}

# 发布包绝不能携带更新签名私钥、会话密钥或发布机密钥目录。
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
  $forbiddenEntries = @(
    $zip.Entries | Where-Object {
      $_.FullName -match '(^|/)(release-keys|private-keys?)(/|$)' -or
      $_.FullName -match '(?i)(private[-_]?key|secret[-_]?key|update-private-key).*\.pem$'
    }
  )
  if ($forbiddenEntries.Count -gt 0) {
    throw "离线构建套件检测到禁止分发的私钥文件：$($forbiddenEntries[0].FullName)"
  }
}
finally {
  $zip.Dispose()
}

$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
  "$archive.sha256",
  "$hash  $([IO.Path]::GetFileName($archive))`n",
  (New-Object System.Text.UTF8Encoding($false))
)
$sourceCommit = (& git -C $root rev-parse HEAD).Trim()
$candidate = [ordered]@{
  schema_version = 1
  product = "PartyOps"
  version = $releaseVersion
  release_tag = $releaseTag
  source_commit = $sourceCommit
  platform = "uos-amd64-arm64-build-kit"
  filename = (Split-Path -Leaf $archive)
  size = (Get-Item -LiteralPath $archive).Length
  sha256 = $hash
  strict_dependency_closure = $true
  native_deb_tested = $false
}
[System.IO.File]::WriteAllText(
  (Join-Path $artifacts "PartyOps-UOS-$releaseVersion-build-kit.candidate.json"),
  ($candidate | ConvertTo-Json -Depth 5),
  (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "UOS 离线构建套件已生成：$archive"

$resolvedStagingRoot = (Resolve-Path -LiteralPath $stagingRoot).Path
$resolvedProjectRoot = (Resolve-Path -LiteralPath $root).Path
if (-not $resolvedStagingRoot.StartsWith($resolvedProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "暂存目录不在项目工作区内，拒绝清理。"
}
Remove-Item -LiteralPath $stagingRoot -Recurse -Force
