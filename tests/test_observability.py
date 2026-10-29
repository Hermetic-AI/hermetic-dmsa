"""可观测性测试。"""

from __future__ import annotations

import io
import json
import logging
import math

import pytest

from dmsa.errors import DMSAError, MetricValueError
from dmsa.observability import (
    COUNT_BUCKETS,
    DEFAULT_BUCKETS,
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
    with pytest.raises(ValueError, match="cannot be decremented"):
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
    with start_span("parent") as parent, start_span("child") as child:
        assert child.parent_id == parent.span_id


def test_counter_decrement_is_dmsa_error() -> None:
    """Counter 递减同时是 DMSAError 与 ValueError（保留既有兜底捕获）。"""
    with pytest.raises(ValueError, match="cannot be decremented") as excinfo:
        Counter("test").inc(-0.5)
    assert isinstance(excinfo.value, DMSAError)


def test_empty_histogram_reports_none_not_zero() -> None:
    """未观测的统计量导出为 null，不用 0 冒充测量结果。"""
    d = Histogram("empty_h").to_dict()
    assert d["count"] == 0
    assert d["mean"] is None
    assert d["min"] is None
    assert d["max"] is None
    assert d["p50"] is None
    assert d["p95"] is None


def test_histogram_tracks_min_max() -> None:
    """Histogram 记录极值。"""
    h = Histogram("extreme", buckets=(1.0, 10.0, math.inf))
    h.observe(0.4)
    h.observe(7.0)
    d = h.to_dict()
    assert d["min"] == 0.4
    assert d["max"] == 7.0
    assert d["sum"] == 7.4


def test_histogram_quantile_interpolates() -> None:
    """分位数在桶内线性插值，落在量程内。"""
    h = Histogram("lat", buckets=(0.01, 0.1, 1.0, math.inf))
    for value in (0.005, 0.02, 0.05, 0.2, 0.5):
        h.observe(value)
    p50 = h.quantile(0.5)
    assert p50 is not None
    assert 0.02 <= p50 <= 0.5


def test_histogram_quantile_beyond_range_is_none() -> None:
    """观测落在 +Inf 桶时不给假的分位数。"""
    h = Histogram("over", buckets=(1.0, math.inf))
    h.observe(500.0)
    assert h.quantile(0.99) is None


def test_histogram_quantile_rejects_bad_q() -> None:
    """分位点越界 raise MetricValueError。"""
    h = Histogram("bad_q")
    h.observe(1.0)
    with pytest.raises(MetricValueError, match="out of range"):
        h.quantile(1.5)


def test_count_buckets_separate_scale_from_latency() -> None:
    """规模类指标用 COUNT_BUCKETS，整数观测不会被压进 +Inf 桶。"""
    registry = MetricsRegistry()
    h = registry.count_histogram("dmsa.aggregate.samples")
    for size in (3, 8, 20, 40, 120):
        h.observe(size)
    assert h.buckets == COUNT_BUCKETS
    d = h.to_dict()
    assert d["buckets"]["5.0"] == 1
    assert d["buckets"]["20.0"] == 3
    assert d["buckets"]["50.0"] == 4
    assert d["buckets"]["100.0"] == 4
    assert d["buckets"]["inf"] == 5
    p50 = d["p50"]
    assert p50 is not None
    assert p50 <= 50.0


def test_registry_histogram_keeps_default_latency_buckets() -> None:
    """registry.histogram 默认仍是延迟桶（不破坏既有调用）。"""
    registry = MetricsRegistry()
    assert registry.histogram("latency").buckets is DEFAULT_BUCKETS


def test_export_is_sorted_and_complete() -> None:
    """export 按名称稳定排序，便于 diff 与快照比对。"""
    registry = MetricsRegistry()
    registry.counter("z_counter").inc()
    registry.counter("a_counter").inc(2)
    registry.count_histogram("b_scale").observe(7)
    payload = registry.export()
    assert [c["name"] for c in payload["counters"]] == ["a_counter", "z_counter"]
    assert [h["name"] for h in payload["histograms"]] == ["b_scale"]
