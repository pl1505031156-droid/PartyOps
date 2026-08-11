$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$corepackCommand = Get-Command "corepack" -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $python)) {
    throw "缺少项目 Python 环境：$python。请先按 README 创建虚拟环境并安装依赖。"
}
if (-not $corepackCommand) {
    throw "未找到 Corepack。请安装 Node.js 22，并启用 package.json 指定的 pnpm 版本。"
}
$corepack = $corepackCommand.Source

& $corepack pnpm --dir (Join-Path $root "frontend") run build
if ($LASTEXITCODE -ne 0) {
    throw "前端生产构建失败，退出码：$LASTEXITCODE"
}
$env:PARTYOPS_ENVIRONMENT = "production"
$env:PARTYOPS_SEED_DEMO = "false"
$env:PARTYOPS_FRONTEND_DIST = Join-Path $root "frontend\dist\client"

& $python -m uvicorn app.main:app --app-dir (Join-Path $root "backend") --host 127.0.0.1 --port 18765
