@echo off
rem Dataset Factory 一键启动：同步依赖 →（需要时）构建前端 → 隐藏拉起本地服务 → 打开浏览器。
rem 服务以无窗口后台进程运行：关闭服务用浏览器页头的电源按钮，或运行 stop.bat。
rem 设 DSF_NO_BROWSER=1 跳过自动打开浏览器；设 DSF_PORT 改端口（默认 8000）。
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if defined DSF_PORT (set "PORT=%DSF_PORT%") else set "PORT=8000"

rem 解析 uv：先找 PATH，再退到 uv 默认安装位置（%USERPROFILE%\.local\bin）。
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

if not exist "frontend\dist\index.html" (
    echo [start] 未找到前端构建产物，开始构建（需要 Node.js / npm）...
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

rem 幂等启动：端口已被监听 = 服务已在运行，直接打开界面、不重复起服务。
netstat -ano | findstr /r /c":%PORT% .*LISTENING" >nul 2>nul
if %errorlevel% equ 0 (
    echo [start] 服务已在运行（端口 %PORT%），直接打开界面。
    if not defined DSF_NO_BROWSER start "" "http://127.0.0.1:%PORT%"
    exit /b 0
)

rem 隐藏拉起服务：Start-Process 给新进程独立的隐藏控制台，不影响本窗口；
rem 日志写数据根 logs\server.log，设置页「服务运行」也可查看。
echo [start] 启动服务（无窗口后台运行）...
set "SERVE_ARGS=run,dsf,serve"
if not "%PORT%"=="8000" set "SERVE_ARGS=run,dsf,serve,--port,%PORT%"
powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath \"%UV%\" -ArgumentList %SERVE_ARGS% -WorkingDirectory 'backend'"

rem 等端口就绪（最多约 15 秒）：就绪即开浏览器；超时保底报错，不静默消失。
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
