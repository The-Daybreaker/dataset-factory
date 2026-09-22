"""接口测试：条目视图 + 产物 txt + 素材原件（asset）三个只读端点。

三端点都是同步只读，用 TestClient 直测（不涉及任务受理，无需 ASGITransport）。
重点在两类边界：**错误形**（二期端点一律 problem+json，type slug 要能被前端拿去
分支）与**只读端点的安全边界**（素材预览不能变成读任意文件的通道：在册校验 +
realpath confine + 路径穿越拒绝）。Range 支持单独验——视频拖动进度条全靠它。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dataset_factory.api import create_app
from dataset_factory.llm import create_config
from dataset_factory.prompts import Prompt, save_prompt
from dataset_factory.runs import RunJournal
from dataset_factory.strategies import create_batch
from dataset_factory.workdir import (
    WorkdirRegistry,
    WorkdirStore,
    import_assets,
    product_filename,
)

_JPEG_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes-0123456789"
_MP4_BYTES = b"\x00\x00\x00\x18ftypmp4fake-video-bytes"
_CAPTION = "一位穿白裙的人物站在窗侧，光线柔和。"


@dataclass(frozen=True)
class Env:
    """一套可直接发请求的环境：客户端 + wid + 工作目录（含批次 s1）。"""

    client: TestClient
    wid: str
    workdir: Path


@pytest.fixture
def env(tmp_path: Path, temp_data_root: Path) -> Env:
    """预置：端点 + 提示词 + 三张已登记素材（两图一视频）+ 批次 s1 + 注册表条目。"""
    create_config("main", "https://api.example.com/v1", "test-model", api_key=None)
    save_prompt(Prompt(name="详细描述", description="d", body="你是打标助手。"))
    workdir = tmp_path / "photos"
    workdir.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "cat_001.jpg").write_bytes(_JPEG_BYTES)
    (source / "cat_002.png").write_bytes(b"\x89PNG-fake-second")
    (source / "clip_001.mp4").write_bytes(_MP4_BYTES)
    import_assets(workdir, source)
    create_batch(
        workdir,
        name="一号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )
    entry = WorkdirRegistry.register(workdir, title="")
    return Env(
        client=TestClient(create_app(frontend_dir=tmp_path / "frontend")),
        wid=entry.id,
        workdir=workdir,
    )


def _items_url(env: Env, seq: int = 1) -> str:
    """条目视图的 URL。"""
    return f"/api/workdirs/{env.wid}/batches/s{seq}/items"


def _txt_url(env: Env, item: str, seq: int = 1) -> str:
    """产物 txt 的 URL。"""
    return f"/api/workdirs/{env.wid}/batches/s{seq}/items/{item}/txt"


def _asset_url(env: Env, item: str) -> str:
    """素材原件的 URL。"""
    return f"/api/workdirs/{env.wid}/items/{item}/asset"


def _add_second_batch(env: Env) -> None:
    """再建一个批次（序号 s2）——验条目状态与产物都按批次各算各的。"""
    create_batch(
        env.workdir,
        name="二号批",
        description="",
        endpoint_id="main",
        prompt_id="详细描述",
        skill_ids=[],
    )


def _write_product(env: Env, item: str, content: str, seq: int = 1) -> None:
    """往工作目录写一份该批次的产物。"""
    (env.workdir / product_filename(seq, item)).write_text(content, encoding="utf-8")


def _corrupt_journal(env: Env) -> None:
    """写一次运行目录，里面的 items.jsonl 是坏行（fail loud 路径的搭景）。"""
    run_dir = WorkdirStore(env.workdir).runs_dir / "20260912T143005Z"
    RunJournal(run_dir)
    (run_dir / "items.jsonl").write_text("{不是 JSON}\n", encoding="utf-8")


# --------------------------------------------------------------------------
# GET items —— 条目视图
# --------------------------------------------------------------------------


def test_items_returns_all_six_groups(env: Env) -> None:
    """六个分组恒在（空组给空列表），登记素材落在排队中，行字段齐全。"""
    response = env.client.get(_items_url(env))

    assert response.status_code == 200
    body = response.json()
    assert body["batch"] == 1
    assert body["query"] == ""
    assert set(body["groups"]) == {
        "queued",
        "done",
        "failed",
        "missing",
        "retry",
        "unimported",
    }
    assert [row["item"] for row in body["groups"]["queued"]] == [
        "cat_001",
        "cat_002",
        "clip_001",
    ]
    first = body["groups"]["queued"][0]
    assert first["name"] == "cat_001.jpg"
    assert first["media"] == "image"
    assert first["status"] == "queued"
    assert first["can_retry"] is False
    assert first["in_retry"] is False


def test_items_query_filters_rows(env: Env) -> None:
    """?q= 按文件名过滤，命中数即该组计数；查询词原样回显。"""
    response = env.client.get(_items_url(env), params={"q": "clip"})

    body = response.json()

    assert body["query"] == "clip"
    assert [row["item"] for row in body["groups"]["queued"]] == ["clip_001"]
    assert body["groups"]["done"] == []


def test_items_view_is_per_batch(env: Env) -> None:
    """条目状态按「策略 × 素材」算：s1 已完成的条目在 s2 视图下仍是排队中。"""
    _add_second_batch(env)
    _write_product(env, "cat_001", _CAPTION, seq=1)

    first = env.client.get(_items_url(env, seq=1)).json()
    second = env.client.get(_items_url(env, seq=2)).json()

    assert [row["item"] for row in first["groups"]["done"]] == ["cat_001"]
    assert second["groups"]["done"] == []
    assert "cat_001" in [row["item"] for row in second["groups"]["queued"]]


def test_items_unknown_wid_returns_problem(env: Env) -> None:
    """wid 不在注册表 → 404 problem+json（workdir-not-found）。"""
    response = env.client.get("/api/workdirs/nope/batches/s1/items")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert (body["type"], body["status"]) == ("workdir-not-found", 404)
    assert body["title"]
    assert body["detail"]


def test_items_unknown_batch_returns_problem(env: Env) -> None:
    """批次不存在 → 404 problem+json（batch-not-found）。"""
    response = env.client.get(_items_url(env, seq=9))

    assert response.status_code == 404
    assert response.json()["type"] == "batch-not-found"


def test_items_malformed_seq_returns_problem(env: Env) -> None:
    """sN 形状不对（不是 s + 正整数）按批次不存在处理，不当 500 抛出去。"""
    response = env.client.get(f"/api/workdirs/{env.wid}/batches/x1/items")

    assert response.status_code == 404
    assert response.json()["type"] == "batch-not-found"


def test_items_corrupt_journal_returns_problem(env: Env) -> None:
    """历史运行流水损坏 → 500 problem+json（fail loud，不静默给一份失真的视图）。"""
    _corrupt_journal(env)

    response = env.client.get(_items_url(env))

    assert response.status_code == 500
    assert response.json()["type"] == "run-journal-corrupted"


# --------------------------------------------------------------------------
# GET items/{item}/txt —— 产物正文
# --------------------------------------------------------------------------


def test_txt_returns_caption_as_plain_text(env: Env) -> None:
    """产物正文按 text/plain 原样返回（只含 caption 本身，中文不转义）。"""
    _write_product(env, "cat_001", _CAPTION)

    response = env.client.get(_txt_url(env, "cat_001"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == _CAPTION


def test_txt_is_scoped_to_its_batch(env: Env) -> None:
    """产物带批次前缀：s1 的产物在 s2 的 URL 下取不到（两套策略各自一份）。"""
    _add_second_batch(env)
    _write_product(env, "cat_001", _CAPTION, seq=1)

    assert env.client.get(_txt_url(env, "cat_001", seq=1)).status_code == 200
    missing = env.client.get(_txt_url(env, "cat_001", seq=2))

    assert missing.status_code == 404
    assert missing.json()["type"] == "product-not-found"


def test_txt_without_product_returns_problem(env: Env) -> None:
    """还没打标 → 404 problem+json（product-not-found），消息指向下一步。"""
    response = env.client.get(_txt_url(env, "cat_001"))

    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "product-not-found"
    assert "尚未打标" in body["detail"]


def test_txt_blank_product_returns_problem(env: Env) -> None:
    """产物存在但为空 / 全空白 = 产物异常：与「没有产物」同一个 404 口径（都意味着没 caption 可看）。"""
    _write_product(env, "cat_001", "   \n ")

    response = env.client.get(_txt_url(env, "cat_001"))

    assert response.status_code == 404
    assert "空的" in response.json()["detail"]


def test_txt_unknown_batch_returns_problem(env: Env) -> None:
    """批次不存在先报批次不存在（不去拼一个没人要的文件路径）。"""
    response = env.client.get(_txt_url(env, "cat_001", seq=9))

    assert response.status_code == 404
    assert response.json()["type"] == "batch-not-found"


def test_txt_refuses_path_like_item_name(env: Env) -> None:
    """条目名想拼路径时两层各拦一半，都不吐工作目录之外的内容。

    编码后的正斜杠会被解码进路径、路由直接匹配不上（通用 404）；反斜杠（Windows
    的路径分隔符）能进到处理器，被条目名校验拦成 400 asset-path-invalid。
    """
    outside = env.workdir.parent / "secret.txt"
    outside.write_text("工作目录之外的内容", encoding="utf-8")

    slashed = env.client.get(_txt_url(env, "..%2Fsecret"))
    backslashed = env.client.get(_txt_url(env, "..%5Csecret"))

    assert slashed.status_code == 404
    assert backslashed.status_code == 400
    assert backslashed.json()["type"] == "asset-path-invalid"
    assert "工作目录之外的内容" not in slashed.text + backslashed.text


# --------------------------------------------------------------------------
# GET items/{item}/asset —— 素材原件（含 Range）
# --------------------------------------------------------------------------


def test_asset_returns_bytes_with_media_type_by_extension(env: Env) -> None:
    """素材原件按字节原样返回，Content-Type 按扩展名定（浏览器才肯渲染）。"""
    response = env.client.get(_asset_url(env, "cat_001"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == _JPEG_BYTES


def test_asset_video_gets_video_media_type(env: Env) -> None:
    """视频条目的 Content-Type 是视频类型——``<video>`` 才会接管播放与 seek。"""
    response = env.client.get(_asset_url(env, "clip_001"))

    assert response.headers["content-type"] == "video/mp4"
    assert response.content == _MP4_BYTES


def test_asset_advertises_range_support(env: Env) -> None:
    """响应带 accept-ranges: bytes 与 content-length（客户端据此决定要不要分段拉）。"""
    response = env.client.get(_asset_url(env, "clip_001"))

    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(_MP4_BYTES))


def test_asset_serves_partial_content_for_range_request(env: Env) -> None:
    """Range 请求 → 206 + 该段字节 + Content-Range（视频拖动进度条就靠这个）。"""
    response = env.client.get(
        _asset_url(env, "clip_001"), headers={"Range": "bytes=4-9"}
    )

    assert response.status_code == 206
    assert response.content == _MP4_BYTES[4:10]
    assert response.headers["content-range"] == f"bytes 4-9/{len(_MP4_BYTES)}"
    assert response.headers["content-length"] == "6"


def test_asset_unsatisfiable_range_returns_416(env: Env) -> None:
    """越界的 Range → 416 并告知文件真实大小（客户端好重新定位）。"""
    response = env.client.get(
        _asset_url(env, "clip_001"),
        headers={"Range": f"bytes={len(_MP4_BYTES) + 100}-"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(_MP4_BYTES)}"


def test_asset_is_inline_not_attachment(env: Env) -> None:
    """不给 content-disposition：界面要在 <img> / <video> 里内联渲染，attachment 会变成下载。"""
    response = env.client.get(_asset_url(env, "cat_001"))

    assert "content-disposition" not in response.headers


def test_asset_unregistered_file_returns_problem(env: Env) -> None:
    """在盘但没登记过 → 404（未登记文件不属于任何批次，预览端点不为它服务）。"""
    (env.workdir / "stray.jpg").write_bytes(_JPEG_BYTES)

    response = env.client.get(_asset_url(env, "stray"))

    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "asset-not-found"
    assert "未登记在册" in body["detail"]


def test_asset_missing_file_returns_problem(env: Env) -> None:
    """登记在册但素材已被删 → 404，消息指向「重新导入」。"""
    (env.workdir / "cat_001.jpg").unlink()

    response = env.client.get(_asset_url(env, "cat_001"))

    assert response.status_code == 404
    assert "缺失" in response.json()["detail"]


def test_asset_unknown_wid_returns_problem(env: Env) -> None:
    """三重校验第一重：wid 不在注册表 → 404 problem+json。"""
    response = env.client.get("/api/workdirs/nope/items/cat_001/asset")

    assert response.status_code == 404
    assert response.json()["type"] == "workdir-not-found"


def test_asset_refuses_path_like_item_name(env: Env) -> None:
    """素材端点同款两层拦截：正斜杠在路由层匹配不上，反斜杠被条目名校验拦成 400。"""
    outside = env.workdir.parent / "outside.jpg"
    outside.write_bytes(b"outside-bytes")

    slashed = env.client.get(_asset_url(env, "..%2Foutside"))
    backslashed = env.client.get(_asset_url(env, "..%5Coutside"))

    assert slashed.status_code == 404
    assert backslashed.status_code == 400
    assert backslashed.json()["type"] == "asset-path-invalid"
    assert slashed.content != b"outside-bytes"
    assert backslashed.content != b"outside-bytes"


def test_asset_item_name_is_url_decoded(env: Env) -> None:
    """中文与空格的素材主干按 URL 编码传进来也能解析（横切约定：条目身份 = 素材主干）。"""
    source = env.workdir.parent / "source"
    (source / "白裙 01.jpg").write_bytes(b"\xff\xd8\xff\xe0chinese-name-bytes")
    import_assets(env.workdir, source)

    response = env.client.get(_asset_url(env, "白裙 01"))

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xff\xe0chinese-name-bytes"
