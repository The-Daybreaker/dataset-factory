"""单元测试：llm 的配置与密钥读取（数据根 / config.json / 双通道 / 脱敏）。

全部离线、不真调 API；用 temp_data_root fixture 把数据根隔离到临时目录，绝不碰真实 ~/.dataset_factory。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataset_factory.llm import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    data_root,
    read_config,
)


def _write_config(
    root: Path,
    base_url: str = "https://api.example.com/v1",
    model: str = "test-model",
) -> None:
    (root / "config.json").write_text(
        json.dumps({"base_url": base_url, "model": model}), encoding="utf-8"
    )


def _write_credentials(root: Path, key: str = "sk-file-key") -> None:
    (root / "credentials").write_text(key, encoding="utf-8")


def test_data_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设 DATASET_FACTORY_HOME 时，数据根 = ~/.dataset_factory。"""
    monkeypatch.delenv("DATASET_FACTORY_HOME", raising=False)
    assert data_root() == Path.home() / ".dataset_factory"


def test_data_root_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """设了 DATASET_FACTORY_HOME 就用它覆盖。"""
    monkeypatch.setenv("DATASET_FACTORY_HOME", str(tmp_path))
    assert data_root() == tmp_path


def test_read_config_from_files(temp_data_root: Path) -> None:
    """正常路径：config.json 出 base_url/model，credentials 出密钥。"""
    _write_config(temp_data_root)
    _write_credentials(temp_data_root, "sk-abc123")
    cfg = read_config()
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "test-model"
    assert cfg.api_key.reveal() == "sk-abc123"


def test_env_key_overrides_credentials(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """双通道：DSF_API_KEY 环境变量优先覆盖 credentials 文件。"""
    _write_config(temp_data_root)
    _write_credentials(temp_data_root, "sk-file-key")
    monkeypatch.setenv("DSF_API_KEY", "sk-env-key")
    assert read_config().api_key.reveal() == "sk-env-key"


def test_env_key_without_credentials_file(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有环境变量、没有 credentials 文件也能读到密钥；两端空白被 strip。"""
    _write_config(temp_data_root)
    monkeypatch.setenv("DSF_API_KEY", "  sk-env-only  ")
    assert read_config().api_key.reveal() == "sk-env-only"


def test_missing_config_json_raises(temp_data_root: Path) -> None:
    """config.json 不存在 → ConfigError，信息指向该文件。"""
    _write_credentials(temp_data_root)
    with pytest.raises(ConfigError, match=r"config\.json"):
        read_config()


def test_malformed_config_json_raises(temp_data_root: Path) -> None:
    """config.json 不是合法 JSON → ConfigError。"""
    (temp_data_root / "config.json").write_text("{not valid json", encoding="utf-8")
    _write_credentials(temp_data_root)
    with pytest.raises(ConfigError, match="JSON"):
        read_config()


def test_config_not_object_raises(temp_data_root: Path) -> None:
    """config.json 顶层不是对象（如数组）→ ConfigError。"""
    (temp_data_root / "config.json").write_text(json.dumps(["a"]), encoding="utf-8")
    _write_credentials(temp_data_root)
    with pytest.raises(ConfigError, match="对象"):
        read_config()


def test_config_missing_fields_raises(temp_data_root: Path) -> None:
    """config.json 缺 model 字段 → ConfigError，信息点名缺哪个。"""
    (temp_data_root / "config.json").write_text(
        json.dumps({"base_url": "https://x/v1"}), encoding="utf-8"
    )
    _write_credentials(temp_data_root)
    with pytest.raises(ConfigError, match="model"):
        read_config()


def test_no_key_anywhere_raises(temp_data_root: Path) -> None:
    """既无环境变量又无 credentials 文件 → ConfigError。"""
    _write_config(temp_data_root)
    with pytest.raises(ConfigError, match="API 密钥"):
        read_config()


def test_empty_credentials_raises(temp_data_root: Path) -> None:
    """credentials 文件只有空白 → 视为无密钥 → ConfigError。"""
    _write_config(temp_data_root)
    _write_credentials(temp_data_root, "   ")
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
