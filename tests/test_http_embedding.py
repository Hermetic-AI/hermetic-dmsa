"""HTTP embedding adapter ordering and validation tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from dmsa.errors import ConfigError
from dmsa.mcp_server.http.embedding import QwenTextEmbedding, TextEmbeddingV4


def test_text_embedding_v4_sorts_indices_and_forwards_dimension() -> None:
    """按响应 index 恢复输入顺序并转发向量维度。"""

    class Embeddings:
        def create(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=[2.0, 2.0]),
                    SimpleNamespace(index=0, embedding=[1.0, 1.0]),
                ]
            )

    client = SimpleNamespace(embeddings=Embeddings())
    embedder = TextEmbeddingV4(client, dimension=2)
    assert embedder.embed_batch(["first", "second"]) == [[1.0, 1.0], [2.0, 2.0]]
    assert client.embeddings.kwargs["dimensions"] == 2


def test_qwen_adapter_uses_native_schema_and_query_document_types() -> None:
    """Qwen 原生 DashScope 请求区分入库与查询文本并按索引复原。"""
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        request_body = json.loads(request.content)
        texts = request_body["input"]["texts"]
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": index, "embedding": [float(index + 1)] * 256}
                        for index in reversed(range(len(texts)))
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    embedder = QwenTextEmbedding(
        "https://embedding.example/api",
        "test-key",
        256,
        model="qwen3.7-text-embedding-flash",
        client=client,
    )
    assert embedder.embed_batch(["first", "second"]) == [[1.0] * 256, [2.0] * 256]
    assert embedder.embed_query("query") == [1.0] * 256
    assert requests[0].headers["authorization"] == "Bearer test-key"
    first_body = __import__("json").loads(requests[0].content)
    query_body = __import__("json").loads(requests[1].content)
    assert first_body["parameters"]["text_type"] == "document"
    assert query_body["parameters"]["text_type"] == "query"
    assert first_body["parameters"]["dimension"] == 256
    client.close()


def test_qwen_adapter_accepts_flash_index_field() -> None:
    """flash 版模型返回 index 字段而不是 text_index, 两种都要能复原顺序."""

    def respond(request: httpx.Request) -> httpx.Response:
        request_body = json.loads(request.content)
        texts = request_body["input"]["texts"]
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"index": index, "type": "embedding", "embedding": [float(index + 1)] * 256}
                        for index in reversed(range(len(texts)))
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    embedder = QwenTextEmbedding(
        "https://embedding.example/api",
        "test-key",
        256,
        model="qwen3.7-text-embedding-flash",
        client=client,
    )
    assert embedder.embed_batch(["first", "second"]) == [[1.0] * 256, [2.0] * 256]
    client.close()


def test_qwen_adapter_chunks_batches_to_provider_limit() -> None:
    """单次请求最多发送 20 条，多个批次仍返回完整顺序。"""
    batch_sizes: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]["texts"]
        batch_sizes.append(len(texts))
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"text_index": i, "embedding": [1.0] * 256} for i in range(len(texts))
                    ]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    embedder = QwenTextEmbedding("https://embedding.example/api", "key", 256, client=client)
    assert len(embedder.embed_batch([str(i) for i in range(21)])) == 21
    assert batch_sizes == [20, 1]
    client.close()


@pytest.mark.parametrize(
    ("endpoint", "dimension"),
    [
        ("http://embedding.example/api", 1024),
        ("https://u:p@example/api", 1024),
        ("https://embedding.example/api?token=secret", 1024),
        ("https://embedding.example/api", 999),
    ],
)
def test_qwen_adapter_rejects_unsafe_or_invalid_configuration(
    endpoint: str, dimension: int
) -> None:
    """拒绝非 TLS、带凭据 URL 及模型不支持的维度。"""
    with pytest.raises(ConfigError):
        QwenTextEmbedding(endpoint, "key", dimension)


@pytest.mark.parametrize("indices", [[0, 0], [0, 2], [1], [False, 1], [0.0, 1]])
def test_text_embedding_v4_rejects_invalid_response_indices(indices: list[object]) -> None:
    """重复、缺失或越界 index 不能错配向量与文本。"""
    client = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(index=index, embedding=[1.0]) for index in indices]
            )
        )
    )
    with pytest.raises(ConfigError, match="request failed"):
        TextEmbeddingV4(client, dimension=1).embed_batch(["a", "b"])
