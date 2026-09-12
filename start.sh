#!/usr/bin/env bash
# Dataset Factory 一键启动：同步依赖 →（需要时）构建前端 → 拉起本地服务并自动打开浏览器。
# Ctrl+C 停止服务。设 DSF_NO_BROWSER=1 可跳过自动打开浏览器。
set -euo pipefail
cd "$(dirname "$0")"

UV="${UV:-uv}"
if ! command -v "$UV" >/dev/null 2>&1; then
    UV="$HOME/.local/bin/uv"
fi
if ! command -v "$UV" >/dev/null 2>&1; then
    echo "[start] 未找到 uv，请先安装：https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "[start] 同步后端依赖..."
(cd backend && "$UV" sync --frozen)

if [ ! -f frontend/dist/index.html ]; then
    echo "[start] 未找到前端构建产物，开始构建（需要 Node.js / npm）..."
    (cd frontend && npm install && npm run build)
fi

if [ -z "${DSF_NO_BROWSER:-}" ]; then
    (
        sleep 2
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "http://127.0.0.1:8000" >/dev/null 2>&1 || true
        elif command -v open >/dev/null 2>&1; then
            open "http://127.0.0.1:8000" >/dev/null 2>&1 || true
        fi
    ) &
fi

echo "[start] 启动服务：http://127.0.0.1:8000 （Ctrl+C 停止）"
cd backend
exec "$UV" run dsf serve
