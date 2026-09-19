"""`_fs` 四件共用助手的等价性与边界测试。

为什么单独写一件：本轮把散在各域的「文件哈希 / 流式搬运哈希 / 规范 JSON 哈希 / 单段安全名字
判定」收进 ``_fs``（层中立模块，谁都能 import 而不破分层）。收进来之前的两处判定是逐字写在
各调用点的四条件，收进来之后一旦有人「顺手收紧或放松」，受影响的是导入对账、完整性扫描与导出
配对三条链。这里既测新助手本身，也**与替换前的表达式逐个输入比对**，把「口径没变」钉成可跑的
证明。流式助手另外测一条：摘要跟着**搬过的字节**走，源文件在流途中变了也不算源文件的账。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from dataset_factory._fs import (
    canonical_sha256,
    hash_file,
    hash_stream,
    is_single_path_segment,
)
from dataset_factory.tasks import TaskCancelledError
from dataset_factory.workdir import WorkdirRegistry, WorkdirStore, import_assets

#: 覆盖：正常名、空名、两段、两种分隔符、NUL、绝对路径、盘符、中文与空格名。
SEGMENT_CASES: list[str] = [
    "a.png",
    "001.txt",
    "描述 v2.png",
    "",
    ".",
    "..",
    "a/b",
    "a" + chr(92) + "b",
    "/abs/path",
    "C:" + chr(92) + "x",
    "a" + chr(0) + "b",
]


def _legacy_segment_check(name: str) -> bool:
    """替换前 workdir/integrity.py 里的那串条件（原样搬来作对照）。"""
    return not (
        Path(name).name != name or "/" in name or "\\" in name or "\x00" in name
    )


@pytest.mark.parametrize("name", SEGMENT_CASES)
def test_segment_check_matches_legacy_expression(name: str) -> None:
    """新助手对每个样例输入的判定必须与原表达式逐位一致（不顺手收紧）。"""
    assert is_single_path_segment(name) is _legacy_segment_check(name)


def test_hash_file_matches_streaming_digest(tmp_path: Path) -> None:
    """`hash_file` 与小块流式手算的结果一致（证明只是换了实现，不是换了口径）。"""
    payload = bytes(range(256)) * 4096
    target = tmp_path / "big.bin"
    target.write_bytes(payload)

    streaming = hashlib.sha256()
    with target.open("rb") as handle:
        while chunk := handle.read(4096):
            streaming.update(chunk)

    assert (
        hash_file(target)
        == streaming.hexdigest()
        == hashlib.sha256(payload).hexdigest()
    )


def test_canonical_hash_is_independent_of_key_order() -> None:
    """规范哈希只看内容：键序不同、嵌套顺序不同都要得到同一摘要。"""
    left = {"b": 1, "a": [{"y": 2, "x": 3}], "名": "值"}
    right = {"a": [{"x": 3, "y": 2}], "b": 1, "名": "值"}

    assert canonical_sha256(left) == canonical_sha256(right)


def test_canonical_hash_is_computed_independently_in_test() -> None:
    """测试侧独立算一遍作对照（防产码与测试共用同一处实现而一起写错）。"""
    data = {"skills": ["b", "a"], "prompt": "详细描述"}
    expected = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    assert canonical_sha256(data) == expected
    # 规范口径只排键、不排数组：清单顺序是内容的一部分（保序是设计约定）。
    assert canonical_sha256({**data, "skills": ["a", "b"]}) != expected


def test_canonical_hash_detects_content_change() -> None:
    """内容差一位就该差一个摘要——快照与「从库更新」比对靠的就是这个敏感性。"""
    base = canonical_sha256({"endpoint": "main", "prompt": "详细描述", "skills": []})
    changed = canonical_sha256({"endpoint": "main", "prompt": "简短描述", "skills": []})

    assert base != changed


@pytest.mark.parametrize("size_label", ["small", "multi-chunk"])
def test_import_record_hash_is_the_landed_bytes(
    temp_data_root: Path, tmp_path: Path, size_label: str
) -> None:
    """导入记录的哈希 = 磁盘上那份字节的哈希（复制导入的锚点必须指向落盘内容）。

    这是 goal G2 点名的不变量：哈希来自「同一次读里既写盘又喂哈希」的那批字节，
    而不是复制完成后再回头去读源文件。这里用两种体量各测一遍（单块与跨多块），
    三方对齐：源字节、磁盘副本字节、`.dsf/imports.json` 里记下的摘要。
    """
    payload = b"dsf" * (7 if size_label == "small" else 900_000)
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "alpha.png").write_bytes(payload)
    workdir = tmp_path / "wd"
    workdir.mkdir()

    WorkdirRegistry.register(workdir)
    import_assets(workdir, source_dir)

    on_disk = workdir / "alpha.png"
    assert on_disk.read_bytes() == payload
    store = WorkdirStore(workdir)
    recorded: dict[str, object] = json.loads(
        store.imports_file.read_text(encoding="utf-8")
    )["files"][0]

    assert recorded["name"] == "alpha.png"
    assert (
        recorded["sha256"] == hash_file(on_disk) == hashlib.sha256(payload).hexdigest()
    )


#: 与 `_fs` 内部块大小同量级：拼输入时按它对齐，才能造出「恰好一块」「跨块」两种边界。
_MIB = 1024 * 1024


@pytest.mark.parametrize(
    "payload",
    [b"", b"x", b"y" * _MIB, b"z" * (_MIB + 7), b"a" * (3 * _MIB)],
    ids=["empty", "1byte", "exactly-one-chunk", "cross-boundary", "three-chunks"],
)
def test_hash_stream_moves_and_digests_every_byte(payload: bytes) -> None:
    """摘要与一次性算全量一致、writer 收到的字节原样等于输入（空文件与块边界都覆盖）。"""
    writer = io.BytesIO()

    digest, moved = hash_stream(io.BytesIO(payload), writer)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert moved == len(payload)
    assert writer.getvalue() == payload


def test_hash_stream_without_writer_only_reports_digest_and_size(
    tmp_path: Path,
) -> None:
    """不给 writer 时只算摘要与字节数（搬迁校验走这条，不落第二份文件）。"""
    payload = b"relocate" * 200_000
    source = tmp_path / "asset.bin"
    source.write_bytes(payload)

    with source.open("rb") as handle:
        digest, moved = hash_stream(handle)

    assert (moved, digest) == (len(payload), hashlib.sha256(payload).hexdigest())


def test_hash_stream_propagates_cancellation_from_on_chunk(tmp_path: Path) -> None:
    """`on_chunk` 抛出的取消异常必须原样冒出来，且是在第一块之后立刻停。"""
    source = tmp_path / "big.bin"
    source.write_bytes(b"q" * (3 * _MIB))
    seen = 0

    def stop_after_first_chunk(_chunk: bytes) -> None:
        nonlocal seen
        seen += 1
        if seen == 1:
            raise TaskCancelledError("用户取消")

    with source.open("rb") as handle, pytest.raises(TaskCancelledError):
        hash_stream(handle, io.BytesIO(), on_chunk=stop_after_first_chunk)

    assert seen == 1


def test_hash_stream_digests_the_bytes_it_moves(tmp_path: Path) -> None:
    """源文件在流途中被改写时，摘要跟的是**搬过的字节**，不是源文件的任一时刻。

    这是 goal 点名不许顺手改掉的那条不变量的机制证明：第一块读完后由 `on_chunk` 把源
    改写成另一段内容，于是「原源」「现源」「落盘」三者互不相同，只有落盘那批字节配得上
    记录里的摘要——先读一遍算哈希、再读一遍写出去的实现会在这里立刻露馅。
    """
    original = b"A" * (3 * _MIB)
    tampered = b"Z" * (2 * _MIB)
    source = tmp_path / "src.bin"
    target = tmp_path / "dst.bin"
    source.write_bytes(original)
    calls = 0

    def tamper_once(_chunk: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            source.write_bytes(tampered)

    with source.open("rb") as reader, target.open("wb") as writer:
        digest, moved = hash_stream(reader, writer, on_chunk=tamper_once)

    # 第一块是原内容，之后读到的是改写后的剩余部分（源已被截到 2 MiB）。
    landed = b"A" * _MIB + b"Z" * _MIB
    assert digest == hashlib.sha256(landed).hexdigest()
    assert moved == len(landed)
    assert target.read_bytes() == landed
    assert digest != hashlib.sha256(original).hexdigest()
    assert digest != hashlib.sha256(source.read_bytes()).hexdigest()


def test_import_record_points_at_landed_bytes_when_source_moves(
    temp_data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复制收尾那一刻源文件变了，导入记录仍指向落盘内容（调用点上的同一条不变量）。

    `os.fsync` 是这条时间缝的落点：它在临时文件写满之后、原子改名之前触发，此刻改写源
    文件，就得到「源已是新内容、磁盘副本还是旧字节」的状态。若实现退回「复制完再回头读
    源算哈希」，记录就会与磁盘副本对不上。
    """
    payload = b"landed" * 200_000
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "beta.png"
    source.write_bytes(payload)
    workdir = tmp_path / "wd"
    workdir.mkdir()
    staged = workdir / ".dsf" / "tmp" / "beta.png"
    real_fsync = os.fsync
    tampered = 0

    def fsync_then_tamper(fd: int) -> None:
        nonlocal tampered
        # 只认「临时文件已写满、还没改名」这一次，其余 fsync（清单落盘）不触发。
        if staged.is_file() and staged.stat().st_size == len(payload) and not tampered:
            tampered += 1
            source.write_bytes(b"torn" * 200_000)
        real_fsync(fd)

    monkeypatch.setattr("dataset_factory.workdir.importer.os.fsync", fsync_then_tamper)
    WorkdirRegistry.register(workdir)

    import_assets(workdir, source_dir)

    on_disk = workdir / "beta.png"
    store = WorkdirStore(workdir)
    recorded: dict[str, object] = json.loads(
        store.imports_file.read_text(encoding="utf-8")
    )["files"][0]
    assert tampered == 1
    assert on_disk.read_bytes() == payload
    assert recorded["sha256"] == hash_file(on_disk)
    assert recorded["sha256"] != hash_file(source)
