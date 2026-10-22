"""HTTP embedding adapter ordering and validation tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dmsa.errors import ConfigError
from dmsa.mcp_server.http.embedding import TextEmbeddingV4


def test_text_embedding_v4_sorts_indices_and_forwards_dimension() -> None:
    """按响应 index 恢复输入顺序并转发向量维度。"""
    class Embeddings:
        def create(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return SimpleNamespace(data=[
                SimpleNamespace(index=1, embedding=[2.0, 2.0]),
                SimpleNamespace(index=0, embedding=[1.0, 1.0]),
            ])

    client = SimpleNamespace(embeddings=Embeddings())
    embedder = TextEmbeddingV4(client, dimension=2)
    assert embedder.embed_batch(["first", "second"]) == [[1.0, 1.0], [2.0, 2.0]]
    assert client.embeddings.kwargs["dimensions"] == 2


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
