@echo off
rem Dataset Factory 停止服务：按端口找到监听进程、确认是本工具的服务后结束整棵进程树。
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

rem 身份校验：只结束本工具拉起的服务（命令行含 dsf 特征），防止误杀占用同端口的其他程序。
powershell -NoProfile -Command "exit ([int]((Get-CimInstance Win32_Process -Filter ('ProcessId=%PID%')).CommandLine -notmatch 'dsf'))" >nul 2>nul
if %errorlevel% equ 0 goto kill

echo [stop] 端口 %PORT% 的监听进程（PID=%PID%）不是本工具的服务，已拒绝结束。
echo [stop] 若确要处理该进程：核对端口（DSF_PORT，需与启动时一致）或在任务管理器手动操作。
exit /b 1

:kill
echo [stop] 结束进程 PID=%PID%（端口 %PORT%）...
taskkill /PID %PID% /T /F >nul 2>nul
if %errorlevel% equ 0 (
    echo [stop] 服务已停止。
) else (
    echo [stop] 结束失败：权限不足或进程已自行退出；可在任务管理器手动结束该 PID。
    exit /b 1
)
