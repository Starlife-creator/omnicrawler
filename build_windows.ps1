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
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
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
# =============================================================================
$appVersion = (& $builderPython -c 'from omnicrawl import __version__; print(__version__)').Trim()
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  OmniCrawler $appVersion — $Edition edition portable build" -ForegroundColor Cyan
Write-Host "  Build root : $buildRoot" -ForegroundColor DarkGray
Write-Host "  Release    : $releaseOutput" -ForegroundColor DarkGray
Write-Host "============================================================" -ForegroundColor Cyan

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

if (-not $SkipDependencyInstall) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw 'Python 3.10 or newer was not found.'
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
        (Join-Path $runtimeRoot 'models\paddlex\omnicrawler-model-manifest.json')
    )) {
        if (-not (Test-Path -LiteralPath $required)) { throw "Runtime asset is missing: $required" }
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
    Set-Content -LiteralPath (Join-Path $releaseRoot 'EDITION.txt') -Encoding utf8
foreach ($directory in @('configs', 'docs', 'examples')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $directory) -Destination $releaseRoot -Recurse
}

# Some cross-platform Python wheels carry macOS/Linux maintenance launchers as
# package data. They cannot run on Windows and are not part of the application
# contract, so keep the Windows-specific portable distribution clear.
$resolvedRelease = [IO.Path]::GetFullPath($releaseRoot).TrimEnd('\') + '\'
Get-ChildItem -LiteralPath $releaseRoot -Recurse -File |
    Where-Object { $_.Extension -in @('.sh', '.command') } |
    ForEach-Object {
        $candidate = [IO.Path]::GetFullPath($_.FullName)
        if (-not $candidate.StartsWith($resolvedRelease, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a non-Windows launcher outside release staging: $candidate"
        }
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
& (Join-Path $releaseRoot 'omnicrawl.exe') capabilities --verify-imports --portable-paths |
    Set-Content -LiteralPath (Join-Path $releaseRoot 'CAPABILITIES.json') -Encoding utf8
Assert-LastExit 'Packaged capability import verification failed.'
& $builderPython -c "from pathlib import Path; from omnicrawl.runtime_manifest import create_runtime_manifest; create_runtime_manifest(Path(r'$releaseRoot'))"
Assert-LastExit 'Runtime integrity manifest generation failed.'
& $builderPython (Join-Path $projectRoot 'tools\generate_release_info.py') `
    --project-root $projectRoot --release-root $releaseRoot --edition $Edition
Assert-LastExit 'Portable release metadata generation failed.'
# Regenerate after adding RELEASE-INFO.json so the integrity manifest covers
# the machine-readable release description as well as executables and runtime.
& $builderPython -c "from pathlib import Path; from omnicrawl.runtime_manifest import create_runtime_manifest; create_runtime_manifest(Path(r'$releaseRoot'))"
Assert-LastExit 'Runtime integrity manifest refresh failed.'
& (Join-Path $releaseRoot 'omnicrawl.exe') runtime-verify --root $releaseRoot
Assert-LastExit 'Packaged runtime integrity verification failed.'
& $builderPython (Join-Path $projectRoot 'tools\portable_smoke_test.py') $releaseRoot --edition $Edition
Assert-LastExit 'Packaged browser/native runtime verification failed.'

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
