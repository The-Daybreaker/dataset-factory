"""单元测试：llm 的配置与密钥读取与写入（数据根 / config.json / 双通道 / 脱敏 / 原子写）。

全部离线、不真调 API；用 temp_data_root fixture 把数据根隔离到临时目录，绝不碰真实 ~/.dataset_factory。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from dataset_factory.llm import (
    ConfigError,
    EndpointConfig,
    SecretValue,
    read_config,
    write_config,
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


def _endpoint(
    base_url: str = "https://api.example.com/v1",
    model: str = "test-model",
    key: str = "sk-write-me",
) -> EndpointConfig:
    return EndpointConfig(base_url=base_url, model=model, api_key=SecretValue(key))


def test_read_config_parses_request_params(temp_data_root: Path) -> None:
    """config.json 里配了请求参数时，read_config 把它们带进 EndpointConfig.request。"""
    (temp_data_root / "config.json").write_text(
        json.dumps(
            {
                "base_url": "https://api.example.com/v1",
                "model": "test-model",
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 2048,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                "timeout_seconds": 300,
                "max_retries": 1,
            }
        ),
        encoding="utf-8",
    )
    _write_credentials(temp_data_root)

    request = read_config().request

    assert request.temperature == 0.7
    assert request.top_p == 0.8
    assert request.max_tokens == 2048
    assert request.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert request.timeout_seconds == 300
    assert request.max_retries == 1


def test_read_config_request_params_default_when_absent(temp_data_root: Path) -> None:
    """没配请求参数时用内置默认；生成参数为 None（意思是「不传」而不是「传 0」）。"""
    _write_config(temp_data_root)
    _write_credentials(temp_data_root)

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
    (temp_data_root / "config.json").write_text(
        json.dumps(
            {"base_url": "https://api.example.com/v1", "model": "m", field: value}
        ),
        encoding="utf-8",
    )
    _write_credentials(temp_data_root)

    with pytest.raises(ConfigError):
        read_config()


def test_write_config_keeps_existing_request_params(temp_data_root: Path) -> None:
    """write_config 只更新端点两项：config.json 里已有的请求参数原样保留（不被抹掉）。"""
    (temp_data_root / "config.json").write_text(
        json.dumps(
            {
                "base_url": "https://old/v1",
                "model": "old-model",
                "temperature": 0.3,
                "timeout_seconds": 300,
            }
        ),
        encoding="utf-8",
    )
    _write_credentials(temp_data_root)

    write_config(_endpoint(base_url="https://new/v1", model="new-model"))

    saved = json.loads((temp_data_root / "config.json").read_text(encoding="utf-8"))
    assert saved["base_url"] == "https://new/v1"
    assert saved["model"] == "new-model"
    assert saved["temperature"] == 0.3
    assert saved["timeout_seconds"] == 300


def test_read_config_from_files(temp_data_root: Path) -> None:
    """正常路径：config.json 出 base_url/model，credentials 出密钥。"""
    _write_config(temp_data_root)
    _write_credentials(temp_data_root, "sk-abc123")
    cfg = read_config()
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "test-model"
    assert cfg.api_key.reveal() == "sk-abc123"


def test_read_config_parses_golden_fixture(temp_data_root: Path) -> None:
    """契约测试：read_config 正确解析一份手写标准 config.json（把磁盘格式钉成独立样例，防读写两侧一起漂移）。"""
    golden = Path(__file__).parent / "fixtures" / "config.json"
    shutil.copyfile(golden, temp_data_root / "config.json")
    _write_credentials(temp_data_root, "sk-golden-fixture")

    cfg = read_config()

    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "example-caption-model"


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


def test_write_config_round_trips_through_read(temp_data_root: Path) -> None:
    """写入后能被读侧原样读回：读写对称、闭环。"""
    write_config(_endpoint(key="sk-round-trip"))

    cfg = read_config()

    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "test-model"
    assert cfg.api_key.reveal() == "sk-round-trip"


def test_write_config_creates_missing_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据根（含多层父目录）不存在时自动创建再写。"""
    root = tmp_path / "nested" / "dsf_home"
    monkeypatch.setenv("DATASET_FACTORY_HOME", str(root))
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    write_config(_endpoint())

    assert (root / "config.json").exists()
    assert (root / "credentials").exists()


def test_write_config_overwrites_existing(temp_data_root: Path) -> None:
    """覆盖写：第二次写的值完全替换第一次。"""
    write_config(_endpoint(model="old-model", key="sk-old"))

    write_config(_endpoint(model="new-model", key="sk-new"))

    cfg = read_config()
    assert cfg.model == "new-model"
    assert cfg.api_key.reveal() == "sk-new"


def test_write_config_leaves_only_target_files(temp_data_root: Path) -> None:
    """原子写收尾干净：数据根里只有两个目标文件，没有残留的 .tmp 临时文件。"""
    write_config(_endpoint())

    names = {p.name for p in temp_data_root.iterdir()}

    assert names == {"config.json", "credentials"}


@pytest.mark.skipif(os.name != "posix", reason="0600 权限语义仅 POSIX 有")
def test_write_config_credentials_owner_only_on_posix(temp_data_root: Path) -> None:
    """Unix 上 credentials 落盘即 0600：同机其他用户读不到密钥。"""
    write_config(_endpoint())

    mode = stat.S_IMODE((temp_data_root / "credentials").stat().st_mode)

    assert mode == 0o600


@pytest.mark.parametrize(
    ("base_url", "model", "key"),
    [
        ("", "m", "sk-x"),
        ("   ", "m", "sk-x"),
        ("https://x/v1", "", "sk-x"),
        ("https://x/v1", "  ", "sk-x"),
        ("https://x/v1", "m", ""),
        ("https://x/v1", "m", "   "),
    ],
)
def test_write_config_rejects_blank_fields(
    temp_data_root: Path, base_url: str, model: str, key: str
) -> None:
    """Fail-Fast：三个字段任一为空（或纯空白）都拒绝写，不落坏配置。"""
    with pytest.raises(ConfigError):
        write_config(_endpoint(base_url=base_url, model=model, key=key))

    assert not (temp_data_root / "config.json").exists()
    assert not (temp_data_root / "credentials").exists()


def test_write_config_strips_surrounding_whitespace(temp_data_root: Path) -> None:
    """写入前 strip：落盘的是干净值，与读侧 strip 一致。"""
    write_config(_endpoint(base_url="  https://x/v1  ", model=" m ", key="  sk-trim  "))

    cfg = read_config()

    assert cfg.base_url == "https://x/v1"
    assert cfg.model == "m"
    assert cfg.api_key.reveal() == "sk-trim"


def test_write_config_error_does_not_leak_secret(temp_data_root: Path) -> None:
    """脱敏：写入报错时，错误信息里绝不含密钥明文。"""
    secret = "sk-super-secret-do-not-leak"  # pragma: allowlist secret

    with pytest.raises(ConfigError) as excinfo:
        write_config(_endpoint(model="", key=secret))

    assert secret not in str(excinfo.value)


def test_write_config_replace_failure_cleans_up(
    temp_data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改名失败（如磁盘满）→ ConfigError，且不留半个损坏文件与残留临时文件。"""

    def _boom(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(ConfigError, match="无法写入"):
        write_config(_endpoint())

    assert not (temp_data_root / "config.json").exists()
    assert [p for p in temp_data_root.iterdir() if p.suffix == ".tmp"] == []


def test_write_config_uncreatable_data_root_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据根建不出来（父路径是普通文件）→ ConfigError，信息可操作。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("DATASET_FACTORY_HOME", str(blocker / "dsf_home"))
    monkeypatch.delenv("DSF_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="准备写入"):
        write_config(_endpoint())


def test_write_config_unencodable_content_raises_and_cleans_up(
    temp_data_root: Path,
) -> None:
    """内容含 UTF-8 无法编码的字符（孤立代理项）→ ConfigError，且清掉临时文件、不落坏文件。"""
    unencodable = "bad-" + chr(0xD800) + "-model"

    with pytest.raises(ConfigError, match="无法编码"):
        write_config(_endpoint(model=unencodable))

    assert not (temp_data_root / "config.json").exists()
    assert [p for p in temp_data_root.iterdir() if p.suffix == ".tmp"] == []
