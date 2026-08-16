function Expand-VerifiedPartyOpsOcrRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Destination
  )

  $archive = Join-Path $RepoRoot "vendor\windows\ocr\tesseract-5.5.3-windows-amd64.zip"
  $expectedArchiveSha256 = "57825338CEAA141C617F66D2A2210B6BEF396436FFC83D242595E5F5F33BF462"
  if (-not (Test-Path -LiteralPath $archive)) {
    throw "缺少固定版本的 Windows 中文 OCR 运行时：$archive"
  }
  $actualArchiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
  if ($actualArchiveSha256 -ne $expectedArchiveSha256) {
    throw "Windows 中文 OCR 运行时 SHA-256 不匹配，拒绝封装来源不明的文件。"
  }

  $artifactsRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "artifacts"))
  $destinationPath = [System.IO.Path]::GetFullPath($Destination)
  if (-not $destinationPath.StartsWith(
      $artifactsRoot + [System.IO.Path]::DirectorySeparatorChar,
      [System.StringComparison]::OrdinalIgnoreCase
    ) -or [System.IO.Path]::GetFileName($destinationPath) -ne "ocr") {
    throw "拒绝向未经验证的目录展开 OCR 运行时：$destinationPath"
  }
  if (Test-Path -LiteralPath $destinationPath) {
    Remove-Item -LiteralPath $destinationPath -Recurse -Force
  }
  Expand-Archive -LiteralPath $archive -DestinationPath $destinationPath -Force

  $tesseract = Join-Path $destinationPath "bin\tesseract.exe"
  $tessdata = Join-Path $destinationPath "tessdata"
  foreach ($required in @(
      $tesseract,
      (Join-Path $tessdata "chi_sim.traineddata"),
      (Join-Path $tessdata "eng.traineddata"),
      (Join-Path $tessdata "osd.traineddata"),
      (Join-Path $destinationPath "licenses\LICENSE-tesseract.txt"),
      (Join-Path $destinationPath "licenses\LICENSE-tessdata-fast.txt"),
      (Join-Path $destinationPath "SOURCE.json")
    )) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "Windows 中文 OCR 运行时缺少必需文件：$required"
    }
  }
  if (Get-ChildItem -LiteralPath $destinationPath -Recurse -File |
      Where-Object { $_.Name -in @("tesseract-uninstall.exe", "lstmtraining.exe", "text2image.exe") }) {
    throw "Windows OCR 发布载荷混入卸载器或训练工具，拒绝封装。"
  }

  $source = Get-Content -Raw -LiteralPath (Join-Path $destinationPath "SOURCE.json") |
    ConvertFrom-Json
  if ($source.version -ne "5.5.3.20260724" -or
      $source.engine_asset_sha256 -ne "bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4") {
    throw "Windows OCR 来源记录与固定版本不一致。"
  }

  $previousTessdata = $env:TESSDATA_PREFIX
  try {
    $env:TESSDATA_PREFIX = $tessdata
    $versionOutput = & $tesseract --version 2>&1
    if ($LASTEXITCODE -ne 0 -or ($versionOutput -join "`n") -notmatch "tesseract v5\.5\.3") {
      throw "Windows Tesseract 5.5.3 无法启动。"
    }
    $languages = & $tesseract --list-langs 2>&1
    if ($LASTEXITCODE -ne 0 -or $languages -notcontains "chi_sim" -or
        $languages -notcontains "eng") {
      throw "Windows Tesseract 中英文模型无法加载。"
    }
  } finally {
    if ($null -eq $previousTessdata) {
      Remove-Item Env:TESSDATA_PREFIX -ErrorAction SilentlyContinue
    } else {
      $env:TESSDATA_PREFIX = $previousTessdata
    }
  }
}
