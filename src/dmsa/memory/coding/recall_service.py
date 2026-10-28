"""可解释检索服务: 多跳迭代召回与召回说明生成.

单跳检索只按用户原话召回, 原话与真实代码标识符对不上时会漏掉证据.
本服务在首轮命中之后, 用命中证据的路径与符号做有界扩展, 再召回一轮,
让同一处代码的其它证据有机会进入结果.

扩展只影响「召回到哪些候选」, 不改变打分口径: 最终排序仍然按原查询的
特征与原策略计算, 因此多跳结果与单跳结果可比.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from dmsa.memory.coding.explanation import (
    RecallExplanation,
    RecallItem,
    RecallRound,
    build_reason,
    matched_terms,
)
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
from dmsa.memory.coding.multihop import SEED_HITS, expand, seed_terms
from dmsa.memory.coding.packing import token_set
from dmsa.memory.coding.recall import (
    DENSE_CHANNEL,
    ChannelRecall,
    collect_channels,
    merge_channels,
)
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.tokenize import query_terms

# 允许的最大跳数: 超过三跳后新增候选迅速收敛, 成本却线性上升.
MAX_HOPS = 3


class RecallService:
    """在单个作用域上执行可解释的多跳检索."""

    def __init__(
        self, store: CodingStore, retriever: CodingRetriever, policy: SearchPolicy
    ) -> None:
        """绑定存储、检索器与默认策略."""
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
        hops: int = 1,
    ) -> RecallExplanation:
        """执行多跳检索并返回带召回说明的结果."""
        require_text(query, "query")
        require_identifier(user_id, "user_id")
        require_optional_identifier(repo, "repo")
        if type(top_k) is not int or not 1 <= top_k <= 50:
            raise CodingMemoryValidationError("top_k must be an integer between 1 and 50")
        if type(hops) is not int or not 1 <= hops <= MAX_HOPS:
            raise CodingMemoryValidationError(f"hops must be an integer between 1 and {MAX_HOPS}")
        selected = policy or self.policy
        if not isinstance(selected, SearchPolicy):
            raise CodingMemoryValidationError("policy must be SearchPolicy")
        scope = scope_key(user_id, repo)
        with self._operation():
            channels, features = collect_channels(self.store, query, scope, selected)
        weights = {name: item.weight for name, item in channels.items()}
        accumulated = {name: list(item.ids) for name, item in channels.items()}
        rounds = [
            RecallRound(
                index=0,
                query=query,
                added_terms=(),
                candidates=len({cid for item in channels.values() for cid in item.ids}),
            )
        ]
        current = query
        for index in range(1, hops):
            current, added, found = self._expand_once(
                scope, current, accumulated, weights, selected, query
            )
            if not added:
                break
            rounds.append(RecallRound(index, current, added, found))
        vector = self.retriever.query_vector(query)
        dense_rows: list[sqlite3.Row] = []
        if vector is not None and selected.dense_weight:
            dense_rows = self._dense_rows(scope, vector, selected)
            if dense_rows:
                accumulated[DENSE_CHANNEL] = [str(row["id"]) for row in dense_rows]
                weights[DENSE_CHANNEL] = selected.dense_weight
        merged = self._merge(accumulated, weights)
        ranks = merge_channels(merged)
        with self._operation():
            hits = self.retriever.assemble(
                query, scope, top_k, selected, ranks, features, dense_rows
            )
        return RecallExplanation(
            query=query,
            hops=len(rounds),
            rounds=tuple(rounds),
            items=self._items(scope, hits, merged, query),
        )

    @staticmethod
    def _merge(
        accumulated: dict[str, list[str]], weights: dict[str, float]
    ) -> dict[str, ChannelRecall]:
        """把各轮累计的通道命中拼成带权重的通道视图."""
        return {
            name: ChannelRecall(name=name, weight=weights.get(name, 0.0), ids=tuple(ids))
            for name, ids in accumulated.items()
        }

    def _expand_once(
        self,
        scope: str,
        current: str,
        accumulated: dict[str, list[str]],
        weights: dict[str, float],
        policy: SearchPolicy,
        original: str,
    ) -> tuple[str, tuple[str, ...], int]:
        """用当前累计结果推导扩展词并再召回一轮."""
        ranks = merge_channels(self._merge(accumulated, weights))
        if not ranks:
            return current, (), 0
        best = sorted(ranks, key=lambda key: (-ranks[key], key))[:SEED_HITS]
        with self._operation():
            seeds = self.store.rows_by_ids(scope, tuple(best))
        expansion = expand(current, seed_terms(seeds), tuple(token_set(original)))
        if not expansion.changed:
            return current, (), 0
        with self._operation():
            more, _ = collect_channels(self.store, expansion.query, scope, policy)
        found: set[str] = set()
        for name, item in more.items():
            accumulated.setdefault(name, []).extend(item.ids)
            weights.setdefault(name, item.weight)
            found.update(item.ids)
        return expansion.query, expansion.terms, len(found)

    def _dense_rows(
        self, scope: str, vector: tuple[float, ...], policy: SearchPolicy
    ) -> list[sqlite3.Row]:
        """按查询向量取稠密候选; 远端检索在锁之外, 库内读取在锁之内."""
        if self.retriever.vector_index is not None:
            ids = self.retriever.dense_index_ids(scope, vector, policy)
            with self._operation():
                rows = self.store.rows_by_ids(scope, ids)
            by_id = {str(row["id"]): row for row in rows}
            return [by_id[point_id] for point_id in ids if point_id in by_id]
        with self._operation():
            return self.retriever.dense_scan_rows(scope, vector, policy)

    def _items(
        self,
        scope: str,
        hits: list[SearchHit],
        merged: dict[str, ChannelRecall],
        query: str,
    ) -> tuple[RecallItem, ...]:
        """把命中结果翻译成带召回来源的解释条目."""
        if not hits:
            return ()
        terms = query_terms(query)
        channels_by_id: dict[str, list[str]] = {}
        for name, item in merged.items():
            for cid in item.ids:
                names = channels_by_id.setdefault(cid, [])
                # 同一通道在多轮里重复命中只记一次, 跨轮重复本身体现在分数上.
                if name not in names:
                    names.append(name)
        with self._operation():
            rows = self.store.rows_by_ids(scope, tuple(hit.id for hit in hits))
        by_id = {str(row["id"]): row for row in rows}
        items: list[RecallItem] = []
        for hit in hits:
            row = by_id.get(hit.id)
            paths = tuple(str(row["paths"]).split()) if row is not None else ()
            symbols = tuple(str(row["symbols"]).split()) if row is not None else ()
            text = str(row["text"]) if row is not None else ""
            found = tuple(channels_by_id.get(hit.id, ()))
            terms_hit = matched_terms(terms, text, " ".join(paths), " ".join(symbols))
            items.append(
                RecallItem(
                    id=hit.id,
                    score=hit.score,
                    channels=found,
                    matched_terms=terms_hit,
                    paths=paths,
                    symbols=symbols,
                    reason=build_reason(found, terms_hit),
                )
            )
        return tuple(items)
