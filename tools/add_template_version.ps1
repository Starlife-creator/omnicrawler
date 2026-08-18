# S4.3.4 ①：不再硬编码其他项目的绝对路径——基于脚本所在目录推导仓库相对路径。
# 用法: powershell -File tools\add_template_version.ps1 [-Base <相对路径>]
[CmdletBinding()]
param(
    [string]$Base = "src\omnicrawler\templates"
)

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$base = Join-Path $ProjectRoot $Base
if (-not (Test-Path -LiteralPath $base)) {
    throw "模板目录不存在: $base"
}
$yamlFiles = Get-ChildItem -Path $base -Recurse -Include "*.yaml", "*.yml"
$added = 0
$skipped = 0
foreach ($f in $yamlFiles) {
    $content = Get-Content $f.FullName -Raw
    if ($content -match "template_version") {
        $skipped++
    } else {
        $newContent = "template_version: 1`n" + $content
        Set-Content -Path $f.FullName -Value $newContent -NoNewline -Encoding UTF8
        $added++
        Write-Host "Added: $($f.FullName.Substring($base.Length))"
    }
}
Write-Host "`nTotal: $($yamlFiles.Count) files, Added: $added, Skipped: $skipped"
