#!/usr/bin/env bash
# Dataset Factory 停止服务：按端口找到监听进程并结束。
# 这是兜底手段（服务卡死、页面打不开时用）；平时用浏览器页头的电源按钮关闭。
# 设 DSF_PORT 改端口（默认 8000），需与启动时一致。
set -euo pipefail

PORT="${DSF_PORT:-8000}"

PID=""
if command -v ss >/dev/null 2>&1; then
    PID="$(ss -ltnp 2>/dev/null | grep ":${PORT} " | grep -o 'pid=[0-9]*' | head -n1 | cut -d= -f2 || true)"
fi
if [ -z "${PID}" ] && command -v lsof >/dev/null 2>&1; then
    PID="$(lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n1 || true)"
fi

if [ -z "${PID}" ]; then
    echo "[stop] 端口 ${PORT} 没有监听中的进程，服务未在运行。"
    exit 0
fi

echo "[stop] 结束进程 PID=${PID}（端口 ${PORT}）..."
kill "${PID}" 2>/dev/null || true
sleep 1
if kill -0 "${PID}" 2>/dev/null; then
    kill -9 "${PID}" 2>/dev/null || true
fi
echo "[stop] 服务已停止。"
