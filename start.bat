@echo off
rem Dataset Factory one-click start: sync deps -> build frontend if needed -> start hidden local server -> open browser.
rem The server runs as a hidden background process. Stop it via the power button in the web UI header, or run stop.bat.
rem Set DSF_NO_BROWSER=1 to skip auto-opening the browser; set DSF_PORT to change the port (default 8000).
rem NOTE: keep every rem line pure ASCII (see the B14 block below for why Chinese rem lines are dangerous here).
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if defined DSF_PORT (set "PORT=%DSF_PORT%") else set "PORT=8000"

rem Resolve uv: PATH first, then the default install location (%USERPROFILE%\.local\bin).
where uv >nul 2>nul
if %errorlevel% equ 0 (
    set "UV=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
) else (
    echo [start] 未找到 uv，请先安装：winget install astral-sh.uv
    pause
    exit /b 1
)

echo [start] 同步后端依赖...
pushd backend
"%UV%" sync --frozen
if errorlevel 1 (
    popd
    echo [start] 依赖同步失败，请检查上方报错。
    pause
    exit /b 1
)
popd

rem B14 (2026-09-21 audit): rebuild when frontend src is newer than dist, else "changed src but see stale UI".
rem Rule: newest mtime under frontend\src later than dist\index.html mtime = rebuild needed.
rem The PowerShell below exits 1 = rebuild needed, and we catch it with "if errorlevel 1".
rem Two hard lessons from real incidents:
rem   1. A bare greater-than sign inside a rem line is executed as redirection by cmd
rem      (it once created a file named after the comment text and broke the errorlevel).
rem   2. Never put Chinese rem comments inside parenthesized blocks: with chcp 65001,
rem      consecutive multi-byte rem lines can trigger cmd's multibyte parsing bug that
rem      eats the first characters of the following line (real case: "if errorlevel"
rem      became "rrorlevel", errorlevel turned 9009, and the rebuild branch fired on
rem      every single run). Therefore: no comments inside blocks at all, and all
rem      rem lines stay ASCII. Chinese in echo lines is fine (they run as commands).
set "NEED_BUILD=0"
if not exist "frontend\dist\index.html" (
    set "NEED_BUILD=1"
    echo [start] 未找到前端构建产物，开始构建（需要 Node.js / npm）...
) else (
    powershell -NoProfile -Command "$newest = (Get-ChildItem -Recurse 'frontend\src' -File | Measure-Object LastWriteTime -Maximum).Maximum; $dist = (Get-Item 'frontend\dist\index.html').LastWriteTime; exit ([int]($null -ne $newest -and $newest -gt $dist))" >nul 2>nul
    if errorlevel 1 (
        set "NEED_BUILD=1"
        echo [start] 检测到前端源码比构建产物新，重新构建（需要 Node.js / npm）...
    )
)
if "%NEED_BUILD%"=="1" (
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        echo [start] 前端依赖安装失败，请检查上方报错。
        pause
        exit /b 1
    )
    call npm run build
    if errorlevel 1 (
        popd
        echo [start] 前端构建失败，请检查上方报错。
        pause
        exit /b 1
    )
    popd
)

rem Idempotent start with identity check: port listening AND owning process is this tool
rem (cmdline contains "dsf") means the server is already running, so just open the UI;
rem if the port is taken by an unrelated program, do not start and show actionable hints.
powershell -NoProfile -Command "$conn = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if (-not $conn) { exit 1 }; $cmdline = (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $conn.OwningProcess)).CommandLine; exit ([int]($cmdline -notmatch 'dsf'))" >nul 2>nul
if %errorlevel% equ 0 (
    echo [start] 服务已在运行（端口 %PORT%），直接打开界面。
    if not defined DSF_NO_BROWSER start "" "http://127.0.0.1:%PORT%"
    exit /b 0
)
netstat -ano | findstr /r /c":%PORT% .*LISTENING" >nul 2>nul
if %errorlevel% equ 0 (
    echo [start] 端口 %PORT% 已被其他程序占用（不是本工具的服务），本次未启动。
    echo [start] 可换端口：set DSF_PORT=8001 后重新运行 start.bat；或结束占用程序后再试。
    pause
    exit /b 1
)

rem Start the server hidden: Start-Process gives the new process its own hidden console,
rem independent of this window. Logs go to the data root logs\server.log, also viewable
rem on the settings page service panel.
echo [start] 启动服务（无窗口后台运行）...
set "SERVE_ARGS=run,dsf,serve"
if not "%PORT%"=="8000" set "SERVE_ARGS=run,dsf,serve,--port,%PORT%"
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath \"%UV%\" -ArgumentList %SERVE_ARGS% -WorkingDirectory 'backend'"

rem Wait for the port (up to about 15 seconds): open browser once ready; on timeout,
rem report instead of failing silently.
set /a TRIES=15
:waitport
ping -n 2 127.0.0.1 >nul
netstat -ano | findstr /r /c":%PORT% .*LISTENING" >nul 2>nul
if %errorlevel% equ 0 goto ready
set /a TRIES-=1
if %TRIES% gtr 0 goto waitport

echo [start] 服务未能启动：端口 %PORT% 在 15 秒内未就绪。
echo [start] 常见原因：端口被无关程序占用（可先运行 stop.bat 清理本工具的旧进程）。
echo [start] 详细日志：%USERPROFILE%\.dataset_factory\logs\server.log
pause
exit /b 1

:ready
echo [start] 服务已就绪：http://127.0.0.1:%PORT%
echo [start] 关闭服务：浏览器页头电源按钮，或 stop.bat
if not defined DSF_NO_BROWSER start "" "http://127.0.0.1:%PORT%"
exit /b 0
