"""OpenAPI 契约快照的一致性检查。

契约（openapi.json）是前后端对齐的单一事实来源：前端从它生成类型、CI 用它做
破坏性变更检测。这里保证「仓库里的快照 == 代码现状」——改了路由忘了重新导出，
这条测试当场红掉，不用等 CI。
"""

from __future__ import annotations

import json
from pathlib import Path

from dataset_factory.api import create_app

app = create_app()

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_openapi_snapshot_matches_code() -> None:
    """快照与代码生成的 spec 必须逐字节一致。"""
    expected = (
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    snapshot_path = BACKEND_DIR / "openapi.json"
    assert snapshot_path.is_file(), (
        "openapi.json 不存在；运行 `uv run python scripts/export_openapi.py` 生成契约快照。"
    )
    actual = snapshot_path.read_text(encoding="utf-8")
    assert actual == expected, (
        "openapi.json 与代码不一致——API 契约变了但快照没更新；"
        "运行 `uv run python scripts/export_openapi.py` 重新导出并提交。"
    )


def test_contract_declares_error_responses() -> None:
    """契约必须覆盖错误路径：每个非 2xx 声明都用统一的 ErrorDetail 形状。

    为什么值得单独立一条：OpenAPI 默认只渲染成功响应，错误体不声明的话，
    前端生成类型时看不到它们——这正是调研时点名的典型盲区。
    """
    spec = app.openapi()
    label_responses = spec["paths"]["/api/label"]["post"]["responses"]
    for code in ("400", "404", "502"):
        assert code in label_responses, f"POST /api/label 缺少 {code} 错误声明"
        ref = label_responses[code]["content"]["application/json"]["schema"]["$ref"]
        assert ref == "#/components/schemas/ErrorDetail"
    assert "ErrorDetail" in spec["components"]["schemas"]
