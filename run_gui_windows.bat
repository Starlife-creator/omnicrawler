@echo off
setlocal
cd /d "%~dp0"

if not exist ".runtime\python\python.exe" (
    echo [ERROR] Bundled Python runtime not found.
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run setup_windows.bat first.
    exit /b 1
)

".runtime\python\python.exe" "tools\rebase_venv.py" || exit /b 1
".venv\Scripts\python.exe" -m omnicrawl.gui %*
