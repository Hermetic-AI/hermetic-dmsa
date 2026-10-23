# ruff: noqa: D415, RUF002
"""HTTP 服务使用的 text-embedding-v4 适配器。"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from dmsa.errors import ConfigError

_QWEN_DIMENSIONS = frozenset({256, 512, 768, 1024, 1536, 2048, 2560})
_DASHSCOPE_BATCH_SIZE = 20


class QwenTextEmbedding:
    """调用 DashScope 原生端点的 Qwen3.7 文本向量适配器。"""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        dimension: int = 1024,
        *,
        model: str = "qwen3.7-text-embedding",
        client: httpx.Client | None = None,
    ) -> None:
        """校验端点、凭据和向量维度，并可注入测试客户端。"""
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigError("embedding endpoint must be a credential-free HTTPS URL")
        if not api_key.strip():
            raise ConfigError("embedding API key must be nonempty")
        if type(dimension) is not int or dimension not in _QWEN_DIMENSIONS:
            raise ConfigError("unsupported qwen3.7-text-embedding dimension")
        if model != "qwen3.7-text-embedding":
            raise ConfigError("unsupported Qwen embedding model")
        self.endpoint = endpoint
        self.api_key = api_key
        self.dimension = dimension
        self.model = model
        self.name = model
        self.fingerprint = f"{model}:{dimension}"
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=False)
        self._owns_client = client is None

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """以 document 类型嵌入记忆文本，按供应商上限拆批并保持输入顺序。"""
        return self._embed(texts, text_type="document")

    def embed_query(self, text: str) -> Sequence[float]:
        """以 query 类型嵌入检索问题，匹配 DashScope 非对称检索约定。"""
        return self._embed([text], text_type="query")[0]

    def _embed(self, texts: Sequence[str], *, text_type: str) -> list[list[float]]:
        """发送有界原生请求并校验返回结构、索引、维度和数值。"""
        output: list[list[float]] = []
        for offset in range(0, len(texts), _DASHSCOPE_BATCH_SIZE):
            batch = list(texts[offset : offset + _DASHSCOPE_BATCH_SIZE])
            if not batch:
                continue
            try:
                response = self._client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "input": {"texts": batch},
                        "parameters": {
                            "dimension": self.dimension,
                            "output_type": "dense",
                            "text_type": text_type,
                        },
                    },
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload["output"]["embeddings"]
                indices = [row["text_index"] for row in rows]
                if (
                    len(rows) != len(batch)
                    or any(type(index) is not int for index in indices)
                    or sorted(indices) != list(range(len(batch)))
                ):
                    raise ValueError("invalid embedding response indices")
                rows.sort(key=lambda row: row["text_index"])
                vectors = [[float(value) for value in row["embedding"]] for row in rows]
                if any(
                    len(vector) != self.dimension
                    or not all(math.isfinite(value) for value in vector)
                    or not any(vector)
                    for vector in vectors
                ):
                    raise ValueError("invalid embedding vector")
            except Exception as exc:
                raise ConfigError("Qwen embedding request failed or returned invalid data") from exc
            output.extend(vectors)
        return output

    def close(self) -> None:
        """关闭内部 HTTP 客户端；注入的客户端由调用者管理。"""
        if self._owns_client:
            self._client.close()


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




