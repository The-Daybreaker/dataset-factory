"""契约件：problem+json 的两个声明助手产出的形状（OpenAPI 快照抓不到这一层）。

发现这条盲区是靠变异度量：把 `problem()` 里的键名 `"model"` 改成 `"MODEL"`、`"content"`
改成 `"CONTENT"`，整份 `openapi.json` 逐字节不变、快照用例照样绿——FastAPI 对 `responses=`
里认不出的键**静默丢弃**，所以「声明面」被快照锁住的程度比想象中弱。这两个助手是 60 多处
错误声明的唯一出处，形状只能在单元层钉：本件把它们的返回值逐字钉住，改坏即刻红。
"""

from __future__ import annotations

import json
from typing import Any

from dataset_factory.api.problems import (
    PROBLEM_MEDIA_TYPE,
    problem,
    problem_response,
    problem_schema_responses,
)
from dataset_factory.api.schemas import Problem


def test_problem_declaration_shape() -> None:
    """一条带说明的声明：model=Problem、content 只挂媒体类型、description 原样带上。"""
    declared = problem("配置不存在")

    assert declared == {
        "model": Problem,
        "content": {PROBLEM_MEDIA_TYPE: {}},
        "description": "配置不存在",
    }


def test_problem_without_description_omits_the_key() -> None:
    """不给说明时不写 description 键——键在不在都会改变契约快照。"""
    assert "description" not in problem()
    assert set(problem(None)) == {"model", "content"}


def test_problem_schema_responses_inlines_the_schema() -> None:
    """一组不带说明的声明：逐个状态码内联整份 Problem schema（与 problem() 是两种产物）。"""
    declared = problem_schema_responses([404, 409])

    assert set(declared) == {404, 409}
    assert declared[404] == {
        "content": {PROBLEM_MEDIA_TYPE: {"schema": Problem.model_json_schema()}}
    }
    assert declared == problem_schema_responses([404, 409])
    assert problem_schema_responses([]) == {}


def test_problem_response_carries_status_in_body_and_problem_media_type() -> None:
    """运行时响应：状态码同时写进响应体，媒体类型是 problem+json，扩展字段按需附加。"""
    response = problem_response(
        409,
        "run-occupied",
        "资源被占用",
        "等当前跑批结束后再试",
        {"occupier": {"batch": "s1"}},
    )

    assert response.status_code == 409
    assert response.media_type == PROBLEM_MEDIA_TYPE
    body = _json_body(response)
    assert body == {
        "type": "run-occupied",
        "title": "资源被占用",
        "status": 409,
        "detail": "等当前跑批结束后再试",
        "occupier": {"batch": "s1"},
    }


def test_problem_response_without_extras_has_no_extra_keys() -> None:
    """不附扩展字段（含传空字典）时响应体只有 RFC 9457 的四件套。"""
    body = _json_body(
        problem_response(404, "task-not-found", "任务不存在", "刷新后再试", {})
    )

    assert body == {
        "type": "task-not-found",
        "title": "任务不存在",
        "status": 404,
        "detail": "刷新后再试",
    }


def _json_body(response: Any) -> dict[str, Any]:
    """把 JSONResponse 的字节体解成字典（不依赖其内部缓存属性）。"""
    parsed: dict[str, Any] = json.loads(response.body.decode("utf-8"))
    return parsed
