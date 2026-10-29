"""同 scope 多路召回、证据质量调分与装包的编排器."""

from __future__ import annotations

import sqlite3

from dmsa.memory.coding.evidence_pack import build_hits
from dmsa.memory.coding.experiences import applicability_adjustment, extract_experience_card
from dmsa.memory.coding.extraction import Chunk
from dmsa.memory.coding.models import (
    CodingMemoryValidationError,
    Embedder,
    SearchHit,
    SearchPolicy,
)
from dmsa.memory.coding.ranking import QueryFeatures, evidence_adjustment
from dmsa.memory.coding.recall import (
    add_channel,
    collect_candidates,
    dense_index_ids,
    dense_scan_rows,
    dense_scan_scores,
    embed_batch,
    query_vector,
)
from dmsa.memory.coding.snapshot import RepoSnapshot, stale_anchors
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.temporal import AsOf
from dmsa.memory.coding.trust import BASE_TRUST, trust_multiplier
from dmsa.memory.coding.trust_store import evicted_ids, trust_map
from dmsa.memory.coding.vector_codec import text_hash
from dmsa.memory.coding.vector_index import VectorIndex

# 显式结果标签是排序先验: 声称成功的来源优先, 已观测失败的来源降权。
# 幅度与通道 RRF 分数同量级, 才能在通道排名接近时改变先后。
_OUTCOME_BONUS = {"reported_success": 0.02, "observed_failure": -0.008}


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
        """计算批量向量, 同模型同正文的已算结果直接复用.

        同一段正文在同一模型下的向量必然相同, 因此按正文哈希复用不改变检索结果,
        只省掉重复的嵌入调用。评测语料里大量切片是重复的仓库文件片段, 实测可以
        省掉约一半的嵌入调用。
        """
        if self.embedder is None or not texts:
            return []
        digests = [text_hash(text) for text in texts]
        cached = self.store.cached_vectors(self.fingerprint or "", tuple(digests))
        # 批内相同正文只算一次。
        pending: dict[str, str] = {}
        for text, digest in zip(texts, digests, strict=True):
            if digest not in cached:
                pending.setdefault(digest, text)
        if pending:
            fresh = embed_batch(self.embedder, list(pending.values()))
            cached.update(zip(pending.keys(), fresh, strict=True))
        return [cached[digest] for digest in digests]

    def collect_candidates(
        self,
        query: str,
        user_id: str,
        policy: SearchPolicy,
        as_of: AsOf | None = None,
    ) -> tuple[dict[str, float], QueryFeatures]:
        """只做库内召回, 返回通道分数与查询特征; 不含任何外部调用."""
        return collect_candidates(self.store, query, user_id, policy, as_of)

    def query_vector(self, query: str) -> tuple[float, ...] | None:
        """计算查询向量; 属于外部调用, 必须在数据库锁之外执行."""
        return query_vector(self.embedder, query)

    def dense_index_ids(
        self,
        user_id: str,
        vector: tuple[float, ...],
        policy: SearchPolicy,
        limit: int | None = None,
    ) -> tuple[str, ...]:
        """在远端向量索引里按 scope 过滤检索; 属于外部调用."""
        return dense_index_ids(
            self.vector_index, self.fingerprint or "", user_id, vector, policy, limit
        )

    def dense_scan_rows(
        self,
        user_id: str,
        vector: tuple[float, ...],
        policy: SearchPolicy,
        as_of: AsOf | None = None,
    ) -> list[sqlite3.Row]:
        """在库内做有界稠密扫描; 只做库内读取."""
        return dense_scan_rows(self.store, user_id, self.fingerprint or "", vector, policy, as_of)

    def dense_scan_scores(
        self,
        user_id: str,
        vector: tuple[float, ...],
        policy: SearchPolicy,
        as_of: AsOf | None = None,
    ) -> list[tuple[sqlite3.Row, float]]:
        """在库内做有界稠密扫描并同时给出余弦相似度; 只做库内读取."""
        return dense_scan_scores(self.store, user_id, self.fingerprint or "", vector, policy, as_of)

    def score_candidates(
        self,
        query: str,
        user_id: str,
        policy: SearchPolicy,
        ranks: dict[str, float],
        features: QueryFeatures,
        snapshot: RepoSnapshot | None,
    ) -> None:
        """在基础分上叠加经验、结果、证据质量与信任调整; 就地更新 ranks."""
        card_rows = self.store.rows_by_ids(user_id, tuple(ranks))
        anchors_map = self.store.anchors_by_ids(user_id, tuple(ranks))
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
            # 证据质量调分按 adjustment_weight 缩放: 未缩放时调分项的量级远大于
            # 通道 RRF 分数在相邻名次之间的差, 会整体压过通道排名。
            quality = policy.experience_weight * policy.adjustment_weight
            ranks[key] = ranks.get(key, 0.0) + quality * evidence_adjustment(
                features, text, str(row["paths"]), str(row["symbols"])
            )
            # 快照确认失效的证据轻微降权, 但保留原文供人工判断。
            if snapshot is not None and stale_anchors(snapshot, anchors_map.get(key, ())):
                ranks[key] = ranks.get(key, 0.0) - policy.stale_penalty
        # 信任度参与调分: 已被观测证明有用的条目上调, 已被判定无用的条目下调;
        # 已淘汰的条目不再进入结果, 但原文仍可按 ID 追溯。
        evicted = evicted_ids(self.store.db, user_id, tuple(ranks))
        trust = trust_map(self.store.db, user_id, tuple(ranks))
        for key in list(ranks):
            if key in evicted:
                ranks.pop(key, None)
                continue
            ranks[key] *= trust_multiplier(trust.get(key, BASE_TRUST), policy.trust_weight)

    def pack_hits(
        self,
        user_id: str,
        top_k: int,
        policy: SearchPolicy,
        ranks: dict[str, float],
        snapshot: RepoSnapshot | None,
        as_of: AsOf | None = None,
    ) -> list[SearchHit]:
        """按分数排序并装包, 返回带来源引用与定位信息的证据."""
        ordered = sorted(ranks, key=lambda key: (-ranks[key], key))
        return build_hits(self.store, user_id, ordered, ranks, top_k, policy, snapshot, as_of)

    def assemble(
        self,
        query: str,
        user_id: str,
        top_k: int,
        policy: SearchPolicy,
        ranks: dict[str, float],
        features: QueryFeatures,
        dense_rows: list[sqlite3.Row],
        as_of: AsOf | None = None,
    ) -> list[SearchHit]:
        """融合稠密通道、打经验分并装包; 只做库内读取."""
        if type(top_k) is not int or top_k < 1:
            raise CodingMemoryValidationError("top_k must be a positive integer")
        if dense_rows:
            add_channel(ranks, policy.dense_weight, dense_rows)
        snapshot = self.store.load_snapshot(user_id)
        self.score_candidates(query, user_id, policy, ranks, features, snapshot)
        return self.pack_hits(user_id, top_k, policy, ranks, snapshot, as_of)
