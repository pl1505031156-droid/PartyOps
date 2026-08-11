$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$gitleaksCommand = Get-Command "gitleaks" -ErrorAction SilentlyContinue
if (-not $gitleaksCommand) {
  throw "未找到 gitleaks。请从 gitleaks/gitleaks 官方 Release 安装固定版本后重试。"
}
$gitleaks = $gitleaksCommand.Source
$config = Join-Path $root ".gitleaks.toml"

# 避免 Windows Git 的全局 Office textconv 把外部工具缺失误报成扫描失败；
# gitleaks 仍直接扫描 Git 对象和当前目录中的普通/二进制内容。
$previousGitConfigSystem = $env:GIT_CONFIG_SYSTEM
$env:GIT_CONFIG_SYSTEM = if ($env:OS -eq "Windows_NT") { "NUL" } else { "/dev/null" }
try {
  & $gitleaks git --redact --no-banner --config $config --exit-code 1 $root
  if ($LASTEXITCODE -ne 0) { throw "Git 历史凭据扫描失败。" }
  & $gitleaks dir --redact --no-banner --config $config --exit-code 1 $root
  if ($LASTEXITCODE -ne 0) { throw "工作区凭据扫描失败。" }
}
finally {
  if ($null -eq $previousGitConfigSystem) {
    Remove-Item Env:GIT_CONFIG_SYSTEM -ErrorAction SilentlyContinue
  }
  else {
    $env:GIT_CONFIG_SYSTEM = $previousGitConfigSystem
  }
}

Write-Host "Git 历史与工作区凭据扫描通过。"
