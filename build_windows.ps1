[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipBrowserDownload,
    [switch]$SkipRuntimeAssetDownload,
    [string]$BuilderPythonPath = '',
    [ValidateSet('Standard', 'Full')][string]$Edition = 'Full',
    [string]$CodeSigningThumbprint = '',
    [switch]$RequireCodeSigning,
    [switch]$Offline,
    [string]$BuildRootPath = '',
    [string]$ReleaseOutputPath = '',
    [string]$BrowserCachePath = '',
    [string]$RuntimeCachePath = ''
)

$ErrorActionPreference = 'Stop'
# S4.3.4 ②：架构断言——便携包仅面向 64 位 x64 环境，早失败避免后期莫名崩溃
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'OmniCrawler 便携包仅支持 64 位 Windows（检测到 32 位操作系统）'
}
if (-not [Environment]::Is64BitProcess) {
    throw '请使用 64 位 PowerShell 执行构建（当前为 32 位进程）'
}
# F4：统一用 $PSScriptRoot（dot-source/部分宿主下 $MyInvocation.MyCommand.Path 可能为空）
$projectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
# Keep .NET's working directory in sync with the script location so that
# [IO.Path]::GetFullPath() resolves relative path parameters (BuildRootPath,
# ReleaseOutputPath, BrowserCachePath, RuntimeCachePath) against the project
# root instead of a stale process CWD.  Without this, relative paths can be
# resolved against the wrong directory (e.g. the user's home) on some shells.
[Environment]::CurrentDirectory = $projectRoot
$buildRoot = if ($BuildRootPath) {
    [IO.Path]::GetFullPath($BuildRootPath)
} else {
    Join-Path $env:TEMP "OmniCrawler-build-$($Edition.ToLower())"
}
$binaryRoot = Join-Path $buildRoot 'bin'
$workRoot = Join-Path $buildRoot 'work'
$browsersRoot = Join-Path $buildRoot 'browsers'
$runtimeRoot = Join-Path $buildRoot 'runtime'
$releaseRoot = Join-Path $buildRoot 'release\OmniCrawler'
$releaseOutput = if ($ReleaseOutputPath) {
    [IO.Path]::GetFullPath($ReleaseOutputPath)
} else {
    Join-Path $projectRoot 'release'
}
$specFile = Join-Path $projectRoot $(if ($Edition -eq 'Full') { 'packaging\OmniCrawler.spec' } else { 'packaging\OmniCrawler-Standard.spec' })
$builderVenv = Join-Path $env:TEMP "OmniCrawler-build-$($Edition.ToLower())-venv"
$builderPython = if ($BuilderPythonPath) { $BuilderPythonPath } else { Join-Path $builderVenv 'Scripts\python.exe' }

if ($Offline) {
    if (-not $BuilderPythonPath) {
        throw '-Offline requires -BuilderPythonPath so the build never creates or downloads an environment.'
    }
    $SkipDependencyInstall = $true
    $SkipBrowserDownload = $true
    $SkipRuntimeAssetDownload = $true
    # LiteLLM otherwise refreshes its model-cost map while PyInstaller imports
    # optional modules.  The bundled backup is sufficient for an offline build.
    $env:LITELLM_LOCAL_MODEL_COST_MAP = 'true'
    if (-not $BrowserCachePath) { $BrowserCachePath = Join-Path $projectRoot 'build_cache\browsers' }
    if ($Edition -eq 'Full' -and -not $RuntimeCachePath) {
        $RuntimeCachePath = Join-Path $projectRoot 'build_cache\runtime'
    }
}

# =============================================================================
# Version guard — version is read from source code, never hardcoded or guessed.
# If you want a different version, bump __version__ in src/omnicrawl/__init__.py
# *before* running this script.  Do NOT edit __version__ as a side effect of
# other work — that is a separate, deliberate operation.
# F1：版本读取移到依赖安装之后（全新构建时构建 venv 尚未创建）。
# F2：读取结果校验非空且形似版本号，否则立即中止。
# =============================================================================
function Read-AppVersion([string]$Python) {
    $versionOutput = (& $Python -c 'from omnicrawl import __version__; print(__version__)').Trim()
    Assert-LastExit 'Could not read the application version from the source tree.'
    if (-not $versionOutput -or $versionOutput -notmatch '^\d+\.\d+') {
        throw "Invalid application version read from source: '$versionOutput'"
    }
    return $versionOutput
}

function Assert-LastExit([string]$Message) {
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

function Reset-TemporaryDirectory([string]$Path) {
    $resolvedBuild = [IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'
    $resolvedTarget = [IO.Path]::GetFullPath($Path)
    if (-not $resolvedTarget.StartsWith($resolvedBuild, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to reset a directory outside the build root: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Path $resolvedTarget -Force | Out-Null
}

function Copy-VerifiedTree([string]$Source, [string]$Destination, [string]$Label) {
    if (-not $Source) { throw "$Label cache path is required." }
    $resolvedSource = [IO.Path]::GetFullPath($Source)
    $resolvedDestination = [IO.Path]::GetFullPath($Destination)
    if (-not (Test-Path -LiteralPath $resolvedSource -PathType Container)) {
        throw "$Label cache directory was not found: $resolvedSource"
    }
    if ($resolvedSource.TrimEnd('\') -eq $resolvedDestination.TrimEnd('\')) {
        throw "$Label cache source and staging destination must differ."
    }
    Reset-TemporaryDirectory $resolvedDestination
    Get-ChildItem -LiteralPath $resolvedSource -Force |
        Copy-Item -Destination $resolvedDestination -Recurse -Force
}

# ---- 依赖安装（F1：版本读取必须在此之后，构建 venv 此时才可用）----
if (-not $SkipDependencyInstall) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw 'Python 3.10 or newer was not found.'
    }
    # F5：显式 -BuilderPythonPath 指向非构建 venv 的解释器时拒绝自动安装，
    # 避免把项目+PyInstaller+完整依赖矩阵灌入系统 Python。
    $resolvedBuilder = [IO.Path]::GetFullPath($builderPython)
    $resolvedVenvPython = [IO.Path]::GetFullPath((Join-Path $builderVenv 'Scripts\python.exe'))
    if ($BuilderPythonPath -and $resolvedBuilder -ne $resolvedVenvPython) {
        throw '-BuilderPythonPath 指向非构建 venv 的解释器；自动安装会污染该系统 Python，请改用 -SkipDependencyInstall。'
    }
    if (-not (Test-Path -LiteralPath $builderPython)) {
        python -m venv $builderVenv
        Assert-LastExit 'Could not create the isolated build environment.'
    }
    & $builderPython -m pip install --upgrade pip setuptools wheel
    Assert-LastExit 'Could not upgrade build tooling.'
    $extras = if ($Edition -eq 'Full') { 'full,dev' } else { 'gui,html,pdf,browser,async-http,security,dev' }
    & $builderPython -m pip install -e "$projectRoot[$extras]" pyinstaller
    Assert-LastExit "Could not install the $Edition build dependency matrix."
} else {
    if (-not $BuilderPythonPath) {
        throw '-SkipDependencyInstall requires -BuilderPythonPath.'
    }
    if ($Edition -eq 'Full') {
        & $builderPython -c 'import PyInstaller, paddleocr, selenium, PyQt6, pyarrow, psycopg, opensearchpy'
    } else {
        & $builderPython -c 'import PyInstaller, PyQt6, playwright, fitz, openpyxl'
    }
    Assert-LastExit "The selected builder Python does not contain all $Edition dependencies."
}

# F1/F2：版本读取移到依赖安装后，并校验非空/形似版本号（内置于 Read-AppVersion）
$appVersion = Read-AppVersion $builderPython
# F26：便携包版本（omnicrawl.__version__）与源码包版本（pyproject）必须一致，
# 否则同次发布产出版本号矛盾的产物
$env:OMNICRAWL_PROJECT_ROOT = $projectRoot
$pyprojectVersion = (& $builderPython -c "import os, tomllib; root = os.environ['OMNICRAWL_PROJECT_ROOT']; data = tomllib.loads(open(os.path.join(root, 'pyproject.toml'), encoding='utf-8').read()); print(data['project']['version'])").Trim()
Remove-Item Env:OMNICRAWL_PROJECT_ROOT -ErrorAction SilentlyContinue
if ($pyprojectVersion -and $pyprojectVersion -ne $appVersion) {
    throw "版本不一致: pyproject=$pyprojectVersion vs omnicrawl.__version__=$appVersion"
}
# F53：构建 venv 的 installed 元数据也必须与源码一致，否则产物会带漂移版本。
$installedVersion = (& $builderPython -c "import importlib.metadata; print(importlib.metadata.version('omnicrawl-platform'))").Trim()
Assert-LastExit 'Could not read the installed version from the build environment.'
if ($installedVersion -and $installedVersion -ne $appVersion) {
    throw "版本元数据漂移: installed=$installedVersion vs src=$appVersion —— 构建环境需重跑 pip install -e . 对齐。"
}
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  OmniCrawler $appVersion — $Edition edition portable build" -ForegroundColor Cyan
Write-Host "  Build root : $buildRoot" -ForegroundColor DarkGray
Write-Host "  Release    : $releaseOutput" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan

# F6：PLAYWRIGHT_BROWSERS_PATH 只在构建期间指向临时 browsers 目录，结束后还原调用者会话
$previousPlaywrightPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
    $env:PLAYWRIGHT_BROWSERS_PATH = $browsersRoot

if (-not $SkipBrowserDownload) {
    & $builderPython -m playwright install chromium
    Assert-LastExit 'Playwright Chromium download failed.'
} elseif ($BrowserCachePath) {
    Copy-VerifiedTree $BrowserCachePath $browsersRoot 'Browser'
}
if (-not (Test-Path -LiteralPath $browsersRoot)) {
    throw "Bundled Chromium was not found: $browsersRoot"
}
# F7：进一步校验目录里确实有可用的 chrome.exe，避免残留空目录直到 ZIP 检查才暴露
$chromeProbe = Get-ChildItem -LiteralPath $browsersRoot -Recurse -Filter 'chrome.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $chromeProbe) {
    throw "Bundled Chromium executable was not found under: $browsersRoot"
}

if ($Edition -eq 'Full' -and -not $SkipRuntimeAssetDownload) {
    & (Join-Path $projectRoot 'tools\prepare_windows_runtime.ps1') `
        -Python $builderPython -RuntimeRoot $runtimeRoot -BrowsersRoot $browsersRoot `
        -CacheRoot (Join-Path $buildRoot 'asset-cache')
    Assert-LastExit 'Windows runtime preparation failed.'
} elseif ($Edition -eq 'Full' -and $RuntimeCachePath) {
    Copy-VerifiedTree $RuntimeCachePath $runtimeRoot 'Runtime asset'
}
if ($Edition -eq 'Full') {
    foreach ($required in @(
        (Join-Path $runtimeRoot 'selenium\chromedriver.exe'),
        (Join-Path $runtimeRoot 'tesseract\tesseract.exe'),
        (Join-Path $runtimeRoot 'tesseract\tessdata\eng.traineddata'),
        (Join-Path $runtimeRoot 'tesseract\tessdata\chi_sim.traineddata'),
        (Join-Path $runtimeRoot 'tesseract\tessdata\osd.traineddata'),
        (Join-Path $runtimeRoot 'models\paddlex\omnicrawler-model-manifest.json')
    )) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Runtime asset is missing: $required" }
    }
    # F8：traineddata 必须是有效 gzip（Tesseract 语言包格式），防止残留空文件通过门禁
    foreach ($lang in @('eng', 'chi_sim', 'osd')) {
        $trained = Join-Path $runtimeRoot "tesseract\tessdata\$lang.traineddata"
        $bytes = [IO.File]::ReadAllBytes($trained)
        if ($bytes.Length -lt 1024 -or $bytes[0] -ne 0x1F -or $bytes[1] -ne 0x8B) {
            throw "Tesseract language pack is not a valid gzip file: $trained"
        }
    }
}

Reset-TemporaryDirectory $binaryRoot
Reset-TemporaryDirectory $workRoot
& $builderPython -m PyInstaller --noconfirm --clean --distpath $binaryRoot --workpath $workRoot $specFile
Assert-LastExit 'PyInstaller build failed.'

$builtFolder = Join-Path $binaryRoot 'OmniCrawler'
foreach ($required in @('OmniCrawler.exe', 'omnicrawl.exe', 'omnicrawl-worker.exe', '_internal')) {
    if (-not (Test-Path -LiteralPath (Join-Path $builtFolder $required))) {
        throw "PyInstaller output is incomplete: $required"
    }
}

if ($CodeSigningThumbprint) {
    $signTool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
    if (-not $signTool) { throw 'signtool.exe was not found for required Authenticode signing.' }
    foreach ($binary in @('OmniCrawler.exe', 'omnicrawl.exe', 'omnicrawl-worker.exe')) {
        & $signTool sign /sha1 $CodeSigningThumbprint /fd SHA256 /tr 'http://timestamp.digicert.com' /td SHA256 (Join-Path $builtFolder $binary)
        Assert-LastExit "Authenticode signing failed: $binary"
    }
} elseif ($RequireCodeSigning) {
    throw '-RequireCodeSigning was specified but -CodeSigningThumbprint is empty.'
}

Reset-TemporaryDirectory $releaseRoot
Copy-Item -Path (Join-Path $builtFolder '*') -Destination $releaseRoot -Recurse -Force
Copy-Item -LiteralPath $browsersRoot -Destination (Join-Path $releaseRoot 'browsers') -Recurse -Force
if ($Edition -eq 'Full') {
    Copy-Item -LiteralPath $runtimeRoot -Destination (Join-Path $releaseRoot 'runtime') -Recurse -Force
}
$launcher = Join-Path $projectRoot 'packaging\OmniCrawler-Launcher.bat'
if (-not (Test-Path -LiteralPath $launcher)) { throw "Portable launcher is missing: $launcher" }
Copy-Item -LiteralPath $launcher -Destination $releaseRoot
foreach ($file in @('packaging\PORTABLE_README.txt', 'packaging\THIRD_PARTY_NOTICES.md', 'README.md', 'LICENSE')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $releaseRoot
}
"OmniCrawler $Edition portable edition" |
    ForEach-Object { [IO.File]::WriteAllText((Join-Path $releaseRoot 'EDITION.txt'), $_, (New-Object Text.UTF8Encoding($false))) }
foreach ($directory in @('configs', 'docs', 'examples')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $directory) -Destination $releaseRoot -Recurse
}

# F14：只清理 _internal/browsers/runtime 内第三方 wheel 携带的 .sh/.command，
# 不再误删 docs/examples 等随包跨平台示例脚本（文档引用它们）
$scopedCleanRoots = @('_internal', 'browsers', 'runtime')
$resolvedRelease = [IO.Path]::GetFullPath($releaseRoot).TrimEnd('\') + '\'
Get-ChildItem -LiteralPath $releaseRoot -Recurse -File |
    Where-Object { $_.Extension -in @('.sh', '.command') } |
    ForEach-Object {
        $candidate = [IO.Path]::GetFullPath($_.FullName)
        if (-not $candidate.StartsWith($resolvedRelease, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a non-Windows launcher outside release staging: $candidate"
        }
        $relative = [IO.Path]::GetRelativePath($resolvedRelease, $candidate)
        $topLevel = ($relative -split '[\\/]')[0]
        if ($topLevel -notin $scopedCleanRoots) { return }
        Remove-Item -LiteralPath $candidate -Force
    }

New-Item -ItemType File -Path (Join-Path $releaseRoot 'PORTABLE.flag') -Force | Out-Null
foreach ($relativeDir in @('data\input', 'data\pdfs', 'work', 'output', 'logs')) {
    New-Item -ItemType Directory -Path (Join-Path $releaseRoot $relativeDir) -Force | Out-Null
}

& $builderPython (Join-Path $projectRoot 'tools\generate_sbom.py') --output (Join-Path $releaseRoot 'SBOM.json')
Assert-LastExit 'SBOM generation failed.'
& (Join-Path $releaseRoot 'omnicrawl.exe') --version
Assert-LastExit 'Packaged CLI version verification failed.'
& (Join-Path $releaseRoot 'omnicrawl.exe') templates validate
Assert-LastExit 'Packaged template verification failed.'
# F11：冒烟测试先于清单生成——其 cwd=releaseRoot 会写 .omnicrawler 缓存，
# 若在清单生成后跑会产出"不在清单中的新文件"导致完整性检查随机失败。
& $builderPython (Join-Path $projectRoot 'tools\portable_smoke_test.py') $releaseRoot --edition $Edition
Assert-LastExit 'Packaged browser/native runtime verification failed.'
# F12：CAPABILITIES.json 无 BOM 写入（带 BOM 会让第三方 json.loads 失败）
$capabilitiesOutput = & (Join-Path $releaseRoot 'omnicrawl.exe') capabilities --verify-imports --portable-paths
Assert-LastExit 'Packaged capability import verification failed.'
[IO.File]::WriteAllText((Join-Path $releaseRoot 'CAPABILITIES.json'), ($capabilitiesOutput -join "`n"), (New-Object Text.UTF8Encoding($false)))
# F9：路径经命令行参数传递，不再把 PowerShell 变量插值进 Python 源码字符串
& $builderPython (Join-Path $projectRoot 'tools\create_runtime_manifest.py') --release-root $releaseRoot
Assert-LastExit 'Runtime integrity manifest generation failed.'
& $builderPython (Join-Path $projectRoot 'tools\generate_release_info.py') `
    --project-root $projectRoot --release-root $releaseRoot --edition $Edition
Assert-LastExit 'Portable release metadata generation failed.'
# Regenerate after adding RELEASE-INFO.json so the integrity manifest covers
# the machine-readable release description as well as executables and runtime.
& $builderPython (Join-Path $projectRoot 'tools\create_runtime_manifest.py') --release-root $releaseRoot
Assert-LastExit 'Runtime integrity manifest refresh failed.'
& (Join-Path $releaseRoot 'omnicrawl.exe') runtime-verify --root $releaseRoot
Assert-LastExit 'Packaged runtime integrity verification failed.'

# $appVersion was already resolved at script startup — reuse it.
New-Item -ItemType Directory -Path $releaseOutput -Force | Out-Null
$releaseArchive = Join-Path $releaseOutput "OmniCrawler-$appVersion-Windows-Portable-$Edition.zip"
& $builderPython (Join-Path $projectRoot 'tools\create_zip.py') $releaseRoot $releaseArchive --root-name 'OmniCrawler'
Assert-LastExit 'ZIP64 portable archive creation failed.'
& $builderPython (Join-Path $projectRoot 'tools\check_release_integrity.py') $projectRoot `
    --portable-zip $releaseArchive --portable-deep
Assert-LastExit 'Portable ZIP integrity verification failed.'
$archiveHash = Get-FileHash -LiteralPath $releaseArchive -Algorithm SHA256
$checksumPath = Join-Path $releaseOutput "SHA256SUMS-$appVersion.txt"
$archiveName = $archiveHash.Path | Split-Path -Leaf
$existingChecksums = if (Test-Path -LiteralPath $checksumPath) {
    Get-Content -LiteralPath $checksumPath -Encoding ascii |
        Where-Object { $_ -and $_ -notmatch ("  " + [regex]::Escape($archiveName) + '$') }
} else {
    @()
}
@("# OmniCrawler $appVersion portable archive SHA-256", $existingChecksums,
    "$($archiveHash.Hash)  $archiveName") |
    Set-Content -LiteralPath $checksumPath -Encoding ascii

Write-Host "Build staging: $releaseRoot"
Write-Host "Portable ZIP: $releaseArchive"
Write-Host "SHA-256: $($archiveHash.Hash)"
Write-Host 'GUI: OmniCrawler.exe'
Write-Host 'CLI: omnicrawl.exe --help'
} finally {
    # F6：构建结束还原调用者会话的 PLAYWRIGHT_BROWSERS_PATH
    if ($null -eq $previousPlaywrightPath) {
        Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
    } else {
        $env:PLAYWRIGHT_BROWSERS_PATH = $previousPlaywrightPath
    }
}
