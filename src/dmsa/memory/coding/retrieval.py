"""同 scope 精确、稀疏、短词子串、可选 dense 召回与证据打包."""

from __future__ import annotations

import math
import re
import sqlite3
from collections import defaultdict

from dmsa.memory.coding.budget import estimate_tokens
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
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.tokenize import query_terms
from dmsa.memory.coding.vector_index import VectorIndex


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """计算余弦相似度."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right)))


def _tokens(text: str) -> set[str]:
    """产生预算统计及冗余比较用 token."""
    return set(re.findall(r"[\w./-]+", text.lower()))


class CodingRetriever:
    """基于稳定 RRF 和确定性证据预算的检索器."""

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
            ranks[str(row["id"])] += weight / (60 + rank)

    def _pack(
        self,
        user_id: str,
        ordered: list[str],
        ranks: dict[str, float],
        top_k: int,
        policy: SearchPolicy,
    ) -> list[SearchHit]:
        """扩展邻居后按冗余、session 与字符/token 预算装包."""
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
        selected: list[SearchHit] = []
        sessions: dict[str, int] = defaultdict(int)
        char_budget, token_budget = policy.max_chars, policy.max_tokens
        token_sets: list[set[str]] = []
        for cid in expanded:
            row = by_id.get(cid)
            if row is None:
                continue
            session = str(row["session_id"])
            if sessions[session] >= policy.per_session_limit:
                continue
            source = (
                f"[request={row['request_id']} session={session} message={row['message_id']} "
                f"chars={row['start']}:{row['end']} role={row['role']} outcome={row['outcome']}]\n"
            )
            content = source + str(row["text"])
            terms = _tokens(content)
            if token_sets and any(
                len(terms & existing) / max(1, len(terms | existing)) >= policy.redundancy_threshold
                for existing in token_sets
            ):
                continue
            size = estimate_tokens(content)
            if len(content) > char_budget or size > token_budget:
                continue
            selected.append(SearchHit(cid, content, ranks[cid]))
            token_sets.append(terms)
            sessions[session] += 1
            char_budget -= len(content)
            token_budget -= size
            if len(selected) >= top_k or char_budget <= 0 or token_budget <= 0:
                break
        return selected

    def search(self, query: str, user_id: str, top_k: int, policy: SearchPolicy) -> list[SearchHit]:
        """融合 scope 内候选并返回可追溯证据."""
        if type(top_k) is not int or top_k < 1:
            raise CodingMemoryValidationError("top_k must be a positive integer")
        terms = tuple(_tokens(query))
        expanded = query_terms(query)
        ranks: dict[str, float] = defaultdict(float)
        exact = self.store.exact_candidates(terms, user_id, policy.candidate_limit)
        sparse = self.store.sparse_candidates(expanded, user_id, policy.candidate_limit)
        short = self.store.substring_candidates(expanded, user_id, policy.candidate_limit)
        self._add_channel(ranks, policy.exact_weight, exact)
        self._add_channel(ranks, policy.sparse_weight, sparse)
        self._add_channel(ranks, policy.sparse_weight, short)
        if self.embedder is not None and policy.dense_weight:
            query_embedder = getattr(self.embedder, "embed_query", None)
            query_vector = (
                tuple(float(value) for value in query_embedder(query))
                if callable(query_embedder)
                else self.embed_new([query])[0]
            )
            if (
                len(query_vector) != self.embedder.dimension
                or not all(math.isfinite(value) for value in query_vector)
                or not any(query_vector)
            ):
                raise CodingMemoryValidationError("embedder returned invalid query vector")
            if self.vector_index is not None:
                try:
                    candidate_ids = self.vector_index.search(
                        user_id, self.fingerprint or "", query_vector, policy.candidate_limit
                    )
                except Exception as exc:
                    raise CodingMemoryError("vector index search failed") from exc
                indexed_rows = self.store.rows_by_ids(user_id, tuple(candidate_ids))
                by_id = {str(row["id"]): row for row in indexed_rows}
                dense_rows = [by_id[point_id] for point_id in candidate_ids if point_id in by_id]
                self._add_channel(ranks, policy.dense_weight, dense_rows)
            else:
                count, dense_candidates = self.store.dense_candidates(
                    user_id, self.fingerprint or "", policy.dense_scan_limit
                )
                if count > policy.dense_scan_limit:
                    raise DenseScopeTooLargeError(
                        f"dense scan has {count} chunks; limit is {policy.dense_scan_limit}"
                    )
                if any(len(vector) != len(query_vector) for _, vector in dense_candidates):
                    raise CodingMemoryValidationError(
                        "stored vector dimension differs from embedder"
                    )
                scored = [(_cosine(query_vector, vector), row) for row, vector in dense_candidates]
                scored.sort(key=lambda item: (-item[0], str(item[1]["id"])))
                self._add_channel(
                    ranks, policy.dense_weight, [row for _, row in scored[: policy.candidate_limit]]
                )
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
            ranks[str(row["id"])] += policy.experience_weight * applicability_adjustment(
                query, card
            )
        ordered = sorted(ranks, key=lambda key: (-ranks[key], key))
        return self._pack(user_id, ordered, ranks, top_k, policy)
