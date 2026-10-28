"""稠密混合检索的通道融合与有界重排。

词面通道沿用 RRF 名次折算, 稠密通道改用批内余弦相似度的极值归一: 语义上
更接近的证据拿到的稠密分明显更多, 而不是只按名次先后拿到几乎相同的分数.
稠密通道折算到同一个 RRF 单位后与词面分相加, 因此混合分与既有检索分同量纲,
可以直接叠加经验分、证据质量调整与信任乘子。

重排只做有界的乘性调整: 跨通道一致命中小幅加分, 与更高排名条目近乎重复
小幅减分, 调整幅度被 rerank_delta 夹住, 不会把明显更差的证据顶上来.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from dmsa.memory.coding.models import CodingMemoryValidationError, SearchHit
from dmsa.memory.coding.packing import token_set
from dmsa.memory.coding.recall import DENSE_CHANNEL, ChannelRecall

# RRF 单位: 权重为 1、名次为 1 的单通道贡献.
RRF_UNIT = 1.0 / 61.0
# 参与重排的候选深度上限, 避免在超大候选集上做重复度比较.
MAX_RERANK_DEPTH = 200


@dataclass(frozen=True)
class HybridPolicy:
    """混合检索的融合与重排参数.

    词面通道的权重仍由检索策略给出, 这里只定义混合检索特有的部分: 稠密
    通道权重、跨通道一致性加分、重复度减分与重排幅度上限.
    """

    dense_weight: float = 1.0
    agreement_bonus: float = 0.05
    redundancy_penalty: float = 0.08
    rerank_delta: float = 0.1
    rerank_depth: int = 20
    dense_min_similarity: float = 0.0

    def __post_init__(self) -> None:
        """拒绝越界或非有限的融合与重排参数."""
        for name, value in (
            ("dense_weight", self.dense_weight),
            ("agreement_bonus", self.agreement_bonus),
            ("redundancy_penalty", self.redundancy_penalty),
            ("rerank_delta", self.rerank_delta),
            ("dense_min_similarity", self.dense_min_similarity),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CodingMemoryValidationError(f"{name} must be a finite number")
            if not math.isfinite(value):
                raise CodingMemoryValidationError(f"{name} must be a finite number")
        if not 0 <= self.dense_weight <= 10:
            raise CodingMemoryValidationError("dense_weight must be in [0, 10]")
        if not 0 <= self.agreement_bonus <= 1:
            raise CodingMemoryValidationError("agreement_bonus must be in [0, 1]")
        if not 0 <= self.redundancy_penalty <= 1:
            raise CodingMemoryValidationError("redundancy_penalty must be in [0, 1]")
        if not 0 <= self.rerank_delta <= 1:
            raise CodingMemoryValidationError("rerank_delta must be in [0, 1]")
        if not -1 <= self.dense_min_similarity <= 1:
            raise CodingMemoryValidationError("dense_min_similarity must be in [-1, 1]")
        if type(self.rerank_depth) is not int or not 1 <= self.rerank_depth <= MAX_RERANK_DEPTH:
            raise CodingMemoryValidationError(
                f"rerank_depth must be an integer between 1 and {MAX_RERANK_DEPTH}"
            )


@dataclass(frozen=True)
class HybridItem:
    """单条候选的融合与重排明细."""

    id: str
    fused: float
    channels: tuple[str, ...]
    contributions: tuple[tuple[str, float], ...]
    agreement: int
    dense_similarity: float | None = None
    rerank_delta: float = 0.0
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的融合明细."""
        return {
            "id": self.id,
            "fused": round(self.fused, 6),
            "score": round(self.score, 6),
            "channels": list(self.channels),
            "contributions": {name: round(value, 6) for name, value in self.contributions},
            "agreement": self.agreement,
            "dense_similarity": (
                None if self.dense_similarity is None else round(self.dense_similarity, 6)
            ),
            "rerank_delta": round(self.rerank_delta, 6),
        }


@dataclass(frozen=True)
class HybridResult:
    """一次混合检索的证据与融合明细."""

    query: str
    hits: tuple[SearchHit, ...]
    items: tuple[HybridItem, ...]
    channels: tuple[str, ...]
    dense_available: bool

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的混合检索视图."""
        return {
            "query": self.query,
            "dense_available": self.dense_available,
            "channels": list(self.channels),
            "hits": [
                {"id": hit.id, "content": hit.content, "score": round(hit.score, 6)}
                for hit in self.hits
            ],
            "items": [item.to_dict() for item in self.items],
        }


def _min_max(values: Mapping[str, float]) -> dict[str, float]:
    """把一组数值做极值归一到 [0, 1]; 全部相等时统一给满分."""
    if not values:
        return {}
    lowest = min(values.values())
    highest = max(values.values())
    if highest - lowest <= 0:
        return dict.fromkeys(values, 1.0)
    return {key: (value - lowest) / (highest - lowest) for key, value in values.items()}


def _jaccard(left: set[str], right: set[str]) -> float:
    """计算两个 token 集合的交并比."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def fuse(
    channels: Mapping[str, ChannelRecall],
    dense_similarities: Mapping[str, float],
    policy: HybridPolicy,
) -> tuple[dict[str, float], dict[str, HybridItem]]:
    """按通道归一化融合词面与稠密召回.

    词面通道的名次贡献与既有 RRF 完全一致, 稠密通道改用批内余弦的极值归一,
    再折算到 RRF 单位相加, 因此融合分与既有检索分同量纲.
    """
    scores: dict[str, float] = {}
    parts: dict[str, dict[str, float]] = {}
    names: dict[str, list[str]] = {}

    def record(chunk_id: str, channel: str, value: float) -> None:
        """累加某条候选在单个通道上的贡献."""
        scores[chunk_id] = scores.get(chunk_id, 0.0) + value
        parts.setdefault(chunk_id, {})[channel] = value
        if channel not in names.setdefault(chunk_id, []):
            names[chunk_id].append(channel)

    for name, channel in channels.items():
        if channel.weight == 0:
            continue
        for rank, chunk_id in enumerate(channel.ids, 1):
            record(chunk_id, name, channel.weight / (60.0 + rank))
    if policy.dense_weight:
        for chunk_id, value in _min_max(dense_similarities).items():
            record(chunk_id, DENSE_CHANNEL, policy.dense_weight * value * RRF_UNIT)
    items = {
        chunk_id: HybridItem(
            id=chunk_id,
            fused=score,
            channels=tuple(names.get(chunk_id, ())),
            contributions=tuple(sorted(parts.get(chunk_id, {}).items())),
            agreement=len(names.get(chunk_id, ())),
            dense_similarity=dense_similarities.get(chunk_id),
        )
        for chunk_id, score in scores.items()
    }
    return scores, items


def rerank(
    scores: Mapping[str, float],
    items: Mapping[str, HybridItem],
    texts: Mapping[str, str],
    policy: HybridPolicy,
    redundancy_threshold: float = 0.93,
) -> tuple[dict[str, float], dict[str, HybridItem]]:
    """按跨通道一致性与重复度做有界乘性重排.

    只有前 rerank_depth 名候选参与重排, 深度之外的候选分数保持不变.
    """
    ordered = sorted(scores, key=lambda key: (-scores[key], key))
    result = dict(scores)
    updated: dict[str, HybridItem] = {}
    for chunk_id, value in scores.items():
        info = items.get(chunk_id)
        if info is not None:
            updated[chunk_id] = replace(info, score=value)
    seen: list[set[str]] = []
    for chunk_id in ordered[: policy.rerank_depth]:
        info = items.get(chunk_id)
        agreement = info.agreement if info is not None else 1
        bonus = min((agreement - 1) * policy.agreement_bonus, policy.rerank_delta)
        tokens = token_set(texts.get(chunk_id, ""))
        penalty = (
            policy.redundancy_penalty
            if seen and any(_jaccard(tokens, other) >= redundancy_threshold for other in seen)
            else 0.0
        )
        delta = max(-policy.rerank_delta, min(policy.rerank_delta, bonus - penalty))
        result[chunk_id] = scores[chunk_id] * (1.0 + delta)
        if info is not None:
            updated[chunk_id] = replace(info, rerank_delta=delta, score=result[chunk_id])
        seen.append(tokens)
    return result, updated
