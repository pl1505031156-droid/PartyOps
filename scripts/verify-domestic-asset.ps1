[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://')]
    [string]$Url,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [long]::MaxValue)]
    [long]$ExpectedBytes,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ExpectedSha256,

    [Parameter(Mandatory = $true)]
    [ValidateSet('modelpack', 'update')]
    [string]$AssetType,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z._-]+$')]
    [string]$ExpectedFileName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedSuffix = @{
    modelpack = '.partyops-modelpack'
    update = '.partyops-update'
}[$AssetType]
if (-not $ExpectedFileName.EndsWith($expectedSuffix, [StringComparison]::Ordinal)) {
    throw "制品类型与文件名不一致：$ExpectedFileName。"
}

function Assert-ControlledUri {
    param([Parameter(Mandatory = $true)][Uri]$Uri)

    $decodedPath = [Uri]::UnescapeDataString($Uri.AbsolutePath)
    $expectedPath = "/downloads/$ExpectedFileName"
    if (-not $decodedPath.Equals($expectedPath, [StringComparison]::Ordinal)) {
        throw "下载地址不在受控路径或文件名不一致：期望 $expectedPath，实际 $decodedPath。"
    }
    if ($Uri.Query -or $Uri.UserInfo) {
        throw '下载地址不得包含查询参数或用户凭据。'
    }
}

function Assert-Response {
    param([Parameter(Mandatory = $true)][Net.Http.HttpResponseMessage]$Response)

    if (-not $Response.IsSuccessStatusCode) {
        throw "HTTP 请求失败：$([int]$Response.StatusCode) $($Response.ReasonPhrase)"
    }
    $contentType = [string]$Response.Content.Headers.ContentType
    if ($contentType -match 'text/html') {
        throw "下载地址返回了 HTML，而不是冻结制品：$contentType"
    }
    Assert-ControlledUri -Uri $Response.RequestMessage.RequestUri
}

$initialUri = [Uri]$Url
Assert-ControlledUri -Uri $initialUri
$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $true
$client = [Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromMinutes(60)
$client.DefaultRequestHeaders.UserAgent.ParseAdd('PartyOps-Release-Asset-Verification/1.0')

try {
    $lastError = $null
    $verified = $null
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            $response = $client.GetAsync(
                $Url,
                [Net.Http.HttpCompletionOption]::ResponseHeadersRead
            ).GetAwaiter().GetResult()
            try {
                $status = [int]$response.StatusCode
                if ($status -eq 429 -and $attempt -lt 2) {
                    Start-Sleep -Seconds 20
                    continue
                }
                if ($status -ge 500 -and $attempt -lt 2) {
                    Start-Sleep -Seconds 2
                    continue
                }
                Assert-Response -Response $response
                $declared = $response.Content.Headers.ContentLength
                if ($null -ne $declared -and $declared -ne $ExpectedBytes) {
                    throw "Content-Length 不一致：期望 $ExpectedBytes，实际 $declared。"
                }

                $stream = $response.Content.ReadAsStream()
                $hash = [Security.Cryptography.SHA256]::Create()
                try {
                    $buffer = [byte[]]::new(1024 * 1024)
                    $prefix = [byte[]]::new(4)
                    [long]$total = 0
                    while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                        if ($total -lt 4) {
                            $copy = [Math]::Min(4 - [int]$total, $read)
                            [Array]::Copy($buffer, 0, $prefix, [int]$total, $copy)
                        }
                        [void]$hash.TransformBlock($buffer, 0, $read, $null, 0)
                        $total += $read
                    }
                    [void]$hash.TransformFinalBlock([byte[]]::new(0), 0, 0)
                    $actualSha = [Convert]::ToHexString($hash.Hash).ToLowerInvariant()
                    $magic = [Convert]::ToHexString($prefix).ToLowerInvariant()
                }
                finally {
                    $hash.Dispose()
                    $stream.Dispose()
                }

                if ($total -ne $ExpectedBytes) {
                    throw "完整回读长度不一致：期望 $ExpectedBytes，实际 $total。"
                }
                if ($actualSha -ne $ExpectedSha256.ToLowerInvariant()) {
                    throw "完整回读 SHA-256 不一致：$actualSha。"
                }
                if ($magic -notin @('504b0304', '504b0506', '504b0708')) {
                    throw "冻结制品不是有效 ZIP 文件头：$magic。"
                }
                $verified = [ordered]@{
                    type = $AssetType
                    filename = $ExpectedFileName
                    url = $response.RequestMessage.RequestUri.AbsoluteUri
                    bytes = $total
                    sha256 = $actualSha
                    magic_hex = $magic
                    verified_at = [DateTimeOffset]::Now.ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd HH:mm:ss') + '（北京时间，UTC+8）'
                }
            }
            finally {
                $response.Dispose()
            }
            break
        }
        catch {
            $lastError = $_
            if ($attempt -lt 2) {
                Start-Sleep -Seconds 2
            }
        }
    }
    if ($null -eq $verified) {
        throw $lastError
    }
    $verified | ConvertTo-Json -Depth 10
}
finally {
    $client.Dispose()
    $handler.Dispose()
}
