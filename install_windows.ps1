[CmdletBinding()]
param(
    [switch]$SkipBrowser,
    [switch]$SkipRuntimeAssets,
    [switch]$Minimal
)
$ErrorActionPreference = 'Stop'
# S4.3.4 ②：架构断言——便携包仅面向 64 位 x64 环境
if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'OmniCrawler 便携包仅支持 64 位 Windows（检测到 32 位操作系统）'
}
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
# F49：原生命令失败不会触发 $ErrorActionPreference，必须显式检查 $LASTEXITCODE
if ($LASTEXITCODE -ne 0) { throw 'Failed to create the virtual environment.' }

$Python = Join-Path $ProjectDirectory '.venv\Scripts\python.exe'
& $Python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }
if ($Minimal) {
    & $Python -m pip install -e '.[html,gui]'
} else {
    & $Python -m pip install -e '.[full,dev]'
}
if ($LASTEXITCODE -ne 0) { throw 'Failed to install project dependencies.' }
if (-not $SkipBrowser -and -not $Minimal) {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectDirectory '.runtime\browsers'
    & $Python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install Playwright Chromium.' }
}
if (-not $SkipRuntimeAssets -and -not $Minimal) {
    & (Join-Path $ProjectDirectory 'tools\prepare_windows_runtime.ps1') `
        -Python $Python `
        -RuntimeRoot (Join-Path $ProjectDirectory '.runtime') `
        -BrowsersRoot (Join-Path $ProjectDirectory '.runtime\browsers')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to prepare the Windows runtime.' }
}
& $Python -m omnicrawler.cli --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'CLI smoke check failed.' }
& $Python -m omnicrawler.cli capabilities --verify-imports
if ($LASTEXITCODE -ne 0) { throw 'Capability import verification failed.' }
$Version = & $Python -c "import importlib.metadata; print(importlib.metadata.version('omnicrawler-platform'))"
if ($LASTEXITCODE -ne 0) { throw 'Failed to read the installed version.' }
# F53：installed 元数据必须与源码 __version__ 一致；漂移即失败
$SrcVersion = & $Python -c "from omnicrawler import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw 'Failed to read the source version.' }
if ($Version -ne $SrcVersion) {
    throw "版本元数据漂移: installed=$Version vs src=$SrcVersion —— 请重跑 pip install -e '.[full,dev]' 对齐后再继续。"
}
Write-Host "OmniCrawler $Version full source environment is ready." -ForegroundColor Green
