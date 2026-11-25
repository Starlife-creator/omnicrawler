<#
.SYNOPSIS
    修复「Windows 完整性级别」导致的 OmniCrawler 写不了数据目录问题。

.DESCRIPTION
    症状（本仓 2026-11-25 实测）：

        [Errno 13] Permission denied: '...\\OmniCrawler\\.omnicrawler\\components\\.registry.lock'
        WARNING 无法创建文件日志: Permission denied: '...\\OmniCrawler\\logs\\omnicrawler.log'

    但目录的 ACL 明明有你的「完全控制」，在资源管理器里也能手动新建文件。

    真正原因：**文件/目录带一个「低完整性」标签**（Windows 强制完整性控制，
    不是普通 ACL）。带这个标签的 .venv\\Scripts\\python.exe 启动后会以
    **Low** 完整性运行，而用户目录（AppData / 桌面）是 **Medium**。低不能写高
    —— 于是对所有数据目录的写入都被拒，错误码就是 13。

    典型来源：便携包被解压进一个带低完整性标签的文件夹；或某些安全工具 /
    开发环境给工作目录设了低完整性。

.PARAMETER ProjectRoot
    项目根目录（含 .venv 与 .runtime 的那一层）。省略时按脚本位置推断。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\\portable\\fix_windows_integrity.ps1
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = ''
)

# $PSScriptRoot 在 param 默认值里求值时机不可靠（实测报 InvalidData），
# 所以在脚本体内计算：脚本在 tools/portable/ 下，往上两级就是项目根。
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    Write-Error '无法推断项目根目录，请用 -ProjectRoot 显式指定。'
    exit 1
}

$ErrorActionPreference = 'Stop'

function Get-IntegrityLabel {
    param([string]$Path)
    $output = & icacls $Path 2>&1
    $line = $output | Where-Object { $_ -match 'Mandatory Label' } | Select-Object -First 1
    if (-not $line) { return 'Medium' }
    if ($line -match 'Low Mandatory') { return 'Low' }
    if ($line -match 'High Mandatory') { return 'High' }
    return 'Medium'
}

Write-Host "项目根目录：$ProjectRoot"

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    Write-Error "目录不存在：$ProjectRoot"
    exit 1
}

$python = Join-Path $ProjectRoot '.venv\\Scripts\\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "找不到 $python —— 先运行 setup_windows.bat"
    exit 1
}

$label = Get-IntegrityLabel -Path $ProjectRoot
Write-Host "项目目录完整性级别：$label"

# 用项目自己的 python 实测一次写入，而不是靠猜。
# ★ 探针写成 -c 单行，**不落临时文件** —— 早先版本写到 $env:TEMP 后删掉，
#   复检时又引用同一路径，于是「修复成功后复检反而报文件不存在」。
$probeCode = @'
import os, pathlib, subprocess
root = pathlib.Path(os.environ['LOCALAPPDATA']) / 'OmniCrawler' / '.omnicrawler' / 'components'
root.mkdir(parents=True, exist_ok=True)
target = root / '.integrity-probe.tmp'
try:
    target.write_bytes(b'x')
    target.unlink()
    print('WRITABLE')
except PermissionError:
    groups = subprocess.run(['whoami', '/groups'], capture_output=True, text=True,
                            errors='replace').stdout
    level = next((ln.strip() for ln in groups.splitlines() if 'S-1-16' in ln), '?')
    print('DENIED|' + level)
'@
function Invoke-WriteProbe {
    $tmp = [System.IO.Path]::GetTempFileName() + '.py'
    try {
        Set-Content -LiteralPath $tmp -Value $probeCode -Encoding UTF8
        return (& $python $tmp 2>&1 | Select-Object -Last 1)
    } finally {
        Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
    }
}
$result = Invoke-WriteProbe

if ($result -like 'WRITABLE*') {
    Write-Host '数据目录可写，不需要修复。' -ForegroundColor Green
    exit 0
}

Write-Host "实测结果：$result" -ForegroundColor Yellow
Write-Host ''

if ($label -eq 'Low') {
    Write-Host '数据目录不可写，原因是项目目录带【低完整性】标签。' -ForegroundColor Yellow
    Write-Host '把它改回【中完整性】（不改变任何文件内容与所有者）：'
    Write-Host ''
    & icacls $ProjectRoot /setintegritylevel '(OI)(CI)M'
    if ($LASTEXITCODE -ne 0) {
        Write-Error '设置失败。请以管理员身份重试本脚本。'
        exit 1
    }
    $after = Get-IntegrityLabel -Path $ProjectRoot
    if ($after -eq 'Low') {
        Write-Error '仍为低完整性 —— 可能有安全软件在持续重置，请检查其策略。'
        exit 1
    }
    Write-Host "已改为：$after" -ForegroundColor Green
    $retry = Invoke-WriteProbe
    if ($retry -like 'WRITABLE*') {
        Write-Host '复检：数据目录现在可写。' -ForegroundColor Green
        exit 0
    }
    Write-Error "复检仍不可写：$retry"
    exit 1
}

Write-Host '完整性级别正常，问题在别处。请依次检查：' -ForegroundColor Yellow
Write-Host '  · 是否有另一个 OmniCrawler 实例正持有 .registry.lock'
Write-Host '  · 数据目录是否被安全软件锁定或位于只读介质'
Write-Host '  · 反复出现时，备份后重命名数据目录再试（会丢失已装组件记录）'
exit 2
