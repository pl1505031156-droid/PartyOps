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
    [ValidateSet('exe', 'deb', 'rpm', 'pkg')]
    [string]$PackageType,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ExpectedFileName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:[-.][0-9A-Za-z.]+)?$')]
    [string]$ExpectedVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedMagic = @{
    exe = '4D5A'
    deb = '213C617263683E0A'
    rpm = 'EDABEEDB'
    pkg = '78617221'
}[$PackageType]

$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $true
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromMinutes(30)
$client.DefaultRequestHeaders.UserAgent.ParseAdd('PartyOps-Release-Verification/1.0')

function Assert-ControlledDownloadUri {
    param(
        [Parameter(Mandatory = $true)]
        [Uri]$Uri
    )

    $decodedPath = [Uri]::UnescapeDataString($Uri.AbsolutePath)
    $expectedPath = "/downloads/$ExpectedFileName"
    if (-not $decodedPath.Equals($expectedPath, [StringComparison]::Ordinal)) {
        throw "下载地址不在受控路径或文件名不一致：期望 $expectedPath，实际 $decodedPath。"
    }
    $fileVersion = $ExpectedVersion
    if ($PackageType -eq 'rpm' -and $ExpectedVersion.Contains('-')) {
        $parts = $ExpectedVersion.Split('-', 2)
        $fileVersion = "$($parts[0])-0.$($parts[1])"
    }
    if ($ExpectedFileName.IndexOf($fileVersion, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "冻结文件名未包含期望版本映射 $fileVersion：$ExpectedFileName。"
    }
}

Assert-ControlledDownloadUri -Uri ([Uri]$Url)

function Assert-PackageResponse {
    param(
        [Parameter(Mandatory = $true)]
        [System.Net.Http.HttpResponseMessage]$Response
    )

    if (-not $Response.IsSuccessStatusCode) {
        throw "HTTP 请求失败：$([int]$Response.StatusCode) $($Response.ReasonPhrase)"
    }

    $contentType = [string]$Response.Content.Headers.ContentType
    if ($contentType -match 'text/html') {
        throw "下载地址返回了 HTML，而不是安装包：$contentType"
    }
}

function Read-PrefixHex {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.Stream]$Stream,

        [Parameter(Mandatory = $true)]
        [int]$Count
    )

    $buffer = [byte[]]::new($Count)
    $offset = 0
    while ($offset -lt $Count) {
        $read = $Stream.Read($buffer, $offset, $Count - $offset)
        if ($read -eq 0) {
            break
        }
        $offset += $read
    }

    if ($offset -eq 0) {
        throw '下载响应为空。'
    }

    return [Convert]::ToHexString($buffer, 0, $offset)
}

try {
    # 先独立验证 Range，防止下载按钮实际指向错误页或不支持断点续传的中间层。
    $rangeRequest = [System.Net.Http.HttpRequestMessage]::new(
        [System.Net.Http.HttpMethod]::Get,
        $Url
    )
    $rangeRequest.Headers.Range = [System.Net.Http.Headers.RangeHeaderValue]::new(0, 7)
    $rangeResponse = $client.SendAsync(
        $rangeRequest,
        [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
    ).GetAwaiter().GetResult()
    try {
        Assert-PackageResponse -Response $rangeResponse
        Assert-ControlledDownloadUri -Uri $rangeResponse.RequestMessage.RequestUri
        $rangeStatus = [int]$rangeResponse.StatusCode
        $rangeSupported = $rangeStatus -eq 206
        $rangeLength = $rangeResponse.Content.Headers.ContentLength
        if ($rangeSupported) {
            if ($rangeLength -ne 8) {
                throw "Range 响应长度应为 8，实际为 $rangeLength。"
            }

            if (
                $null -eq $rangeResponse.Content.Headers.ContentRange -or
                $rangeResponse.Content.Headers.ContentRange.Length -ne $ExpectedBytes
            ) {
                throw 'Range 响应中的文件总长度与冻结清单不一致。'
            }
        }
        elseif ($rangeStatus -eq 200) {
            # 部分国内静态托管层会忽略 Range。此时继续读取前缀并在下方执行
            # 完整流式校验，但如实记录不支持断点续传，不能宣称已返回 206。
            if ($null -ne $rangeLength -and $rangeLength -ne $ExpectedBytes) {
                throw "服务器忽略 Range，且完整 Content-Length 与冻结清单不一致：$rangeLength。"
            }
        }
        else {
            throw "Range 探测返回了不支持的状态码：$rangeStatus。"
        }

        $rangeStream = $rangeResponse.Content.ReadAsStream()
        try {
            $rangeMagic = Read-PrefixHex -Stream $rangeStream -Count 8
        }
        finally {
            $rangeStream.Dispose()
        }
    }
    finally {
        $rangeResponse.Dispose()
        $rangeRequest.Dispose()
    }

    if (-not $rangeMagic.StartsWith($expectedMagic, [StringComparison]::OrdinalIgnoreCase)) {
        throw "文件头不匹配：期望 $expectedMagic，实际 $rangeMagic。"
    }

    $lastError = $null
    $fullResult = $null
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        try {
            $response = $client.GetAsync(
                $Url,
                [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead
            ).GetAwaiter().GetResult()
            try {
                if ([int]$response.StatusCode -eq 429) {
                    if ($attempt -lt 2) {
                        Start-Sleep -Seconds 20
                        continue
                    }
                }

                if ([int]$response.StatusCode -ge 500 -and $attempt -lt 2) {
                    Start-Sleep -Seconds 2
                    continue
                }

                Assert-PackageResponse -Response $response
                Assert-ControlledDownloadUri -Uri $response.RequestMessage.RequestUri
                if (
                    $null -ne $response.Content.Headers.ContentLength -and
                    $response.Content.Headers.ContentLength -ne $ExpectedBytes
                ) {
                    throw "Content-Length 不一致：期望 $ExpectedBytes，实际 $($response.Content.Headers.ContentLength)。"
                }

                $stream = $response.Content.ReadAsStream()
                $hash = [System.Security.Cryptography.IncrementalHash]::CreateHash(
                    [System.Security.Cryptography.HashAlgorithmName]::SHA256
                )
                try {
                    $buffer = [byte[]]::new(1024 * 1024)
                    $prefix = [System.Collections.Generic.List[byte]]::new(8)
                    [long]$total = 0
                    while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                        $hash.AppendData($buffer, 0, $read)
                        if ($prefix.Count -lt 8) {
                            $take = [Math]::Min(8 - $prefix.Count, $read)
                            for ($index = 0; $index -lt $take; $index++) {
                                $prefix.Add($buffer[$index])
                            }
                        }
                        $total += $read
                    }

                    $actualHash = [Convert]::ToHexString($hash.GetHashAndReset()).ToLowerInvariant()
                    $actualMagic = [Convert]::ToHexString($prefix.ToArray())
                }
                finally {
                    $hash.Dispose()
                    $stream.Dispose()
                }

                if ($total -ne $ExpectedBytes) {
                    throw "完整下载字节数不一致：期望 $ExpectedBytes，实际 $total。"
                }
                if ($actualHash -ne $ExpectedSha256.ToLowerInvariant()) {
                    throw "SHA-256 不一致：期望 $ExpectedSha256，实际 $actualHash。"
                }
                if (-not $actualMagic.StartsWith($expectedMagic, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "完整下载文件头不匹配：期望 $expectedMagic，实际 $actualMagic。"
                }

                $fullResult = [pscustomobject]@{
                    url = $Url
                    final_url = [string]$response.RequestMessage.RequestUri
                    status = [int]$response.StatusCode
                    content_type = [string]$response.Content.Headers.ContentType
                    content_length_header = $response.Content.Headers.ContentLength
                    bytes = $total
                    sha256 = $actualHash
                    magic = $actualMagic
                    range_status = $rangeStatus
                    range_supported = $rangeSupported
                    range_magic = $rangeMagic
                    filename = $ExpectedFileName
                    expected_version = $ExpectedVersion
                    verified_at = "$([DateTimeOffset]::Now.ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd HH:mm:ss'))（北京时间，UTC+8）"
                    verified = $true
                }
                break
            }
            finally {
                $response.Dispose()
            }
        }
        catch {
            $lastError = $_
            if ($attempt -lt 2) {
                Start-Sleep -Seconds 2
                continue
            }
        }
    }

    if ($null -eq $fullResult) {
        throw $lastError
    }

    $fullResult | ConvertTo-Json -Compress
}
finally {
    $client.Dispose()
    $handler.Dispose()
}
