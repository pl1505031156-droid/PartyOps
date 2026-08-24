param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$RuntimePath,

    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ModelPath,

    [Parameter(Mandatory = $true)]
    [string]$ModelId,

    [ValidateRange(1024, 65535)]
    [int]$Port = 18876,

    [ValidateRange(1, 16)]
    [int]$Threads = 4,

    [ValidateRange(32, 4096)]
    [int]$MaxTokens = 64
)

$ErrorActionPreference = "Stop"
$evidenceRoot = Join-Path $PSScriptRoot "..\.tmp\model-runtime"
New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
$safeName = ($ModelId -replace "[^0-9A-Za-z._-]", "-")
$stdoutPath = Join-Path $evidenceRoot "$safeName-stdout.log"
$stderrPath = Join-Path $evidenceRoot "$safeName-stderr.log"
$process = Start-Process `
    -FilePath $RuntimePath `
    -ArgumentList @(
        "--model", $ModelPath,
        "--host", "127.0.0.1",
        "--port", [string]$Port,
        "--ctx-size", "4096",
        "--threads", [string]$Threads,
        "--parallel", "1"
    ) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

$peakWorkingSet = 0L
$ready = $false
$startedAt = Get-Date
try {
    for ($attempt = 0; $attempt -lt 120; $attempt += 1) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) {
            throw "llama-server 提前退出，退出码 $($process.ExitCode)；请检查 $stderrPath"
        }
        if ($process.PeakWorkingSet64 -gt $peakWorkingSet) {
            $peakWorkingSet = $process.PeakWorkingSet64
        }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        }
        catch {
            # 模型载入期间端口尚未就绪，继续观察精确进程。
        }
    }
    if (-not $ready) {
        throw "llama-server 健康检查超时；请检查 $stderrPath"
    }

    $body = @{
        model = $ModelId
        temperature = 0.2
        max_tokens = $MaxTokens
        chat_template_kwargs = @{ enable_thinking = $false }
        messages = @(
            @{ role = "system"; content = "你是只读本地助手。" },
            @{ role = "user"; content = "请用一句中文说明党务资料应先核对来源。" }
        )
    } | ConvertTo-Json -Depth 6 -Compress
    $responseTimer = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:$Port/v1/chat/completions" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec 180
    $responseTimer.Stop()
    $process.Refresh()
    if ($process.PeakWorkingSet64 -gt $peakWorkingSet) {
        $peakWorkingSet = $process.PeakWorkingSet64
    }
    $content = ([string]$response.choices[0].message.content).Trim()
    if (-not $content) {
        throw "模型返回空内容"
    }

    [pscustomobject]@{
        model = $ModelId
        ready = $ready
        response_nonempty = $true
        startup_seconds = [math]::Round(((Get-Date) - $startedAt).TotalSeconds - $responseTimer.Elapsed.TotalSeconds, 3)
        response_seconds = [math]::Round($responseTimer.Elapsed.TotalSeconds, 3)
        peak_working_set_mb = [math]::Ceiling($peakWorkingSet / 1MB)
        response = $content
        stderr_log = $stderrPath
    } | ConvertTo-Json -Compress
}
finally {
    if (-not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
}
