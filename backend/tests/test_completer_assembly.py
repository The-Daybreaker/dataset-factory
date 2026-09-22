"""单元测试：策略快照 → 打标客户端的装配（`runs.runner.completer_for_snapshot`）。

这是「本批到底会往哪个端点、用哪个模型、带哪些参数发请求」的唯一装配点，
也是变异度量清单里最大的一个覆盖空洞（48 条变异体没有任何用例跑到）。装配规则有三条
必须钉住：端点三要素取**快照**（快照隔离，库端事后改配置不影响本批）；密钥**现读**
（运行时凭据，绝不进快照）；API 格式不是当前支持的那个就 fail loud（静默换协议会
把整批结果打废）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dataset_factory.llm import (
    SUPPORTED_API_FORMAT,
    ConfigError,
    EndpointConfig,
    OpenAIChatClient,
    SecretValue,
)
from dataset_factory.llm.endpoints import create_config
from dataset_factory.runs import completer_for_snapshot

_SNAPSHOT_BASE: dict[str, Any] = {
    "name": "snap-endpoint",
    "base_url": "https://snap.example/v1",
    "model": "m-snap",
    "api_format": SUPPORTED_API_FORMAT,
}


def _captured_config(
    monkeypatch: pytest.MonkeyPatch, endpoint_block: dict[str, Any]
) -> EndpointConfig:
    """把装配出口换成记录器，返回真正交给客户端的端点配置。"""
    captured: list[EndpointConfig] = []

    def record(config: EndpointConfig) -> None:
        captured.append(config)

    monkeypatch.setattr("dataset_factory.runs.runner.build_completer", record)
    completer_for_snapshot(endpoint_block)
    assert len(captured) == 1
    return captured[0]


def test_endpoint_and_model_come_from_the_snapshot(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """base_url / model / 请求参数取快照值——数据根里另有一套配置也不看它。"""
    create_config(
        "snap-endpoint",
        base_url="https://library.example/v1",
        model="m-library",
        api_key=SecretValue("stored-live"),
        request_params={"temperature": 0.9},
    )
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    config = _captured_config(
        monkeypatch,
        {**_SNAPSHOT_BASE, "request_params": {"temperature": 0.3, "max_tokens": 512}},
    )

    assert config.base_url == "https://snap.example/v1"
    assert config.model == "m-snap"
    assert config.request.temperature == 0.3
    assert config.request.max_tokens == 512


def test_api_key_is_read_live_and_never_taken_from_the_block(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """密钥现读数据根：快照块里即使混进 api_key 字段也不作数。"""
    create_config(
        "snap-endpoint",
        base_url="https://snap.example/v1",
        model="m-snap",
        api_key=SecretValue("stored-rotated"),
    )
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    stale_key = "snapshot-stale"

    config = _captured_config(monkeypatch, {**_SNAPSHOT_BASE, "api_key": stale_key})

    assert config.api_key.reveal() == "stored-rotated"


def test_env_channel_wins_over_the_stored_key(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """双通道判定与 config 层同一份：环境变量优先于 credentials 文件。"""
    create_config(
        "snap-endpoint",
        base_url="https://snap.example/v1",
        model="m-snap",
        api_key=SecretValue("file-channel"),
    )
    monkeypatch.setenv("DSF_API_KEY", "env-channel")

    config = _captured_config(monkeypatch, dict(_SNAPSHOT_BASE))

    assert config.api_key.reveal() == "env-channel"


@pytest.mark.parametrize("api_format", [None, "", _SNAPSHOT_BASE["api_format"]])
def test_missing_or_default_api_format_assembles(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path, api_format: str | None
) -> None:
    """旧快照没写 api_format（或写成空）视为当前支持格式，不报错。"""
    create_config(
        "snap-endpoint",
        base_url="https://snap.example/v1",
        model="m-snap",
        api_key=SecretValue("stored-live"),
    )
    monkeypatch.delenv("DSF_API_KEY", raising=False)
    block = {k: v for k, v in _SNAPSHOT_BASE.items() if k != "api_format"}
    if api_format is not None:
        block["api_format"] = api_format

    config = _captured_config(monkeypatch, block)

    assert config.base_url == "https://snap.example/v1"


def test_unsupported_api_format_fails_loud(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """API 格式不是当前支持的：点名格式并给出可操作下一步，绝不静默按 OpenAI 发请求。"""
    block = {**_SNAPSHOT_BASE, "api_format": "anthropic-messages"}

    with pytest.raises(ConfigError, match="暂不支持"):
        completer_for_snapshot(block)


def test_no_key_on_either_channel_raises(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """两个密钥通道都没有：报错点出配置命令与环境变量名，不装配半截客户端。"""
    create_config(
        "snap-endpoint",
        base_url="https://snap.example/v1",
        model="m-snap",
        api_key=None,
    )
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="DSF_API_KEY"):
        completer_for_snapshot(dict(_SNAPSHOT_BASE))


def test_request_params_absent_use_built_in_defaults(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """快照没带请求参数：全部回内置默认（None = 不传），而不是 0 或空串。"""
    create_config(
        "snap-endpoint",
        base_url="https://snap.example/v1",
        model="m-snap",
        api_key=SecretValue("stored-live"),
    )
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    config = _captured_config(
        monkeypatch, {k: v for k, v in _SNAPSHOT_BASE.items() if k != "request_params"}
    )

    assert config.request.temperature is None
    assert config.request.top_p is None
    assert config.request.max_tokens is None
    assert config.request.extra_body is None
    assert config.request.timeout_seconds == pytest.approx(120.0)
    assert config.request.max_retries == 2


def test_real_assembly_returns_a_completer(
    monkeypatch: pytest.MonkeyPatch, temp_data_root: Path
) -> None:
    """不打桩的真实装配：返回可用的 OpenAI 兼容客户端（协议接线本身也进覆盖）。"""
    create_config(
        "snap-endpoint",
        base_url="https://snap.example/v1",
        model="m-snap",
        api_key=SecretValue("stored-live"),
        request_params={"timeout_seconds": 33, "max_retries": 0},
    )
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    completer = completer_for_snapshot(
        {**_SNAPSHOT_BASE, "request_params": {"timeout_seconds": 33, "max_retries": 0}}
    )

    assert isinstance(completer, OpenAIChatClient)
