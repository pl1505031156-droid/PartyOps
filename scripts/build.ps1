$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$pnpm = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

& $pnpm --dir (Join-Path $root "frontend") run build
$env:PARTYOPS_ENVIRONMENT = "production"
$env:PARTYOPS_SEED_DEMO = "false"
$env:PARTYOPS_FRONTEND_DIST = Join-Path $root "frontend\dist\client"

& $python -m uvicorn app.main:app --app-dir (Join-Path $root "backend") --host 127.0.0.1 --port 18765
