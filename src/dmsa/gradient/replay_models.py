"""离线检索回放证据数据模型."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from dmsa.gradient.policy import OfflineRSIError

GATE_VERSION = "offline-rsi-gate-v1"


def digest(value: object) -> str:
    """返回规范 JSON SHA-256 摘要."""
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise OfflineRSIError("value is not canonically serializable") from exc
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ReplayCase:
    """带分层标签的开发集样本."""

    case_id: str
    user_id: str
    query: str
    relevant_ids: tuple[str, ...]
    slice: str
    noise_group: str = "default"

    def __post_init__(self) -> None:
        """拒绝缺失标签、可变相关 ID 和非文本字段."""
        if any(not isinstance(x, str) or not x.strip()
               for x in (self.case_id, self.user_id, self.query, self.slice, self.noise_group)):
            raise OfflineRSIError("replay case requires nonempty text fields")
        if (not isinstance(self.relevant_ids, tuple) or not self.relevant_ids
                or any(not isinstance(x, str) or not x for x in self.relevant_ids)
                or len(set(self.relevant_ids)) != len(self.relevant_ids)):
            raise OfflineRSIError("replay case requires unique relevant IDs")


@dataclass(frozen=True)
class CaseResult:
    """一个样本实际召回的稳定 ID 及预算用量."""

    case_id: str
    retrieved_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    slice: str
    used_chars: int = 0
    used_tokens: int = 0

    @property
    def recall(self) -> float:
        """返回样本的相关证据召回率."""
        overlap = len(set(self.retrieved_ids) & set(self.relevant_ids))
        return overlap / max(1, len(self.relevant_ids))


def result_dict(value: CaseResult) -> dict[str, Any]:
    """将结果转为规范 JSON 字段."""
    return {
        "case_id": value.case_id,
        "retrieved_ids": value.retrieved_ids,
        "relevant_ids": value.relevant_ids,
        "slice": value.slice,
        "used_chars": value.used_chars,
        "used_tokens": value.used_tokens,
    }


@dataclass(frozen=True)
class ReplayReport:
    """绑定策略、语料、样本及预算的不可变配对结果."""

    baseline_hash: str
    candidate_hash: str
    corpus_hash: str
    cases_hash: str
    top_k: int
    max_chars: int
    max_tokens: int
    before: tuple[CaseResult, ...]
    after: tuple[CaseResult, ...]
    gate_version: str = GATE_VERSION

    @property
    def report_hash(self) -> str:
        """返回完整证据报告摘要."""
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的规范字段."""
        return {
            "baseline_hash": self.baseline_hash,
            "candidate_hash": self.candidate_hash,
            "corpus_hash": self.corpus_hash,
            "cases_hash": self.cases_hash,
            "top_k": self.top_k,
            "max_chars": self.max_chars,
            "max_tokens": self.max_tokens,
            "before": [result_dict(x) for x in self.before],
            "after": [result_dict(x) for x in self.after],
            "gate_version": self.gate_version,
        }
