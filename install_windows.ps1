[CmdletBinding()]
param(
    [switch]$SkipBrowser,
    [switch]$SkipRuntimeAssets,
    [switch]$Minimal
)
$ErrorActionPreference = 'Stop'
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDirectory

$BundledPython = Join-Path $ProjectDirectory '.runtime\python\python.exe'
if (Test-Path -LiteralPath $BundledPython) {
    & $BundledPython -m venv --copies .venv
} else {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        & py -3 -m venv .venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv .venv
    } else {
        throw 'Python 3.10 or newer was not found. Add .runtime\\python\\python.exe or install Python from python.org and enable Add Python to PATH.'
    }
}

$Python = Join-Path $ProjectDirectory '.venv\Scripts\python.exe'
& $Python -m pip install --upgrade pip setuptools wheel
if ($Minimal) {
    & $Python -m pip install -e '.[html,gui]'
} else {
    & $Python -m pip install -e '.[full,dev]'
}
if (-not $SkipBrowser -and -not $Minimal) {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectDirectory '.runtime\browsers'
    & $Python -m playwright install chromium
}
if (-not $SkipRuntimeAssets -and -not $Minimal) {
    & (Join-Path $ProjectDirectory 'tools\prepare_windows_runtime.ps1') `
        -Python $Python `
        -RuntimeRoot (Join-Path $ProjectDirectory '.runtime') `
        -BrowsersRoot (Join-Path $ProjectDirectory '.runtime\browsers')
}
& $Python -m omnicrawl.cli --help | Out-Null
& $Python -m omnicrawl.cli capabilities --verify-imports
$Version = & $Python -c "import importlib.metadata; print(importlib.metadata.version('omnicrawl-platform'))"
Write-Host "OmniCrawler $Version full source environment is ready." -ForegroundColor Green
