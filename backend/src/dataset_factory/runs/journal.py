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
from pathlib import Path
from typing import cast

from .._fs import atomic_write_text
from .errors import RunJournalCorruptedError

__all__ = ["RunJournal", "load_recent_success_hashes"]

_RUN_JSON_NAME = "run.json"
_ITEMS_JSONL_NAME = "items.jsonl"
_RUN_LOG_NAME = "run.log"

#: items.jsonl 的合法终态（断点续跑哈希只认 succeeded 行——产物出自成功打标）。
_ITEM_STATUSES = frozenset({"succeeded", "failed"})


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


def load_recent_success_hashes(runs_dir: Path) -> dict[str, str]:
    """扫历史运行流水，取每条素材最近一次**成功**打标时读取的素材哈希。

    断点续跑的跳过判定（E1）：有产物的条目，当前素材哈希与「最近一次成功打标」
    一致才跳过——产物出自成功打标，所以比对锚点取 succeeded 行（失败的尝试没有
    产物，与产物无关）。运行目录名字典序 = 时间序，从新到旧扫，每条素材取第一次
    遇到的 succeeded 行；一个运行里同一素材以最后一行为准（重试后成功的行）。

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
            if record["status"] != "succeeded":
                continue
            item = cast(str, record["item"])
            if item not in hashes:
                hashes[item] = cast(str, record["asset_hash"])
    return hashes


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
