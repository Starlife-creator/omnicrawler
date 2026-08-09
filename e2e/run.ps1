[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$Install,
    [switch]$Browser,
    [switch]$FullRegression,
    [double]$CoverageTarget = 95
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Install) {
    & $Python -m pip install -e "$repoRoot`[html,pdf,browser,dev]"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$arguments = @("$repoRoot/e2e/run.py", "--coverage-target", "$CoverageTarget")
if ($Browser) { $arguments += "--browser" }
if ($FullRegression) { $arguments += "--full-regression" }
& $Python @arguments
exit $LASTEXITCODE
