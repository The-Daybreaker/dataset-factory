"""端点多配置端点：GET/POST /api/endpoints、PUT/DELETE /api/endpoints/{id}、POST .../{id}/activate、POST /api/endpoints/test。

设置页「列表 + 详情」与工作台切换器的数据面。**寻址一律用配置 ID**（内部稳定身份，
显示名可改可重名、不参与寻址）。密钥只进不出：请求体可带密钥落盘，任何响应只报有无
（has_api_key）、绝不回内容。错误状态码由 app 的全局异常映射表按异常类型给出（404
不存在 / 409 删除当前使用 / 400 其余配置错），本文件不做 try/except 翻译——test 例外：
连通性探测的成败是业务结果而非服务器错误，HTTP 恒 200。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ..llm import (
    SUPPORTED_API_FORMAT,
    EndpointConfig,
    EndpointConfigInfo,
    SecretValue,
    config_info,
    create_config,
    delete_config,
    env_api_key,
    first_api_key,
    list_configs,
    parse_request_params,
    probe_endpoint,
    read_stored_api_key,
    rename_config,
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
            "description": "显示名不合法 / 字段为空 / API 格式暂未支持",
        },
    },
)
def create(request: EndpointCreateRequest) -> EndpointConfigSummary:
    """新增一套端点配置；当前没有生效配置时自动设为当前使用。"""
    api_key = _parse_key(request.api_key)
    cid = create_config(
        name=request.name,
        base_url=request.base_url,
        model=request.model,
        api_key=api_key,
        api_format=request.api_format,
        request_params=_params_payload(request.request_params),
    )
    return _summary_of(cid)


@router.put(
    "/{cid}",
    response_model=EndpointConfigSummary,
    responses={
        400: {
            "model": ErrorDetail,
            "description": "字段为空 / API 格式暂未支持 / 显示名不合法",
        },
        404: {"model": ErrorDetail, "description": "配置不存在"},
    },
)
def update(cid: str, request: EndpointUpdateRequest) -> EndpointConfigSummary:
    """更新一套配置的端点字段；api_key 缺省沿用已存密钥、参数块缺省沿用已有参数。

    带 ``new_name`` 时改显示名（只写 config.json 的 name 字段；显示名允许重名，
    无冲突语义——身份是 ID）。
    """
    api_key = _parse_key(request.api_key)
    clean = update_config(
        cid=cid,
        base_url=request.base_url,
        model=request.model,
        api_key=api_key,
        api_format=request.api_format,
        request_params=_params_payload(request.request_params),
    )
    if request.new_name is not None:
        clean = rename_config(clean, request.new_name)
    return _summary_of(clean)


@router.delete(
    "/{cid}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorDetail, "description": "配置不存在"},
        409: {
            "model": ErrorDetail,
            "description": "是当前使用中的配置（先切换到其他配置再删）",
        },
    },
)
def remove(cid: str) -> Response:
    """删除一套端点配置（连同其密钥文件）。"""
    delete_config(cid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{cid}/activate",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorDetail, "description": "配置不存在"}},
)
def activate(cid: str) -> Response:
    """把一套配置设为当前使用；对新请求立即生效。"""
    set_active_config(cid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=EndpointTestResult)
def test_connection(request: EndpointTestRequest) -> EndpointTestResult:
    """测试端点连通性：用表单当前值两档探测，不必先保存。

    密钥三通道（2026-09-21 审计定案，与实调的 resolve_api_key 对齐）：表单值 > 环境变量
    DSF_API_KEY > 该配置已存密钥——之前探测不认环境变量通道，会出现「实调能通、测试
    连接却报无密钥」的假故障。api_format 是真校验不是摆设：与受支持格式不符时直接给出
    可操作失败，不留「改了以为生效」的假字段。
    """
    if request.api_format != SUPPORTED_API_FORMAT:
        return EndpointTestResult(
            ok=False,
            message=(
                f"API 格式「{request.api_format}」暂不支持"
                f"（当前仅支持 {SUPPORTED_API_FORMAT}）——请改回默认值。"
            ),
            latency_ms=0.0,
        )
    key = first_api_key(
        _parse_key(request.api_key),
        env_api_key(),
        read_stored_api_key(request.id) if request.id is not None else None,
    )
    if key is None:
        return EndpointTestResult(
            ok=False,
            message="未提供密钥，且该配置名下没有已存密钥；请填写密钥后重试。",
            latency_ms=0.0,
        )
    request_params = parse_request_params(_params_payload(request.request_params) or {})
    result = probe_endpoint(
        EndpointConfig(
            base_url=request.base_url,
            model=request.model,
            api_key=key,
            request=request_params,
        )
    )
    return EndpointTestResult(
        ok=result.ok,
        message=result.message,
        latency_ms=result.latency_ms,
        effective_params=_probe_params_echo(request),
    )


def _probe_params_echo(request: EndpointTestRequest) -> dict[str, object]:
    """回显本次探测实际发出的关键参数（探测专用传输参数不在此列）。

    「测试连接」的价值不只是通不通，还包括让用户核对「我会以什么参数发请求」——思考
    开关是否带上、透传对象写对没有，在这里一眼可见（2026-09-21 审计定案 A1）。
    """
    echo: dict[str, object] = {"model": request.model, "stream": True, "max_tokens": 1}
    params = request.request_params
    if params is not None:
        if params.temperature is not None:
            echo["temperature"] = params.temperature
        if params.top_p is not None:
            echo["top_p"] = params.top_p
        if params.enable_thinking is not None:
            echo["enable_thinking"] = params.enable_thinking
        if params.extra_body is not None:
            echo["extra_body"] = dict(params.extra_body)
    return echo


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
    if params.enable_thinking is not None:
        payload["enable_thinking"] = params.enable_thinking
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
