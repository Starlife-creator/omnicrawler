[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$BrowsersRoot,
    # F18：缓存目录带资产版本号，跨版本不再静默复用旧资产
    [string]$CacheRoot = (Join-Path $env:TEMP 'OmniCrawler-runtime-assets-v1'),
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

# S4.3.3：第三方二进制已知哈希表（资产完整路径 → SHA-256）。
# 构建前必须由维护者填充/复核；`Get-Asset -RequireKnownHash` 的资产
# 在表中找不到哈希时拒绝下载（fail-closed），不再"首次下载后自行记录"。
# 获取方式：下载后运行 Get-FileHash -Algorithm SHA256。
#
# 哈希钉责任分工（B12-005）——哪些资产由谁钉哈希：
#   - 本表 `$KNOWN_SHA256`：仅覆盖 3 个**手动 GitHub 资产**（tesseract 安装器、
#     7zr、7z 解压器），由本脚本维护者钉死，任何更新必须同步复核哈希。
#   - Chromium / chromedriver：由 Python 侧 Playwright `install chromium`
#     管理（Playwright 自身维护二进制哈希），**不在本表**、不在此钉。
#   - PaddleOCR 模型：由 `tools/download_and_smoke_test.py` 下载，CDN 分发、
#     **无哈希钉**（仅冒烟验证，见该工具 docstring），属已知责任边界。
#   - 后续新增手动下载资产：必须在本表登记真实哈希，否则 fail-closed 拒绝。
$KNOWN_SHA256 = @{
    # 哈希于 2026-08-11 由官方 release 资产本地下载计算并核对大小登记
    # （tesseract 21,381,872 B / 7zr 602,112 B / 7z2602 1,657,896 B）。
    (Join-Path $CacheRoot 'tesseract-ocr-w64-setup-5.5.0.20241111.exe') = 'f3fc4236425b690c8be756f35793f77394ee004be0a6460a440c754d892f68bc'
    (Join-Path $CacheRoot '7zr.exe')                                   = '56b8cc9f4971cef253644fafe54063ed7fdca551d4dee0f8c6baa81b855acd72'
    (Join-Path $CacheRoot '7z2602-x64.exe')                            = '6745fa76dc2ea031596d8678f6f6b99c3c1b435b4164a63485adbbc7b8d82ef0'
}

function Get-Asset([string]$Uri, [string]$Destination, [int64]$MinimumBytes = 1024, [string]$Sha256 = '', [switch]$RequireKnownHash) {
    # F15/F16：复用前必须通过 SHA-256 校验（显式传入或上次下载记录）；下载走 .part 临时名 + 原子改名，
    # 中断的半截文件永远不会被当作完整资产复用。
    # S4.3.3：RequireKnownHash 且无已知哈希（显式参数或 $KNOWN_SHA256 表）→ 拒绝下载。
    $hashPath = Join-Path (Split-Path -Parent $Destination) '.asset-hashes.json'
    $known = @{}
    if (Test-Path -LiteralPath $hashPath) {
        try {
            $stored = Get-Content -LiteralPath $hashPath -Raw | ConvertFrom-Json
            $stored.PSObject.Properties | ForEach-Object { $known[$_.Name] = [string]$_.Value }
        } catch { $known = @{} }
    }
    if (-not $Sha256 -and $RequireKnownHash -and $KNOWN_SHA256.ContainsKey($Destination)) {
        $Sha256 = [string]$KNOWN_SHA256[$Destination]
    }
    if (-not $Sha256 -and $RequireKnownHash) {
        throw "SHA-256 未知，拒绝下载第三方二进制（fail-closed）: $Destination`n请在 tools/prepare_windows_runtime.ps1 的 `$KNOWN_SHA256 中登记真实哈希"
    }
    if (Test-Path -LiteralPath $Destination) {
        $cached = Get-Item -LiteralPath $Destination
        if ($cached.Length -ge $MinimumBytes) {
            $cachedHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
            $expected = if ($Sha256) { $Sha256 } elseif ($known.ContainsKey($Destination)) { $known[$Destination] } else { '' }
            if ($expected -and $cachedHash -eq $expected) {
                Write-Host "Reuse (hash verified): $Destination"
                return
            }
            if ($expected) {
                Write-Warning "Cache hash mismatch, re-downloading: $Destination"
            }
        }
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    $tempDestination = "$Destination.part"
    $maxRetries = 3
    for ($i = 1; $i -le $maxRetries; $i++) {
        Write-Host "Download (attempt $i/$maxRetries): $Uri"
        try {
            Invoke-WebRequest -Uri $Uri -OutFile $tempDestination -TimeoutSec 300
            break
        } catch {
            if ($i -eq $maxRetries) { throw }
            Write-Warning "Attempt $i failed: $($_.Exception.Message). Retrying in 10s..."
            Start-Sleep -Seconds 10
        }
    }
    if ((Get-Item -LiteralPath $tempDestination).Length -lt $MinimumBytes) {
        Remove-Item -LiteralPath $tempDestination -Force -ErrorAction SilentlyContinue
        throw "Downloaded file is unexpectedly small: $Destination"
    }
    $actual = (Get-FileHash -LiteralPath $tempDestination -Algorithm SHA256).Hash
    $expected = if ($Sha256) { $Sha256 } elseif ($known.ContainsKey($Destination)) { $known[$Destination] } else { '' }
    if ($expected -and $actual -ne $expected) {
        Remove-Item -LiteralPath $tempDestination -Force -ErrorAction SilentlyContinue
        throw "SHA-256 校验失败: $Destination`n  期望: $expected`n  实际: $actual"
    }
    # 记录首次下载的哈希作为后续复用基准（F16：缓存复用不再只比大小）
    $known[$Destination] = $actual
    try { $known | ConvertTo-Json | Set-Content -LiteralPath $hashPath -Encoding utf8 } catch { }
    Move-Item -LiteralPath $tempDestination -Destination $Destination -Force
    Write-Host "Downloaded: $Destination"
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
    Get-Asset 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe' $tesseractInstaller 10000000 -RequireKnownHash
    Get-Asset 'https://github.com/ip7z/7zip/releases/download/26.02/7zr.exe' $sevenR 500000 -RequireKnownHash
    Get-Asset 'https://github.com/ip7z/7zip/releases/download/26.02/7z2602-x64.exe' $sevenInstaller 1000000 -RequireKnownHash
    New-Item -ItemType Directory -Path $sevenRoot, $tesseractExtract -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $sevenRoot '7z.exe'))) {
        & $sevenR x $sevenInstaller ("-o$sevenRoot") -y | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the 7-Zip build helper.' }
    }
    $seven = Join-Path $sevenRoot '7z.exe'
    $tesseractRoot = Join-Path $RuntimeRoot 'tesseract'
    $tessdataRoot = Join-Path $tesseractRoot 'tessdata'
    New-Item -ItemType Directory -Path $tesseractRoot, $tessdataRoot -Force | Out-Null
    # F19：已提取的 tesseract.exe 直接复用，离线重建不再白耗时解压；
    # 但必须 exe 与 DLL 齐备才算可复用（防上次中断留下残缺目录）
    $tessComplete = (Test-Path -LiteralPath (Join-Path $tesseractRoot 'tesseract.exe')) -and
        (Get-ChildItem -LiteralPath $tesseractRoot -Filter '*.dll' -ErrorAction SilentlyContinue)
    if (-not $tessComplete) {
        & $seven x $tesseractInstaller ("-o$tesseractExtract") -y | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract Tesseract.' }
        Copy-Item -LiteralPath (Join-Path $tesseractExtract 'tesseract.exe') -Destination $tesseractRoot -Force
        Get-ChildItem -LiteralPath $tesseractExtract -File -Filter '*.dll' |
            ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $tesseractRoot -Force }
    }
    $requiredLanguages = @('eng', 'chi_sim', 'osd')
    foreach ($language in $requiredLanguages) {
        $langPath = Join-Path $tessdataRoot "$language.traineddata"
        Get-Asset ("https://github.com/tesseract-ocr/tessdata_fast/raw/main/$language.traineddata") $langPath 100000
        # Explicit post-download guard. Get-Asset reuses an existing file when it
        # already meets the minimum size, but a previous interrupted run may have
        # left a zero-byte/partial stub behind; fail loudly instead of letting a
        # half-prepared cache slip through to the build (which then fails much
        # later at the OCR self-test).
        if (-not (Test-Path -LiteralPath $langPath) -or (Get-Item -LiteralPath $langPath).Length -lt 100000) {
            throw "Tesseract language pack missing or corrupt after download: $langPath"
        }
        # A failed download can land an HTML error page (GitHub 404 / rate-limit)
        # that still exceeds the minimum size. Reject anything that does not
        # start with '<' (HTML). NOTE: tessdata_fast ships UNCOMPRESSED LSTM
        # blobs — an earlier "must be gzip (1F 8B)" magic check wrongly rejected
        # valid eng.traineddata (4,113,088 B, loads fine in tesseract). Real
        # usability is verified afterwards via --list-langs and the F17 OCR probe.
        $magic = Get-Content -LiteralPath $langPath -Encoding Byte -TotalCount 2 -ErrorAction SilentlyContinue
        if (-not ($magic -and $magic[0] -ne 0x3C)) {
            throw "Tesseract language pack looks like an HTML error page: $langPath"
        }
    }
    & (Join-Path $tesseractRoot 'tesseract.exe') --tessdata-dir $tessdataRoot --list-langs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Tesseract language verification failed.' }
    # F17：不仅列语言，还要用 chi_sim 实际识别合成图——语言包结构损坏但 gzip 头正常时也能暴露。
    # 实测（本机 Windows PowerShell 5.1）：$ErrorActionPreference='Stop' 下原生 stderr
    # （tesseract 的 "Estimating resolution..." 正常输出）无论 2>&1 还是 2>$null 都会抛
    # NativeCommandError；唯一有效的是临时切 SilentlyContinue。
    $probePng = Join-Path $env:TEMP 'omnicrawler_ocr_probe.png'
    & $Python -c "from PIL import Image, ImageDraw; import os; img = Image.new('RGB', (560, 100), 'white'); ImageDraw.Draw(img).text((20, 30), 'OmniCrawler OCR 123', fill='black'); img.save(os.environ['TEMP'] + '/omnicrawler_ocr_probe.png')"
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    & (Join-Path $tesseractRoot 'tesseract.exe') $probePng stdout --tessdata-dir $tessdataRoot -l chi_sim 2>&1 | Out-Null
    $ErrorActionPreference = $oldEap
    if ($LASTEXITCODE -ne 0) { throw 'Tesseract chi_sim 实际识别失败（语言包可能损坏）' }
    Remove-Item -LiteralPath $probePng -Force -ErrorAction SilentlyContinue
}

if (-not $SkipOcrModels) {
    Write-Host 'Prepare and verify all PPStructureV3 offline models...'
    $modelRoot = Join-Path $RuntimeRoot 'models\paddlex'
    & $Python (Join-Path $ProjectRoot 'tools\download_and_smoke_test.py') $modelRoot --source aistudio
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
