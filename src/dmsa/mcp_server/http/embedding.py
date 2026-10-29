"""HTTP 服务使用的 text-embedding-v4 适配器。"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from dmsa.errors import ConfigError

_QWEN_DIMENSIONS = frozenset({256, 512, 768, 1024, 1536, 2048, 2560})
# 两个 Qwen 模型名指向不同权重(实测同一条文本的向量余弦仅 0.065), 因此各自
# 保留独立指纹, 已写入的向量只在其指纹下可被稠密通道召回。
_QWEN_MODELS = frozenset({"qwen3.7-text-embedding", "qwen3.7-text-embedding-flash"})
_DASHSCOPE_BATCH_SIZE = 20
# 供应商限流(429)与临时故障的重试口径: 平台并发下发多条 Add, 一次限流就会让
# 整条写入失败, 因此适配器自己退避重试, 不把 429 抛给调用方。
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_EMBED_RETRY_ATTEMPTS = 6
_EMBED_RETRY_BASE_DELAY = 0.5
_EMBED_RETRY_MAX_DELAY = 8.0
# 出站请求并发闸门: 实测供应商在 16 路并发、每批 20 条 1800 字符(约 4900 token)
# 的平台真实形态下 96/96 全部成功, 单请求也没有 token 上限(30000 token 仍可过),
# 因此闸门只用来挡住异常突发, 收得太紧反而会把一批写入拖成分钟级。
_EMBED_MAX_CONCURRENCY = 12


def _row_index(row: dict[str, Any]) -> int | None:
    """读取单条向量结果的序号字段, 兼容两个模型版本的字段名."""
    for key in ("text_index", "index"):
        value = row.get(key)
        if type(value) is int:
            return value
    return None


class QwenTextEmbedding:
    """调用 DashScope 原生端点的 Qwen3.7 文本向量适配器。"""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        dimension: int = 1024,
        *,
        model: str = "qwen3.7-text-embedding-flash",
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
        if model not in _QWEN_MODELS:
            raise ConfigError("unsupported Qwen embedding model")
        self.endpoint = endpoint
        self.api_key = api_key
        self.dimension = dimension
        self.model = model
        self.name = model
        self.fingerprint = f"{model}:{dimension}"
        self._client = client or httpx.Client(timeout=60.0, follow_redirects=False)
        self._owns_client = client is None
        self._gate = threading.BoundedSemaphore(_EMBED_MAX_CONCURRENCY)

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
            output.extend(self._post_batch(batch, text_type))
        return output

    def _post_batch(self, batch: list[str], text_type: str) -> list[list[float]]:
        """发送一批并校验结构；限流与临时故障按退避重试。

        一次 Add 的全部切片共用同一个请求，供应商一旦限流整条写入就失败，
        因此这里必须自己退避重试，不能把 429 直接抛给调用方。
        """
        delay = _EMBED_RETRY_BASE_DELAY
        last: Exception | None = None
        for attempt in range(_EMBED_RETRY_ATTEMPTS):
            try:
                return self._request_batch(batch, text_type)
            except httpx.HTTPStatusError as exc:
                last = exc
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    raise ConfigError(
                        "Qwen embedding request failed or returned invalid data"
                    ) from exc
            except httpx.TransportError as exc:
                last = exc
            if attempt == _EMBED_RETRY_ATTEMPTS - 1:
                break
            time.sleep(delay)
            delay = min(delay * 2, _EMBED_RETRY_MAX_DELAY)
        raise ConfigError("Qwen embedding request failed or returned invalid data") from last

    def _request_batch(self, batch: list[str], text_type: str) -> list[list[float]]:
        """发出一次批量请求并校验返回结构、索引、维度和数值。"""
        with self._gate:
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
            # 两个 Qwen 模型版本返回的序号字段名不同: 旧版是 text_index,
            # flash 版是 index。两种都要认, 否则会因取不到字段而整批失败。
            order: list[int] = []
            for row in rows:
                index = _row_index(row)
                if index is None:
                    raise ValueError("invalid embedding response indices")
                order.append(index)
            if len(rows) != len(batch) or sorted(order) != list(range(len(batch))):
                raise ValueError("invalid embedding response indices")
            rows.sort(key=lambda row: _row_index(row) or 0)
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
            if any(
                len(vector) != self.dimension
                or not all(math.isfinite(value) for value in vector)
                or not any(vector)
                for vector in vectors
            ):
                raise ValueError("invalid embedding vector")
            return vectors

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
