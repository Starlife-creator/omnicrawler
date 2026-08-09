@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0OmniCrawler.exe" (
    echo [ERROR] 未找到 OmniCrawler.exe
    echo 请确认已完整解压便携包，且未单独复制本启动器。
    echo 程序目录: %~dp0
    pause
    exit /b 1
)

start "" "%~dp0OmniCrawler.exe" %*

rem F55/F36：冷启动可能较慢（杀软首扫/解压），轮询最长 60s 再判定失败，
rem 避免慢机器上 GUI 正在加载却被误报"启动失败"。
set /a waited=0
:wait_loop
tasklist /fi "imagename eq OmniCrawler.exe" 2>nul | findstr /i "OmniCrawler.exe" >nul
if not errorlevel 1 goto started
set /a waited+=1
if %waited% geq 60 goto failed
timeout /t 1 /nobreak >nul
goto wait_loop

:started
exit /b 0

:failed
echo.
echo Logs: %~dp0logs\\  (gui.log / local-worker.log)
echo [ERROR] OmniCrawler.exe 在 60 秒内未启动（可能缺少 DLL 或被安全软件拦截）。
echo 请尝试重新解压完整便携包后再次启动。
pause
exit /b 1
