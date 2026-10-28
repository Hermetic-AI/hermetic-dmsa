"""切片感知的失败诊断聚合: 把多条反馈收敛成可提案的诊断.

同一类失败如果只集中在一个任务或切片里, 更可能是噪声而不是通用规律, 因此不会
触发策略提案; 跨切片重复出现的失败才会进入提案空间.
"""

from __future__ import annotations

from dataclasses import dataclass

from dmsa.gradient.policy import FailureDiagnostic

# 提案所需的最小重复失败次数。
DEFAULT_MINIMUM_SUPPORT = 2
# 提案所需的最小失败切片数; 失败次数更高时允许切片单一。
DEFAULT_MINIMUM_SLICES = 2
# 单一切片内允许被视为噪声的最大失败次数。
SINGLE_SLICE_LIMIT = 3


@dataclass(frozen=True)
class Diagnosis:
    """一类失败的支持度、反例数与切片分布."""

    kind: str
    failures: int
    success_counterexamples: int
    slices: int

    def to_diagnostic(self) -> FailureDiagnostic:
        """转换为提案器使用的失败诊断."""
        return FailureDiagnostic(
            kind=self.kind,
            failures=self.failures,
            success_counterexamples=self.success_counterexamples,
        )


def diagnose(
    rows: tuple[tuple[str, str, str], ...],
    *,
    minimum_support: int = DEFAULT_MINIMUM_SUPPORT,
    minimum_slices: int = DEFAULT_MINIMUM_SLICES,
) -> tuple[Diagnosis, ...]:
    """按失败类型聚合反馈, 返回达到提案门槛的诊断."""
    if type(minimum_support) is not int or minimum_support < 1:
        raise ValueError("minimum_support must be a positive integer")
    if type(minimum_slices) is not int or minimum_slices < 1:
        raise ValueError("minimum_slices must be a positive integer")
    failures: dict[str, int] = {}
    counterexamples: dict[str, int] = {}
    slices: dict[str, set[str]] = {}
    for kind, outcome, slice_id in rows:
        if outcome == "failure":
            failures[kind] = failures.get(kind, 0) + 1
            slices.setdefault(kind, set()).add(slice_id)
        elif outcome == "success" and kind != "unknown":
            counterexamples[kind] = counterexamples.get(kind, 0) + 1
    result: list[Diagnosis] = []
    for kind, count in failures.items():
        if count < minimum_support:
            continue
        distinct = len(slices.get(kind, ()))
        if distinct < minimum_slices and count < SINGLE_SLICE_LIMIT:
            continue
        result.append(
            Diagnosis(
                kind=kind,
                failures=count,
                success_counterexamples=counterexamples.get(kind, 0),
                slices=distinct,
            )
        )
    return tuple(sorted(result, key=lambda item: (-item.failures, item.kind)))
