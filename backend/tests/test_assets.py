"""单元测试：workdir 素材清单与身份解析（扫描 / 出身 / 产物命名 / confine / 未导入分类）。

素材与 ``.dsf/`` 都落 tmp_path；仍一律挂 temp_data_root 隔离数据根——WorkdirStore 的
读取路径不碰数据根，但「拿不准就挂」比事后排查污染便宜得多。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dataset_factory.workdir import (
    AssetNotFoundError,
    AssetPathError,
    WorkdirStore,
    confine_to_workdir,
    import_assets,
    is_product_name,
    media_kind,
    mime_for_suffix,
    product_filename,
    product_has_content,
    product_path,
    product_pattern,
    registered_origins,
    resolve_asset,
    scan_assets,
    unimported_files,
)
from dataset_factory.workdir import importer as importer_module


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """一个真实存在的临时工作目录。"""
    target = tmp_path / "photos"
    target.mkdir()
    return target


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """工作目录之外的来源目录（导入时素材从这里复制进去）。"""
    origin = tmp_path / "source"
    origin.mkdir()
    return origin


def _import(workdir: Path, source: Path, files: dict[str, bytes]) -> None:
    """在来源目录写下文件并导入一次（走完真导入通道，素材即登记在册）。"""
    for name, blob in files.items():
        (source / name).write_bytes(blob)
    import_assets(workdir, source)


def _registered(workdir: Path) -> set[str]:
    """当前登记在册的文件名集合。"""
    return set(registered_origins(WorkdirStore(workdir)))


# --------------------------------------------------------------------------
# 扫描与出身
# --------------------------------------------------------------------------


def test_scan_assets_maps_stem_to_path(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """扫描按素材主干建映射：图片与视频都收，非素材文件与子目录不收。"""
    _import(workdir, source, {"cat_001.jpg": b"a", "clip_001.mp4": b"b"})
    (workdir / "notes.txt").write_text("笔记", encoding="utf-8")
    (workdir / "thumbs").mkdir()

    assets = scan_assets(workdir)

    assert sorted(assets) == ["cat_001", "clip_001"]
    assert assets["cat_001"].name == "cat_001.jpg"
    assert assets["clip_001"].name == "clip_001.mp4"


def test_scan_assets_of_missing_dir_is_empty(tmp_path: Path) -> None:
    """目录不存在时扫描给空映射（工作目录被移走的场景不该炸在扫描这一步）。"""
    assert scan_assets(tmp_path / "gone") == {}


def test_registered_origins_takes_latest_import(
    workdir: Path, tmp_path: Path, temp_data_root: Path
) -> None:
    """同一素材多次导入取最近一次：出身（来源目录）刷新到最新那条记录。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _import(workdir, first, {"cat_001.jpg": b"same-bytes"})
    _import(workdir, second, {"cat_001.jpg": b"same-bytes"})

    origins = registered_origins(WorkdirStore(workdir))

    assert origins["cat_001.jpg"].source == str(second)


# --------------------------------------------------------------------------
# 素材解析：在册 + confine（预览端点的后两重校验）
# --------------------------------------------------------------------------


def test_resolve_asset_returns_registered_file(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """在册且素材在盘：按主干解析出工作目录里那份文件。"""
    _import(workdir, source, {"cat_001.jpg": b"a"})

    assert resolve_asset(workdir, "cat_001") == workdir / "cat_001.jpg"


def test_resolve_asset_rejects_unregistered_file(
    workdir: Path, temp_data_root: Path
) -> None:
    """在盘但没登记过：拒绝——未登记文件不属于任何批次（M2），预览端点不为它服务。"""
    (workdir / "stray.jpg").write_bytes(b"dropped-by-hand")

    with pytest.raises(AssetNotFoundError, match="未登记在册"):
        resolve_asset(workdir, "stray")


def test_resolve_asset_reports_registered_but_missing(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """登记在册但素材已不在工作目录：按缺失拒绝，消息指向「重新导入」。"""
    _import(workdir, source, {"cat_001.jpg": b"a"})
    (workdir / "cat_001.jpg").unlink()

    with pytest.raises(AssetNotFoundError, match="缺失"):
        resolve_asset(workdir, "cat_001")


@pytest.mark.parametrize("item", ["", "../cat_001", "sub/cat_001", "sub\\cat_001"])
def test_resolve_asset_rejects_path_like_item_name(
    workdir: Path, item: str, temp_data_root: Path
) -> None:
    """条目名必须是素材主干：空名、带分隔符或想往上跳的一律拒（不是「找不到」而是「不合法」）。"""
    with pytest.raises(AssetPathError):
        resolve_asset(workdir, item)


def test_confine_rejects_path_outside_workdir(workdir: Path, tmp_path: Path) -> None:
    """realpath 落在工作目录之外即拒——只读端点也不能变成读任意文件的通道。"""
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")

    with pytest.raises(AssetPathError, match="越出工作目录"):
        confine_to_workdir(workdir, outside)


def test_confine_accepts_path_inside_workdir(workdir: Path) -> None:
    """工作目录内的文件放行，并原样返回调用方给的那份路径。"""
    inside = workdir / "cat_001.jpg"
    inside.write_bytes(b"x")

    assert confine_to_workdir(workdir, inside) == inside


def test_confine_rejects_symlink_pointing_outside(
    workdir: Path, tmp_path: Path
) -> None:
    """工作目录里的符号链接指向外部时拒绝：字符串前缀看着合法，realpath 已经出界。"""
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"outside-bytes")
    link = workdir / "link.jpg"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前系统不允许创建符号链接（Windows 需开发者模式或管理员权限）")

    with pytest.raises(AssetPathError):
        confine_to_workdir(workdir, link)


# --------------------------------------------------------------------------
# 产物命名契约
# --------------------------------------------------------------------------


def test_product_filename_and_pattern_follow_contract(workdir: Path) -> None:
    """产物名 = s<N>__<主干>.txt，glob 模式只捞该批次自己的产物。"""
    (workdir / product_filename(1, "cat_001")).write_text("一", encoding="utf-8")
    (workdir / product_filename(2, "cat_001")).write_text("二", encoding="utf-8")
    (workdir / product_filename(11, "cat_001")).write_text("十一", encoding="utf-8")

    assert product_filename(1, "cat_001") == "s1__cat_001.txt"
    assert [p.name for p in workdir.glob(product_pattern(1))] == ["s1__cat_001.txt"]


def test_product_path_points_at_batch_product(workdir: Path) -> None:
    """合法主干解析出该批次的产物路径；文件在不在都能给路径，存在性由调用方判。"""
    assert product_path(workdir, 2, "cat_001") == workdir / "s2__cat_001.txt"


@pytest.mark.parametrize("item", ["", "../cat_001", "sub/cat_001", "sub\\cat_001"])
def test_product_path_rejects_path_like_item_name(workdir: Path, item: str) -> None:
    """产物路径是拼出来的（不像素材那样从扫描结果里取），所以条目名同样要过合法性这一关。"""
    with pytest.raises(AssetPathError):
        product_path(workdir, 1, item)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("s1__cat_001.txt", True),
        ("s12__clip_001.txt", True),
        ("s0__cat_001.txt", False),
        ("s1__.txt", False),
        ("notes.txt", False),
        ("cat_001.txt", False),
        ("s1__cat_001.txt.bak", False),
    ],
)
def test_is_product_name_recognizes_only_tool_products(
    name: str, expected: bool
) -> None:
    """只有符合命名契约的才算本工具产物——序号是正整数、主干非空、以 .txt 收尾。"""
    assert is_product_name(name) is expected


def test_product_has_content_requires_non_blank_text(workdir: Path) -> None:
    """「已有产物」= 文件在且去掉空白仍有内容；空文件 / 全空白 / 不存在都不算。"""
    filled = workdir / "filled.txt"
    filled.write_text("一段描述", encoding="utf-8")
    blank = workdir / "blank.txt"
    blank.write_text("  \n\t ", encoding="utf-8")
    empty = workdir / "empty.txt"
    empty.write_text("", encoding="utf-8")

    assert product_has_content(filled) is True
    assert product_has_content(blank) is False
    assert product_has_content(empty) is False
    assert product_has_content(workdir / "absent.txt") is False


def test_product_has_content_treats_undecodable_as_absent(workdir: Path) -> None:
    """产物被换成非 UTF-8 字节时按「没有产物」处理：重打比报错更符合用户目的。"""
    broken = workdir / "broken.txt"
    broken.write_bytes(b"\xff\xfe\x00bad-bytes")

    assert product_has_content(broken) is False


# --------------------------------------------------------------------------
# MIME 与媒体形态
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "mime", "kind"),
    [
        ("cat.jpg", "image/jpeg", "image"),
        ("cat.JPEG", "image/jpeg", "image"),
        ("clip.mkv", "video/x-matroska", "video"),
        ("clip.avi", "video/x-msvideo", "video"),
        ("notes.txt", "application/octet-stream", "file"),
        ("scene.heic", "application/octet-stream", "file"),
    ],
)
def test_mime_and_media_kind_by_extension(name: str, mime: str, kind: str) -> None:
    """MIME 与媒体形态都按扩展名定，大小写不敏感；白名单外落「未知二进制 / file」。"""
    suffix = Path(name).suffix

    assert mime_for_suffix(suffix) == mime
    assert media_kind(name) == kind


# --------------------------------------------------------------------------
# 未导入清单
# --------------------------------------------------------------------------


def test_unimported_files_classifies_three_reasons(
    workdir: Path,
    source: Path,
    temp_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未导入三类原因各归各位：扩展名不支持 / 超出大小上限（带上限）/ 未登记。"""
    monkeypatch.setattr(importer_module, "MAX_IMAGE_BYTES", 10)
    _import(workdir, source, {"cat_001.jpg": b"123"})
    (workdir / "scene.heic").write_bytes(b"x")
    (workdir / "big.jpg").write_bytes(b"12345678901")
    (workdir / "stray.jpg").write_bytes(b"1234")

    rows = {row.name: row for row in unimported_files(workdir, _registered(workdir))}

    assert rows["scene.heic"].reason == "扩展名不支持"
    assert rows["scene.heic"].limit is None
    assert rows["big.jpg"].reason == "超出大小上限"
    assert rows["big.jpg"].limit == 10
    assert rows["big.jpg"].size == 11
    assert rows["stray.jpg"].reason == "未登记"
    assert "cat_001.jpg" not in rows


def test_unimported_files_excludes_tool_products(
    workdir: Path, source: Path, temp_data_root: Path
) -> None:
    """本工具写的产物 txt 不列入未导入，用户自己放进来的 txt 仍照常列出。

    产物是工具自己写进工作目录的，列进去会让几百条「s1__xxx.txt · 扩展名不支持」
    把真正需要用户处置的文件淹掉。
    """
    _import(workdir, source, {"cat_001.jpg": b"a"})
    (workdir / product_filename(1, "cat_001")).write_text("描述", encoding="utf-8")
    (workdir / "notes.txt").write_text("笔记", encoding="utf-8")

    names = [row.name for row in unimported_files(workdir, _registered(workdir))]

    assert names == ["notes.txt"]


def test_unimported_files_of_missing_dir_is_empty(tmp_path: Path) -> None:
    """工作目录整个不存在时给空清单（目录被移走 / 删掉不该炸在这一步）。"""
    assert unimported_files(tmp_path / "gone", set()) == []


def test_unimported_files_sorted_by_name(workdir: Path, temp_data_root: Path) -> None:
    """清单按文件名排序（响应顺序确定，前端与契约快照才好比对）。"""
    (workdir / "b.heic").write_bytes(b"x")
    (workdir / "a.heic").write_bytes(b"x")
    (workdir / "c.heic").write_bytes(b"x")

    names = [row.name for row in unimported_files(workdir, set())]

    assert names == ["a.heic", "b.heic", "c.heic"]
