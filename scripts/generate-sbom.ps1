$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = if ($env:PARTYOPS_PYTHON) { $env:PARTYOPS_PYTHON } else { Join-Path $root "backend\.venv\Scripts\python.exe" }
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$website = Join-Path $root "website"
$docs = Join-Path $root "docs"

if (-not (Test-Path -LiteralPath $python)) {
  throw "缺少 cyclonedx-bom；请先安装 backend/requirements-dev.txt。"
}

& $python -m cyclonedx_py requirements `
  (Join-Path $backend "requirements-release.txt") `
  --spec-version 1.6 `
  --output-format JSON `
  --output-reproducible `
  --output-file (Join-Path $docs "sbom-python.cdx.json")
if ($LASTEXITCODE -ne 0) { throw "Python SBOM 生成失败。" }

& corepack pnpm --dir $frontend sbom `
  --sbom-format cyclonedx `
  --sbom-spec-version 1.6 `
  --prod `
  --out (Join-Path $docs "sbom-frontend.cdx.json")
if ($LASTEXITCODE -ne 0) { throw "前端 SBOM 生成失败。" }

& corepack pnpm --dir $website sbom `
  --sbom-format cyclonedx `
  --sbom-spec-version 1.6 `
  --prod `
  --out (Join-Path $docs "sbom-website.cdx.json")
if ($LASTEXITCODE -ne 0) { throw "官网 SBOM 生成失败。" }

Get-Content (Join-Path $docs "sbom-python.cdx.json") -Raw | ConvertFrom-Json | Out-Null
Get-Content (Join-Path $docs "sbom-frontend.cdx.json") -Raw | ConvertFrom-Json | Out-Null
Get-Content (Join-Path $docs "sbom-website.cdx.json") -Raw | ConvertFrom-Json | Out-Null
Write-Host "已生成并校验 Python、前端与官网 CycloneDX 1.6 SBOM。"
