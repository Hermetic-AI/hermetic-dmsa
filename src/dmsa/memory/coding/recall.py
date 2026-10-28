"""候选召回通道: 精确、稀疏、子串、代码锚点与可选稠密."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    DenseScopeTooLargeError,
    Embedder,
    SearchPolicy,
)
from dmsa.memory.coding.packing import token_set as _tokens
from dmsa.memory.coding.ranking import QueryFeatures, query_features
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.storage_queries import dense_candidates as dense_candidates_query
from dmsa.memory.coding.temporal import AsOf
from dmsa.memory.coding.tokenize import query_terms
from dmsa.memory.coding.vector_index import VectorIndex

# 稠密通道名; 其余通道名在 collect_channels 里定义.
DENSE_CHANNEL = "dense"


@dataclass(frozen=True)
class ChannelRecall:
    """单个召回通道的结果: 通道名、权重与命中的切片顺序."""

    name: str
    weight: float
    ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的通道摘要."""
        return {"name": self.name, "weight": self.weight, "hits": len(self.ids)}


def add_channel(ranks: dict[str, float], weight: float, rows: list[sqlite3.Row]) -> None:
    """将单通道排名以 RRF 加入总分."""
    if weight == 0:
        return
    for rank, row in enumerate(rows, 1):
        key = str(row["id"])
        ranks[key] = ranks.get(key, 0.0) + weight / (60 + rank)


def merge_channels(channels: Mapping[str, ChannelRecall]) -> dict[str, float]:
    """把各通道命中按 RRF 合并成累计分数.

    同一通道的命中顺序决定名次; 多轮召回时把各轮结果顺序拼接后再计分,
    因此跨轮重复出现的切片会得到两次贡献, 形成强化信号.
    """
    ranks: dict[str, float] = defaultdict(float)
    for channel in channels.values():
        if channel.weight == 0:
            continue
        for rank, chunk in enumerate(channel.ids, 1):
            ranks[chunk] += channel.weight / (60 + rank)
    return dict(ranks)


def collect_channels(
    store: CodingStore,
    query: str,
    user_id: str,
    policy: SearchPolicy,
    as_of: AsOf | None = None,
) -> tuple[dict[str, ChannelRecall], QueryFeatures]:
    """按通道分别召回, 保留每个通道的命中顺序供召回解释使用."""
    terms = tuple(_tokens(query))
    expanded = query_terms(query)
    raw = (
        (
            "exact",
            policy.exact_weight,
            store.exact_candidates(terms, user_id, policy.candidate_limit, as_of),
        ),
        (
            "sparse",
            policy.sparse_weight,
            store.sparse_candidates(expanded, user_id, policy.candidate_limit, as_of),
        ),
        (
            "substring",
            policy.sparse_weight,
            store.substring_candidates(expanded, user_id, policy.candidate_limit, as_of),
        ),
        (
            "anchor",
            policy.anchor_weight,
            store.anchor_candidates(expanded, user_id, policy.candidate_limit, as_of),
        ),
    )
    channels = {
        name: ChannelRecall(name=name, weight=weight, ids=tuple(str(row["id"]) for row in rows))
        for name, weight, rows in raw
    }
    return channels, query_features(query)


def collect_candidates(
    store: CodingStore,
    query: str,
    user_id: str,
    policy: SearchPolicy,
    as_of: AsOf | None = None,
) -> tuple[dict[str, float], QueryFeatures]:
    """只做库内召回, 返回通道分数与查询特征; 不含任何外部调用."""
    channels, features = collect_channels(store, query, user_id, policy, as_of)
    return merge_channels(channels), features


def query_vector(embedder: Embedder | None, query: str) -> tuple[float, ...] | None:
    """计算查询向量; 属于外部调用, 必须在数据库锁之外执行."""
    if embedder is None:
        return None
    query_embedder = getattr(embedder, "embed_query", None)
    vector = (
        tuple(float(value) for value in query_embedder(query))
        if callable(query_embedder)
        else embed_batch(embedder, [query])[0]
    )
    if (
        len(vector) != embedder.dimension
        or not all(math.isfinite(value) for value in vector)
        or not any(vector)
    ):
        raise CodingMemoryValidationError("embedder returned invalid query vector")
    return vector


def embed_batch(embedder: Embedder, texts: list[str]) -> list[tuple[float, ...]]:
    """计算并校验批量向量."""
    try:
        values = embedder.embed_batch(texts)
        if not isinstance(values, (list, tuple)):
            values = list(values)
    except Exception as exc:
        raise CodingMemoryError("embedding provider operation failed") from exc
    if len(values) != len(texts):
        raise CodingMemoryValidationError("embedder returned wrong vector count")
    result = []
    for vector in values:
        try:
            item = tuple(float(value) for value in vector)
        except (TypeError, ValueError) as exc:
            raise CodingMemoryValidationError("embedder returned nonnumeric vector") from exc
        if (
            len(item) != embedder.dimension
            or not all(math.isfinite(value) for value in item)
            or not any(item)
        ):
            raise CodingMemoryValidationError("embedder returned invalid vector")
        result.append(item)
    return result


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """计算余弦相似度."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right)))


def dense_index_ids(
    vector_index: VectorIndex | None,
    fingerprint: str,
    user_id: str,
    vector: tuple[float, ...],
    policy: SearchPolicy,
    limit: int | None = None,
) -> tuple[str, ...]:
    """在远端向量索引里按 scope 过滤检索; 属于外部调用.

    远端索引没有时间维度, 时序检索只能多取候选再在本地按坐标收敛.
    """
    if vector_index is None:
        return ()
    try:
        return tuple(
            vector_index.search(user_id, fingerprint, vector, limit or policy.candidate_limit)
        )
    except Exception as exc:
        raise CodingMemoryError("vector index search failed") from exc


def dense_scan_scores(
    store: CodingStore,
    user_id: str,
    fingerprint: str,
    vector: tuple[float, ...],
    policy: SearchPolicy,
    as_of: AsOf | None = None,
) -> list[tuple[sqlite3.Row, float]]:
    """在库内做有界稠密扫描, 同时给出每条候选的余弦相似度."""
    count, dense_candidates = dense_candidates_query(
        store.db, user_id, fingerprint, policy.dense_scan_limit, as_of
    )
    if count > policy.dense_scan_limit:
        raise DenseScopeTooLargeError(
            f"dense scan has {count} chunks; limit is {policy.dense_scan_limit}"
        )
    if any(len(stored) != len(vector) for _, stored in dense_candidates):
        raise CodingMemoryValidationError("stored vector dimension differs from embedder")
    scored = [(row, cosine(vector, stored)) for row, stored in dense_candidates]
    scored.sort(key=lambda item: (-item[1], str(item[0]["id"])))
    return scored[: policy.candidate_limit]


def dense_scan_rows(
    store: CodingStore,
    user_id: str,
    fingerprint: str,
    vector: tuple[float, ...],
    policy: SearchPolicy,
    as_of: AsOf | None = None,
) -> list[sqlite3.Row]:
    """在库内做有界稠密扫描; 只做库内读取."""
    return [row for row, _ in dense_scan_scores(store, user_id, fingerprint, vector, policy, as_of)]
