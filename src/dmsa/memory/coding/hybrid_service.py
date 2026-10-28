"""稠密混合检索服务: 通道融合、有界重排与证据装配。

词面通道沿用既有检索策略的权重, 稠密通道按余弦相似度参与融合, 融合之后
再做一次有界重排: 跨通道一致命中的条目小幅上调, 与更高排名条目近乎重复
的小幅下调, 调整幅度有上限, 只改变相近候选之间的先后次序.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from dmsa.memory.coding.hybrid import HybridItem, HybridPolicy, HybridResult, fuse, rerank
from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    SearchHit,
    SearchPolicy,
    require_identifier,
    require_optional_identifier,
    require_text,
)
from dmsa.memory.coding.recall import DENSE_CHANNEL, ChannelRecall, collect_channels, cosine
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.storage_queries import vectors_by_ids

# 单次混合检索允许的最大返回条数.
MAX_TOP_K = 50


class HybridService:
    """在同一作用域上执行稠密混合检索."""

    def __init__(
        self, store: CodingStore, retriever: CodingRetriever, policy: SearchPolicy
    ) -> None:
        """绑定存储、检索器与默认检索策略."""
        self.store = store
        self.retriever = retriever
        self.policy = policy

    @contextmanager
    def _operation(self) -> Iterator[None]:
        """同一连接的读取与事务互斥, 防止观察到未提交的部分请求."""
        with self.store.lock:
            try:
                yield
            except (sqlite3.Error, OverflowError) as exc:
                raise CodingMemoryError("coding memory database operation failed") from exc

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy | None = None,
        repo: str = "",
        hybrid: HybridPolicy | None = None,
    ) -> HybridResult:
        """执行混合检索并返回带融合明细的结果."""
        require_text(query, "query")
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
            raise CodingMemoryValidationError(f"top_k must be an integer between 1 and {MAX_TOP_K}")
        selected = policy or self.policy
        if not isinstance(selected, SearchPolicy):
            raise CodingMemoryValidationError("policy must be SearchPolicy")
        hybrid_policy = hybrid or HybridPolicy()
        if not isinstance(hybrid_policy, HybridPolicy):
            raise CodingMemoryValidationError("hybrid must be HybridPolicy")
        scope = scope_key(user_id, repo)
        with self._operation():
            channels, features = collect_channels(self.store, query, scope, selected)
        dense_scores, dense_available = self._dense(scope, query, selected, hybrid_policy)
        fused, items = fuse(channels, dense_scores, hybrid_policy)
        with self._operation():
            snapshot = self.store.load_snapshot(scope)
            base = dict(fused)
            self.retriever.score_candidates(query, scope, selected, base, features, snapshot)
            texts = self._texts(scope, base, hybrid_policy.rerank_depth)
            reranked, detail = rerank(
                base, items, texts, hybrid_policy, selected.redundancy_threshold
            )
            hits = self.retriever.pack_hits(scope, top_k, selected, reranked, snapshot)
        return HybridResult(
            query=query,
            hits=tuple(hits),
            items=self._items(hits, detail),
            channels=self._channels(channels, dense_available),
            dense_available=dense_available,
        )

    def _dense(
        self,
        scope: str,
        query: str,
        policy: SearchPolicy,
        hybrid: HybridPolicy,
    ) -> tuple[dict[str, float], bool]:
        """取稠密候选及余弦相似度; 没有嵌入器或没有候选时如实报告不可用."""
        if not hybrid.dense_weight:
            return {}, False
        vector = self.retriever.query_vector(query)
        if vector is None:
            return {}, False
        if self.retriever.vector_index is not None:
            ids = self.retriever.dense_index_ids(scope, vector, policy)
            with self._operation():
                stored = vectors_by_ids(self.store.db, scope, ids)
            scores = {
                chunk_id: cosine(vector, values)
                for chunk_id, values in stored.items()
                if len(values) == len(vector)
            }
        else:
            with self._operation():
                rows = self.retriever.dense_scan_scores(scope, vector, policy)
            scores = {str(row["id"]): similarity for row, similarity in rows}
        filtered = {
            chunk_id: value
            for chunk_id, value in scores.items()
            if value >= hybrid.dense_min_similarity
        }
        return filtered, bool(filtered)

    def _texts(self, scope: str, scores: Mapping[str, float], limit: int) -> dict[str, str]:
        """读取参与重排的候选正文, 供重复度比较使用."""
        top = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
        if not top:
            return {}
        rows = self.store.rows_by_ids(scope, tuple(top))
        return {str(row["id"]): str(row["text"]) for row in rows}

    @staticmethod
    def _items(hits: list[SearchHit], detail: Mapping[str, HybridItem]) -> tuple[HybridItem, ...]:
        """按返回顺序整理命中条目的融合明细."""
        return tuple(detail[hit.id] for hit in hits if hit.id in detail)

    @staticmethod
    def _channels(channels: Mapping[str, ChannelRecall], dense_available: bool) -> tuple[str, ...]:
        """列出实际参与的召回通道名."""
        names = [name for name in channels if channels[name].weight]
        if dense_available:
            names.append(DENSE_CHANNEL)
        return tuple(names)
