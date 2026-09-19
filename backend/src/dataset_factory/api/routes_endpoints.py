"""端点多配置端点：GET/POST /api/endpoints、PUT/DELETE /api/endpoints/{name}、POST .../{name}/activate、POST /api/endpoints/test。

设置页「列表 + 详情」与工作台切换器的数据面。密钥只进不出：请求体可带密钥落盘，
任何响应只报有无（has_api_key）、绝不回内容。错误状态码由 app 的全局异常映射表按
异常类型给出（404 不存在 / 409 重名与删除当前使用 / 400 其余配置错），本文件不做
try/except 翻译——test 例外：连通性探测的成败是业务结果而非服务器错误，HTTP 恒 200。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..llm import (
    EndpointConfig,
    EndpointConfigInfo,
    SecretValue,
    config_info,
    create_config,
    delete_config,
    first_api_key,
    list_configs,
    probe_endpoint,
    read_stored_api_key,
    set_active_config,
    update_config,
)
from .schemas import (
    EndpointConfigSummary,
    EndpointCreateRequest,
    EndpointRequestParams,
    EndpointTestRequest,
    EndpointTestResult,
    EndpointUpdateRequest,
    ErrorDetail,
)

router = APIRouter(prefix="/api/endpoints", tags=["端点配置"])


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
        request_params=_params_payload(request.request_params),
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
    """更新一套配置的端点字段；api_key 缺省沿用已存密钥、参数块缺省沿用已有参数。"""
    api_key = _parse_key(request.api_key)
    clean = update_config(
        name=name,
        base_url=request.base_url,
        model=request.model,
        api_key=api_key,
        api_format=request.api_format,
        request_params=_params_payload(request.request_params),
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
    """测试端点连通性：用表单当前值发一个极小的真实请求，不必先保存。"""
    key = first_api_key(
        _parse_key(request.api_key),
        read_stored_api_key(request.name) if request.name is not None else None,
    )
    if key is None:
        return EndpointTestResult(
            ok=False,
            message="未提供密钥，且该配置名下没有已存密钥；请填写密钥后重试。",
            latency_ms=0.0,
        )
    result = probe_endpoint(
        EndpointConfig(base_url=request.base_url, model=request.model, api_key=key)
    )
    return EndpointTestResult(
        ok=result.ok, message=result.message, latency_ms=result.latency_ms
    )


def _parse_key(raw: str | None) -> SecretValue | None:
    """请求体里的密钥 → SecretValue；空白视为未提供（创建即暂不配置、更新即沿用）。"""
    if raw is None or not raw.strip():
        return None
    return SecretValue(raw.strip())


def _params_payload(
    params: EndpointRequestParams | None,
) -> dict[str, object] | None:
    """请求参数模型 → 存储键值（只含实际提供的键；None 原样透传 = 沿用语义）。

    刻意不用 model_dump(exclude_none=True)：它的排除是递归的，会把 extra_body 内层的
    null 值也一并丢掉，破坏透传内容——这里显式搬运，只在外层键上做「null = 不设」。
    """
    if params is None:
        return None
    payload: dict[str, object] = {}
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if params.top_p is not None:
        payload["top_p"] = params.top_p
    if params.max_tokens is not None:
        payload["max_tokens"] = params.max_tokens
    if params.extra_body is not None:
        payload["extra_body"] = dict(params.extra_body)
    if params.timeout_seconds is not None:
        payload["timeout_seconds"] = params.timeout_seconds
    if params.max_retries is not None:
        payload["max_retries"] = params.max_retries
    return payload


def _to_summary(info: EndpointConfigInfo) -> EndpointConfigSummary:
    """存储概要 → 响应模型：字段同名，交给 pydantic 按属性取值（含嵌套的 request_params）。"""
    return EndpointConfigSummary.model_validate(info)


def _summary_of(name: str) -> EndpointConfigSummary:
    """写盘后重读一份概要（保证响应反映的是落盘事实，不是请求参数）。"""
    return _to_summary(config_info(name))
