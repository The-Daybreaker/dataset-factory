"""运行记录回读测试：以磁盘记录为依据、按批次隔离并拒绝损坏数据。"""

from pathlib import Path

import pytest

from dataset_factory.runs.errors import RunJournalCorruptedError
from dataset_factory.runs.journal import RunJournal, load_latest_run, read_run_text


def write_record(runs: Path, run_id: str, batch: int) -> RunJournal:
    """准备一份完整的运行记录及人读日志。"""
    journal = RunJournal(runs / run_id)
    journal.write_run_json(
        {
            "run_id": run_id,
            "batch": batch,
            "mode": "retry",
            "trigger": "cli",
            "strategy_hash": "abc",
            "snapshot": f"strategies/s{batch}.json",
            "dsf_version": "0.1.0",
            "status": "completed",
            "counters": {
                "planned": 1,
                "attempted": 1,
                "succeeded": 1,
                "failed": 0,
                "skipped": 0,
            },
            "started_at": "2026-09-17T01:00:00+00:00",
            "finished_at": "2026-09-17T01:00:01+00:00",
        }
    )
    journal.append_log_line("运行成功")
    return journal


def test_latest_run_filters_batch_before_selecting(tmp_path: Path) -> None:
    """另一批次的新运行不能覆盖当前批次的最近一次结果。"""
    write_record(tmp_path, "001", 1)
    write_record(tmp_path, "002", 1)
    write_record(tmp_path, "003", 2)

    result = load_latest_run(tmp_path, 1)

    assert result is not None
    assert result.run_id == "002"
    assert result.counters.succeeded == 1
    assert result.trigger == "cli"


def test_latest_run_returns_none_without_matching_history(tmp_path: Path) -> None:
    """无运行或只有其他批次时返回空态。"""
    assert load_latest_run(tmp_path / "missing", 1) is None
    write_record(tmp_path, "001", 2)

    assert load_latest_run(tmp_path, 1) is None


def test_latest_run_rejects_corrupted_metadata(tmp_path: Path) -> None:
    """损坏的摘要不能被静默当作没有运行。"""
    journal = write_record(tmp_path, "001", 1)
    journal.run_json_path.write_text('{"batch": 1}', encoding="utf-8")

    with pytest.raises(RunJournalCorruptedError):
        load_latest_run(tmp_path, 1)


def test_run_text_reads_fixed_files_and_empty_unattempted_items(tmp_path: Path) -> None:
    """日志可读，尚无条目尝试时不存在的流水返回空文本。"""
    write_record(tmp_path, "001", 1)

    assert read_run_text(tmp_path, "001", 1, "run.log") == "运行成功\n"
    assert read_run_text(tmp_path, "001", 1, "items.jsonl") == ""


@pytest.mark.parametrize(
    ("run_id", "batch", "filename"),
    [
        ("001", 2, "run.log"),
        ("../001", 1, "run.log"),
        ("001", 1, "run.json"),
        ("missing", 1, "run.log"),
    ],
)
def test_run_text_rejects_invalid_scope(
    tmp_path: Path, run_id: str, batch: int, filename: str
) -> None:
    """拒绝批次不匹配、越界路径、任意文件名及不存在的运行。"""
    write_record(tmp_path, "001", 1)

    with pytest.raises(RunJournalCorruptedError):
        read_run_text(tmp_path, run_id, batch, filename)
