$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"
$corepackCommand = Get-Command "corepack" -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $python)) {
  throw "缺少后端 Python 环境：$python。请先按开发说明安装锁定依赖。"
}
if (-not $corepackCommand) {
  throw "未找到 Corepack。请安装 Node.js 22 并启用 package.json 指定的 pnpm 版本。"
}
$corepack = $corepackCommand.Source

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][scriptblock]$Command,
    [Parameter(Mandatory = $true)][string]$Name
  )
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Name 失败，退出码：$LASTEXITCODE"
  }
}

Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "frontend") audit --prod --audit-level high } "前端生产依赖审计"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "website") audit --prod --audit-level high } "官网生产依赖审计"
Invoke-Checked { & $python -m pip check } "Python 依赖一致性检查"
Invoke-Checked { & $python -m pip_audit -r (Join-Path $root "backend\requirements-release.txt") } "Python 依赖审计"
Invoke-Checked { & (Join-Path $root "scripts\scan-secrets.ps1") } "Git 历史与工作区凭据扫描"
Invoke-Checked {
  & $python -m bandit -r (Join-Path $root "backend\app") (Join-Path $root "packaging\windows") -x (Join-Path $root "backend\.test-data") -ll
} "Python 中高危静态安全扫描"
Invoke-Checked { & $python -m compileall -q (Join-Path $root "backend\app") (Join-Path $root "backend\tests") } "Python 编译检查"
Invoke-Checked { & $python -m ruff check (Join-Path $root "backend\app") (Join-Path $root "backend\tests") } "Python Ruff 检查"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "frontend") run typecheck } "前端类型检查"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "frontend") run test:coverage } "前端覆盖率测试"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "frontend") run test:sites } "静态入口测试"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "frontend") run build } "前端生产构建"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "website") run test:coverage } "官网覆盖率测试"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "website") run test:sites } "官网静态入口测试"
Invoke-Checked { & $corepack pnpm --dir (Join-Path $root "website") run build } "官网生产构建"
Push-Location (Join-Path $root "backend")
try {
    Invoke-Checked {
      & $python -m coverage erase
      & $python -m coverage run --branch -m pytest tests
    } "后端全量测试"
    Invoke-Checked {
      & $python -m coverage html
      & $python -m coverage json --fail-under=0 -o coverage-release.json
    } "后端覆盖率报告"
    Invoke-Checked {
      & $python (Join-Path $root "scripts\verify-coverage.py") coverage-release.json --line 95 --branch 92
    } "后端覆盖率门禁"
}
finally {
    Pop-Location
}
