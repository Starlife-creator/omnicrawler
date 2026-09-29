@echo off
setlocal
cd /d "%~dp0"

rem F88：布局 B（versions\ + current.txt 指针）优先；指针缺失/为空/指向不存在 ⇒ 回退就地布局。
set "TARGET_EXE=%~dp0OmniCrawler.exe"
set "CURRENT_PTR=%~dp0versions\current.txt"
if not exist "%CURRENT_PTR%" goto resolve_done
set /p POINTED_VERSION=<"%CURRENT_PTR%"
if "%POINTED_VERSION%"=="" goto resolve_done
set "POINTED_EXE=%~dp0versions\%POINTED_VERSION%\OmniCrawler.exe"
if not exist "%POINTED_EXE%" goto resolve_done
set "TARGET_EXE=%POINTED_EXE%"
:resolve_done

if not exist "%TARGET_EXE%" (
    echo [ERROR] 未找到 OmniCrawler.exe（尝试：%TARGET_EXE%）
    echo 请确认已完整解压便携包，且未单独复制本启动器。
    echo 程序目录: %~dp0
    pause
    exit /b 1
)

start "" "%TARGET_EXE%" %*

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
