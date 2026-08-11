$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$corepackCommand = Get-Command "corepack" -ErrorAction SilentlyContinue
if (-not $corepackCommand) {
    throw "未找到 Corepack。请安装 Node.js 22，并启用 package.json 指定的 pnpm 版本。"
}
$corepack = $corepackCommand.Source

if (-not (Test-Path -LiteralPath $python)) {
    python -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Python 虚拟环境创建失败，退出码：$LASTEXITCODE"
    }
}

& $python -m pip install -r (Join-Path $root "backend\requirements-dev.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Python 开发依赖安装失败，退出码：$LASTEXITCODE"
}
& $corepack pnpm --dir (Join-Path $root "frontend") install --frozen-lockfile
if ($LASTEXITCODE -ne 0) {
    throw "前端依赖安装失败，退出码：$LASTEXITCODE"
}

$env:PARTYOPS_ENVIRONMENT = "development"
$env:PARTYOPS_SEED_DEMO = "true"
Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18765" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
Start-Process -FilePath $corepack -ArgumentList "pnpm", "--dir", (Join-Path $root "frontend"), "dev", "--host", "127.0.0.1", "--port", "4173" -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden

Write-Host "党建智办开发服务已启动："
Write-Host "前端 http://127.0.0.1:4173"
Write-Host "API  http://127.0.0.1:18765/api/v1/health"
