#!/usr/bin/env bash
# Dataset Factory 一键启动：同步依赖 →（需要时）构建前端 → 后台拉起服务 → 打开浏览器。
# 服务以无窗口后台进程运行：关闭服务用浏览器页头的电源按钮，或运行 ./stop.sh。
# 设 DSF_NO_BROWSER=1 跳过自动打开浏览器；设 DSF_PORT 改端口（默认 8000）。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${DSF_PORT:-8000}"

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

port_listening() {
    ss -ltn 2>/dev/null | grep -q ":${PORT} " && return 0
    netstat -ltn 2>/dev/null | grep -q ":${PORT} " && return 0
    return 1
}

find_listener_pid() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | grep ":${PORT} " | grep -o 'pid=[0-9]*' | head -n1 | cut -d= -f2 || true
    elif command -v lsof >/dev/null 2>&1; then
        lsof -ti tcp:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -n1 || true
    fi
}

listener_is_ours() {
    # 监听进程的命令行含 dsf 特征才算本工具的服务；取不到 cmdline 按不是处理（保守，宁可重新走启动检查）。
    local pid
    pid="$(find_listener_pid)"
    [ -n "${pid}" ] || return 1
    ps -p "${pid}" -o command= 2>/dev/null | grep -q dsf
}

# 幂等启动（含身份校验）：端口在监听且监听进程是本工具 = 服务已在运行，直接打开界面、
# 不重复起服务；端口被无关程序占用则不启动，给出可操作提示。
if listener_is_ours; then
    echo "[start] 服务已在运行（端口 ${PORT}），直接打开界面。"
    if [ -z "${DSF_NO_BROWSER:-}" ]; then
        xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
    fi
    exit 0
fi
if port_listening; then
    echo "[start] 端口 ${PORT} 已被其他程序占用（不是本工具的服务），本次未启动。" >&2
    echo "[start] 可换端口：DSF_PORT=8001 ./start.sh；或结束占用程序后再试。" >&2
    exit 1
fi

echo "[start] 启动服务（无窗口后台运行）..."
(cd backend && nohup "$UV" run dsf serve --port "${PORT}" >/dev/null 2>&1 &)

# 等端口就绪（最多约 15 秒）：就绪即开浏览器；超时保底报错，不静默消失。
for _ in $(seq 1 15); do
    sleep 1
    if port_listening; then
        echo "[start] 服务已就绪：http://127.0.0.1:${PORT}"
        echo "[start] 关闭服务：浏览器页头电源按钮，或 ./stop.sh"
        if [ -z "${DSF_NO_BROWSER:-}" ]; then
            xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
        fi
        exit 0
    fi
done

echo "[start] 服务未能启动：端口 ${PORT} 在 15 秒内未就绪。" >&2
echo "[start] 详细日志：~/.dataset_factory/logs/server.log" >&2
exit 1
