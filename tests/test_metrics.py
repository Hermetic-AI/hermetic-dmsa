"""指标埋点测试：延迟/计数、检索交付量与 HTTP 指标出口。"""

from __future__ import annotations

import math
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dmsa.mcp_server.http.app import create_app
from dmsa.memory import CodingMemory, SearchPolicy
from dmsa.memory.coding.models import CodingMemoryError, SourceMessage
from dmsa.memory.coding.recall import embed_batch, query_vector
from dmsa.observability import (
    COUNT_BUCKETS,
    MetricsRegistry,
    get_registry,
    observe_count,
    start_span,
    timed,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    """每个用例从空注册表开始，指标值才是本用例产生的。"""
    registry = get_registry()
    registry.reset()
    yield registry
    registry.reset()


class _StubEmbedder:
    """最小 embedder 替身：只保证维度正确，不产生任何外部调用。"""

    dimension = 2
    fingerprint = "stub"

    def embed_batch(self, texts: list[str]) -> list[tuple[float, ...]]:
        """返回固定向量，长度随输入。"""
        return [(0.5, 0.5) for _ in texts]

    def embed_query(self, query: str) -> tuple[float, ...]:
        """查询向量与文本向量同口径。"""
        return (0.5, 0.5)


class _RaisingEmbedder(_StubEmbedder):
    """上游故障替身：用于验证失败计数。"""

    def embed_batch(self, texts: list[str]) -> list[tuple[float, ...]]:
        """总是抛出，模拟 provider 不可用。"""
        raise TimeoutError("provider down")


def test_timed_records_latency_and_calls() -> None:
    """timed 写入延迟直方图与调用次数。"""
    registry = MetricsRegistry()
    with timed("dmsa.demo.latency", registry=registry):
        time.sleep(0.001)
    histogram = registry.histogram("dmsa.demo.latency")
    assert histogram.to_dict()["count"] == 1
    assert registry.counter("dmsa.demo.calls").value == 1.0
    assert registry.counter("dmsa.demo.failures").value == 0.0


def test_timed_counts_failures_and_reraises() -> None:
    """异常路径同时计入 calls 与 failures，并原样抛出。"""
    registry = MetricsRegistry()
    with pytest.raises(RuntimeError, match="boom"), timed("dmsa.demo.latency", registry=registry):
        raise RuntimeError("boom")
    assert registry.counter("dmsa.demo.calls").value == 1.0
    assert registry.counter("dmsa.demo.failures").value == 1.0
    assert registry.histogram("dmsa.demo.latency").to_dict()["count"] == 0


def test_observe_count_uses_scale_buckets() -> None:
    """observe_count 走规模桶，大整数不会被压进 +Inf。"""
    registry = MetricsRegistry()
    observe_count("dmsa.demo.items", 1200, registry=registry)
    histogram = registry.histogram("dmsa.demo.items")
    assert histogram.buckets == COUNT_BUCKETS
    assert histogram.to_dict()["buckets"]["inf"] == 1


def test_embedding_latency_metrics_track_provider_calls() -> None:
    """嵌入调用产生延迟与条数指标；上游故障计入 failures。"""
    embed_batch(_StubEmbedder(), ["a", "b"])
    names = {item["name"] for item in get_registry().export()["counters"]}
    assert "dmsa.embedding.batch.calls" in names
    histograms = {item["name"]: item for item in get_registry().export()["histograms"]}
    assert histograms["dmsa.embedding.batch.latency"]["count"] == 1
    assert histograms["dmsa.embedding.batch.items"]["buckets"]["2.0"] == 1
    with pytest.raises(CodingMemoryError, match="operation failed"):
        embed_batch(_RaisingEmbedder(), ["a"])
    assert get_registry().counter("dmsa.embedding.batch.failures").value == 1.0


def test_query_vector_metrics_do_not_count_validation_failures() -> None:
    """查询向量化计入外部调用；校验失败同样落在同一指标上。"""
    vector = query_vector(_StubEmbedder(), "bug")
    assert vector == (0.5, 0.5)
    assert get_registry().histogram("dmsa.embedding.query.latency").to_dict()["count"] == 1
    assert get_registry().counter("dmsa.embedding.query.calls").value == 1.0
    assert math.isfinite(
        float(get_registry().histogram("dmsa.embedding.query.latency").to_dict()["mean"] or 0.0)
    )


def test_search_metrics_report_latency_and_delivered_items(tmp_path) -> None:
    """检索主流程报告端到端延迟与实际交付条数。"""
    memory = CodingMemory(
        tmp_path / "search.sqlite",
        policy=SearchPolicy(dense_weight=0.0),
    )
    try:
        memory.add("r1", "u", "s", [SourceMessage("user", "logging bug fixed")])
        hits = memory.search("bug", "u", top_k=5)
        exported = get_registry().export()
        histograms = {item["name"]: item for item in exported["histograms"]}
        assert histograms["dmsa.search.latency"]["count"] == 1
        returned = histograms["dmsa.search.returned"]
        assert returned["buckets"][str(float(len(hits)))] == 1
        # 写入侧的分块数量决定 embedding 批量大小
        assert histograms["dmsa.write.chunks"]["count"] == 1
    finally:
        memory.close()


def test_span_latency_is_exported_as_metric() -> None:
    """span 埋点退出时自动写入同名延迟指标（span 名有界，基数安全）。"""
    with start_span("dmsa.demo_op") as span:
        span.set_attribute("kind", "test")
    names = {item["name"] for item in get_registry().export()["histograms"]}
    assert "dmsa.span.demo_op.latency" in names


def test_span_failure_counter_increments() -> None:
    """span 内抛异常记入 failures，便于区分快失败与慢失败。"""
    with pytest.raises(RuntimeError, match="x"), start_span("dmsa.demo_bad"):
        raise RuntimeError("x")
    assert get_registry().counter("dmsa.span.demo_bad.failures").value == 1.0


def test_http_metrics_endpoint_is_authenticated_and_reports_snapshot(tmp_path) -> None:
    """/metrics 需要鉴权，且能看到请求延迟与检索交付量。"""
    memory = CodingMemory(tmp_path / "http.sqlite", policy=SearchPolicy(dense_weight=0.0))
    client = TestClient(create_app(memory, "secret"))
    try:
        assert client.get("/metrics").status_code == 401
        assert client.get("/status").status_code == 401
        headers = {"Authorization": "Bearer secret"}
        payload = {
            "request_id": "r1",
            "user_id": "u",
            "session_id": "s",
            "messages": [{"role": "user", "content": "metrics snapshot"}],
        }
        assert client.post("/add", json=payload, headers=headers).status_code == 200
        assert client.get("/health").status_code == 200
        assert client.post("/search", json={"query": "metrics", "user_id": "u"}, headers=headers)
        snapshot = client.get("/metrics", headers=headers).json()
        names = {item["name"] for item in snapshot["histograms"]}
        assert {"dmsa.http.latency", "dmsa.http.search.latency", "dmsa.search.latency"} <= names
        counters = {item["name"]: item["value"] for item in snapshot["counters"]}
        assert counters["dmsa.http.requests.2xx"] >= 2
        assert counters["dmsa.http.health_checks"] == 1
        assert snapshot["uptime_seconds"] >= 0
        status = client.get("/status", headers=headers, params={"scope": "u"}).json()
        assert status["embedding"] == {"enabled": False, "fingerprint": None}
        assert status["vector_index"] == "sqlite"
        assert status["backend"] == "CodingMemory"
        assert status["uptime_seconds"] >= 0
        # 凭据与请求原文都不允许出现在指标或状态里
        assert "secret" not in str(snapshot)
    finally:
        memory.close()
