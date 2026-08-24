param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$source = Get-Item -LiteralPath $SourcePath -ErrorAction Stop
if (-not $source.PSIsContainer -and $source.Length -gt 0) {
    $output = [IO.Path]::GetFullPath($OutputDirectory)
    $artifactsRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\artifacts'))
    if (-not $output.StartsWith($artifactsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'CLOUDSTUDIO_OUTPUT_OUTSIDE_ARTIFACTS'
    }
    if (Test-Path -LiteralPath $output) {
        throw 'CLOUDSTUDIO_OUTPUT_ALREADY_EXISTS'
    }
} else {
    throw 'CLOUDSTUDIO_SOURCE_INVALID'
}

$extension = $source.Extension.ToLowerInvariant()
$contentTypes = @{
    '.exe' = 'application/vnd.microsoft.portable-executable'
    '.deb' = 'application/vnd.debian.binary-package'
    '.rpm' = 'application/x-rpm'
    '.pkg' = 'application/octet-stream'
    '.partyops-modelpack' = 'application/zip'
    '.partyops-update' = 'application/zip'
    '.json' = 'application/json; charset=utf-8'
}
$contentType = $contentTypes[$extension]
if (-not $contentType) {
    throw "CLOUDSTUDIO_EXTENSION_UNSUPPORTED: $extension"
}

$sha256 = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$stream = [IO.File]::OpenRead($source.FullName)
try {
    $magic = [byte[]]::new(4)
    if ($stream.Read($magic, 0, $magic.Length) -ne $magic.Length) {
        throw 'CLOUDSTUDIO_SOURCE_TOO_SHORT'
    }
}
finally {
    $stream.Dispose()
}
$magicHex = [Convert]::ToHexString($magic).ToLowerInvariant()

[IO.Directory]::CreateDirectory($output) | Out-Null
[IO.File]::Copy((Join-Path $PSScriptRoot 'cloudstudio-chunk-server.js'), (Join-Path $output '_serve.js'))
$metadata = [ordered]@{
    version = $Version
    file_name = $source.Name
    bytes = [long]$source.Length
    sha256 = $sha256
    magic_hex = $magicHex
    content_type = $contentType
}
[IO.File]::WriteAllText(
    (Join-Path $output 'upload-metadata.json'),
    ($metadata | ConvertTo-Json -Depth 5),
    [Text.UTF8Encoding]::new($false)
)

$tokenBytes = [byte[]]::new(32)
[Security.Cryptography.RandomNumberGenerator]::Fill($tokenBytes)
$token = [Convert]::ToBase64String($tokenBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$tokenPath = Join-Path $output '.upload-token'
[IO.File]::WriteAllText($tokenPath, $token, [Text.UTF8Encoding]::new($false))
& icacls.exe $tokenPath /inheritance:r /grant:r "$([Security.Principal.WindowsIdentity]::GetCurrent().Name):(R,W)" | Out-Null

$index = '<!doctype html><meta charset="utf-8"><title>PartyOps 下载服务</title><p>PartyOps 冻结制品下载服务。</p>'
[IO.File]::WriteAllText((Join-Path $output 'index.html'), $index, [Text.UTF8Encoding]::new($false))
Write-Output ($metadata | ConvertTo-Json -Depth 5 -Compress)
