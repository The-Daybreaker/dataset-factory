"""接口测试：CLI 侧日志装配（级别映射、流向 stderr、文件日志的滚动参数）。

这三件事都是对外承诺：`dsf` 的结果正文走 stdout 供外部 agent 解析，日志只能走 stderr；
`--log-level` / `DSF_LOG_LEVEL` 的名字要真生效；serve 的文件日志是后台运行唯一的完整日志来源，
滚动参数与编码决定它能不能被读到。行形状也钉住——它是人和脚本解析日志的依据。
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest

from dataset_factory._obs import RequestIdFilter, reset_request_id, set_request_id
from dataset_factory.cli.main import add_server_file_handler, configure_logging

_LINE_START = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} "


@pytest.fixture
def root_logging() -> Iterator[None]:
    """用例结束后把 root logger 的 handler 与级别还原（模块导入期已配过一次日志）。"""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def _stderr_stream(monkeypatch: pytest.MonkeyPatch, level_name: str) -> StringIO:
    """把 stderr 换成内存流后配置日志，返回这条流。"""
    stream = StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    configure_logging(level_name)
    return stream


def test_logs_go_to_stderr_in_the_agreed_shape(
    monkeypatch: pytest.MonkeyPatch, root_logging: None
) -> None:
    """日志进 stderr，行形如「时间 级别 [请求id] logger: 正文」，无请求上下文时 id 为 `-`。"""
    stream = _stderr_stream(monkeypatch, "INFO")
    logger = logging.getLogger("dataset_factory.test.logging")

    logger.info("打标完成 %s", "cat_001")
    token = set_request_id("req-7")
    try:
        logger.info("带请求 id 的一行")
    finally:
        reset_request_id(token)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert re.fullmatch(
        _LINE_START + r"INFO \[-\] dataset_factory\.test\.logging: 打标完成 cat_001",
        lines[0],
    )
    assert re.fullmatch(
        _LINE_START
        + r"INFO \[req-7\] dataset_factory\.test\.logging: 带请求 id 的一行",
        lines[1],
    )


@pytest.mark.parametrize(
    ("level_name", "expected_level"),
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("warning", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("不存在的级别名", logging.INFO),
    ],
)
def test_level_name_sets_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
    root_logging: None,
    level_name: str,
    expected_level: int,
) -> None:
    """级别名大小写不敏感地映射到阈值，认不出来时回落到 INFO；阈值真的挡记录。"""
    stream = _stderr_stream(monkeypatch, level_name)
    logger = logging.getLogger("dataset_factory.test.level")

    logger.debug("调试")
    logger.info("信息")
    logger.warning("警告")
    logger.error("错误")

    written = stream.getvalue()
    assert logging.getLogger().level == expected_level
    assert ("错误" in written) is (expected_level <= logging.ERROR)
    assert ("警告" in written) is (expected_level <= logging.WARNING)
    assert ("信息" in written) is (expected_level <= logging.INFO)
    assert ("调试" in written) is (expected_level <= logging.DEBUG)


def test_reconfiguring_replaces_the_previous_handler(
    monkeypatch: pytest.MonkeyPatch, root_logging: None
) -> None:
    """重复配置以应用为准：root 上始终只有一个 handler（uvicorn 等库配过的会被覆盖）。"""
    _stderr_stream(monkeypatch, "INFO")
    first = list(logging.getLogger().handlers)
    second_stream = StringIO()
    monkeypatch.setattr(sys, "stderr", second_stream)
    configure_logging("WARNING")

    assert len(logging.getLogger().handlers) == 1
    assert logging.getLogger().handlers[0] not in first
    logging.getLogger("dataset_factory.test.reconfigure").info("被挡住")
    assert second_stream.getvalue() == ""


def test_server_file_handler_rolling_and_encoding(
    tmp_path: Path, root_logging: None
) -> None:
    """serve 的文件日志：滚动 5 MiB × 3 份、UTF-8、挂在 root 上、父目录自动建出来。"""
    target = tmp_path / "logs" / "nested" / "server.log"

    handler = add_server_file_handler(target)
    try:
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 5 * 1024 * 1024
        assert handler.backupCount == 3
        assert handler.encoding == "utf-8"
        assert target.parent.is_dir()
        assert handler in logging.getLogger().handlers
        assert any(isinstance(f, RequestIdFilter) for f in handler.filters)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_server_file_handler_line_matches_the_stderr_shape(
    tmp_path: Path, root_logging: None
) -> None:
    """文件侧与终端侧同一套行形状：中文按 UTF-8 落盘，请求 id 一样注入。"""
    target = tmp_path / "server.log"
    handler = add_server_file_handler(target)
    logger = logging.getLogger("dataset_factory.test.file")
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("写入探测 一条")
        token = set_request_id("req-9")
        try:
            logger.warning("带请求 id 的一条")
        finally:
            reset_request_id(token)
    finally:
        handler.flush()
        logging.getLogger().removeHandler(handler)
        handler.close()

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert re.fullmatch(
        _LINE_START + r"WARNING \[-\] dataset_factory\.test\.file: 写入探测 一条",
        lines[0],
    )
    assert re.fullmatch(
        _LINE_START
        + r"WARNING \[req-9\] dataset_factory\.test\.file: 带请求 id 的一条",
        lines[1],
    )
