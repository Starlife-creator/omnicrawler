[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$BrowsersRoot,
    [string]$CacheRoot = (Join-Path $env:TEMP 'OmniCrawler-1.0-assets'),
    [switch]$SkipOcrModels,
    [switch]$SkipTesseract,
    [switch]$SkipSelenium
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$BrowsersRoot = [IO.Path]::GetFullPath($BrowsersRoot)
$CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
New-Item -ItemType Directory -Path $RuntimeRoot, $CacheRoot -Force | Out-Null

function Get-Asset([string]$Uri, [string]$Destination, [int64]$MinimumBytes = 1024) {
    if ((Test-Path -LiteralPath $Destination) -and (Get-Item -LiteralPath $Destination).Length -ge $MinimumBytes) {
        Write-Host "Reuse: $Destination"
        return
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    $maxRetries = 3
    for ($i = 1; $i -le $maxRetries; $i++) {
        Write-Host "Download (attempt $i/$maxRetries): $Uri"
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $Destination -TimeoutSec 300
            break
        } catch {
            if ($i -eq $maxRetries) { throw }
            Write-Warning "Attempt $i failed: $($_.Exception.Message). Retrying in 10s..."
            Start-Sleep -Seconds 10
        }
    }
    if ((Get-Item -LiteralPath $Destination).Length -lt $MinimumBytes) {
        throw "Downloaded file is unexpectedly small: $Destination"
    }
}

$chrome = Get-ChildItem -LiteralPath $BrowsersRoot -Recurse -File -Filter 'chrome.exe' |
    Where-Object { $_.FullName -match 'chromium-[^\\]+\\chrome-win(?:64)?\\chrome\.exe$' } |
    Select-Object -First 1
if (-not $chrome) {
    throw "Playwright Chromium was not found under $BrowsersRoot"
}

if (-not $SkipSelenium) {
    Write-Host 'Prepare matching ChromeDriver...'
    $manager = (& $Python -c "import os, selenium; print(os.path.join(os.path.dirname(selenium.__file__), 'webdriver', 'common', 'windows', 'selenium-manager.exe'))").Trim()
    if (-not (Test-Path -LiteralPath $manager)) {
        throw "Selenium Manager was not found: $manager"
    }
    $seleniumCache = Join-Path $CacheRoot 'selenium'
    $managerOutput = & $manager --browser chrome --browser-path $chrome.FullName `
        --cache-path $seleniumCache --avoid-browser-download --skip-driver-in-path `
        --skip-browser-in-path --avoid-stats --timeout 600 --output JSON
    if ($LASTEXITCODE -ne 0) {
        throw 'Selenium Manager failed.'
    }
    $managerReport = ($managerOutput -join "`n") | ConvertFrom-Json
    $driverSource = [string]$managerReport.result.driver_path
    if (-not (Test-Path -LiteralPath $driverSource)) {
        throw "ChromeDriver was not downloaded: $driverSource"
    }
    $seleniumRoot = Join-Path $RuntimeRoot 'selenium'
    New-Item -ItemType Directory -Path $seleniumRoot -Force | Out-Null
    Copy-Item -LiteralPath $driverSource -Destination (Join-Path $seleniumRoot 'chromedriver.exe') -Force
}

if (-not $SkipTesseract) {
    Write-Host 'Prepare Tesseract 5 portable runtime...'
    $tesseractInstaller = Join-Path $CacheRoot 'tesseract-ocr-w64-setup-5.5.0.20241111.exe'
    $sevenR = Join-Path $CacheRoot '7zr.exe'
    $sevenInstaller = Join-Path $CacheRoot '7z2602-x64.exe'
    $sevenRoot = Join-Path $CacheRoot '7zip-full'
    $tesseractExtract = Join-Path $CacheRoot 'tesseract-extracted'
    Get-Asset 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe' $tesseractInstaller 10000000
    Get-Asset 'https://github.com/ip7z/7zip/releases/download/26.02/7zr.exe' $sevenR 500000
    Get-Asset 'https://github.com/ip7z/7zip/releases/download/26.02/7z2602-x64.exe' $sevenInstaller 1000000
    New-Item -ItemType Directory -Path $sevenRoot, $tesseractExtract -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $sevenRoot '7z.exe'))) {
        & $sevenR x $sevenInstaller ("-o$sevenRoot") -y | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the 7-Zip build helper.' }
    }
    $seven = Join-Path $sevenRoot '7z.exe'
    & $seven x $tesseractInstaller ("-o$tesseractExtract") -y | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract Tesseract.' }

    $tesseractRoot = Join-Path $RuntimeRoot 'tesseract'
    $tessdataRoot = Join-Path $tesseractRoot 'tessdata'
    New-Item -ItemType Directory -Path $tesseractRoot, $tessdataRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $tesseractExtract 'tesseract.exe') -Destination $tesseractRoot -Force
    Get-ChildItem -LiteralPath $tesseractExtract -File -Filter '*.dll' |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $tesseractRoot -Force }
    foreach ($language in @('eng', 'chi_sim', 'osd')) {
        Get-Asset ("https://github.com/tesseract-ocr/tessdata_fast/raw/main/$language.traineddata") `
            (Join-Path $tessdataRoot "$language.traineddata") 100000
    }
    & (Join-Path $tesseractRoot 'tesseract.exe') --tessdata-dir $tessdataRoot --list-langs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Tesseract language verification failed.' }
}

if (-not $SkipOcrModels) {
    Write-Host 'Prepare and verify all PPStructureV3 offline models...'
    $modelRoot = Join-Path $RuntimeRoot 'models\paddlex'
    & $Python (Join-Path $ProjectRoot 'tools\download_ocr_models.py') $modelRoot --source aistudio
    if ($LASTEXITCODE -ne 0) { throw 'PaddleOCR model verification failed.' }
}

$files = Get-ChildItem -LiteralPath $RuntimeRoot -Recurse -File
$manifest = [ordered]@{
    schema = 1
    generated_at = [DateTime]::UtcNow.ToString('o')
    chrome = $chrome.FullName
    files = $files.Count
    bytes = ($files | Measure-Object Length -Sum).Sum
    sha256 = @($files | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($RuntimeRoot.Length + 1).Replace('\', '/')
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    })
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $RuntimeRoot 'runtime-manifest.json') -Encoding utf8
Write-Host "Windows runtime ready: $RuntimeRoot"
