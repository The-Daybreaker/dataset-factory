@echo off
rem Dataset Factory 停止服务：按端口找到监听进程并结束整棵进程树。
rem 这是兜底手段（服务卡死、页面打不开时用）；平时用浏览器页头的电源按钮关闭。
rem 设 DSF_PORT 改端口（默认 8000），需与启动时一致。
setlocal
chcp 65001 >nul

if defined DSF_PORT (set "PORT=%DSF_PORT%") else set "PORT=8000"

set "PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c":%PORT% .*LISTENING"') do set "PID=%%P"
if not defined PID (
    echo [stop] 端口 %PORT% 没有监听中的进程，服务未在运行。
    exit /b 0
)

echo [stop] 结束进程 PID=%PID%（端口 %PORT%）...
taskkill /PID %PID% /T /F >nul 2>nul
if %errorlevel% equ 0 (
    echo [stop] 服务已停止。
) else (
    echo [stop] 结束失败：权限不足或进程已自行退出；可在任务管理器手动结束该 PID。
    exit /b 1
)
