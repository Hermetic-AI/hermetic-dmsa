"""可观测性测试。"""

from __future__ import annotations

import io
import json
import logging

import pytest

from dmsa.observability import (
    Counter,
    Histogram,
    MetricsRegistry,
    get_logger,
    get_registry,
    start_span,
)


def test_counter_increments() -> None:
    """Counter 单调递增。"""
    c = Counter("test_counter", help="test")
    c.inc()
    c.inc(2.5)
    assert c.value == 3.5


def test_counter_cannot_decrement() -> None:
    """Counter 不允许负数。"""
    c = Counter("test")
    with pytest.raises(ValueError):
        c.inc(-1.0)


def test_histogram_observe() -> None:
    """Histogram 正确分桶。"""
    h = Histogram("test_h", buckets=(1.0, 5.0, 10.0))
    h.observe(0.5)
    h.observe(3.0)
    h.observe(7.0)
    d = h.to_dict()
    assert d["count"] == 3
    assert d["buckets"]["1.0"] == 1
    assert d["buckets"]["5.0"] == 2
    assert d["buckets"]["10.0"] == 3


def test_metrics_registry() -> None:
    """MetricsRegistry 复用同名 metric。"""
    registry = MetricsRegistry()
    c1 = registry.counter("hits")
    c2 = registry.counter("hits")
    assert c1 is c2
    c1.inc()
    assert c2.value == 1.0


def test_global_registry() -> None:
    """get_registry() 返回全局 singleton。"""
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2


def test_json_logger_outputs_json() -> None:
    """JsonLogger 输出 JSON 格式日志。"""
    logger = get_logger("test_json_logger")

    # 捕获 stderr
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    from dmsa.observability.logging import JsonFormatter

    handler.setFormatter(JsonFormatter())
    std_logger = logging.getLogger("test_json_logger")
    std_logger.addHandler(handler)
    std_logger.setLevel(logging.INFO)

    logger.info("hello", node_id="abc", count=3)

    output = stream.getvalue()
    assert "hello" in output
    # 应该包含 JSON 解析后的字段
    parsed = json.loads(output.strip())
    assert parsed["message"] == "hello"
    assert parsed["node_id"] == "abc"
    assert parsed["count"] == 3


def test_trace_span_lifecycle() -> None:
    """TraceSpan 生命周期管理。"""
    with start_span("test_op", key="value") as span:
        assert span.end_time is None
        span.set_attribute("extra", 42)
        assert span.attributes["key"] == "value"
    # 退出 with 块后 span 标记为结束
    assert span.end_time is not None
    assert span.attributes["extra"] == 42


def test_trace_span_nesting() -> None:
    """嵌套 span 形成父子关系。"""
    with start_span("parent") as parent:
        with start_span("child") as child:
            assert child.parent_id == parent.span_id
