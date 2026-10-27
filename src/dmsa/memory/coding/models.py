"""Coding memory 的来源与检索数据模型."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from dmsa.errors import MemoryLayerError


class CodingMemoryError(MemoryLayerError):
    """Coding memory 基础异常."""


class CodingMemoryValidationError(CodingMemoryError):
    """请求或嵌入数据不符合接口约束."""


class CodingMemoryConflictError(CodingMemoryError):
    """幂等键已被不同来源内容占用."""


class DenseScopeTooLargeError(CodingMemoryError):
    """Dense reference scan 超出显式范围上限."""


def require_text(value: object, field: str) -> None:
    """校验必填纯文本字段并保证可存入 UTF-8 数据库."""
    if not isinstance(value, str) or not value.strip():
        raise CodingMemoryValidationError(f"{field} must be nonempty text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise CodingMemoryValidationError(f"{field} must be valid Unicode") from exc


def require_identifier(value: object, field: str) -> None:
    """校验参与稳定标识或 scope 的文本字段。."""
    require_text(value, field)
    if "\0" in cast(str, value):
        raise CodingMemoryValidationError(f"{field} must not contain NUL")


@dataclass(frozen=True)
class SourceMessage:
    """一条有序、已规范化的纯文本来源消息."""

    role: str
    content: str
    timestamp: int | None = None

    def __post_init__(self) -> None:
        """拒绝内容部件及非法原始字段."""
        require_text(self.role, "role")
        require_text(self.content, "content")
        if self.timestamp is not None and (type(self.timestamp) is not int or self.timestamp < 0):
            raise CodingMemoryValidationError("timestamp must be a nonnegative integer")


@dataclass(frozen=True)
class SearchPolicy:
    """有界检索通道、扩展和证据预算设置."""

    exact_weight: float = 1.0
    sparse_weight: float = 1.0
    dense_weight: float = 1.0
    experience_weight: float = 1.0
    candidate_limit: int = 240
    neighbor_window: int = 1
    max_chars: int = 120000
    per_session_limit: int = 16
    redundancy_threshold: float = 0.93
    dense_scan_limit: int = 10000
    max_tokens: int = 32000
    evidence_limit: int = 12
    score_floor_ratio: float = 0.45
    per_path_limit: int = 6
    min_evidence: int = 4

    def __post_init__(self) -> None:
        """拒绝非有限权重、布尔整数和无界策略值."""
        weights = (self.exact_weight, self.sparse_weight, self.dense_weight, self.experience_weight)
        for value in (*weights, self.redundancy_threshold, self.score_floor_ratio):
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise CodingMemoryValidationError("policy weights must be finite numbers")
            if not math.isfinite(value):
                raise CodingMemoryValidationError("policy weights must be finite numbers")
        if any(not 0 <= value <= 10 for value in weights[:3]) or sum(weights[:3]) == 0:
            raise CodingMemoryValidationError("channel weights must be in [0, 10] and not all zero")
        if not 0 <= self.experience_weight <= 10:
            raise CodingMemoryValidationError("experience_weight must be in [0, 10]")
        if not 0 <= self.score_floor_ratio <= 1:
            raise CodingMemoryValidationError("score_floor_ratio must be in [0, 1]")
        bounds = (
            (self.candidate_limit, 1, 1000),
            (self.neighbor_window, 0, 10),
            (self.max_chars, 1, 1000000),
            (self.per_session_limit, 1, 1000),
            (self.dense_scan_limit, 1, 100000),
            (self.max_tokens, 1, 1000000),
            (self.evidence_limit, 1, 1000),
            (self.per_path_limit, 1, 1000),
            (self.min_evidence, 0, 1000),
        )
        for value, lower, upper in bounds:
            if type(value) is not int or not lower <= value <= upper:
                raise CodingMemoryValidationError("policy integer outside allowed bounds")
        if not 0 <= self.redundancy_threshold <= 1:
            raise CodingMemoryValidationError("redundancy_threshold must be in [0, 1]")


@dataclass(frozen=True)
class SearchHit:
    """稳定来源 ID、带 provenance 的证据文本及排序分数."""

    id: str
    content: str
    score: float


@dataclass(frozen=True)
class CorpusState:
    """在单一 SQLite 快照中读取的 scope revision 与来源指纹."""

    revision: int
    fingerprint: str


class Embedder(Protocol):
    """批量嵌入器接口, 实例还须提供 fingerprint 或 name 字符串."""

    dimension: int

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """返回与输入一一对应的向量."""
