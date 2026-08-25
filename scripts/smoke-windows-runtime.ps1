param(
  [string]$RuntimeDir = "",
  [int]$Port = 18940,
  [string]$DataRoot = "",
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) {
  $Python = if ($env:PARTYOPS_PYTHON) { $env:PARTYOPS_PYTHON } else { Join-Path $projectRoot ".venv\Scripts\python.exe" }
}
if (-not (Test-Path -LiteralPath $Python)) {
  throw "未找到用于读取冒烟数据库的 Python：$Python；可通过 -Python 或 PARTYOPS_PYTHON 指定。"
}
if (-not $RuntimeDir) { $RuntimeDir = Join-Path $projectRoot "artifacts\PartyOps-1.4.5-rc.3-windows-amd64" }
$RuntimeDir = (Resolve-Path -LiteralPath $RuntimeDir).Path
if (-not $DataRoot) {
  $DataRoot = Join-Path $projectRoot (".run-win-smoke-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss"))
}
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
$DataRoot = (Resolve-Path -LiteralPath $DataRoot).Path

$env:PARTYOPS_MODE = "host"
$env:PARTYOPS_ENVIRONMENT = "test"
$env:PARTYOPS_HOST = "127.0.0.1"
$env:PARTYOPS_PORT = [string]$Port
$env:PARTYOPS_AGENT_PORT = [string]($Port + 1)
$env:PARTYOPS_DATA_DIR = $DataRoot
$env:PARTYOPS_STRICT_SQLITE = "true"
$env:PARTYOPS_TLS_ENABLED = "false"
$env:PARTYOPS_SEED_DEMO = "false"
$stdout = Join-Path $DataRoot "stdout.log"
$stderr = Join-Path $DataRoot "stderr.log"
$executable = Join-Path $RuntimeDir "PartyOps.exe"

# 冒烟端口必须独占。否则可能把另一套正在运行的 PartyOps 健康响应
# 误判为当前候选运行时的结果，继而在错误的数据目录上读取迁移版本。
$occupied = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($occupied) {
  $owner = ($occupied | Select-Object -First 1).OwningProcess
  throw "冒烟端口 $Port 已被进程 $owner 占用，请通过 -Port 指定空闲端口。"
}
$process = Start-Process -FilePath $executable -WorkingDirectory $RuntimeDir -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$health = $null
try {
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($process.HasExited) { break }
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2
      if ($health.status -eq "ok") { break }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $health -or $health.status -ne "ok") {
    $detail = Get-Content -Raw $stderr -ErrorAction SilentlyContinue
    throw "冻结主程序未通过健康检查。日志：$detail"
  }
  if ($process.HasExited) {
    throw "冻结主程序在健康检查后提前退出，退出码为 $($process.ExitCode)。"
  }
  $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq $process.Id } |
    Select-Object -First 1
  if (-not $listener) {
    throw "端口 $Port 的健康响应不属于本次启动的冻结主程序（PID $($process.Id)）。"
  }
  $revision = & $Python -c "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute('select version_num from alembic_version').fetchone()[0])" (Join-Path $DataRoot "partyops.db")
  if ($LASTEXITCODE -ne 0) { throw "无法读取冒烟数据库迁移版本。" }
  if ($revision -ne "0023") { throw "冻结主程序数据库版本为 $revision，不是 0023。" }
  if ([version]$health.sqlite.version -lt [version]"3.51.3") {
    throw "冻结主程序 SQLite 为 $($health.sqlite.version)，低于 3.51.3。"
  }
  [pscustomobject]@{
    status = $health.status
    app_version = $health.app_version
    sqlite_version = $health.sqlite.version
    schema_revision = $revision
    data_root = $DataRoot
  } | ConvertTo-Json -Depth 3
} finally {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit()
  }
}
