"""跑批执行器：串行逐条调用 labeling 纯素材路径，产出运行日志三件套与业务事件。

线程与并发模型（design 定案，串行 = 并发度 1 的同一条代码路径）：``run()`` 在调用
线程里完整执行（Web 层开线程跑、CLI 前台直跑），运行锁归这条线程；条目结果统一由
它落盘（日志单写入者）；``stop()`` 可从其他线程置位协作取消信号（threading.Event），
跑批在条目边界停下——已完成部分保留，批次随时可继续。

full 模式：计划 = 工作目录现状 ∩ 导入登记（批次成员由登记界定）；启动时对「有产物」
条目现算素材哈希、与最近一次成功打标的哈希比对（E1），一致才跳过——「处理时刻的
输入哈希」是判定「变在打标前还是打标后」的唯一锚点。
retry 模式：计划 = 重试列表快照（运行开始的瞬间拍下，运行期编辑只影响下一次）；
结束后成功的条目出列、仍失败的保留（「还没补完的账」）。

失败重试（F5）：可重试类（network / timeout / rate-limit / 5xx / llm-content）首次
尝试 + 最多重试 3 次（1s / 2s / 4s 退避乘随机抖动；429 优先遵循端点 Retry-After；
单条最多 4 次请求）；不可重试类（bad-request / config / asset-unreadable）不重试，
记为失败跳过。失败条目不产生产物，逐条进运行流水。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from .._fs import atomic_write_text
from ..labeling import LabelingEngine, MaterialOversizeError, MaterialReadError
from ..llm import (
    SUPPORTED_API_FORMAT,
    Completer,
    EndpointConfig,
    build_completer,
    parse_request_params,
    read_stored_api_key,
    resolve_api_key,
)
from ..llm.endpoints import ConfigError
from ..llm.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConnectionError,
    LLMError,
    LLMNotFoundError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMUnexpectedError,
    UnsupportedImageError,
)
from ..strategies.batches import delete_batch, get_batch, read_snapshot
from ..strategies.snapshot import tool_version
from ..workdir.assets import (
    product_filename,
    product_has_content,
    registered_origins,
    scan_assets,
)
from ..workdir.errors import WorkdirMetadataCorruptedError
from ..workdir.importer import hash_file
from ..workdir.locks import RunLock
from ..workdir.store import WorkdirStore
from .control import stop_requested
from .errors import BatchInactiveError
from .journal import RunJournal, load_recent_success_hashes

__all__ = [
    "RETRYABLE_REASON_CODES",
    "BatchRunner",
    "ItemUpdatedEvent",
    "RunFinishedEvent",
    "RunMode",
    "RunReport",
    "RunStartedEvent",
    "RunTrigger",
    "add_retry_items",
    "clear_retry_list",
    "completer_for_snapshot",
    "read_retry_list",
    "remove_retry_items",
]

logger = logging.getLogger(__name__)

#: 跑批模式：full = 全量（未完成的都打）；retry = 只打重试列表快照。
RunMode = Literal["full", "retry"]
#: 触发来源（进 run.json 的出身记录）。
RunTrigger = Literal["web", "cli"]

# 失败重试的节奏（F5）：首次尝试 + 最多 3 次重试 = 单条最多 4 次请求。
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1.0
_JITTER_RATIO = 0.2

#: run.json 里 status 的取值：骨架写 running，正常结束写 completed，被停止写 interrupted。
_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_INTERRUPTED = "interrupted"
#: 进度快照（current 端点）的运行前状态：已受理、后台线程尚未跑起来。
_STATUS_PENDING = "pending"
#: 进度快照的失败终态：跑批没能启动（如跨进程锁被占 / 快照在启动前被删）。
_STATUS_FAILED = "failed"

# state.json 的重试列表键（结构由本域定义，WorkdirStore 只忠实存取；缺键视为空）。
_RETRY_LIST_KEY = "retry_list"


@dataclass(frozen=True)
class RunReport:
    """一次运行的最终报告（run() 的返回值；CLI 汇报与 Web 收尾的数据源）。

    Attributes:
        run_id: 运行 id（= ``.dsf/runs/`` 下的目录名）。
        status: ``completed``（跑完计划）或 ``interrupted``（被手动停止）。
        mode: 本次运行模式。
        counters: 计数（planned / attempted / succeeded / failed / skipped）。
        run_dir: 运行目录绝对路径（run.log 对外展示用）。
    """

    run_id: str
    status: str
    mode: str
    counters: dict[str, int]
    run_dir: Path


@dataclass(frozen=True)
class RunStartedEvent:
    """run-started：运行已启动、计划已定（SSE 首帧）。"""

    run_id: str
    mode: str
    batch: int
    planned: int

    @property
    def kind(self) -> str:
        """SSE 事件类型名。"""
        return "run-started"

    def to_payload(self) -> dict[str, Any]:
        """序列化为 SSE data 载荷。"""
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "batch": self.batch,
            "planned": self.planned,
        }


@dataclass(frozen=True)
class ItemUpdatedEvent:
    """item-updated：条目状态变化（started = 开始打标；succeeded / failed = 终态）。"""

    item: str
    batch: int
    status: str
    attempt: int
    reason_code: str | None = None
    message: str | None = None

    @property
    def kind(self) -> str:
        """SSE 事件类型名。"""
        return "item-updated"

    def to_payload(self) -> dict[str, Any]:
        """序列化为 SSE data 载荷。"""
        return {
            "item": self.item,
            "batch": self.batch,
            "status": self.status,
            "attempt": self.attempt,
            "reason_code": self.reason_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class RunFinishedEvent:
    """run-finished：运行结束（completed / interrupted），带最终计数。"""

    run_id: str
    batch: int
    status: str
    counters: dict[str, int]
    error: str | None = None

    @property
    def kind(self) -> str:
        """SSE 事件类型名。"""
        return "run-finished"

    def to_payload(self) -> dict[str, Any]:
        """序列化为 SSE data 载荷。"""
        return {
            "run_id": self.run_id,
            "batch": self.batch,
            "status": self.status,
            "counters": dict(self.counters),
            "error": self.error,
        }


#: 业务事件的联合（SSE 桥接与 CLI 进度打印的消费对象）。
RunEvent = RunStartedEvent | ItemUpdatedEvent | RunFinishedEvent


class _BlankCaptionError(Exception):
    """内部哨兵：模型返回了空白描述——按 llm-content（可重试）失败处理。"""


class BatchRunner:
    """一个批次的跑批执行器：持锁、定计划、逐条打标、落三件套、发事件。

    一次运行对应一个实例；同一工作目录同一时刻只允许一个运行（运行锁保证）。
    ``completer`` 与 :class:`~dataset_factory.labeling.LabelingEngine` 同款注入式
    设计——测试注入假实现即可离线跑全流程。
    """

    def __init__(
        self,
        workdir: Path,
        seq: int,
        completer: Any,
        *,
        mode: RunMode,
        trigger: RunTrigger,
        retry_items: list[str] | None = None,
        video_fps: int = 2,
        video_max_frames: int = 16,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        """绑定批次与运行参数。

        Args:
            workdir: 工作目录路径（须已登记）。
            seq: 批次序号（sN 的 N）。
            completer: 实现 llm.Completer 协议的客户端（逐条打标的唯一模型通道）。
            mode: full / retry。
            trigger: web / cli（出身记录）。
            retry_items: 本次重打的明确条目；仅 retry 模式可用，不替换持久名单。
            video_fps: 视频条目的抽帧 fps。
            video_max_frames: 视频条目的抽帧帧数上限。
            sleeper: 退避等待函数（注入替代 time.sleep，测试不打真盹）。
        """
        self._workdir = workdir
        if retry_items is not None and (mode != "retry" or not retry_items):
            raise ValueError("明确条目仅用于 retry 模式，且不能为空。")
        self._retry_items = (
            list(dict.fromkeys(retry_items)) if retry_items is not None else None
        )
        self._seq = seq
        self._completer = completer
        self._mode: RunMode = mode
        self._trigger: RunTrigger = trigger
        self._video_fps = video_fps
        self._video_max_frames = video_max_frames
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._stop_event = threading.Event()
        self._owns_run_lock = False
        self._subscribers: list[Callable[[RunEvent], None]] = []
        self._subscriber_guard = threading.Lock()
        self._finished_event: RunFinishedEvent | None = None
        # 进度快照（current 端点的数据源）：run_id 构造时预分配（POST 受理响应要
        # 立即返回它；目录创建仍在持锁后进行——跨进程同秒撞名时输家抢不到锁、
        # 不会真建目录）。counters 全程持有一份运行中镜像，供无锁读取。
        self._run_id = _new_run_id(WorkdirStore(workdir).runs_dir)
        self._status = _STATUS_PENDING
        self._error: str | None = None
        self._current_item: str | None = None
        self._counters: dict[str, int] = {
            "planned": 0,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
        }

    # -- 进度快照（current 端点数据源；跨线程无锁读——单键读写原子性够用） ----

    def snapshot(self) -> dict[str, Any]:
        """当前运行进度快照：run_id / batch / mode / status / counters / 当前条目。"""
        return {
            "run_id": self._run_id,
            "batch": self._seq,
            "mode": self._mode,
            "status": self._status,
            "counters": dict(self._counters),
            "current_item": self._current_item,
            "error": self._error,
        }

    # -- 停止与事件订阅（供入口层跨线程调用） --------------------------------

    def stop(self) -> None:
        """请求停止（协作取消）：当前条目在下一个安全点（条目边界 / 退避后）停下。"""
        self._stop_event.set()

    def _should_stop(self) -> bool:
        if self._stop_event.is_set():
            return True
        if (
            stop_requested(self._workdir, self._run_id)
            or not get_batch(self._workdir, self._seq).active
        ):
            self._stop_event.set()
        return self._stop_event.is_set()

    def subscribe(self, callback: Callable[[RunEvent], None]) -> Callable[[], None]:
        """订阅业务事件（多播——多个 SSE 连接各订各的），返回退订函数。

        终态与订阅原子交接，避免订阅前刚好结束导致消费者永远等不到收尾。
        回调异常只记日志、绝不中断跑批
        （消费者死了不该连累生产者）。
        """
        with self._subscriber_guard:
            finished = self._finished_event
            if finished is None:
                self._subscribers.append(callback)
        if finished is not None:
            try:
                callback(finished)
            except Exception:
                logger.warning("跑批终态订阅者回调异常（已忽略）", exc_info=True)

        def _unsubscribe() -> None:
            with self._subscriber_guard, suppress(ValueError):
                self._subscribers.remove(callback)

        return _unsubscribe

    # -- 主流程 ---------------------------------------------------------------

    def run(self) -> RunReport:
        """执行一次跑批（阻塞到结束 / 停止），返回最终报告。

        启动失败的异常（跨进程锁被占、批次在受理后被删等）在此收口：状态置
        ``failed``、错误信息进进度快照、向订阅者发一条 failed 的 run-finished
        事件后原样重抛——HTTP 受理已返回 202，失败原因只能走 SSE / current 呈现。

        Raises:
            BatchNotFoundError: 批次不存在（strategies 域异常冒泡）。
            BatchInactiveError: 批次已停用（隐藏），不允许跑批。
            StrategyNotFoundError: 快照缺失或损坏（批次元数据与文件不一致）。
            RunOccupiedError: 工作目录已有跑批在运行（运行锁被占用）。
            RunJournalCorruptedError: 历史运行流水损坏（full 模式续跑判定要读它）。
        """
        self._status = _STATUS_RUNNING
        try:
            return self._run()
        except Exception as exc:
            self._status = _STATUS_FAILED
            self._error = str(exc)
            self._emit(
                RunFinishedEvent(
                    run_id=self._run_id,
                    batch=self._seq,
                    status=_STATUS_FAILED,
                    counters=self._counters,
                    error=self._error,
                )
            )
            raise

    def _run(self) -> RunReport:
        """run() 的原始执行体（锁的获取与释放都在这里）。

        acquire 也在 try/finally 内：抢锁成功后的任何失败（如 run-info 写盘，
        虽已被 RunLock 内部消化）都必须走到 release——锁泄漏等于该工作目录
        死锁到进程重启，违背文件锁「无需人工清理」的选型根基。
        """
        entry = get_batch(self._workdir, self._seq)
        if not entry.active:
            raise BatchInactiveError(
                f"批次 s{self._seq} 已停用（隐藏）——请先在批次列表里「显示」再跑批。"
            )
        snapshot = read_snapshot(self._workdir, self._seq)
        store = WorkdirStore(self._workdir)
        lock = RunLock(store.dsf_path)
        lock.acquire(
            {
                "pid": os.getpid(),
                "started_at": _utc_now_iso(),
                "hostname": platform.node(),
                "batch": f"s{self._seq}",
                "mode": self._mode,
                "run_id": self._run_id,
            }
        )
        try:
            self._owns_run_lock = True
            snapshot = read_snapshot(self._workdir, self._seq)
            return self._run_locked(store, snapshot)
        finally:
            self._owns_run_lock = False
            lock.release()

    def _run_locked(self, store: WorkdirStore, snapshot: Any) -> RunReport:
        """持锁后的执行主体：定计划 → 逐条打标 → 收尾（全部在调度线程）。

        计划构建放在创建运行目录**之前**：计划阶段失败（历史流水损坏等）零痕迹，
        不留下空 run 目录。
        """
        counters = self._counters
        planned_stems: list[str] = []
        assets: dict[str, Path] = {}
        if self._mode == "retry":
            if self._retry_items is not None:
                add_retry_items(self._workdir, self._seq, self._retry_items)
            planned_stems = (
                list(self._retry_items)
                if self._retry_items is not None
                else list(read_retry_list(self._workdir, self._seq))
            )
        else:
            planned_stems, skip_stems, assets = _plan_full(
                self._workdir, self._seq, store.runs_dir
            )
            counters["skipped"] = len(skip_stems)

        journal = RunJournal(store.runs_dir / self._run_id)
        started_at = _utc_now_iso()
        model = cast(str, snapshot.endpoint["model"])
        engine = LabelingEngine(self._completer, model)

        counters["planned"] = len(planned_stems)
        strategy_hash = _snapshot_file_hash(store, self._seq)
        run_meta: dict[str, object] = {
            "run_id": self._run_id,
            "batch": self._seq,
            "mode": self._mode,
            "trigger": self._trigger,
            "strategy_hash": strategy_hash,
            "snapshot": f"strategies/s{self._seq}.json",
            "dsf_version": tool_version(),
            "status": _STATUS_RUNNING,
            "counters": dict(counters),
            "started_at": started_at,
            "finished_at": None,
        }
        journal.write_run_json(run_meta)
        journal.append_log_line(
            f"[{started_at}] 启动：批次 s{self._seq}、模式 {self._mode}、"
            f"触发 {self._trigger}、计划 {counters['planned']} 条"
            f"（启动时跳过 {counters['skipped']}）、快照哈希 {strategy_hash[:12]}…、"
            f"工具 {tool_version()}"
        )
        self._emit(
            RunStartedEvent(
                run_id=self._run_id,
                mode=self._mode,
                batch=self._seq,
                planned=counters["planned"],
            )
        )

        interrupted = False
        succeeded_items: list[str] = []
        for item in planned_stems:
            if self._should_stop():
                interrupted = True
                break
            self._current_item = item
            # 素材路径与计划同源（full 用计划期的目录扫描结果，retry 现解析）——
            # 「比对哈希的那份」与「实际读取送模型的那份」必须是同一个文件。
            asset_path = (
                assets.get(item)
                if self._mode == "full"
                else _resolve_asset(self._workdir, item)
            )
            if self._label_one(journal, engine, snapshot, item, asset_path):
                succeeded_items.append(item)

        status = (
            _STATUS_INTERRUPTED
            if interrupted or self._should_stop()
            else _STATUS_COMPLETED
        )
        self._status = status
        finished_at = _utc_now_iso()
        run_meta["status"] = status
        run_meta["counters"] = dict(counters)
        run_meta["finished_at"] = finished_at
        # 终态 run.json 先落（attempted 由逐条记账实时维护，收尾不再重算）——
        # 之后的收尾步骤全部「可容忍失败」：它们失败只损失便利，绝不能把已经
        # 落盘的终态推翻成快照里的 failed（机制读到的终态必须唯一）。
        journal.write_run_json(run_meta)
        if self._mode == "retry" and succeeded_items:
            # 出列经 mutate_state 在状态锁内完成（调度线程持运行锁 → 短暂取状态锁，
            # 锁序 run.lock → state.lock）：本次成功的条目移出重试列表，仍失败与
            # 中断没跑到的保留（「还没补完的账」）。失败可容忍：出列没成功 =
            # 成功条目仍留在列表，下次重试幂等重打（多花一次调用，方向安全）。
            try:
                remove_retry_items(self._workdir, self._seq, succeeded_items)
            except (OSError, WorkdirMetadataCorruptedError):
                logger.warning(
                    "跑批 %s 的重试列表出列失败（成功条目仍在列表，下次重试幂等重打）",
                    self._run_id,
                    exc_info=True,
                )
        journal.append_log_line(
            f"[{finished_at}] 结束（{status}）：成功 {counters['succeeded']}、"
            f"失败 {counters['failed']}、跳过 {counters['skipped']}、"
            f"尝试 {counters['attempted']} / 计划 {counters['planned']}"
        )
        self._emit(
            RunFinishedEvent(
                run_id=self._run_id, batch=self._seq, status=status, counters=counters
            )
        )
        return RunReport(
            run_id=self._run_id,
            status=status,
            mode=self._mode,
            counters=dict(counters),
            run_dir=journal.run_dir,
        )

    # -- 单条执行 -------------------------------------------------------------

    def _label_one(
        self,
        journal: RunJournal,
        engine: LabelingEngine,
        snapshot: Any,
        item: str,
        asset_path: Path | None,
    ) -> bool:
        """打一条素材：退避重试循环到成功或判死，写流水、发事件、记人读日志。

        Returns:
            True = 最终成功（重试模式出列用）；False = 失败、素材缺失或中途中断。
        """
        self._emit(
            ItemUpdatedEvent(item=item, batch=self._seq, status="started", attempt=0)
        )

        attempt = 0
        while True:
            attempt += 1
            failure = self._attempt_one(
                journal, engine, snapshot, item, asset_path, attempt
            )
            if failure is None:
                return True

            # 失败路径：可重试且没到上限且没被停止 → 退避后重试；否则记死。
            if (
                failure.retryable
                and attempt < _MAX_ATTEMPTS
                and not self._should_stop()
            ):
                delay = failure.retry_after or _backoff_seconds(attempt)
                journal.append_log_line(
                    f"[{_utc_now_compact()}] {item} 尝试 {attempt} 失败"
                    f"（{failure.reason_code}：{_one_line(failure.message)}）；"
                    f"{delay:.1f}s 后重试"
                )
                self._sleep(delay)
                if self._should_stop():
                    # 退避中被打断：本条不写终态行（回到「排队中」，下次续跑再打）。
                    return False
                continue
            if self._should_stop():
                # 停止信号在：正在处理的条目无论失败可否重试都不写终态行——
                # 「被打断」不是「失败」（模型调用中打断与退避中打断口径一致），
                # 记成失败会让停止后的失败统计凭空多账。
                return False
            self._counters["failed"] += 1
            self._counters["attempted"] += 1
            journal.append_item(
                {
                    "item": item,
                    "batch": self._seq,
                    "status": "failed",
                    "attempt": attempt,
                    "reason_code": failure.reason_code,
                    "message": _one_line(failure.message),
                    "elapsed_ms": failure.elapsed_ms,
                }
            )
            self._emit(
                ItemUpdatedEvent(
                    item=item,
                    batch=self._seq,
                    status="failed",
                    attempt=attempt,
                    reason_code=failure.reason_code,
                    message=_one_line(failure.message),
                )
            )
            journal.append_log_line(
                f"[{_utc_now_compact()}] {item} 失败（{failure.reason_code}："
                f"{_one_line(failure.message)}）"
            )
            return False

    def _attempt_one(
        self,
        journal: RunJournal,
        engine: LabelingEngine,
        snapshot: Any,
        item: str,
        asset_path: Path | None,
        attempt: int,
    ) -> _Failure | None:
        """跑一次尝试：成功完成记账并返回 None；失败返回 _Failure（不产生产物）。

        成功路径刻意分两段：**产物原子写**失败按素材 / 系统问题转失败（重试同条
        多半还撞，且产物没写成、无双计风险）；**记账段**（items.jsonl / 事件 / 计数
        / 人读日志）在产物写成功之后执行——其中 items.jsonl 写盘失败属结构性错误，
        直接冒泡给 run() 的 failed 收口（此时产物已在盘上、计数未增、绝无「成功又
        记失败」的双计；E1 下次续跑按「有产物无锚点」重打，天然自愈）。
        """
        started = perf_counter()

        def _elapsed() -> int:
            return int((perf_counter() - started) * 1000)

        try:
            if asset_path is None:
                # raise 作统一失败处理的入口：缺失、空白描述与模型错误共用
                # 同一套「退避重试 / 记死」逻辑（TRY301 定点豁免，拆开反而散）。
                raise MaterialReadError(  # noqa: TRY301
                    f"素材 {item} 不在工作目录（缺失）——请先补回素材再重试。"
                )
            result = engine.label_material(
                asset_path,
                prompt_body=cast(str, snapshot.prompt["body"]),
                skill_texts=[cast(str, block["body"]) for block in snapshot.skills],
                video_fps=self._video_fps,
                video_max_frames=self._video_max_frames,
            )
            if not result.caption.strip():
                raise _BlankCaptionError()  # noqa: TRY301 —— 同上
        except _BlankCaptionError:
            return _Failure(
                "llm-content",
                "模型返回了空白描述；按内容层失败处理。",
                retryable=True,
                elapsed_ms=_elapsed(),
            )
        except (MaterialReadError, MaterialOversizeError) as exc:
            return _Failure("asset-unreadable", str(exc), elapsed_ms=_elapsed())
        except ValueError as exc:
            # 快照提示词空白 / 素材扩展名白名单外——批次配置类，重试不会变好。
            return _Failure("config", str(exc), elapsed_ms=_elapsed())
        except LLMError as exc:
            reason_code, retryable = _classify_llm_error(exc)
            return _Failure(
                reason_code,
                str(exc),
                retryable=retryable,
                retry_after=getattr(exc, "retry_after", None),
                elapsed_ms=_elapsed(),
            )
        # 成功：产物原子写（只有完整产物算已有产物，中断不留半截）。
        try:
            atomic_write_text(
                self._workdir / product_filename(self._seq, item), result.caption
            )
        except OSError as exc:
            return _Failure(
                "config",
                f"写入产物失败：{exc.strerror or exc}",
                elapsed_ms=_elapsed(),
            )
        # 记账段（产物已落盘；append_item 失败冒泡走 failed 收口，见 docstring）。
        journal.append_item(
            {
                "item": item,
                "batch": self._seq,
                "status": "succeeded",
                "attempt": attempt,
                "asset_hash": result.asset_hash,
                "elapsed_ms": _elapsed(),
            }
        )
        self._counters["succeeded"] += 1
        self._counters["attempted"] += 1
        self._emit(
            ItemUpdatedEvent(
                item=item, batch=self._seq, status="succeeded", attempt=attempt
            )
        )
        journal.append_log_line(f"[{_utc_now_compact()}] {item} 尝试 {attempt} 成功")
        return None

    # -- 事件多播 -------------------------------------------------------------

    def _emit(self, event: RunEvent) -> None:
        """把事件发给全部订阅者（回调异常只记日志，不中断跑批）。"""
        if self._owns_run_lock:
            try:
                atomic_write_text(
                    self._workdir / ".dsf" / "run-status.json",
                    json.dumps(self.snapshot(), ensure_ascii=False),
                )
            except OSError:
                logger.warning("运行进度快照写入失败", exc_info=True)
        with self._subscriber_guard:
            if isinstance(event, RunFinishedEvent):
                self._finished_event = event
            callbacks = list(self._subscribers)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                # 消费者异常不连累跑批；BLE001 对「记日志后继续」的宽捕获不报警
                # （memory 48④），无需 noqa。
                logger.warning("跑批事件订阅者回调异常（已忽略）", exc_info=True)


@dataclass(frozen=True)
class _Failure:
    """一次尝试的失败信息（内部中间对象，不进流水——落盘前拆成字段）。"""

    reason_code: str
    message: str
    retryable: bool = False
    retry_after: float | None = None
    elapsed_ms: int = 0


def _plan_full(
    workdir: Path, seq: int, runs_dir: Path
) -> tuple[list[str], list[str], dict[str, Path]]:
    """full 模式定计划：登记在册素材逐条判定「跳过 / 要打」。

    跳过判定（E1 续跑）：有产物（txt 非空白）且当前素材哈希与**本批次**最近一次
    成功打标一致 → 跳过；无产物、产物空白、无锚点（从没成功打过）、哈希不一致
    （打标后素材被换过）→ 重打。只对有产物条目现算哈希（无产物的本来就要打）。

    Returns:
        (要打的素材主干列表, 跳过的素材主干列表, 主干 → 素材路径映射)。
        前两个列表均按主干排序（确定性）；映射供执行循环直接取用——比对哈希的
        那份与实际读取送模型的那份必须是同一个文件（同主干多扩展时防两处各取各的）。
    """
    store = WorkdirStore(workdir)
    registered = set(registered_origins(store))
    assets = scan_assets(workdir)
    hashes = load_recent_success_hashes(runs_dir, seq)

    to_label: list[str] = []
    skipped: list[str] = []
    for stem, path in sorted(assets.items()):
        if path.name not in registered:
            continue  # 未登记素材不打标（M2：批次成员由导入登记界定）
        product = workdir / product_filename(seq, stem)
        if product_has_content(product):
            recorded = hashes.get(stem)
            if recorded is not None and hash_file(path) == recorded:
                skipped.append(stem)
                continue
        to_label.append(stem)
    return to_label, skipped, assets


def _resolve_asset(workdir: Path, item: str) -> Path | None:
    """按素材主干解析工作目录里的素材文件；找不到（缺失）返回 None。

    经 workdir 域的 scan_assets 同源解析（retry 模式的逐条解析——列表通常很短，
    全扫成本可忽略）。扫描归 workdir 域是因为条目视图与素材预览端点也要按同一份
    解析取素材：同主干多扩展并存时，比对哈希的那份、送模型的那份与界面预览的那份
    必须是同一个文件。
    """
    return scan_assets(workdir).get(item)


#: 可重试类原因码（F5 两类清单之一）：网络 / 超时 / 限流 / 服务端 / 内容层异常——
#: 再试一次有可能变好。另一半（bad-request / config / asset-unreadable）重试不会变好，
#: 直接记死。这份集合既是执行器退避重试的判定依据，也是条目视图「这条能不能加入
#: 重试列表」的依据：原因码由本模块产出、由 items 消费，两边读同一份清单。
#:
#: 它与 llm 异常类上的 ``retryable`` 类属性**不是同一份东西**，别把两边「修正」成一致：
#: 那是「单次 HTTP 调用值不值得重试」的判断，这里是「记进运行流水的原因码」层面的清单
#: ——流水里只有原因码，读不回异常对象，条目视图只能靠这份集合判。两者在 llm-content
#: 上有意分歧：裸 LLMError 在批量语境下意味着「模型没返回可用文本」，值得再试一次，
#: 而 llm 层对它的默认判定是不可重试。
RETRYABLE_REASON_CODES = frozenset(
    {"network", "timeout", "rate-limit", "5xx", "llm-content"}
)


def _reason_code(exc: LLMError) -> str:
    """把 llm 分类异常映射为原因码（F5 的两类清单）。

    判定顺序「先具体后一般」：各分类异常都是 LLMError 子类；裸 LLMError（模型没
    返回可用文本）属内容层异常。
    """
    if isinstance(exc, LLMRateLimitError):
        return "rate-limit"
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMConnectionError):
        return "network"
    if isinstance(exc, LLMServerError):
        return "5xx"
    if isinstance(exc, LLMBadRequestError):
        return "bad-request"
    if isinstance(exc, (LLMNotFoundError, LLMAuthError, LLMUnexpectedError)):
        return "config"
    if isinstance(exc, UnsupportedImageError):
        # 内容不是真图片（导入只看扩展名）——重试不会变好，按素材问题记死。
        return "asset-unreadable"
    return "llm-content"


def _classify_llm_error(exc: LLMError) -> tuple[str, bool]:
    """异常 → (原因码, 可否重试)；可重试与否一律查 RETRYABLE_REASON_CODES。"""
    code = _reason_code(exc)
    return code, code in RETRYABLE_REASON_CODES


def _backoff_seconds(attempt: int) -> float:
    """第 attempt 次尝试失败后的退避秒数：1s / 2s / 4s 乘 ±20% 随机抖动。"""
    base = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    # S311：抖动只为了让重试错峰，不是加密用途。
    return base * random.uniform(1 - _JITTER_RATIO, 1 + _JITTER_RATIO)  # noqa: S311


def _utc_now_iso() -> str:
    """当前 UTC 时刻（ISO 8601，进 run.json / 占用者信息）。"""
    return datetime.now(UTC).isoformat()


def _utc_now_compact() -> str:
    """当前 UTC 时刻（秒精度紧凑 ISO，run.log 行首时间戳用）。"""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _one_line(message: str) -> str:
    """把异常消息压成单行（进流水与人读日志，换行会破坏 JSONL / 日志行结构）。"""
    return " ".join(message.split())


def _new_run_id(runs_dir: Path) -> str:
    """分配运行目录名：UTC 时间戳定宽（字典序即时间序），撞名加序号（同秒防撞）。"""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = stamp
    seq = 1
    while (runs_dir / candidate).exists():
        candidate = f"{stamp}-{seq}"
        seq += 1
    return candidate


def _snapshot_file_hash(store: WorkdirStore, seq: int) -> str:
    """快照文件的 SHA-256（run.json 的策略哈希锚点——快照被手改可被发现）。"""
    data = (store.strategies_dir / f"s{seq}.json").read_bytes()
    return hashlib.sha256(data).hexdigest()


# -- 客户端装配（快照 → Completer） ---------------------------------------------


def completer_for_snapshot(endpoint_block: dict[str, Any]) -> Completer:
    """从策略快照的端点块装配打标客户端。

    三要素的取处刻意不同：base_url / model / 请求参数取**快照**（快照隔离——库端
    事后改配置不影响本批）；密钥**现读**数据根（密钥是运行时凭据不是内容资产，
    绝不进快照，且可能被轮换——双通道判定与 config 层同一份）。API 格式只支持
    当前期唯一格式（快照来自旧版本工具时 fail loud，不静默用错协议）。

    Args:
        endpoint_block: 快照 JSON 的 endpoint 块（name / base_url / model /
            api_format / request_params）。

    Returns:
        实现 Completer 协议的客户端。

    Raises:
        ConfigError: API 格式不支持，或该配置的密钥两个通道都拿不到。
    """
    api_format = str(endpoint_block.get("api_format") or SUPPORTED_API_FORMAT)
    if api_format != SUPPORTED_API_FORMAT:
        raise ConfigError(
            f"快照的 API 格式「{api_format}」暂不支持（当前仅支持 "
            f"{SUPPORTED_API_FORMAT}）；请新建批次重新应用策略。"
        )
    config_name = str(endpoint_block["name"])
    api_key = resolve_api_key(read_stored_api_key(config_name))
    params = cast("dict[str, object]", endpoint_block.get("request_params") or {})
    return build_completer(
        EndpointConfig(
            base_url=cast(str, endpoint_block["base_url"]),
            model=cast(str, endpoint_block["model"]),
            api_key=api_key,
            request=parse_request_params(params),
        )
    )


# -- 重试列表（state.json 的 retry_list 键；结构由本域定义） --------------------
# 名单是跨批次共享的一份列表（条目带批次序号）；读 = 无锁（原子写保证不读半截），
# 写 = 一律经 mutate_state 在状态锁内完成。资格判定（哪些条目可入列）在
# items.retry_rejections（用条目视图现算），本节只管名单存取。


def _retry_records(state: dict[str, object]) -> list[dict[str, object]]:
    """取重试列表原始记录（缺键视为空；形状不对 fail loud——静默清零等于丢账）。"""
    raw = state.get(_RETRY_LIST_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkdirMetadataCorruptedError(
            "工作目录状态文件的重试列表损坏——请检查 .dsf/state.json。"
        )
    records: list[dict[str, object]] = []
    for entry in cast("list[object]", raw):
        if not isinstance(entry, dict):
            raise WorkdirMetadataCorruptedError(
                "工作目录状态文件的重试列表形状不对——请检查 .dsf/state.json。"
            )
        record = cast("dict[str, object]", entry)
        batch = record.get("batch")
        item = record.get("item")
        if (
            not isinstance(batch, int)
            or isinstance(batch, bool)
            or not isinstance(item, str)
        ):
            raise WorkdirMetadataCorruptedError(
                "工作目录状态文件的重试列表条目缺字段或类型不对——"
                "请检查 .dsf/state.json。"
            )
        records.append(record)
    return records


def read_retry_list(workdir: Path, seq: int) -> list[str]:
    """读某批次的重试列表（列表顺序即重试顺序；缺键视为空）。"""
    state = WorkdirStore(workdir).read_state()
    return [
        str(record["item"])
        for record in _retry_records(state)
        if record["batch"] == seq
    ]


def add_retry_items(workdir: Path, seq: int, items: list[str]) -> list[str]:
    """把条目加入该批次的重试列表（幂等去重），返回当前名单。

    名单是「意愿」的记录：入列后素材再变坏不影响（开始重试时拍快照、运行时
    按失败记录处置）；所以这里只管存取、不重判资格——资格在加入的入口（api 层）
    用当刻的条目视图判过即可。
    """

    def mutator(state: dict[str, object]) -> list[str]:
        get_batch(workdir, seq)
        records = _retry_records(state)
        current = [str(record["item"]) for record in records if record["batch"] == seq]
        for item in items:
            if item not in current:
                current.append(item)
                records.append({"batch": seq, "item": item})
        state[_RETRY_LIST_KEY] = records
        return current

    return WorkdirStore(workdir).mutate_state(mutator)


def remove_retry_items(workdir: Path, seq: int, items: list[str]) -> list[str]:
    """把条目移出该批次的重试列表（幂等：不在名单里的条目忽略），返回当前名单。

    两个使用者：跑批收尾的出列（本次成功的条目移出、仍失败的保留——「还没补完
    的账」）与端点的逐条移出。其他批次的条目不动（名单共享一份、按批次隔离）。
    """

    def mutator(state: dict[str, object]) -> list[str]:
        records = _retry_records(state)
        removal = set(items)
        kept = [
            record
            for record in records
            if not (record["batch"] == seq and record["item"] in removal)
        ]
        state[_RETRY_LIST_KEY] = kept
        return [str(record["item"]) for record in kept if record["batch"] == seq]

    return WorkdirStore(workdir).mutate_state(mutator)


def clear_retry_list(workdir: Path, seq: int) -> None:
    """清空该批次的重试列表（整体清空；其他批次的条目不动）。"""

    def mutator(state: dict[str, object]) -> None:
        records = _retry_records(state)
        state[_RETRY_LIST_KEY] = [
            record for record in records if record["batch"] != seq
        ]

    WorkdirStore(workdir).mutate_state(mutator)


def remove_batch(workdir: Path, seq: int) -> int:
    """删除批次及其附属重试记录，共用一次状态写入和运行锁。"""

    def cleanup(state: dict[str, object]) -> None:
        state[_RETRY_LIST_KEY] = [
            record for record in _retry_records(state) if record["batch"] != seq
        ]

    return delete_batch(workdir, seq, cleanup_state=cleanup)
