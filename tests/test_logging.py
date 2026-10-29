"""日志内核测试：格式、颜色、级别、环境变量与幂等装配。"""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

from dmsa.errors import InvalidConfigValueError
from dmsa.observability import configure_root, get_logger, trace_context
from dmsa.observability.log_config import (
    ManagedStreamHandler,
    parse_format,
    parse_level,
    quiet_third_party,
    should_colorize,
)
from dmsa.observability.log_formatters import JsonFormatter, TextFormatter

_TEXT_HEAD = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| INFO     \| dmsa\.test:emit_info:\d+ \| "
)


@pytest.fixture
def restored_root():
    """测试结束后移除本模块装入的 handler，避免污染其他用例。"""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, ManagedStreamHandler):
            root.removeHandler(handler)
            handler.close()


def emit_info(logger) -> None:
    """在固定位置打一条 INFO，用于断言函数名与行号。"""
    logger.info("hello", node_id="abc", count=3)  # line offset +1 from def


def _run(stream: io.StringIO, **kwargs) -> None:
    """在给定流上装配日志并打一条样本。"""
    configure_root(stream=stream, color="never", **kwargs)
    emit_info(get_logger("dmsa.test"))


def test_text_formatter_has_timestamp_level_and_location() -> None:
    """文本行含毫秒时间戳、级别与 函数名:行号。"""
    stream = io.StringIO()
    _run(stream, level="info")
    line = stream.getvalue().splitlines()[0]
    assert _TEXT_HEAD.match(line), line
    assert "hello" in line


def test_text_formatter_renders_extra_fields() -> None:
    """extra 字段以 key=value 追加在消息后。"""
    stream = io.StringIO()
    _run(stream)
    line = stream.getvalue().splitlines()[0]
    assert "node_id=abc" in line
    assert "count=3" in line


def test_text_formatter_paints_ansi_when_enabled() -> None:
    """打开颜色后级别与分隔符带 ANSI 序列。"""
    stream = io.StringIO()
    configure_root(stream=stream, level="info", color="always")
    emit_info(get_logger("dmsa.test"))
    assert "\033[32m" in stream.getvalue()


def test_error_message_is_red() -> None:
    """ERROR 级别的消息本体标红。"""
    stream = io.StringIO()
    configure_root(stream=stream, level="info", color="always")
    get_logger("dmsa.test").error("boom")
    assert "\033[31mboom\033[0m" in stream.getvalue()


def test_json_formatter_carries_location_and_fields() -> None:
    """JSON 行包含 location/func/line 与业务字段。"""
    stream = io.StringIO()
    _run(stream, fmt="json")
    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "hello"
    assert payload["func"] == "emit_info"
    assert payload["node_id"] == "abc"
    assert payload["location"].startswith("dmsa.test:emit_info:")


def test_trace_context_appears_in_both_formats() -> None:
    """trace_context 内的日志在两种格式里都带 trace。"""
    text_stream = io.StringIO()
    _run(text_stream)
    with trace_context("req-42"):
        get_logger("dmsa.test").info("inside")
    assert "trace=req-42" in text_stream.getvalue().splitlines()[-1]

    json_stream = io.StringIO()
    configure_root(stream=json_stream, level="info", fmt="json")
    with trace_context("req-43"):
        get_logger("dmsa.test").info("inside")
    assert json.loads(json_stream.getvalue().splitlines()[-1])["trace_id"] == "req-43"


def test_configure_root_reads_env(monkeypatch, restored_root) -> None:
    """DMSA_LOG_LEVEL / DMSA_LOG_FORMAT 无显式参数时生效。"""
    monkeypatch.setenv("DMSA_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("DMSA_LOG_FORMAT", "json")
    stream = io.StringIO()
    configure_root(stream=stream, color="never")
    assert logging.getLogger().level == logging.WARNING
    get_logger("dmsa.test").info("dropped")
    get_logger("dmsa.test").warning("kept")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "kept"


def test_configure_root_is_idempotent(restored_root) -> None:
    """重复装配只保留一个自有 handler，且不破坏外部 handler。"""
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    first = io.StringIO()
    configure_root(stream=first, level="info", color="never")
    second = io.StringIO()
    configure_root(stream=second, level="info", color="never")
    handlers = logging.getLogger().handlers
    assert sum(isinstance(h, ManagedStreamHandler) for h in handlers) == 1
    assert foreign in handlers
    get_logger("dmsa.test").info("once")
    assert first.getvalue() == ""
    assert second.getvalue().count("once") == 1
    logging.getLogger().removeHandler(foreign)


def test_parse_level_accepts_names_numbers_and_defaults() -> None:
    """级别解析覆盖名称、大小写、整数与默认值。"""
    assert parse_level("info") == logging.INFO
    assert parse_level("Warning") == logging.WARNING
    assert parse_level("5") == 5
    assert parse_level(20) == 20
    assert parse_level(None) == logging.INFO
    assert parse_level("") == logging.INFO
    assert parse_level("off") > logging.CRITICAL


def test_parse_level_rejects_unknown() -> None:
    """非法级别直接报错，不静默回落。"""
    with pytest.raises(InvalidConfigValueError, match="unknown log level"):
        parse_level("loud")


def test_parse_format_aliases_and_errors() -> None:
    """格式接受别名，未知值报错。"""
    assert parse_format("JSON") == "json"
    assert parse_format("pretty") == "text"
    assert parse_format(None) == "text"
    with pytest.raises(InvalidConfigValueError, match="unknown log format"):
        parse_format("yaml")


@pytest.mark.parametrize(
    ("mode", "no_color", "force_color", "tty", "expected"),
    [
        ("always", "", "", False, True),
        ("never", "", "1", True, False),
        ("auto", "1", "1", True, False),
        ("auto", "", "1", False, True),
        ("auto", "", "", True, True),
        ("auto", "", "", False, False),
    ],
)
def test_should_colorize_matrix(
    monkeypatch, mode: str, no_color: str, force_color: str, tty: bool, expected: bool
) -> None:
    """颜色开关优先级：显式模式 > NO_COLOR > FORCE_COLOR > TTY。"""
    monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setenv("FORCE_COLOR", force_color)
    stream = io.StringIO()
    monkeypatch.setattr(stream, "isatty", lambda: tty, raising=False)
    assert should_colorize(stream, mode) is expected


def test_quiet_third_party_suppresses_httpx(monkeypatch) -> None:
    """INFO 级别下压制吵闹三方库，DEBUG 下放开。"""
    monkeypatch.delenv("DMSA_LOG_LEVEL", raising=False)
    quiet_third_party(logging.INFO)
    assert logging.getLogger("httpx").level == logging.WARNING
    quiet_third_party(logging.DEBUG)
    assert logging.getLogger("httpx").level != logging.WARNING


def test_configure_root_writes_rotating_file(tmp_path, monkeypatch, restored_root) -> None:
    """DMSA_LOG_FILE 命中时同一条日志同时进文件。"""
    target = tmp_path / "logs" / "dmsa.log"
    monkeypatch.setenv("DMSA_LOG_FILE", str(target))
    configure_root(stream=io.StringIO(), level="info", color="never")
    get_logger("dmsa.test").info("written-to-file")
    assert "written-to-file" in target.read_text(encoding="utf-8")


def test_unwritable_log_file_degrades_to_console(tmp_path, monkeypatch, restored_root) -> None:
    """文件不可写时不崩溃，只在控制台告警并继续写控制台。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not-a-directory", encoding="utf-8")
    monkeypatch.setenv("DMSA_LOG_FILE", str(blocker / "nested" / "dmsa.log"))
    stream = io.StringIO()
    configure_root(stream=stream, level="info", color="never")
    get_logger("dmsa.test").info("still-logging")
    assert "still-logging" in stream.getvalue()


def test_exception_includes_traceback(restored_root) -> None:
    """exception() 输出附带 traceback。"""
    stream = io.StringIO()
    configure_root(stream=stream, level="info", color="never")
    try:
        raise ValueError("bad-value")
    except ValueError:
        get_logger("dmsa.test").exception("handled")
    text = stream.getvalue()
    assert "Traceback" in text
    assert "bad-value" in text


def test_formatter_objects_render_standalone() -> None:
    """formatter 可脱离根配置单独使用（测试与嵌入场景）。"""
    record = logging.LogRecord("dmsa.x", logging.INFO, "x.py", 7, "plain %s", ("msg",), None)
    record.funcName = "caller"
    assert TextFormatter(use_colors=False).format(record).endswith("plain msg")
    assert JsonFormatter().format(record).find('"message": "plain msg"') > 0
