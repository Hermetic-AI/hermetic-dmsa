"""零依赖哈希嵌入 + Embedder Protocol。

默认实现 :class:`HashEmbedder`：用 SHA-256 哈希文本生成固定维度向量。
确定性 + 零依赖。生产环境可换 OpenAI / sentence-transformers（通过 Protocol）。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from typing import Protocol


class EmbedderProtocol(Protocol):
    """嵌入器接口。"""

    name: str
    dim: int

    def embed(self, text: str) -> list[float]:
        """把单段文本转为向量。"""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入。"""
        ...


class HashEmbedder:
    """零依赖的确定性哈希嵌入器。

    算法：对文本做 SHA-256，把 32 字节扩展到 ``dim`` 维 float32。

    优点：零依赖 + 确定性（同一文本永远同向量）。
    缺点：无语义意义（两个相似文本不会自然有相似向量），适合测试 / 演示。
    生产建议换 :class:`OpenAIEmbedder` 或 sentence-transformers。
    """

    name = "hash"
    dim: int

    def __init__(self, dim: int = 256) -> None:
        """初始化哈希嵌入器, dim 决定输出向量长度。"""
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        """嵌入单段文本。"""
        if not isinstance(text, str):
            raise TypeError(f"text must be str, got {type(text).__name__}")
        # 重复哈希凑够 dim 个 float
        n_hashes = math.ceil(self.dim / 32) + 1
        data = text.encode("utf-8")
        chunks: list[bytes] = []
        for i in range(n_hashes):
            h = hashlib.sha256(data + str(i).encode("ascii")).digest()
            chunks.append(h)
        combined = b"".join(chunks)[: self.dim]
        return [(b / 255.0) - 0.5 for b in combined]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（直接循环 embed）。"""
        return [self.embed(t) for t in texts]


def cosine_similarity(
    a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]
) -> float:
    """计算两个向量的余弦相似度。零依赖。"""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def cosine_distance(
    a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]
) -> float:
    """余弦距离 = 1 - 余弦相似度，范围 ``[0, 1]``。"""
    return 1.0 - cosine_similarity(a, b)


def normalize_vector(v: list[float] | tuple[float, ...]) -> list[float]:
    """L2 归一化。"""
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        return list(v)
    return [x / norm for x in v]


def vector_mean(vectors: Iterable[list[float] | tuple[float, ...]]) -> list[float]:
    """计算向量均值（按列求平均）。"""
    vecs = list(vectors)
    if not vecs:
        raise ValueError("cannot compute mean of empty sequence")
    dim = len(vecs[0])
    sums = [0.0] * dim
    for v in vecs:
        for i, x in enumerate(v):
            sums[i] += x
    return [s / len(vecs) for s in sums]


__all__ = [
    "EmbedderProtocol",
    "HashEmbedder",
    "cosine_distance",
    "cosine_similarity",
    "normalize_vector",
    "vector_mean",
]
