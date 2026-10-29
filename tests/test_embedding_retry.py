"""嵌入适配器的限流退避重试与并发闸门测试."""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from dmsa.errors import ConfigError
from dmsa.mcp_server.http.embedding import QwenTextEmbedding


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch) -> None:
    """把退避时长压到毫秒级, 免得用例真的去睡."""
    monkeypatch.setattr("dmsa.mcp_server.http.embedding._EMBED_RETRY_BASE_DELAY", 0.001)
    monkeypatch.setattr("dmsa.mcp_server.http.embedding._EMBED_RETRY_MAX_DELAY", 0.002)


def _embedder(respond) -> tuple[QwenTextEmbedding, list[int]]:
    """构造一个按固定脚本应答的适配器, 记录每次请求的尝试次数."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return respond(request, attempts["count"])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedder = QwenTextEmbedding(
        "https://embedding.example/api",
        "test-key",
        256,
        model="qwen3.7-text-embedding-flash",
        client=client,
    )
    return embedder, attempts


def _ok(request: httpx.Request) -> httpx.Response:
    """返回一条合法向量."""
    texts = json.loads(request.content)["input"]["texts"]
    return httpx.Response(
        200,
        json={
            "output": {
                "embeddings": [
                    {"index": index, "type": "embedding", "embedding": [1.0] * 256}
                    for index in range(len(texts))
                ]
            }
        },
    )


def test_rate_limit_is_retried_until_success() -> None:
    """供应商限流时按退避重试, 而不是直接失败."""

    def respond(request: httpx.Request, count: int) -> httpx.Response:
        if count < 3:
            return httpx.Response(429, json={"message": "too many requests"})
        return _ok(request)

    embedder, attempts = _embedder(respond)
    assert embedder.embed_batch(["文本"]) == [[1.0] * 256]
    assert attempts["count"] == 3
    embedder.close()


def test_non_retryable_status_fails_immediately() -> None:
    """鉴权类错误不该重试, 直接失败."""

    def respond(request: httpx.Request, count: int) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad key"})

    embedder, attempts = _embedder(respond)
    with pytest.raises(ConfigError, match="invalid data"):
        embedder.embed_batch(["文本"])
    assert attempts["count"] == 1
    embedder.close()


def test_retry_gives_up_after_attempt_budget() -> None:
    """持续限流时在重试预算用尽后失败."""

    def respond(request: httpx.Request, count: int) -> httpx.Response:
        return httpx.Response(429, json={"message": "too many requests"})

    embedder, attempts = _embedder(respond)
    with pytest.raises(ConfigError):
        embedder.embed_batch(["文本"])
    assert attempts["count"] == 6
    embedder.close()


def test_concurrent_calls_are_gated() -> None:
    """并发调用不会同时压向供应商, 出站请求数被闸门限制."""
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def respond(request: httpx.Request, count: int) -> httpx.Response:
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        threading.Event().wait(0.05)
        with lock:
            active["now"] -= 1
        return _ok(request)

    embedder, _ = _embedder(respond)
    threads = [
        threading.Thread(target=embedder.embed_batch, args=([f"文本{i}"],)) for i in range(12)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert active["peak"] <= 12, "出站并发不得超过闸门上限"
    embedder.close()
