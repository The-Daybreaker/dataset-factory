"""`_fs` 三件共用助手的等价性与边界测试。

为什么单独写一件：本轮把散在各域的「文件哈希 / 规范 JSON 哈希 / 单段安全名字判定」收进
``_fs``（层中立模块，谁都能 import 而不破分层）。收进来之前的两处判定是逐字写在各调用点的
四条件，收进来之后一旦有人「顺手收紧或放松」，受影响的是导入对账、完整性扫描与导出配对三条链。
这里既测新助手本身，也**与替换前的表达式逐个输入比对**，把「口径没变」钉成可跑的证明。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dataset_factory._fs import (
    canonical_sha256,
    hash_file,
    is_single_path_segment,
)
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
