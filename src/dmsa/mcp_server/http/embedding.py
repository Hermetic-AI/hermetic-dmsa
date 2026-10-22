# ruff: noqa: D415
"""HTTP 服务使用的 text-embedding-v4 适配器。"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dmsa.errors import ConfigError


class TextEmbeddingV4:
    """将 OpenAI 协议 client 转为 CodingMemory Embedder 接口。"""
    name = "text-embedding-v4"
    fingerprint = name
    def __init__(self, client: Any, dimension: int = 1536) -> None:
        """绑定预配置 client 与向量维度。"""
        if type(dimension) is not int or dimension <= 0:
            raise ConfigError("embedding dimension must be positive")
        self.client = client
        self.dimension = dimension

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """请求真实模型向量并检查返回维度。"""
        try:
            response = self.client.embeddings.create(
                model=self.name, input=list(texts), dimensions=self.dimension
            )
            items = list(response.data)
            indices = [item.index for item in items]
            if (
                len(items) != len(texts)
                or any(type(index) is not int for index in indices)
                or sorted(indices) != list(range(len(texts)))
            ):
                raise ValueError("embedding response indices are invalid")
            items.sort(key=lambda item: item.index)
            vectors = [list(item.embedding) for item in items]
        except Exception as exc:
            raise ConfigError("text embedding request failed") from exc
        if len(vectors) != len(texts) or any(len(vector) != self.dimension for vector in vectors):
            raise ConfigError("text embedding dimension mismatch")
        return vectors




