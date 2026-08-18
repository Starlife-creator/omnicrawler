@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found. Run setup_windows.bat first.
  exit /b 1
)

rem F50：无内置解释器（安装脚本已回退系统 Python）时跳过 rebase，直接用 .venv
if exist ".runtime\python\python.exe" (
  ".runtime\python\python.exe" "tools\rebase_venv.py" || exit /b 1
) else (
  echo [INFO] Bundled Python runtime not found; using the existing virtual environment directly.
)

".venv\Scripts\python.exe" -m omnicrawler workbench %*
