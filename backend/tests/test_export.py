"""导出核心：配对资格、真实 ZIP、变更与取消的交付边界。"""

from dataclasses import replace
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest

from dataset_factory.export import ExportError, build_export_plan, write_export
from dataset_factory.tasks import TaskCancelledError
from dataset_factory.workdir import WorkdirStore


def _register(workdir: Path, names: list[str]) -> None:
    """显式登记顺序，测试计划不依赖文件系统排序。"""
    WorkdirStore(workdir).append_import_record(
        {
            "imported_at": "now",
            "source": "",
            "files": [{"name": name, "sha256": "import-hash"} for name in names],
        }
    )


def _pair(workdir: Path, name: str, seq: int = 1) -> None:
    """建立唯一素材和对应批次的 caption。"""
    (workdir / name).write_bytes(name.encode())
    (workdir / f"s{seq}__{Path(name).stem}.txt").write_text(
        f"caption {seq} {name}", encoding="utf-8"
    )


def test_export_flat_zip_preserves_bytes_and_import_order(tmp_path: Path) -> None:
    """混合图片视频按登记顺序编号，ZIP 无子目录与元数据，内容逐字节不变。"""
    names = ["z.MP4", "a.jpg", "b.png"]
    for name in names:
        _pair(tmp_path, name)
    _register(tmp_path, names)

    plan = build_export_plan(tmp_path, 1, labeling_hashes={"z": "old"})
    output = write_export(tmp_path, plan, tmp_path / "dataset.zip")

    assert [row.integrity for row in plan.included] == ["changed", "unknown", "unknown"]
    assert plan.total_bytes == sum(
        (tmp_path / name).stat().st_size
        + (tmp_path / f"s1__{Path(name).stem}.txt").stat().st_size
        for name in names
    )
    with ZipFile(output) as archive:
        assert archive.namelist() == [
            "001.MP4",
            "001.txt",
            "002.jpg",
            "002.txt",
            "003.png",
            "003.txt",
        ]
        for index, name in enumerate(names, 1):
            assert (
                archive.read(f"{index:03d}{Path(name).suffix}")
                == (tmp_path / name).read_bytes()
            )
            assert (
                archive.read(f"{index:03d}.txt")
                == (tmp_path / f"s1__{Path(name).stem}.txt").read_bytes()
            )


def test_export_excludes_credentials_and_internal_metadata(tmp_path: Path) -> None:
    """导出仅包含登记配对，目录内凭据与内部元数据不进入 ZIP。"""
    marker = b"fake-credential-marker-for-export-test"
    _pair(tmp_path, "sample.jpg")
    _register(tmp_path, ["sample.jpg"])
    (tmp_path / "credentials").write_bytes(marker)
    (tmp_path / ".env").write_bytes(marker)
    (tmp_path / ".dsf" / "private.json").write_bytes(marker)

    plan = build_export_plan(tmp_path, 1, labeling_hashes={})
    output = write_export(tmp_path, plan, tmp_path / "dataset.zip")

    with ZipFile(output) as archive:
        assert archive.namelist() == ["001.jpg", "001.txt"]
        assert all(marker not in archive.read(name) for name in archive.namelist())


def test_plan_explains_all_exclusions(tmp_path: Path) -> None:
    """缺失、无产物、空产物、未完成、手动排除、主干冲突与未登记分别给原因。"""
    names = [
        "missing.jpg",
        "no.jpg",
        "empty.jpg",
        "failed.jpg",
        "manual.jpg",
        "clash.jpg",
    ]
    for name in names[1:]:
        _pair(tmp_path, name)
    (tmp_path / "s1__no.txt").rename(tmp_path / "other-caption.txt")
    (tmp_path / "s1__empty.txt").write_text(" \n\t", encoding="utf-8")
    (tmp_path / "clash.png").write_bytes(b"conflict")
    _pair(tmp_path, "new.jpg")
    _register(tmp_path, names)

    plan = build_export_plan(
        tmp_path,
        1,
        labeling_hashes={},
        failed_items={"failed"},
        excluded_items={"manual"},
    )

    reasons = {row.name: row.reason for row in plan.excluded}
    assert plan.included == []
    assert reasons == {
        "missing.jpg": "缺失",
        "no.jpg": "无配对产物",
        "empty.jpg": "产物异常",
        "failed.jpg": "未完成",
        "manual.jpg": "用户排除",
        "clash.jpg": "配对冲突",
        "clash.png": "未登记",
        "new.jpg": "未登记",
        "other-caption.txt": "未登记",
    }


def test_original_names_and_batch_caption_are_preserved(tmp_path: Path) -> None:
    """关闭编号时中文素材名保留并提示风险，只打包所选批次的 caption。"""
    _pair(tmp_path, "素材.jpg", 1)
    _pair(tmp_path, "素材.jpg", 2)
    _register(tmp_path, ["素材.jpg"])

    plan = build_export_plan(tmp_path, 2, labeling_hashes={}, sequential=False)
    output = write_export(tmp_path, plan, tmp_path / "dataset.zip")

    assert plan.non_ascii_names
    with ZipFile(output) as archive:
        assert archive.namelist() == ["素材.jpg", "素材.txt"]
        assert archive.read("素材.txt").decode() == "caption 2 素材.jpg"


def test_sequence_width_expands_at_one_thousand(tmp_path: Path) -> None:
    """千条时全部编号统一升级四位，首尾仍正确配对。"""
    names = [f"image-{index}.jpg" for index in range(1000)]
    for name in names:
        _pair(tmp_path, name)
    _register(tmp_path, names)

    plan = build_export_plan(tmp_path, 1, labeling_hashes={})

    assert plan.included[0].asset_name == "0001.jpg"
    assert plan.included[-1].caption_name == "1000.txt"


def test_original_names_exclude_case_insensitive_caption_collisions(
    tmp_path: Path,
) -> None:
    """保留原名时，大小写不同但会解压成同名 caption 的素材提前列为冲突。"""
    _pair(tmp_path, "Photo.jpg")
    _pair(tmp_path, "photo.png")
    _register(tmp_path, ["Photo.jpg", "photo.png"])

    plan = build_export_plan(tmp_path, 1, labeling_hashes={}, sequential=False)

    assert not plan.included
    assert len(plan.excluded) == 2
    assert all(row.reason == "配对冲突" for row in plan.excluded)


@pytest.mark.parametrize("changed_name", ["a.jpg", "s1__a.txt"])
def test_changed_input_aborts_zip_without_touching_source(
    tmp_path: Path, changed_name: str
) -> None:
    """预览后素材或 caption 被修改，写包失败且不留下半截交付物。"""
    _pair(tmp_path, "a.jpg")
    _register(tmp_path, ["a.jpg"])
    plan = build_export_plan(tmp_path, 1, labeling_hashes={})
    (tmp_path / changed_name).write_bytes(b"changed")

    with pytest.raises(ExportError, match="变化"):
        write_export(tmp_path, plan, tmp_path / "dataset.zip")

    assert not (tmp_path / "dataset.zip").exists()
    assert not list(tmp_path.glob(".dsf-export-*"))
    assert (tmp_path / changed_name).read_bytes() == b"changed"


@pytest.mark.parametrize("content", [b"", b" \n\t", b"\xff"])
def test_invalid_caption_is_excluded(tmp_path: Path, content: bytes) -> None:
    """空白或非 UTF-8 caption 不进入导出计划。"""
    _pair(tmp_path, "a.jpg")
    _register(tmp_path, ["a.jpg"])
    (tmp_path / "s1__a.txt").write_bytes(content)

    plan = build_export_plan(tmp_path, 1, labeling_hashes={})

    assert plan.included == []
    assert plan.excluded[0].reason == "产物异常"


def test_cancelled_plan_does_not_read_materials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开始前已取消的导出计划不再读取素材。"""
    stop = Event()
    stop.set()

    def unexpected_read(path: Path) -> bytes:
        pytest.fail("取消后的任务仍读取文件")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)

    with pytest.raises(TaskCancelledError):
        build_export_plan(tmp_path, 1, labeling_hashes={}, should_stop=stop)


def test_cancel_during_export_leaves_no_zip(tmp_path: Path) -> None:
    """一对文件完成后取消，临时 ZIP 清理且不会发布最终文件。"""
    _pair(tmp_path, "a.jpg")
    _register(tmp_path, ["a.jpg"])
    plan = build_export_plan(tmp_path, 1, labeling_hashes={})
    stop = Event()

    def cancel(value: float) -> None:
        stop.set()

    with pytest.raises(TaskCancelledError):
        write_export(
            tmp_path, plan, tmp_path / "dataset.zip", should_stop=stop, progress=cancel
        )

    assert not (tmp_path / "dataset.zip").exists()
    assert not list(tmp_path.glob(".dsf-export-*"))


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    """已存在的交付包即使与目标同名，也绝不覆盖。"""
    _pair(tmp_path, "a.jpg")
    _register(tmp_path, ["a.jpg"])
    plan = build_export_plan(tmp_path, 1, labeling_hashes={})
    output = tmp_path / "dataset.zip"
    output.write_bytes(b"existing")

    with pytest.raises(ExportError, match="已存在"):
        write_export(tmp_path, plan, output)

    assert output.read_bytes() == b"existing"


@pytest.mark.parametrize("name", ["../a.jpg", "..\\a.jpg", "001.txt"])
def test_unsafe_or_conflicting_output_names_are_rejected(
    tmp_path: Path, name: str
) -> None:
    """库调用方传入包含路径或图文重名的计划时，不生成危险 ZIP。"""
    _pair(tmp_path, "a.jpg")
    _register(tmp_path, ["a.jpg"])
    plan = build_export_plan(tmp_path, 1, labeling_hashes={})
    plan = replace(plan, included=[replace(plan.included[0], asset_name=name)])

    with pytest.raises(ExportError, match="冲突"):
        write_export(tmp_path, plan, tmp_path / "dataset.zip")

    assert not (tmp_path / "dataset.zip").exists()
