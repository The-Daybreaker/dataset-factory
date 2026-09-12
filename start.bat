@echo off
rem Dataset Factory 一键启动：同步依赖 →（需要时）构建前端 → 拉起本地服务并自动打开浏览器。
rem 双击运行即可；Ctrl+C 停止服务。设 DSF_NO_BROWSER=1 可跳过自动打开浏览器。
setlocal
chcp 65001 >nul
cd /d "%~dp0"

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

if not defined DSF_NO_BROWSER (
    start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 2; Start-Process 'http://127.0.0.1:8000'"
)

echo [start] 启动服务：http://127.0.0.1:8000 （Ctrl+C 停止）
pushd backend
"%UV%" run dsf serve
popd
