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

rem ★ 2026-11-25：workbench 是 Tk 窗口程序，用 pythonw.exe 不留 cmd 黑框。
rem 与 run_gui_windows.bat 同一处理（那边 2026-11-24 已改）。
rem 注意：rebase 仍用 console python —— 它的报错要能被看到。
set PYTHONW=.venv\Scripts\pythonw.exe
if not exist "%PYTHONW%" (
    ".venv\Scripts\python.exe" -m omnicrawler workbench %*
    exit /b %ERRORLEVEL%
)
start "OmniCrawler Workbench" "%PYTHONW%" -m omnicrawler workbench %*
exit /b 0
