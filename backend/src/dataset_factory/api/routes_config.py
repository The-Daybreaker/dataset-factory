"""配置端点：GET /api/config、PUT /api/config——当前使用（active）配置的读写，密钥只进不出。

多配置语义（ADR「端点多配置与激活机制」）：GET / PUT 都作用于**当前使用的配置**；一套
配置都没有时，PUT 创建 default 配置并设为当前使用。多套配置的管理（列表 / 新增 / 删除 /
切换）由后续的多配置管理端点提供，本文件只保留「当前配置」这两个入口。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..llm import (
    MIGRATED_CONFIG_NAME,
    ConfigError,
    SecretValue,
    active_config_name,
    create_config,
    describe_config,
    has_config,
    read_stored_api_key,
    set_active_config,
)
from ..llm import update_config as update_active_config
from .schemas import ConfigResponse, ConfigUpdateRequest, ErrorDetail

router = APIRouter(prefix="/api/config", tags=["配置"])


@router.get("", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    """查看当前使用的配置（密钥只报来源与是否已配置，绝不回内容）。"""
    desc = describe_config()
    return ConfigResponse(
        name=desc.name,
        base_url=desc.base_url,
        model=desc.model,
        api_key_configured=desc.key_source is not None,
        key_source=desc.key_source,
    )


@router.put(
    "",
    status_code=204,
    responses={400: {"model": ErrorDetail, "description": "参数不合法 / 未提供密钥"}},
)
def update_config(request: ConfigUpdateRequest) -> None:
    """更新当前使用的配置；尚无可用配置时创建 default 并启用。

    api_key 缺省沿用该配置已存的密钥（Web 表单改 base_url 不必重输密钥）。
    """
    provided = request.api_key.strip() if request.api_key else ""
    active = active_config_name()
    try:
        if active is not None and has_config(active):
            if provided:
                api_key: SecretValue | None = SecretValue(provided)
            else:
                stored = read_stored_api_key(active)
                if stored is None:
                    raise HTTPException(
                        status_code=400,
                        detail="未提供 api_key，且该配置没有已存的密钥；请填写 api_key。",
                    )
                api_key = None
            update_active_config(
                active, base_url=request.base_url, model=request.model, api_key=api_key
            )
            return
        if not provided:
            raise HTTPException(
                status_code=400,
                detail="未提供 api_key，且当前没有已配置的密钥；请填写 api_key。",
            )
        create_config(
            MIGRATED_CONFIG_NAME,
            base_url=request.base_url,
            model=request.model,
            api_key=SecretValue(provided),
        )
        # create_config 只在指针缺失时自动激活；指针悬空时这里显式补一次。
        set_active_config(MIGRATED_CONFIG_NAME)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
