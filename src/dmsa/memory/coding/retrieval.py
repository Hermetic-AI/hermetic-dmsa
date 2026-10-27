"""同 scope 精确、稀疏、短词子串、可选 dense 召回与证据打包."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Mapping

from dmsa.memory.coding.budget import estimate_tokens
from dmsa.memory.coding.episodes import SUPERSEDED_OUTCOME
from dmsa.memory.coding.experiences import applicability_adjustment, extract_experience_card
from dmsa.memory.coding.extraction import Chunk
from dmsa.memory.coding.models import (
    CodingMemoryError,
    CodingMemoryValidationError,
    DenseScopeTooLargeError,
    Embedder,
    SearchHit,
    SearchPolicy,
)
from dmsa.memory.coding.packing import select_evidence
from dmsa.memory.coding.packing import token_set as _tokens
from dmsa.memory.coding.ranking import (
    QueryFeatures,
    evidence_adjustment,
    is_stale_write,
    query_features,
)
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.tokenize import query_terms
from dmsa.memory.coding.vector_index import VectorIndex


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """计算余弦相似度."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right)))


# 显式结果标签只做小幅排序先验: 声称成功的来源优先, 已观测失败的来源降权。
_OUTCOME_BONUS = {"reported_success": 0.004, "observed_failure": -0.002}


class CodingRetriever:
    """基于稳定 RRF 和确定性证据预算的检索器."""

    # 截断后的正文短于该值时直接放弃, 避免只有来源头没有内容的噪声条目。
    MIN_TRUNCATED_CHARS = 160

    def __init__(
        self, store: CodingStore, embedder: Embedder | None, vector_index: VectorIndex | None = None
    ) -> None:
        """初始化可选 dense 嵌入器."""
        self.store = store
        self.embedder = embedder
        self.vector_index = vector_index
        self.fingerprint = self._fingerprint(embedder) if embedder else None

    @staticmethod
    def _fingerprint(embedder: Embedder) -> str:
        """验证并获取明确的模型指纹."""
        fingerprint = getattr(embedder, "fingerprint", None) or getattr(embedder, "name", None)
        dimension = getattr(embedder, "dimension", None)
        if (
            not isinstance(fingerprint, str)
            or not fingerprint
            or type(dimension) is not int
            or dimension <= 0
        ):
            raise CodingMemoryValidationError(
                "embedder requires fingerprint/name and positive dimension"
            )
        return fingerprint

    def embed_new(self, texts: list[str]) -> list[tuple[float, ...]]:
        """计算并校验批量向量."""
        if self.embedder is None or not texts:
            return []
        try:
            values = self.embedder.embed_batch(texts)
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
                len(item) != self.embedder.dimension
                or not all(math.isfinite(value) for value in item)
                or not any(item)
            ):
                raise CodingMemoryValidationError("embedder returned invalid vector")
            result.append(item)
        return result

    @staticmethod
    def _add_channel(ranks: dict[str, float], weight: float, rows: list[sqlite3.Row]) -> None:
        """将单通道排名以 RRF 加入总分."""
        if weight == 0:
            return
        for rank, row in enumerate(rows, 1):
            key = str(row["id"])
            ranks[key] = ranks.get(key, 0.0) + weight / (60 + rank)

    def _fit(
        self, source: str, body: str, char_budget: int, token_budget: int
    ) -> str | None:
        """把正文裁到剩余预算内; 预算不足时保留可辨识前缀并显式标记截断."""
        marker = "\n[truncated]"
        room = min(len(body), char_budget - len(source) - len(marker))
        if room < self.MIN_TRUNCATED_CHARS:
            return None
        if estimate_tokens(source + body[:room] + marker) <= token_budget:
            return source + body[:room] + marker
        low, high = 0, room
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens(source + body[:middle] + marker) <= token_budget:
                low = middle
            else:
                high = middle - 1
        if low < self.MIN_TRUNCATED_CHARS:
            return None
        return source + body[:low] + marker

    def _pack(
        self,
        user_id: str,
        ordered: list[str],
        ranks: dict[str, float],
        top_k: int,
        policy: SearchPolicy,
    ) -> list[SearchHit]:
        """扩展邻居后按限额、冗余与预算装包."""
        expanded = list(ordered)
        seen = set(expanded)
        for cid in ordered:
            for neighbor in self.store.neighbors(user_id, cid, policy.neighbor_window):
                neighbor_id = str(neighbor["id"])
                if neighbor_id not in seen:
                    seen.add(neighbor_id)
                    ranks[neighbor_id] = ranks[cid] * 0.5
                    expanded.append(neighbor_id)
        rows = self.store.rows_by_ids(user_id, tuple(expanded))
        by_id = {str(row["id"]): row for row in rows}
        expanded.sort(key=lambda cid: (-ranks[cid], cid))
        ranked: list[tuple[str, float, Mapping[str, object]]] = []
        for cid in expanded:
            row = by_id.get(cid)
            if row is None:
                continue
            body = str(row["text"])
            if policy.experience_weight and is_stale_write(body):
                continue
            if str(row["outcome"]) == SUPERSEDED_OUTCOME:
                continue
            ranked.append((cid, ranks[cid], dict(row)))
        selected = select_evidence(ranked, top_k, policy)
        hits: list[SearchHit] = []
        char_budget, token_budget = policy.max_chars, policy.max_tokens
        for cid, score, item in selected:
            source = (
                f"[request={item['request_id']} session={item['session_id']} "
                f"message={item['message_id']} chars={item['start']}:{item['end']} "
                f"role={item['role']} outcome={item['outcome']}]\n"
            )
            body = str(item["text"])
            content = source + body
            size = estimate_tokens(content)
            if len(content) > char_budget or size > token_budget:
                truncated = self._fit(source, body, char_budget, token_budget)
                if truncated is None:
                    continue
                content, size = truncated, estimate_tokens(truncated)
            hits.append(SearchHit(cid, content, score))
            char_budget -= len(content)
            token_budget -= size
            if char_budget <= 0 or token_budget <= 0:
                break
        return hits

    def collect_candidates(
        self, query: str, user_id: str, policy: SearchPolicy
    ) -> tuple[dict[str, float], QueryFeatures]:
        """只做库内召回, 返回通道分数与查询特征; 不含任何外部调用."""
        terms = tuple(_tokens(query))
        expanded = query_terms(query)
        ranks: dict[str, float] = defaultdict(float)
        exact = self.store.exact_candidates(terms, user_id, policy.candidate_limit)
        sparse = self.store.sparse_candidates(expanded, user_id, policy.candidate_limit)
        short = self.store.substring_candidates(expanded, user_id, policy.candidate_limit)
        self._add_channel(ranks, policy.exact_weight, exact)
        self._add_channel(ranks, policy.sparse_weight, sparse)
        self._add_channel(ranks, policy.sparse_weight, short)
        return dict(ranks), query_features(query)

    def query_vector(self, query: str) -> tuple[float, ...] | None:
        """计算查询向量; 属于外部调用, 必须在数据库锁之外执行."""
        if self.embedder is None:
            return None
        query_embedder = getattr(self.embedder, "embed_query", None)
        vector = (
            tuple(float(value) for value in query_embedder(query))
            if callable(query_embedder)
            else self.embed_new([query])[0]
        )
        if (
            len(vector) != self.embedder.dimension
            or not all(math.isfinite(value) for value in vector)
            or not any(vector)
        ):
            raise CodingMemoryValidationError("embedder returned invalid query vector")
        return vector

    def dense_index_ids(
        self, user_id: str, vector: tuple[float, ...], policy: SearchPolicy
    ) -> tuple[str, ...]:
        """在远端向量索引里按 scope 过滤检索; 属于外部调用."""
        if self.vector_index is None:
            return ()
        try:
            return tuple(
                self.vector_index.search(
                    user_id, self.fingerprint or "", vector, policy.candidate_limit
                )
            )
        except Exception as exc:
            raise CodingMemoryError("vector index search failed") from exc

    def dense_scan_rows(
        self, user_id: str, vector: tuple[float, ...], policy: SearchPolicy
    ) -> list[sqlite3.Row]:
        """在库内做有界稠密扫描; 只做库内读取."""
        count, dense_candidates = self.store.dense_candidates(
            user_id, self.fingerprint or "", policy.dense_scan_limit
        )
        if count > policy.dense_scan_limit:
            raise DenseScopeTooLargeError(
                f"dense scan has {count} chunks; limit is {policy.dense_scan_limit}"
            )
        if any(len(stored) != len(vector) for _, stored in dense_candidates):
            raise CodingMemoryValidationError("stored vector dimension differs from embedder")
        scored = [(_cosine(vector, stored), row) for row, stored in dense_candidates]
        scored.sort(key=lambda item: (-item[0], str(item[1]["id"])))
        return [row for _, row in scored[: policy.candidate_limit]]

    def assemble(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy,
        ranks: dict[str, float],
        features: QueryFeatures,
        dense_rows: list[sqlite3.Row],
    ) -> list[SearchHit]:
        """融合稠密通道、打经验分并装包; 只做库内读取."""
        if type(top_k) is not int or top_k < 1:
            raise CodingMemoryValidationError("top_k must be a positive integer")
        if dense_rows:
            self._add_channel(ranks, policy.dense_weight, dense_rows)
        card_rows = self.store.rows_by_ids(user_id, tuple(ranks))
        for row in card_rows:
            text = str(row["text"])
            card = extract_experience_card(
                Chunk(
                    int(row["message_index"]),
                    int(row["start"]),
                    int(row["end"]),
                    text,
                    tuple(str(row["paths"]).split()),
                    tuple(str(row["symbols"]).split()),
                    str(row["outcome"]),
                )
            )
            key = str(row["id"])
            ranks[key] = ranks.get(key, 0.0) + policy.experience_weight * (
                applicability_adjustment(query, card)
            )
            ranks[key] = ranks.get(key, 0.0) + policy.experience_weight * (
                _OUTCOME_BONUS.get(str(row["outcome"]), 0.0)
            )
            ranks[key] = ranks.get(key, 0.0) + policy.experience_weight * evidence_adjustment(
                features, text, str(row["paths"]), str(row["symbols"])
            )
        ordered = sorted(ranks, key=lambda key: (-ranks[key], key))
        return self._pack(user_id, ordered, ranks, top_k, policy)
