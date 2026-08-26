param(
  [string]$RuntimeDir = "",
  [int]$Port = 18940,
  [string]$DataRoot = "",
  [string]$Python = "",
  [switch]$UpgradeFrom0023
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Python) {
  $Python = if ($env:PARTYOPS_PYTHON) { $env:PARTYOPS_PYTHON } else { Join-Path $projectRoot ".venv\Scripts\python.exe" }
}
if (-not (Test-Path -LiteralPath $Python)) {
  throw "未找到用于读取冒烟数据库的 Python：$Python；可通过 -Python 或 PARTYOPS_PYTHON 指定。"
}
if (-not $RuntimeDir) { $RuntimeDir = Join-Path $projectRoot "artifacts\PartyOps-1.4.5-rc.4-windows-amd64" }
$RuntimeDir = (Resolve-Path -LiteralPath $RuntimeDir).Path
if (-not $DataRoot) {
  $DataRoot = Join-Path $projectRoot (".run-win-smoke-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss"))
}
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
$DataRoot = (Resolve-Path -LiteralPath $DataRoot).Path
if ($UpgradeFrom0023) {
  if (Get-ChildItem -LiteralPath $DataRoot -Force | Select-Object -First 1) {
    throw "0023 覆盖升级冒烟目录必须为空：$DataRoot"
  }
  & $Python (Join-Path $projectRoot "scripts\create-0023-upgrade-fixture.py") `
    --repo-root $projectRoot --data-root $DataRoot
  if ($LASTEXITCODE -ne 0) { throw "无法创建真实 0023 覆盖升级基线。" }
}

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
  if ($revision -ne "0024") { throw "冻结主程序数据库版本为 $revision，不是 0024。" }
  if ($UpgradeFrom0023) {
    $fixture = & $Python -c "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute('select display_name from users where id=?', ('rc4-native-upgrade-admin',)).fetchone()[0])" (Join-Path $DataRoot "partyops.db")
    if ($LASTEXITCODE -ne 0 -or $fixture -ne "原生覆盖升级管理员") {
      throw "覆盖升级后原有管理员记录丢失或损坏。"
    }
    $preserved = Join-Path $DataRoot "attachments\preserved.txt"
    if (-not (Test-Path -LiteralPath $preserved) -or
        (Get-Content -Raw -LiteralPath $preserved -Encoding UTF8) -ne "rc4 原生覆盖升级必须保留附件") {
      throw "覆盖升级后原有附件丢失或损坏。"
    }
    if (-not (Get-ChildItem -LiteralPath (Join-Path $DataRoot "backups") -Filter "backup-*.zip" -File | Select-Object -First 1)) {
      throw "覆盖升级未生成经过校验的迁移前备份。"
    }
  }
  if ([version]$health.sqlite.version -lt [version]"3.51.3") {
    throw "冻结主程序 SQLite 为 $($health.sqlite.version)，低于 3.51.3。"
  }
  [pscustomobject]@{
    status = $health.status
    app_version = $health.app_version
    sqlite_version = $health.sqlite.version
    schema_revision = $revision
    upgrade_from_0023 = [bool]$UpgradeFrom0023
    data_root = $DataRoot
  } | ConvertTo-Json -Depth 3
} finally {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit()
  }
}
