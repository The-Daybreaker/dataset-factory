"""端点多配置端点：GET/POST /api/endpoints、PUT/DELETE /api/endpoints/{name}、POST .../{name}/activate、POST /api/endpoints/test。

设置页「列表 + 详情」与工作台切换器的数据面。密钥只进不出：请求体可带密钥落盘，
任何响应只报有无（has_api_key）、绝不回内容。错误状态码由 app 的全局异常映射表按
异常类型给出（404 不存在 / 409 重名与删除当前使用 / 400 其余配置错），本文件不做
try/except 翻译——test 例外：连通性探测的成败是业务结果而非服务器错误，HTTP 恒 200。
"""

from __future__ import annotations

from time import perf_counter
from typing import cast

from fastapi import APIRouter, Response, status

from .._obs import ms_since
from ..llm import (
    SUPPORTED_API_FORMAT,
    EndpointConfig,
    EndpointConfigInfo,
    LLMError,
    Message,
    RequestConfig,
    SecretValue,
    TextPart,
    active_config_name,
    build_completer,
    create_config,
    delete_config,
    has_stored_key,
    list_configs,
    read_config_data,
    read_stored_api_key,
    set_active_config,
    update_config,
)
from .schemas import (
    EndpointConfigSummary,
    EndpointCreateRequest,
    EndpointTestRequest,
    EndpointTestResult,
    EndpointUpdateRequest,
    ErrorDetail,
)

router = APIRouter(prefix="/api/endpoints", tags=["端点配置"])

# 连通性探测的传输参数：比正式打标更急——15 秒等不到就报超时、不重试（用户在等结果）。
_TEST_TIMEOUT_SECONDS = 15.0


@router.get("", response_model=list[EndpointConfigSummary])
def list_all() -> list[EndpointConfigSummary]:
    """列出全部端点配置（按名称排序；密钥只报有无）。"""
    return [_to_summary(info) for info in list_configs()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=EndpointConfigSummary,
    responses={
        400: {
            "model": ErrorDetail,
            "description": "名称不合法 / 字段为空 / API 格式暂未支持",
        },
        409: {"model": ErrorDetail, "description": "已存在同名（不区分大小写）配置"},
    },
)
def create(request: EndpointCreateRequest) -> EndpointConfigSummary:
    """新增一套端点配置；当前没有生效配置时自动设为当前使用。"""
    api_key = _parse_key(request.api_key)
    name = create_config(
        name=request.name,
        base_url=request.base_url,
        model=request.model,
        api_key=api_key,
        api_format=request.api_format,
    )
    return _summary_of(name)


@router.put(
    "/{name}",
    response_model=EndpointConfigSummary,
    responses={
        400: {
            "model": ErrorDetail,
            "description": "字段为空 / API 格式暂未支持",
        },
        404: {"model": ErrorDetail, "description": "配置不存在"},
    },
)
def update(name: str, request: EndpointUpdateRequest) -> EndpointConfigSummary:
    """更新一套配置的端点字段；api_key 缺省沿用已存密钥（不强迫重输）。"""
    api_key = _parse_key(request.api_key)
    clean = update_config(
        name=name,
        base_url=request.base_url,
        model=request.model,
        api_key=api_key,
        api_format=request.api_format,
    )
    return _summary_of(clean)


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorDetail, "description": "配置不存在"},
        409: {
            "model": ErrorDetail,
            "description": "是当前使用中的配置（先切换到其他配置再删）",
        },
    },
)
def remove(name: str) -> Response:
    """删除一套端点配置（连同其密钥文件）。"""
    delete_config(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{name}/activate",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "配置不存在"}},
)
def activate(name: str) -> Response:
    """把一套配置设为当前使用；对新请求立即生效。"""
    set_active_config(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=EndpointTestResult)
def test_connection(request: EndpointTestRequest) -> EndpointTestResult:
    """测试端点连通性：用表单当前值发一个极小的真实请求（max_tokens=1），不必先保存。"""
    key: SecretValue | None
    if request.api_key is not None and request.api_key.strip() != "":
        key = SecretValue(request.api_key.strip())
    elif request.name is not None:
        key = read_stored_api_key(request.name)
    else:
        key = None
    if key is None:
        return EndpointTestResult(
            ok=False,
            message="未提供密钥，且该配置名下没有已存密钥；请填写密钥后重试。",
            latency_ms=0.0,
        )
    config = EndpointConfig(
        base_url=request.base_url,
        model=request.model,
        api_key=key,
        request=RequestConfig(
            timeout_seconds=_TEST_TIMEOUT_SECONDS,
            max_retries=0,
        ),
    )
    return _probe(config)


def _probe(config: EndpointConfig) -> EndpointTestResult:
    """发一个极小的真实请求探测连通性；失败翻译成分类消息（llm 错误本身就可操作）。"""
    start = perf_counter()
    try:
        build_completer(config).complete(
            [Message(role="user", parts=(TextPart(text="ping"),))],
        )
    except LLMError as exc:
        return EndpointTestResult(
            ok=False, message=str(exc), latency_ms=ms_since(start)
        )
    return EndpointTestResult(
        ok=True, message="连接成功，模型应答正常。", latency_ms=ms_since(start)
    )


def _parse_key(raw: str | None) -> SecretValue | None:
    """请求体里的密钥 → SecretValue；空白视为未提供（创建即暂不配置、更新即沿用）。"""
    if raw is None or not raw.strip():
        return None
    return SecretValue(raw.strip())


def _to_summary(info: EndpointConfigInfo) -> EndpointConfigSummary:
    """存储概要 → 响应模型（形状一致，显式搬运以守住响应契约）。"""
    return EndpointConfigSummary(
        name=info.name,
        base_url=info.base_url,
        model=info.model,
        api_format=info.api_format,
        has_api_key=info.has_api_key,
        is_active=info.is_active,
    )


def _summary_of(name: str) -> EndpointConfigSummary:
    """写盘后重读一份概要（保证响应反映的是落盘事实，不是请求参数）。"""
    data = read_config_data(name)
    return EndpointConfigSummary(
        name=name,
        base_url=cast(str, data["base_url"]),
        model=cast(str, data["model"]),
        api_format=cast("str | None", data.get("api_format")) or SUPPORTED_API_FORMAT,
        has_api_key=has_stored_key(name),
        is_active=active_config_name() == name,
    )
