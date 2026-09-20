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
# 自上次 reset 起「进过闸门」的调用次数。闸门是全局的，端点连通性探测（前端 chip
# 自动发起，走同一个端点配置与模型名）也会进闸门——只报「有没有」无法区分是谁进的，
# 于是「条目正在模型调用中」这个握手可能被探测调用抢先满足（2026-09-20 实锤）。
_GATED_ENTERED_COUNT = 0
_GATED_COUNT_GUARD = threading.Lock()

# 日志级别沿用 `dsf serve` 的口径（环境变量 DSF_LOG_LEVEL，缺省 INFO）。这里把变量名写一遍
# 而不去 import cli 里的：那是个私有名，跨模块引用私有名会被 pyright strict 判违规。
_LOG_LEVEL_ENV = "DSF_LOG_LEVEL"
_DEFAULT_LOG_LEVEL = "INFO"

logger = logging.getLogger(__name__)


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


def _carries_asset(payload: dict[str, object]) -> bool:
    """这次模型调用是否带了素材（图片 / 视频走 image_url / video_url 内容块）。

    用途：把「批次的打标调用」与「端点连通性探测」分开。闸门是全局的，而前端 chip 的
    连通性探测走同一个端点配置与模型名——不分开，探测就会先进闸门、把测试的
    ``gated-entered`` 握手抢先满足，测试于是在条目**尚未派发**时就按了停止
    （2026-09-20 实锤：该次运行全长 183ms、尝试 0、无产物，而 succeeded 断言
    在这个交织里永远不可能成立）。

    探测（``llm.probe_endpoint``）只发一条 text "ping"、不带素材，所以「带不带素材」
    是两者在 payload 上最本质的差别。一旦这个判据失准，后果是**大声失败**——
    gated-entered 永不置位、测试的 poll 直接超时——不会静默放行。

    Args:
        payload: 收到的 chat completions 请求体。

    Returns:
        请求消息里是否存在素材内容块。
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    asset_block_types = {"image_url", "video_url"}
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in asset_block_types:
                return True
    return False


def build_fake_llm_app() -> FastAPI:
    """提供固定回复及可显式放行的模型，用于验证真实运行中的停止。"""
    app = FastAPI()

    @app.post("/__test__/gated-entered")
    def gated_entered() -> dict[str, object]:
        """返回模型请求是否已到达等待点，以及自上次 reset 起进过闸门的次数。

        次数是排查「握手被谁满足」的关键：count 比测试开始发车时的基线多 1 以上，
        说明有可能是别的调用（如端点连通性探测）先进了闸门。
        """
        with _GATED_COUNT_GUARD:
            count = _GATED_ENTERED_COUNT
        return {"entered": _GATED_ENTERED.is_set(), "count": count}

    @app.post("/__test__/gated-release")
    def gated_release() -> dict[str, bool]:
        """允许等待中的模型返回。"""
        _GATED_RELEASE.set()
        return {"released": True}

    @app.post("/__test__/gated-reset")
    def gated_reset() -> dict[str, bool]:
        """为串行测试重置等待点与计数。"""
        global _GATED_ENTERED_COUNT
        _GATED_ENTERED.clear()
        _GATED_RELEASE.clear()
        with _GATED_COUNT_GUARD:
            _GATED_ENTERED_COUNT = 0
        return {"reset": True}

    @app.post("/v1/chat/completions")
    def chat_completions(payload: dict[str, object]) -> Response:
        """返回固定的 OpenAI 兼容响应。

        只有**带素材**的调用才进闸门：连通性探测走同一个模型名，但它不带素材，让它
        也进闸门会把测试的「条目正在模型调用中」握手污染掉（见 :func:`_carries_asset`）。
        """
        model = payload.get("model", "fake-e2e-model")
        if model == _GATED_MODEL and _carries_asset(payload):
            global _GATED_ENTERED_COUNT
            with _GATED_COUNT_GUARD:
                _GATED_ENTERED_COUNT += 1
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


def _configure_logging() -> None:
    """把被测服务的日志接到应用自己的配置上（stderr + request id），级别读 DSF_LOG_LEVEL。

    必须在 ``DATASET_FACTORY_HOME`` 落到环境变量之后再调用——应用在导入期就解析数据根，
    而本函数要导入应用模块（同 main 里那句注释的理由）。

    为什么要配：E2E 的价值全在「失败时能查」。原先这里把 root logger 压到 WARNING、
    uvicorn 的 access log 又是关的，于是「任务停在原地」这类故障现场一行证据都不留——
    本轮连跑五轮的失败日志里没有一条服务输出，排查只能靠猜。
    """
    from dataset_factory.cli.main import configure_logging

    configure_logging(os.environ.get(_LOG_LEVEL_ENV, _DEFAULT_LOG_LEVEL))
    # 请求记录由应用中间件负责（带耗时与 request id，比 uvicorn 的 access log 信息多），
    # 这里把 uvicorn 自己的 logger 压到 WARNING：同一件事不打两遍，也免掉逐连接噪声。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    args = _parse_args()

    _sweep_stale_data_homes(grace_seconds=_STALE_DATA_HOME_GRACE_SECONDS)
    owns_data_home = args.data_home is None
    data_home = _resolve_data_home(args.data_home, port=args.port)
    # 数据根必须在导入 dataset_factory 之前落到环境变量上：应用在导入期就解析它。
    os.environ["DATASET_FACTORY_HOME"] = str(data_home)
    _configure_logging()
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

    logger.info(
        "E2E 被测服务启动中：http://127.0.0.1:%d（数据根 %s）", args.port, data_home
    )
    uvicorn.run(
        system_app, host="127.0.0.1", port=args.port, log_config=None, access_log=False
    )


if __name__ == "__main__":
    main()
