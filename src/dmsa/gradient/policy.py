"""离线 RSI 的有界检索策略与诊断提案。."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, cast

from dmsa.errors import DMSAError
from dmsa.memory import CodingMemoryValidationError, SearchPolicy


class OfflineRSIError(DMSAError):
    """离线策略进化或持久化操作失败。."""


@dataclass(frozen=True)
class RetrievalPolicy:
    """不可变策略包装, 沿用 L1 SearchPolicy 校验。."""

    search: SearchPolicy = field(default_factory=SearchPolicy)

    def __post_init__(self) -> None:
        """仅接受已验证的 L1 策略。."""
        if not isinstance(self.search, SearchPolicy):
            raise OfflineRSIError("search must be SearchPolicy")

    def changed(self, **values: int | float) -> RetrievalPolicy:
        """返回经过 SearchPolicy 验证的新策略。."""
        try:
            return RetrievalPolicy(replace(self.search, **cast(Any, values)))
        except (TypeError, ValueError, CodingMemoryValidationError) as exc:
            raise OfflineRSIError("invalid policy proposal") from exc

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> RetrievalPolicy:
        """从持久化字段重建并验证策略。."""
        try:
            return cls(SearchPolicy(**cast(Any, values)))
        except (TypeError, ValueError, CodingMemoryValidationError) as exc:
            raise OfflineRSIError("invalid stored policy") from exc


@dataclass(frozen=True)
class FailureDiagnostic:
    """开发集中的失败类别与对应成功反例计数。."""

    kind: str
    failures: int
    success_counterexamples: int = 0


def _validate_diagnostics(diagnostics: tuple[FailureDiagnostic, ...]) -> None:
    """拒绝格式错误的诊断记录。."""
    for item in diagnostics:
        if (
            not isinstance(item, FailureDiagnostic)
            or not isinstance(item.kind, str)
            or type(item.failures) is not int
            or item.failures < 0
            or type(item.success_counterexamples) is not int
            or item.success_counterexamples < 0
        ):
            raise OfflineRSIError("malformed failure diagnostic")


def propose_policy(
    current: RetrievalPolicy,
    diagnostics: tuple[FailureDiagnostic, ...],
    minimum_support: int = 2,
    *,
    development_only: bool = False,
) -> tuple[RetrievalPolicy, ...]:
    """按重复失败提出单步小改动, 成功反例抑制过度调整。."""
    if development_only is not True:
        raise OfflineRSIError("development_only=True is required")
    if type(minimum_support) is not int or minimum_support < 2:
        raise OfflineRSIError("minimum_support must be at least two")
    proposals: list[RetrievalPolicy] = []
    _validate_diagnostics(diagnostics)
    for item in diagnostics:
        if item.failures < minimum_support or item.success_counterexamples > item.failures:
            continue
        policy = current.search
        if item.kind == "exact_miss":
            proposals.append(current.changed(exact_weight=min(10.0, policy.exact_weight + 0.25)))
        elif item.kind == "redundancy":
            proposals.append(
                current.changed(redundancy_threshold=max(0.0, policy.redundancy_threshold - 0.02))
            )
        elif item.kind == "missing_context":
            proposals.append(current.changed(neighbor_window=min(10, policy.neighbor_window + 1)))
    return tuple(dict.fromkeys(proposals))
