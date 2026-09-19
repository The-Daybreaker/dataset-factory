"""响应模型与域对象的绑定规则（api 层不再逐字段搬运，规则就得有人钉住）。

换成 pydantic 按属性 / 按字典取值之后，「对外契约 = 模型字段表」这条成了唯一事实：
存储层加字段不会让响应悄悄多出一项，模型要的字段少给一个也照样当场报错。
这两件事原来由 api 层那份手抄字段表兜着，现在由下面的用例兜。
"""

from __future__ import annotations

from dataclasses import asdict

import pytest
from pydantic import ValidationError

from dataset_factory.api.schemas import BatchView, WorkdirInfo
from dataset_factory.strategies import BatchEntry
from dataset_factory.workdir import WorkdirEntry


def test_workdir_info_binds_by_attribute() -> None:
    """注册表条目按属性取值，响应字段表就是契约的全部内容。"""
    entry = WorkdirEntry(id="w1", path="/dsf/w1", title="示例", last_used_at=1.5)

    assert WorkdirInfo.model_validate(entry).model_dump() == {
        "id": "w1",
        "path": "/dsf/w1",
        "title": "示例",
        "last_used_at": 1.5,
    }


def test_computed_overlay_wins_and_storage_only_fields_stay_out() -> None:
    """计算字段覆盖同名存储字段；不在契约里的存储字段（snapshot）不外泄。"""
    entry = BatchEntry(
        seq=7,
        name="跑批 A",
        description="说明",
        snapshot="s7.json",
        active=True,
        created_at="2026-09-19T00:00:00+00:00",
    )

    view = BatchView.model_validate(
        asdict(entry) | {"id": "s7", "product_count": 3, "run_total": None}
    ).model_dump()

    assert view["id"] == "s7"
    assert view["seq"] == 7
    assert view["product_count"] == 3
    assert view["run_status"] is None
    assert "snapshot" not in view


def test_missing_contract_field_still_fails_loudly() -> None:
    """域对象给不出模型必填字段时报错，而不是静默用默认值糊过去。"""

    class _Partial:
        id = "w1"

    with pytest.raises(ValidationError):
        WorkdirInfo.model_validate(_Partial())
