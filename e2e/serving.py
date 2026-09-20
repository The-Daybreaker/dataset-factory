"""E2E 专用的一体化被测服务：真实系统 + 假模型端点，一个进程一个端口。

为什么这么做：Playwright 的 webServer 选项要求「起一个 URL 可等待的服务」。
把被测系统（dataset_factory 的 FastAPI app）和假 LLM 端点 mount 到同一个
app 上，E2E 测试只需要一个端口；打标请求经真实浏览器 → 真 HTTP → 打标引擎
→ openai SDK → 假端点（同源另一个路径）→ 落盘 → 响应，链路里的每一跳都是真的，
只有「模型本身」是假的——这正是系统测试的边界选择在 E2E 层的复用。

**一个端口一份数据根**：用例会往数据根里写东西（提示词、工作目录、快照、锁），
而 Playwright 在 workers=1 时会把多个测试文件塞进同一个 worker 顺序跑——共用一份
数据根就等于让前一个文件的痕迹变成后一个文件的地基，症状是「单独跑必绿、连跑偶发
红」。所以「一个测试文件一个端口」：端口表在 tests/fixtures/isolated-servers.ts，
每个进程启动时自建一份临时数据根，谁都不碰谁的东西。

运行方式由 playwright.config.ts 的 webServer 负责（uv run --project ../backend），
本脚本只管装配与启动，Ctrl+C 退出。
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, StreamingResponse
from filelock import FileLock, Timeout

# dataset_factory 源码在 backend/src（e2e 工程没有自己的 Python 环境）。
BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

DEFAULT_PORT = 8765
_OWNER_LOCK_NAME = ".e2e-owner.lock"
# 清扫的年龄下限：刚建出来还没立锁的数据根不碰（建目录与立锁之间有极短的空窗，
# 多个服务同时启动时靠这道下限避免擦肩）。判「原主还在不在」靠的是占用锁本身。
_STALE_DATA_HOME_GRACE_SECONDS = 60
_FAKE_REPLY = "E2E 假模型的打标结果"
_GATED_MODEL = "gated-e2e-model"
_GATED_ENTERED = threading.Event()
_GATED_RELEASE = threading.Event()


def _sweep_stale_data_homes(*, grace_seconds: float) -> None:
    """清掉历史 E2E 留在系统临时目录里、已经没人用的数据根。

    Playwright 在 Windows 上硬杀 webServer 进程、``atexit`` 不执行，所以非正常退出会
    留下数据根；下次启动顺手清掉，污染就不会无限增长。

    「还有没有人用」靠数据根里的占用锁判断：抢得到锁 = 原主已死（文件锁由操作系统在
    进程终止时随句柄自动释放，正常退出、崩溃、被强杀都一样），抢不到 = 正在跑、跳过。
    这比按年龄一刀切准，也不会误删同一台机器上同时跑着的其他文件的数据根。

    Args:
        grace_seconds: 数据根的修改时间早于「现在减去这个秒数」才纳入候选。
    """
    now = time.time()
    for entry in Path(tempfile.gettempdir()).glob("dsf-e2e-*"):
        if not entry.is_dir():
            continue
        try:
            if now - entry.stat().st_mtime < grace_seconds:
                continue
        except OSError:
            continue
        lock = FileLock(str(entry / _OWNER_LOCK_NAME))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            continue
        # 先放锁再删：Windows 上「删一个自己正锁着的文件」会失败，留下只剩空壳的残留。
        # 放锁与删除之间没有风险——本脚本的服务只会新建数据根，从不认领现存的目录。
        lock.release()
        shutil.rmtree(entry, ignore_errors=True)


def _resolve_data_home(explicit: str | None, *, port: int) -> Path:
    """确定本次运行的数据根。

    Args:
        explicit: ``--data-home`` 传入的路径；``None`` 表示自建。
        port: 本进程监听的端口，只用于给自建目录起个可辨认的名字。

    Returns:
        本进程专用的数据根目录。
    """
    if explicit:
        home = Path(explicit).resolve()
        home.mkdir(parents=True, exist_ok=True)
        return home
    return Path(tempfile.mkdtemp(prefix=f"dsf-e2e-{port}-"))


def _claim_data_home(data_home: Path) -> FileLock:
    """在数据根里立占用锁，供别的进程判断这份数据根还有没有主。

    Args:
        data_home: 数据根目录。

    Returns:
        已持有的文件锁，由调用方持有到进程结束。
    """
    lock = FileLock(str(data_home / _OWNER_LOCK_NAME))
    lock.acquire()
    return lock


def _release_and_remove(data_home: Path, lock: FileLock) -> None:
    """收尾：先放锁、再删目录。

    Args:
        data_home: 自建的临时数据根。
        lock: 该数据根的占用锁。
    """
    # 顺序不能反：Windows 上被进程锁住的文件删不掉，先删会留下一份删不干净的残留。
    lock.release()
    shutil.rmtree(data_home, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数：端口与数据根都可指定，一个测试文件一份独立环境。"""
    parser = argparse.ArgumentParser(description="E2E 被测服务（系统 + 假模型端点）")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"监听端口（默认 {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--data-home",
        default=None,
        help="数据根目录（默认在系统临时目录里自建一份）",
    )
    return parser.parse_args(argv)


def build_fake_llm_app() -> FastAPI:
    """提供固定回复及可显式放行的模型，用于验证真实运行中的停止。"""
    app = FastAPI()

    @app.post("/__test__/gated-entered")
    def gated_entered() -> dict[str, bool]:
        """返回模型请求是否已到达等待点。"""
        return {"entered": _GATED_ENTERED.is_set()}

    @app.post("/__test__/gated-release")
    def gated_release() -> dict[str, bool]:
        """允许等待中的模型返回。"""
        _GATED_RELEASE.set()
        return {"released": True}

    @app.post("/__test__/gated-reset")
    def gated_reset() -> dict[str, bool]:
        """为串行测试重置等待点。"""
        _GATED_ENTERED.clear()
        _GATED_RELEASE.clear()
        return {"reset": True}

    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict[str, object]) -> Response:
        """返回固定的 OpenAI 兼容响应。"""
        model = payload.get("model", "fake-e2e-model")
        if model == _GATED_MODEL:
            _GATED_ENTERED.set()
            if not _GATED_RELEASE.wait(timeout=30):
                return JSONResponse({"error": "gate timed out"}, status_code=504)
        if payload.get("stream"):
            import json

            def chunks() -> object:
                half = len(_FAKE_REPLY) // 2
                for piece in (_FAKE_REPLY[:half], _FAKE_REPLY[half:]):
                    frame = {
                        "id": "chatcmpl-e2e-stream",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": piece},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
                stop = {
                    "id": "chatcmpl-e2e-stream",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(stop, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(chunks(), media_type="text/event-stream")
        return JSONResponse(
            {
                "id": "chatcmpl-e2e-001",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": _FAKE_REPLY,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        )

    return app


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.WARNING)

    _sweep_stale_data_homes(grace_seconds=_STALE_DATA_HOME_GRACE_SECONDS)
    owns_data_home = args.data_home is None
    data_home = _resolve_data_home(args.data_home, port=args.port)
    # 数据根必须在导入 dataset_factory 之前落到环境变量上：应用在导入期就解析它。
    os.environ["DATASET_FACTORY_HOME"] = str(data_home)
    owner_lock = _claim_data_home(data_home)
    if owns_data_home:
        # 自建的临时数据根由本进程负责收尾；--data-home 显式指定的归调用方管，
        # 不自作主张删掉（那种场景通常是有人想跑完进去看现场）。
        atexit.register(_release_and_remove, data_home, owner_lock)

    from dataset_factory.api import create_app
    from dataset_factory.llm import DEFAULT_CONFIG_NAME, SecretValue, create_config
    from starlette.routing import Mount

    system_app = create_app()
    # 端点配置预先写进数据根：打标请求将指向同源 /fake-llm/v1。
    create_config(
        DEFAULT_CONFIG_NAME,
        base_url=f"http://127.0.0.1:{args.port}/fake-llm/v1",
        model="fake-e2e-model",
        api_key=SecretValue("sk-e2e-not-a-real-key"),
    )
    # 假端点必须**插在路由表最前**：system_app 已经有一个 mount("/", StaticFiles)
    # （前端托管），Starlette 按注册顺序匹配，/ 前缀会吞掉后面所有路径——
    # 后置的 mount("/fake-llm") 永远轮不到，POST 还会被 StaticFiles 回 405。
    system_app.routes.insert(0, Mount("/fake-llm", app=build_fake_llm_app()))

    import uvicorn

    uvicorn.run(
        system_app, host="127.0.0.1", port=args.port, log_config=None, access_log=False
    )


if __name__ == "__main__":
    main()
