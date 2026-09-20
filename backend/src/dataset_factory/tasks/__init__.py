"""tasks 包：长任务句柄横切设施（二期新增）。

为什么单立一包：导入 / 搬迁 / 打包三类长任务共用「202 + task_id → 轮询 + cancel」
这套模式（Google LRO 同构，见 design「后端端点清单」横切约定）；但各任务的自愈语义
（重入幂等 / 旧位置完整 / 删半截 zip）由调用方在任务体里实现，本包只管任务状态机。

关键取舍：
- **任务体 = 同步阻塞函数、跑在工作线程**（asyncio.to_thread）——文件复制 / 打包是
  阻塞 IO，占着事件循环会卡死其他请求；协作式取消用 threading.Event（循环线程 set、
  工作线程检查，跨线程可见）。
- **状态存内存、重启即丢**——三类任务均有自愈语义，界面不设「恢复任务」入口；
  任务 404 只指动作不解释机制（design 2026-09-16 定）。
- **状态词汇四态**：running / succeeded / failed / cancelled；cancelled 只能由任务体
  检查到取消信号后抛 TaskCancelled 进入（任务体决定停在哪，不被强杀）；正常返回即
  succeeded，即使取消信号已置位（收尾竞态下以实际完成为准）。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from collections.abc import Callable
from time import perf_counter

from .._obs import ms_since

__all__ = [
    "RETRY_AFTER_SECONDS",
    "TaskCancelledError",
    "TaskInfo",
    "TaskManager",
    "TaskNotFoundError",
]

#: Retry-After 建议轮询间隔（秒）：受理 202 响应带此头，提示前端轮询节奏
#: （Microsoft Graph 长动作同款；design 2026-09-16 定）。
RETRY_AFTER_SECONDS = 2

#: task_id 长度（随机短 ID，与 wid 同一量级）。
_TASK_ID_LENGTH = 12

TaskId = str
#: 任务结果载荷（JSON 可序列化；由任务体返回，如导入条数 / 导出路径）。
TaskResult = dict[str, object]

logger = logging.getLogger(__name__)


class TaskNotFoundError(Exception):
    """任务不存在（可能因服务重启内存清空）。消息只指动作，不解释机制。"""


class TaskCancelledError(Exception):
    """任务体检查到取消信号后抛出，干净退出；管理器统一记 cancelled。"""


#: 长任务执行体：`(task_id, should_stop) -> 结果载荷 | None`（同步阻塞函数，管理器丢线程）。
#:
#: 协作式取消约定：任务体在安全点（每处理完一个文件等）检查 `should_stop.is_set()`，
#: 需要中止就抛 `TaskCancelledError`——管理器据此记 cancelled；不检查也不影响正确性，
#: 只是取消不生效（任务自然跑完记 succeeded）。自愈语义由任务体保证，不由本包负责。
TaskRunner = Callable[[TaskId, threading.Event], TaskResult | None]


class TaskInfo:
    """任务只读快照（GET /tasks/{id} 响应体）。

    Attributes:
        id: 任务短 ID。
        status: running / succeeded / failed / cancelled 之一。
        progress: 0.0–1.0，任务体经管理器回报；未回报为 0.0。
        result: 成功载荷；未完成为 None。
        error: 失败时的可操作错误消息；其余状态为 None。
    """

    def __init__(self, task_id: TaskId) -> None:
        """以 running 起始建快照（create 即受理，无 pending 态）。"""
        self.id = task_id
        self.status: str = "running"
        self.progress: float = 0.0
        self.result: TaskResult | None = None
        self.error: str | None = None


class _TaskRecord:
    """内部任务记录：快照 + 取消信号 + 受理时刻。"""

    __slots__ = ("accepted", "info", "should_stop")

    def __init__(self, task_id: TaskId) -> None:
        self.info = TaskInfo(task_id)
        self.should_stop = threading.Event()
        # 受理时刻：与「开始执行」相减 = 事件循环调度 + 线程池排队耗时（perf_counter 单调时钟）。
        self.accepted = perf_counter()


def _generate_task_id() -> TaskId:
    """随机短 ID（secrets，与 wid 同一「随机短 ID + 查重」模式）。"""
    return secrets.token_hex(6)[:_TASK_ID_LENGTH]


def _invoke(
    runner: TaskRunner,
    task_id: TaskId,
    should_stop: threading.Event,
    accepted: float,
) -> TaskResult | None:
    """在工作线程里执行任务体，并留下「线程真的跑起来了」这一行。

    受理行与这一行之间的时间差 = 事件循环调度 + 线程池排队。任务卡住时先看这段：它把
    「任务没被派出去」和「任务派出去了但自己不推进」两种完全不同的故障分开。

    Args:
        runner: 任务体（同步阻塞函数）。
        task_id: 任务短 ID。
        should_stop: 协作式取消信号。
        accepted: 受理时刻的 ``perf_counter()`` 读数。

    Returns:
        任务体返回的结果载荷（可能为 None）。
    """
    logger.info(
        "长任务开始执行：%s（受理后 %.0fms，线程 %s）",
        task_id,
        ms_since(accepted),
        threading.current_thread().name,
    )
    return runner(task_id, should_stop)


class TaskManager:
    """内存态任务管理器：受理、查状态、协作式取消。

    生命周期 = 进程生命周期（重启即丢，不落盘——自愈语义使恢复记录无必要）。
    并发模型：create 在事件循环内调用、立刻派工作线程跑任务体；get / update_progress /
    set_progress 等读改的是独立字段（GIL 下赋值原子），无需加锁。
    """

    def __init__(self) -> None:
        """空表起步；任务记录随进程存活、重启即清。"""
        self._tasks: dict[TaskId, _TaskRecord] = {}

    def create(self, runner: TaskRunner) -> TaskId:
        """受理一个长任务：分配 task_id、立即派后台线程执行。

        任务体是 :data:`TaskRunner`——同步阻塞函数，管理器传入 task_id 与 should_stop 事件；
        它按 `TaskCancelledError` 报告取消、按返回值报告结果，其余异常一律记 failed。

        Returns:
            新分配的 task_id。
        """
        task_id = _generate_task_id()
        while task_id in self._tasks:  # 生成时查重（撞号重摇）
            task_id = _generate_task_id()
        record = _TaskRecord(task_id)
        self._tasks[task_id] = record
        # 受理 / 开始执行 / 终态这三行是「任务停在原地」类故障的唯一线索：从受理到线程真正
        # 开始跑之间隔着事件循环调度与线程池排队，这三段时间外部一律显示为「运行中 · 0%」，
        # 只能靠日志分开。三行都自动带上创建它的那个请求的 request id（to_thread 会传播
        # contextvars），因此能与中间件记的 POST 行对上。
        logger.info("长任务受理：%s", task_id)
        # 包一层 asyncio 任务：事件循环不被阻塞（to_thread 丢线程池），异常集中收口。
        asyncio.get_running_loop().create_task(
            self._run(record, runner), name=f"task-{task_id}"
        )
        return task_id

    async def _run(self, record: _TaskRecord, runner: TaskRunner) -> None:
        """任务包装：丢线程执行任务体，按退出方式归档状态。"""
        task_id = record.info.id
        try:
            result = await asyncio.to_thread(
                _invoke, runner, task_id, record.should_stop, record.accepted
            )
        except TaskCancelledError:
            record.info.status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - 任务体异常边界：任何业务异常都归档为 failed
            record.info.status = "failed"
            record.info.error = str(exc) or type(exc).__name__
            # 失败原因同时进日志：任务失败大多是用户级校验（目标已存在、路径不合法…），
            # 界面会显示、日志里却什么都没有——事后只看得到「状态 failed」，查不出为什么
            # （2026-09-20 实锤：搬迁任务 15ms 就 failed，日志里只有状态、没有原因）。
            logger.warning(
                "长任务失败：%s（受理至失败 %.0fms）：%s",
                task_id,
                ms_since(record.accepted),
                record.info.error,
            )
        else:
            record.info.status = "succeeded"
            if result is not None:
                record.info.result = result
        logger.info(
            "长任务结束：%s 状态 %s（受理至结束 %.0fms）",
            task_id,
            record.info.status,
            ms_since(record.accepted),
        )

    def get(self, task_id: TaskId) -> TaskInfo:
        """查任务快照；不存在抛 TaskNotFoundError（404，消息只指动作）。"""
        record = self._tasks.get(task_id)
        if record is None:
            raise TaskNotFoundError(
                "任务已失效（服务重启会清空任务），请回到原入口重新执行。"
            )
        return record.info

    def set_progress(self, task_id: TaskId, progress: float) -> None:
        """任务体（工作线程内）回报进度；越界钳到 0.0–1.0。

        终态后的回报为 no-op（收尾竞态下不得改写已归档状态）。
        """
        record = self._tasks.get(task_id)
        if record is None or record.info.status != "running":
            return
        clamped = min(1.0, max(0.0, progress))
        if clamped > 0 and record.info.progress == 0:
            # 只记首次：进度是逐文件回报的，逐条记会把日志淹掉。首个非零进度意味着任务体
            # 已越过「盘点 / 建目录」这类前置阶段——挪不动进展时看它到没到。
            logger.info("长任务首个进度：%s（%.0f%%）", task_id, clamped * 100)
        record.info.progress = clamped

    def request_cancel(self, task_id: TaskId) -> TaskInfo:
        """协作式取消：置取消信号即返回当前快照。

        何时真正停由任务体决定（安全点检查信号）；已终态的任务调用本方法为
        no-op（幂等返回现状，前端拿到的就是最新状态）。
        不存在抛 TaskNotFoundError。
        """
        record = self._tasks.get(task_id)
        if record is None:
            raise TaskNotFoundError(
                "任务已失效（服务重启会清空任务），请回到原入口重新执行。"
            )
        if record.info.status == "running":
            record.should_stop.set()
        return record.info
