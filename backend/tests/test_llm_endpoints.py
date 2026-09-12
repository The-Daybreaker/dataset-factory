"""单元测试：端点多配置存储（endpoints/ 目录、active 指针、名称校验）。

全部离线；temp_data_root 把数据根隔离到临时目录。覆盖：CRUD 与设为当前使用、名称校验
边界、密钥只进不出、原子写失败不留脏文件。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from dataset_factory.llm import (
    SUPPORTED_API_FORMAT,
    ConfigError,
    SecretValue,
    active_config_name,
    create_config,
    delete_config,
    has_config,
    has_stored_key,
    list_configs,
    read_config_data,
    read_stored_api_key,
    set_active_config,
    update_config,
)


def _create(name: str, key: str = "sk-key") -> None:
    """测试便捷封装：建一套带密钥的配置。"""
    create_config(
        name,
        base_url=f"https://{name}.example.com/v1",
        model=f"m-{name}",
        api_key=SecretValue(key),
    )


def test_create_writes_files_and_auto_activates(temp_data_root: Path) -> None:
    """创建第一套配置：落两个文件 + active 指针指向它（第一套创建完就能用）。"""
    _create("default")

    endpoint_dir = temp_data_root / "endpoints" / "default"
    assert (endpoint_dir / "config.json").is_file()
    assert (endpoint_dir / "credentials").is_file()
    assert active_config_name() == "default"

    saved = json.loads((endpoint_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://default.example.com/v1"
    assert saved["model"] == "m-default"
    assert saved["api_format"] == SUPPORTED_API_FORMAT


def test_create_second_does_not_steal_active(temp_data_root: Path) -> None:
    """创建第二套配置不抢当前使用权；列表里只有第一套标 active。"""
    _create("alpha")
    _create("beta")

    assert active_config_name() == "alpha"
    infos = {info.name: info for info in list_configs()}
    assert infos["alpha"].is_active
    assert not infos["beta"].is_active


def test_list_sorted_casefold(temp_data_root: Path) -> None:
    """列表按名称排序（不区分大小写）：大写排在小写前面按字母序而非 ASCII 码位。"""
    _create("beta")
    _create("Alpha")
    _create("charlie")

    assert [info.name for info in list_configs()] == ["Alpha", "beta", "charlie"]


def test_create_rejects_duplicate_casefold(temp_data_root: Path) -> None:
    """重名检查不区分大小写（Windows 目录名不区分大小写，跨平台口径取其严）。"""
    _create("Foo")

    with pytest.raises(ConfigError, match="不区分大小写"):
        _create("foo")


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        ".",
        "..",
        ".hidden",
        "trailing.",
        "a/b",
        "a\\b",
        "a:b",
        "a<b",
        'a"b',
        "a|b",
        "a?b",
        "a*b",
        "a\x01b",
        "名" * 65,
    ],
)
def test_create_rejects_invalid_names(temp_data_root: Path, bad_name: str) -> None:
    """名称校验边界：空 / 路径穿越形态 / Windows 保留字符 / 控制字符 / 超长一律拒绝，不落盘。"""
    with pytest.raises(ConfigError):
        create_config(bad_name, base_url="https://x/v1", model="m", api_key=None)

    assert not (temp_data_root / "endpoints").exists()


def test_create_rejects_blank_endpoint_fields(temp_data_root: Path) -> None:
    """base_url / model 去空白后为空 → 拒绝，不落盘。"""
    with pytest.raises(ConfigError):
        create_config("x", base_url="   ", model="m", api_key=None)

    with pytest.raises(ConfigError):
        create_config("x", base_url="https://x/v1", model="", api_key=None)

    assert not (temp_data_root / "endpoints").exists()


def test_create_rejects_blank_api_key(temp_data_root: Path) -> None:
    """给了密钥但内容空白 → 拒绝（要就不给、要就给有效的，不留半配置）。"""
    with pytest.raises(ConfigError, match="密钥"):
        create_config(
            "x", base_url="https://x/v1", model="m", api_key=SecretValue("   ")
        )


def test_create_rejects_unsupported_api_format(temp_data_root: Path) -> None:
    """api_format 不是当前唯一支持值 → 拒绝（预留字段、不开放乱填）。"""
    with pytest.raises(ConfigError, match="暂未支持"):
        create_config(
            "x",
            base_url="https://x/v1",
            model="m",
            api_key=None,
            api_format="anthropic-messages",
        )


def test_create_without_key_then_env_fallback(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """创建时不带密钥：credentials 不落盘（has_stored_key False），存储层不猜环境变量。"""
    monkeypatch.setenv("DSF_API_KEY", "sk-env")

    create_config("nokey", base_url="https://x/v1", model="m", api_key=None)

    assert has_config("nokey")
    assert not has_stored_key("nokey")
    assert not (temp_data_root / "endpoints" / "nokey" / "credentials").exists()


def test_update_changes_fields_and_keeps_key_and_params(temp_data_root: Path) -> None:
    """更新：端点字段换新；不带密钥沿用已存密钥；用户手配的请求参数原样保留。"""
    _create("prod", key="sk-keep-me")
    config_path = temp_data_root / "endpoints" / "prod" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["temperature"] = 0.3
    data["timeout_seconds"] = 300
    config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    update_config("prod", base_url="https://new/v1", model="new-model")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://new/v1"
    assert saved["model"] == "new-model"
    assert saved["temperature"] == 0.3
    assert saved["timeout_seconds"] == 300
    stored = read_stored_api_key("prod")
    assert stored is not None
    assert stored.reveal() == "sk-keep-me"


def test_update_with_new_key_overwrites_credentials(temp_data_root: Path) -> None:
    """更新时给了新密钥：credentials 被替换。"""
    _create("prod", key="sk-old")

    update_config(
        "prod",
        base_url="https://new/v1",
        model="m",
        api_key=SecretValue("sk-brand-new"),
    )

    stored = read_stored_api_key("prod")
    assert stored is not None
    assert stored.reveal() == "sk-brand-new"


def test_update_missing_config_raises(temp_data_root: Path) -> None:
    """更新不存在的配置 → ConfigError。"""
    with pytest.raises(ConfigError, match="不存在"):
        update_config("ghost", base_url="https://x/v1", model="m")


def test_delete_refuses_active_and_allows_others(temp_data_root: Path) -> None:
    """删除当前使用中的配置被拒；切换后可删；目录连同 credentials 一起消失。"""
    _create("a")
    _create("b")

    with pytest.raises(ConfigError, match="当前使用"):
        delete_config("a")

    set_active_config("b")
    delete_config("a")

    assert not (temp_data_root / "endpoints" / "a").exists()
    assert not has_config("a")
    with pytest.raises(ConfigError, match="不存在"):
        delete_config("a")


def test_set_active_requires_existing(temp_data_root: Path) -> None:
    """切换到不存在的配置 → ConfigError，指针不动。"""
    _create("a")

    with pytest.raises(ConfigError, match="不存在"):
        set_active_config("ghost")

    assert active_config_name() == "a"


def test_active_none_when_unconfigured(temp_data_root: Path) -> None:
    """全新数据根：没有指针、没有配置，探一探不报错。"""
    assert active_config_name() is None
    assert list_configs() == []
    assert not has_config("anything")


def test_read_config_data_validates(temp_data_root: Path) -> None:
    """read_config_data 对损坏数据 fail loud：非法 JSON / 缺字段都点名配置与原因。"""
    _create("bad")
    config_path = temp_data_root / "endpoints" / "bad" / "config.json"
    config_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="JSON"):
        read_config_data("bad")

    config_path.write_text(json.dumps({"base_url": "https://x/v1"}), encoding="utf-8")

    with pytest.raises(ConfigError, match="model"):
        read_config_data("bad")


def test_write_failure_leaves_no_tmp_files(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落盘中途失败（如磁盘满）→ ConfigError，且不留临时文件残骸。"""

    def _boom(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(ConfigError, match="无法写入"):
        create_config("x", base_url="https://x/v1", model="m", api_key=None)

    assert not has_config("x")
    assert list((temp_data_root / "endpoints").rglob("*.tmp")) == []


@pytest.mark.skipif(os.name != "posix", reason="0600 权限语义仅 POSIX 有")
def test_credentials_owner_only_on_posix(temp_data_root: Path) -> None:
    """Unix 上每套配置的 credentials 落盘即 0600：同机其他用户读不到密钥。"""
    _create("sec")

    mode = stat.S_IMODE(
        (temp_data_root / "endpoints" / "sec" / "credentials").stat().st_mode
    )

    assert mode == 0o600


def test_create_error_does_not_leak_secret(temp_data_root: Path) -> None:
    """脱敏：创建失败（字段空）时，错误信息里绝不含密钥明文。"""
    secret = "sk-super-secret-do-not-leak"  # pragma: allowlist secret

    with pytest.raises(ConfigError) as excinfo:
        create_config(
            "x", base_url="https://x/v1", model="", api_key=SecretValue(secret)
        )

    assert secret not in str(excinfo.value)
