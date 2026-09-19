"""运行日志三件套（``run.json`` / ``items.jsonl`` / ``run.log``）的落盘与回读。

分工规则（design「运行日志的字段与保留」）：**凡被机制读取的信息进 run.json /
items.jsonl；只给人看的进 run.log**。写入者 = 调度线程单写入者（并发约定的根基）；
items.jsonl 追加后 flush + fsync 才算落盘（判定依据，崩溃也不能丢），run.log 只 flush
（人读投影，丢了不伤判定）。

items.jsonl 与导入记录同一套崩溃安全读法：末尾无换行 = 写到一半的残缺行，砍掉残缺
尾巴再解析；中间的完整行损坏则 fail loud（``RunJournalCorruptedError``）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from .._fs import atomic_write_text
from .errors import RunJournalCorruptedError

__all__ = [
    "ItemRecord",
    "RunJournal",
    "load_latest_item_records",
    "load_recent_success_hashes",
]

_RUN_JSON_NAME = "run.json"
_ITEMS_JSONL_NAME = "items.jsonl"
_RUN_LOG_NAME = "run.log"

#: items.jsonl 的合法终态（断点续跑哈希只认 succeeded 行——产物出自成功打标）。
_ITEM_STATUSES = frozenset({"succeeded", "failed"})


class RunCounters(BaseModel):
    """运行记录中的计数，读取时拒绝负数与隐式类型转换。"""

    model_config = ConfigDict(strict=True)
    planned: int = Field(ge=0)
    attempted: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)


def empty_counters() -> dict[str, int]:
    """五个计数的初始镜像（全 0），键名跟着 `RunCounters` 走。

    执行器的运行中镜像与跨进程读到的占位进度都要一份「还没数过任何条目」的计数；
    在此前两处各手写一遍键名，加一个计数就会有一处漏键（读侧 `RunCounters` 严格模式
    会当场拒收）。
    """
    return dict.fromkeys(RunCounters.model_fields, 0)


class RunRecord(BaseModel):
    """磁盘上的一次运行摘要；与当前进程的运行活性分开。"""

    model_config = ConfigDict(strict=True)
    run_id: str
    batch: int = Field(ge=1)
    mode: str
    trigger: str
    strategy_hash: str
    snapshot: str
    dsf_version: str
    status: str
    counters: RunCounters
    started_at: str
    finished_at: str | None


def load_latest_run(runs_dir: Path, seq: int) -> RunRecord | None:
    """按目录时间序回读本批次最近一次运行，不把其他批次的统计混入。"""
    if not runs_dir.is_dir():
        return None
    for directory in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
        path = directory / _RUN_JSON_NAME
        if not directory.is_dir() or not path.exists():
            continue
        if not path.resolve().is_relative_to(runs_dir.resolve()):
            raise RunJournalCorruptedError("运行记录路径不在运行目录内。")
        try:
            record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RunJournalCorruptedError(f"运行记录无法读取：{path}") from exc
        if record.run_id != directory.name:
            raise RunJournalCorruptedError(f"运行记录标识与目录不一致：{path}")
        if record.batch == seq:
            return record
    return None


def read_run_text(runs_dir: Path, run_id: str, seq: int, filename: str) -> str:
    """读取指定运行的人读日志或原始流水，仅接受两种固定文件名。"""
    if filename not in {_RUN_LOG_NAME, _ITEMS_JSONL_NAME}:
        raise RunJournalCorruptedError("不支持的运行记录文件。")
    directory = runs_dir / run_id
    if directory.parent != runs_dir or not directory.resolve().is_relative_to(
        runs_dir.resolve()
    ):
        raise RunJournalCorruptedError("运行记录路径无效。")
    try:
        meta_path = directory / _RUN_JSON_NAME
        text_path = directory / filename
        if not all(
            p.resolve().is_relative_to(runs_dir.resolve())
            for p in (meta_path, text_path)
        ):
            raise RunJournalCorruptedError("运行记录路径不在运行目录内。")
        record = RunRecord.model_validate_json(meta_path.read_text(encoding="utf-8"))
        if record.batch != seq or record.run_id != run_id:
            raise RunJournalCorruptedError("运行记录不属于当前批次。")
        if not text_path.exists():
            return ""
        return text_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise RunJournalCorruptedError(
            "运行记录无法读取，请检查文件是否仍在。"
        ) from exc


class RunJournal:
    """一次运行的三件套写入器：构造即绑定 run 目录，全部写入经本类（单写入者）。"""

    def __init__(self, run_dir: Path) -> None:
        """确保 run 目录存在并绑定（目录名即 run_id，由执行器先分配）。"""
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    @property
    def run_json_path(self) -> Path:
        """``run.json`` 路径。"""
        return self.run_dir / _RUN_JSON_NAME

    @property
    def items_path(self) -> Path:
        """``items.jsonl`` 路径。"""
        return self.run_dir / _ITEMS_JSONL_NAME

    @property
    def log_path(self) -> Path:
        """``run.log`` 路径（对外展示绝对路径用的就是它）。"""
        return self.run_dir / _RUN_LOG_NAME

    def write_run_json(self, meta: dict[str, object]) -> None:
        """原子写 run 级元数据（骨架与结束时写定都走这里——整份重写，字段以本次为准）。"""
        atomic_write_text(
            self.run_json_path, json.dumps(meta, ensure_ascii=False, indent=2)
        )

    def append_item(self, record: dict[str, object]) -> None:
        """追加一条条目结果到 items.jsonl（append-only；写入后 flush + fsync 落盘）。"""
        line = json.dumps(record, ensure_ascii=False)
        with self.items_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_log_line(self, line: str) -> None:
        """追加一行人读日志（flush 不 fsync——run.log 不参与判定，丢尾行可接受）。"""
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()


def load_recent_success_hashes(runs_dir: Path, seq: int) -> dict[str, str]:
    """扫**本批次**历史运行流水，取每条素材最近一次成功打标时读取的素材哈希。

    断点续跑的跳过判定（E1）：有产物的条目，当前素材哈希与「最近一次成功打标」
    一致才跳过。锚点必须按批次过滤（items.jsonl 行内必带 batch）——产物是
    「策略 × 素材」维度的，s2 用素材 v1 打的产物，不能拿 s1 后来对 v2 打标
    的哈希当锚点，否则过期产物会被错误跳过。运行目录名字典序 = 时间序，从新到
    旧扫，每条素材取最新运行里的成功行（正常写入同条目每次运行至多一行终态，
    多行为历史异常数据、以首条成功行为准）。

    Args:
        runs_dir: ``.dsf/runs/`` 目录。
        seq: 批次序号（只认该批次的流水行）。

    Returns:
        素材主干 → 素材哈希（只含有过成功打标的条目；从没成功过的不在映射里，
        调用方按「无锚点 → 重打」处理）。

    Raises:
        RunJournalCorruptedError: 某次运行的 items.jsonl 损坏（fail loud，不静默跳过——
            坏流水会让跳过判定失真）。
    """
    if not runs_dir.is_dir():
        return {}
    hashes: dict[str, str] = {}
    for run_dir in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
        items_path = run_dir / _ITEMS_JSONL_NAME
        if not items_path.is_file():
            continue
        for record in _read_items_file(items_path):
            if record["status"] != "succeeded" or record.get("batch") != seq:
                continue
            item = cast(str, record["item"])
            if item not in hashes:
                hashes[item] = cast(str, record["asset_hash"])
    return hashes


@dataclass(frozen=True)
class ItemRecord:
    """一条素材在某批次里**最近一次**运行的结果（条目视图分组判定的依据）。

    Attributes:
        status: ``succeeded`` 或 ``failed``。
        attempt: 那次的尝试序号（1–4，F5 自动重试的真实次数）。
        reason_code: 失败原因码（F5 两类清单）；成功时为 None。
        message: 失败原因的人读消息；成功时为 None。
    """

    status: str
    attempt: int
    reason_code: str | None
    message: str | None


def load_latest_item_records(runs_dir: Path, seq: int) -> dict[str, ItemRecord]:
    """扫本批次历史运行流水，取每条素材**各自最近一次**的结果记录。

    为什么按「每条素材各自最近一次」而不是「最近一次运行目录」取：retry 模式的
    运行只覆盖名单里那几条，若整体只认最新那个运行目录，上一轮全量跑批留下的失败
    记录会被这一轮的窄运行整体抹掉——那些至今仍未完成的条目会错误地回到「排队中」
    （明明没有任何东西在排队）。逐条取最近记录，「这条最后一次尝试的结果是什么」
    才是界面要回答的问题。

    运行目录名字典序 = 时间序，从新到旧扫，每条素材取第一次遇到的记录；行内
    batch 字段做批次过滤（runs/ 是工作目录级共享，多批次并存时不能串账）。

    Args:
        runs_dir: ``.dsf/runs/`` 目录。
        seq: 批次序号（只认该批次的流水行）。

    Returns:
        素材主干 → 最近一次记录（从没被实际调用过的条目不在映射里，调用方按
        「无失败记录」处理）。

    Raises:
        RunJournalCorruptedError: 某次运行的 items.jsonl 损坏（fail loud——
            坏流水会让分组判定失真，用户可用「清理运行记录」移除后重试）。
    """
    if not runs_dir.is_dir():
        return {}
    records: dict[str, ItemRecord] = {}
    for run_dir in sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True):
        items_path = run_dir / _ITEMS_JSONL_NAME
        if not items_path.is_file():
            continue
        for record in _read_items_file(items_path):
            if record.get("batch") != seq:
                continue
            item = cast(str, record["item"])
            if item in records:
                continue
            reason_code = record.get("reason_code")
            message = record.get("message")
            records[item] = ItemRecord(
                status=cast(str, record["status"]),
                attempt=cast(int, record["attempt"]),
                reason_code=reason_code if isinstance(reason_code, str) else None,
                message=message if isinstance(message, str) else None,
            )
    return records


def _read_items_file(path: Path) -> list[dict[str, object]]:
    """读一份 items.jsonl（崩溃安全：砍残缺尾行；中间坏行 fail loud）。"""

    def _corrupted() -> RunJournalCorruptedError:
        return RunJournalCorruptedError(
            f"运行流水文件损坏（{path}）——可用「清理运行记录」移除该次运行后重试。"
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _corrupted() from exc
    if raw and not raw.endswith("\n"):
        raw = raw[: raw.rfind("\n") + 1]
    records: list[dict[str, object]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            data: object = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _corrupted() from exc
        if not isinstance(data, dict):
            raise _corrupted()
        record = cast("dict[str, object]", data)
        if (
            not isinstance(record.get("item"), str)
            or record.get("status") not in _ITEM_STATUSES
            or not isinstance(record.get("attempt"), int)
            or isinstance(record.get("attempt"), bool)
        ):
            raise _corrupted()
        if record["status"] == "succeeded" and not isinstance(
            record.get("asset_hash"), str
        ):
            raise _corrupted()
        records.append(record)
    return records
