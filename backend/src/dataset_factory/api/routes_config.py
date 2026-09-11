"""配置端点：GET /api/config、PUT /api/config——密钥只进不出（写入、绝不回显）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..llm import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    describe_config,
    read_stored_api_key,
    write_config,
)
from .schemas import ConfigResponse, ConfigUpdateRequest

router = APIRouter(prefix="/api/config", tags=["配置"])


@router.get("", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    """查看当前配置（密钥只报来源与是否已配置，绝不回内容）。"""
    base_url, model, key_source = describe_config()
    return ConfigResponse(
        base_url=base_url,
        model=model,
        api_key_configured=key_source is not None,
        key_source=key_source,
    )


@router.put("", status_code=204)
def update_config(request: ConfigUpdateRequest) -> None:
    """更新配置；api_key 缺省沿用现有密钥（Web 表单改 base_url 不必重输密钥）。"""
    api_key: SecretValue
    if request.api_key is not None and request.api_key.strip():
        api_key = SecretValue(request.api_key.strip())
    else:
        stored = read_stored_api_key()
        if stored is None:
            raise HTTPException(
                status_code=400,
                detail="未提供 api_key，且当前没有已配置的密钥；请填写 api_key。",
            )
        api_key = stored
    try:
        write_config(
            EndpointConfig(
                base_url=request.base_url, model=request.model, api_key=api_key
            )
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
