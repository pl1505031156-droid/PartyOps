param(
  [int]$Port = 18940,
  [string]$DataRoot = "",
  [string]$PythonPath = "",
  [switch]$Source
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = if ($Source) {
  ""
} else {
  (Resolve-Path (Join-Path $projectRoot "artifacts\PartyOps-1.4.5-rc.2-windows-amd64")).Path
}
if (-not $DataRoot) { $DataRoot = Join-Path $projectRoot ".run-chrome-141" }
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null

$env:PARTYOPS_MODE = "host"
$env:PARTYOPS_ENVIRONMENT = "test"
$env:PARTYOPS_HOST = "127.0.0.1"
$env:PARTYOPS_PORT = [string]$Port
$env:PARTYOPS_AGENT_PORT = [string]($Port + 1)
$env:PARTYOPS_DATA_DIR = $DataRoot
$env:PARTYOPS_FRONTEND_DIST = Join-Path $projectRoot "frontend\dist\client"
$env:PARTYOPS_STRICT_SQLITE = if ($Source) { "false" } else { "true" }
$env:PARTYOPS_TLS_ENABLED = "false"
$env:PARTYOPS_SEED_DEMO = "true"
$stdout = Join-Path $DataRoot "stdout.log"
$stderr = Join-Path $DataRoot "stderr.log"
$occupied = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($occupied) {
  $owner = ($occupied | Select-Object -First 1).OwningProcess
  throw "QA 端口 $Port 已被进程 $owner 占用，请通过 -Port 指定空闲端口。"
}
$executable = if ($Source) {
  if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { Join-Path $projectRoot ".venv\Scripts\python.exe" }
} else {
  Join-Path $runtimeRoot "PartyOps.exe"
}
if (-not (Test-Path -LiteralPath $executable)) {
  throw "QA 运行时不存在：$executable"
}
$workingDirectory = if ($Source) { Join-Path $projectRoot "backend" } else { $runtimeRoot }
$arguments = if ($Source) { @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", [string]$Port) } else { @() }
$startParameters = @{
  FilePath = $executable
  WorkingDirectory = $workingDirectory
  WindowStyle = "Hidden"
  RedirectStandardOutput = $stdout
  RedirectStandardError = $stderr
  PassThru = $true
}
# Windows PowerShell 5 会拒绝空 ArgumentList；冻结主程序本来就不需要参数。
if ($arguments.Count -gt 0) { $startParameters.ArgumentList = $arguments }
$process = Start-Process @startParameters

$health = $null
for ($attempt = 0; $attempt -lt 60; $attempt++) {
  if ($process.HasExited) { break }
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 2
    if ($health.status -eq "ok") { break }
  }
  catch {
    Start-Sleep -Milliseconds 500
  }
}
if (-not $health -or $health.status -ne "ok") {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  $detail = Get-Content -Raw $stderr -ErrorAction SilentlyContinue
  throw "QA 预览未通过健康检查：$detail"
}
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
  Where-Object { $Source -or $_.OwningProcess -eq $process.Id } |
  Select-Object -First 1
# venv 的 python.exe 在部分 Windows 环境会把实际解释器交给子进程，
# 因而源码 QA 以“启动前端口空闲 + 当前端口健康”作为归属证据；冻结候选
# 仍严格要求监听 PID 与本次启动 PID 一致。
if ((-not $Source -and $process.HasExited) -or -not $listener) {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  throw "QA 健康响应不属于本次启动的冻结主程序（PID $($process.Id)）。"
}

[pscustomobject]@{
  pid = $process.Id
  url = "http://127.0.0.1:$Port"
  data_root = $DataRoot
  app_version = $health.app_version
  sqlite_version = $health.sqlite.version
} | ConvertTo-Json -Depth 3
