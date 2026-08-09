$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$pnpm = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

if (-not (Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $root ".venv")
}

& $python -m pip install -r (Join-Path $root "backend\requirements-dev.txt")
& $pnpm install --dir (Join-Path $root "frontend") --prefer-offline

$env:PARTYOPS_ENVIRONMENT = "development"
$env:PARTYOPS_SEED_DEMO = "true"
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18765" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
Start-Process -FilePath $pnpm -ArgumentList "--dir", (Join-Path $root "frontend"), "dev", "--host", "127.0.0.1", "--port", "4173" -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden

Write-Host "党建智办开发服务已启动："
Write-Host "前端 http://127.0.0.1:4173"
Write-Host "API  http://127.0.0.1:18765/api/v1/health"
