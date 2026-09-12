"""单元测试：llm 请求侧配置视图（当前使用配置的读取 / 请求参数解析 / 密钥双通道 / 脱敏）。

全部离线、不真调 API；用 temp_data_root fixture 把数据根隔离到临时目录，绝不碰真实 ~/.dataset_factory。
多配置存储本身（CRUD / active 指针 / 名称校验）见 test_llm_endpoints.py；这里直接在
endpoints/ 下摆好已激活的配置文件作为读取输入，聚焦「读侧怎么解析」。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from dataset_factory.llm import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    read_config,
)


def _seed_active_config(
    root: Path, config: dict[str, object], key: str = "sk-file-key"
) -> None:
    """在数据根下摆一套已激活的 default 配置（存储行为由 test_llm_endpoints.py 负责）。"""
    endpoint_dir = root / "endpoints" / "default"
    endpoint_dir.mkdir(parents=True, exist_ok=True)
    (endpoint_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    (endpoint_dir / "credentials").write_text(key, encoding="utf-8")
    (root / "endpoints" / "active").write_text("default\n", encoding="utf-8")


def _seed_config_only(root: Path, config: dict[str, object]) -> None:
    """只摆 config.json、不摆密钥（供密钥缺失 / 损坏类用例自由组合）。"""
    endpoint_dir = root / "endpoints" / "default"
    endpoint_dir.mkdir(parents=True, exist_ok=True)
    (endpoint_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    (root / "endpoints" / "active").write_text("default\n", encoding="utf-8")


def _write_raw_config(root: Path, content: str) -> None:
    """按原文摆 config.json（不走 json.dumps，供损坏内容用例）。"""
    endpoint_dir = root / "endpoints" / "default"
    endpoint_dir.mkdir(parents=True, exist_ok=True)
    (endpoint_dir / "config.json").write_text(content, encoding="utf-8")
    (root / "endpoints" / "active").write_text("default\n", encoding="utf-8")


def test_read_config_parses_request_params(temp_data_root: Path) -> None:
    """config.json 里配了请求参数时，read_config 把它们带进 EndpointConfig.request。"""
    _seed_active_config(
        temp_data_root,
        {
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
            "temperature": 0.7,
            "top_p": 0.8,
            "max_tokens": 2048,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            "timeout_seconds": 300,
            "max_retries": 1,
        },
    )

    request = read_config().request

    assert request.temperature == 0.7
    assert request.top_p == 0.8
    assert request.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert request.max_tokens == 2048
    assert request.timeout_seconds == 300
    assert request.max_retries == 1


def test_read_config_request_params_default_when_absent(temp_data_root: Path) -> None:
    """没配请求参数时用内置默认；生成参数为 None（意思是「不传」而不是「传 0」）。"""
    _seed_active_config(
        temp_data_root,
        {"base_url": "https://api.example.com/v1", "model": "test-model"},
    )

    request = read_config().request

    assert request.temperature is None
    assert request.top_p is None
    assert request.max_tokens is None
    assert request.extra_body is None
    assert request.timeout_seconds == 120.0
    assert request.max_retries == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", "热"),
        ("max_tokens", 1.5),
        ("extra_body", [1, 2]),
        ("timeout_seconds", True),
    ],
)
def test_read_config_rejects_bad_request_param(
    temp_data_root: Path, field: str, value: object
) -> None:
    """请求参数字段类型不对 → ConfigError（边界 fail loud，不静默退回默认值）。"""
    _seed_config_only(
        temp_data_root,
        {"base_url": "https://api.example.com/v1", "model": "m", field: value},
    )

    with pytest.raises(ConfigError):
        read_config()


def test_read_config_from_files(temp_data_root: Path) -> None:
    """正常路径：config.json 出 base_url/model，credentials 出密钥。"""
    _seed_active_config(
        temp_data_root,
        {"base_url": "https://api.example.com/v1", "model": "test-model"},
        key="sk-abc123",
    )

    cfg = read_config()

    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "test-model"
    assert cfg.api_key.reveal() == "sk-abc123"


def test_read_config_parses_golden_fixture(temp_data_root: Path) -> None:
    """契约测试：read_config 正确解析一份手写标准 config.json（把磁盘格式钉成独立样例，防读写两侧一起漂移）。"""
    golden = Path(__file__).parent / "fixtures" / "config.json"
    endpoint_dir = temp_data_root / "endpoints" / "default"
    endpoint_dir.mkdir(parents=True)
    shutil.copyfile(golden, endpoint_dir / "config.json")
    (endpoint_dir / "credentials").write_text("sk-golden-fixture", encoding="utf-8")
    (temp_data_root / "endpoints" / "active").write_text("default\n", encoding="utf-8")

    cfg = read_config()

    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "example-caption-model"


def test_env_key_overrides_credentials(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双通道：DSF_API_KEY 环境变量优先覆盖 credentials 文件。"""
    _seed_active_config(
        temp_data_root,
        {"base_url": "https://api.example.com/v1", "model": "test-model"},
        key="sk-file-key",
    )
    monkeypatch.setenv("DSF_API_KEY", "sk-env-key")

    assert read_config().api_key.reveal() == "sk-env-key"


def test_env_key_without_credentials_file(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有环境变量、没有 credentials 文件也能读到密钥；两端空白被 strip。"""
    _seed_config_only(
        temp_data_root,
        {"base_url": "https://api.example.com/v1", "model": "test-model"},
    )
    monkeypatch.setenv("DSF_API_KEY", "  sk-env-only  ")

    assert read_config().api_key.reveal() == "sk-env-only"


def test_no_config_anywhere_raises(temp_data_root: Path) -> None:
    """什么配置都没有 → ConfigError，信息可操作（指向 dsf config set / 设置页）。"""
    with pytest.raises(ConfigError, match="未配置任何端点"):
        read_config()


def test_active_pointer_dangling_raises(temp_data_root: Path) -> None:
    """active 指向不存在的配置 → ConfigError，提示重新选择。"""
    (temp_data_root / "endpoints").mkdir()
    (temp_data_root / "endpoints" / "active").write_text("ghost\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="重新选择"):
        read_config()


def test_malformed_config_json_raises(temp_data_root: Path) -> None:
    """config.json 不是合法 JSON → ConfigError。"""
    _write_raw_config(temp_data_root, "{not valid json")

    with pytest.raises(ConfigError, match="JSON"):
        read_config()


def test_config_not_object_raises(temp_data_root: Path) -> None:
    """config.json 顶层不是对象（如数组）→ ConfigError。"""
    _write_raw_config(temp_data_root, json.dumps(["a"]))

    with pytest.raises(ConfigError, match="对象"):
        read_config()


def test_config_missing_fields_raises(temp_data_root: Path) -> None:
    """config.json 缺 model 字段 → ConfigError，信息点名缺哪个。"""
    _seed_config_only(temp_data_root, {"base_url": "https://x/v1"})

    with pytest.raises(ConfigError, match="model"):
        read_config()


def test_no_key_anywhere_raises(temp_data_root: Path) -> None:
    """既无环境变量又无 credentials 文件 → ConfigError。"""
    _seed_config_only(temp_data_root, {"base_url": "https://x/v1", "model": "m"})

    with pytest.raises(ConfigError, match="API 密钥"):
        read_config()


def test_empty_credentials_raises(temp_data_root: Path) -> None:
    """credentials 文件只有空白 → 视为无密钥 → ConfigError。"""
    _seed_active_config(
        temp_data_root, {"base_url": "https://x/v1", "model": "m"}, key="   "
    )

    with pytest.raises(ConfigError, match="API 密钥"):
        read_config()


def test_secret_value_masks_repr() -> None:
    """SecretValue 的 repr / str 都不含真实密钥，reveal() 才拿得到。"""
    secret = SecretValue("sk-super-secret")

    assert "sk-super-secret" not in repr(secret)
    assert "sk-super-secret" not in str(secret)
    assert secret.reveal() == "sk-super-secret"


def test_endpoint_config_repr_masks_key() -> None:
    """EndpointConfig 的 repr 里密钥被掩码，但非敏感字段正常显示。"""
    cfg = EndpointConfig(
        base_url="https://x/v1", model="m", api_key=SecretValue("sk-leak-me")
    )

    text = repr(cfg)

    assert "sk-leak-me" not in text
    assert "https://x/v1" in text


def test_request_config_fields_keep_types(temp_data_root: Path) -> None:
    """RequestConfig 字段类型收紧：读侧产出的 extra_body 是普通 dict（可交给 SDK）。"""
    _seed_active_config(
        temp_data_root,
        {
            "base_url": "https://x/v1",
            "model": "m",
            "extra_body": {"enable_thinking": False},
        },
    )

    extra = read_config().request.extra_body

    assert extra is not None
    assert cast(dict[str, object], extra)["enable_thinking"] is False
