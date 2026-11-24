@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run setup_windows.bat first.
    exit /b 1
)

rem F50：无内置解释器（安装脚本已回退系统 Python）时跳过 rebase，直接用 .venv
if exist ".runtime\python\python.exe" (
    ".runtime\python\python.exe" "tools\portable\rebase_venv.py" || exit /b 1
) else (
    echo [INFO] Bundled Python runtime not found; using the existing virtual environment directly.
)

rem ★ 2026-11-24：用 pythonw.exe 而不是 python.exe。
rem 用 python.exe 会留下一个 cmd 黑框，并把 WARNING（如「无法创建文件日志」）
rem 打在窗口后面 —— 用户看到的是「GUI 背后漂着一个 Python 框」，很像出错。
rem pythonw 是同一解释器的无控制台变体，GUI 本身不需要 stdin/stdout。
rem
rem 但**不能**直接 `start pythonw` 然后立刻退出：那样 rebase 的报错会被吞掉。
rem 所以先探测 pythonw 是否存在，存在就用它（rebase 仍用 console python，见上）；
rem 不存在则退回 python.exe（老旧环境兜底）。
set PYTHONW=.venv\Scripts\pythonw.exe
if not exist "%PYTHONW%" (
    rem 兜底：没有 pythonw 时仍可启动，但会有黑框（比起不来好）。
    ".venv\Scripts\python.exe" -m omnicrawler.gui %*
    exit /b %ERRORLEVEL%
)
start "OmniCrawler" "%PYTHONW%" -m omnicrawler.gui %*
exit /b 0
