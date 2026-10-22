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
    dim: int

    def __init__(self, client: Any, dimension: int = 1536) -> None:
        """绑定预配置 client 与向量维度。"""
        if dimension <= 0:
            raise ConfigError("embedding dimension must be positive")
        self.client = client
        self.dimension = dimension
        self.dim = dimension

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """请求真实模型向量并检查返回维度。"""
        try:
            response = self.client.embeddings.create(model=self.name, input=list(texts))
            vectors = [list(item.embedding) for item in response.data]
        except Exception as exc:
            raise ConfigError("text embedding request failed") from exc
        if any(len(vector) != self.dimension for vector in vectors):
            raise ConfigError("text embedding dimension mismatch")
        return vectors




