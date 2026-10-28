"""任务级门禁: 用真实任务结果约束策略晋升.

任务结果本身不参与根因推断, 只回答一个问题: 在已经成功的任务里, 当时真正帮上忙的
证据, 换用候选策略后是否还能被召回. 任何一条成功任务丢失已确认证据, 候选策略即被拒绝.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dmsa.gradient.policy import OfflineRSIError, RetrievalPolicy
from dmsa.gradient.replay_models import ReplayCase
from dmsa.memory import CodingMemory, scope_key

# 门禁比较时使用的 top_k, 与检索回放保持同一量级.
DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class TaskCase:
    """一条带任务结果与已确认证据的样本."""

    task_id: str
    scope: str
    query: str
    relevant_ids: tuple[str, ...]
    outcome: str
    trace_id: str = ""

    def __post_init__(self) -> None:
        """拒绝空标识、未知终态与空证据标签."""
        if not self.task_id or not self.query:
            raise OfflineRSIError("task case requires nonempty identifiers")
        if self.outcome not in {"success", "failure"}:
            raise OfflineRSIError("task case outcome must be success or failure")
        if not self.relevant_ids or any(
            not isinstance(item, str) or not item for item in self.relevant_ids
        ):
            raise OfflineRSIError("task case requires confirmed relevant evidence")

    def to_replay_case(self) -> ReplayCase:
        """转换为回放样本, 切片标签使用任务标识."""
        return ReplayCase(
            case_id=f"task:{self.task_id}",
            user_id=self.scope,
            query=self.query,
            relevant_ids=self.relevant_ids,
            slice=self.task_id,
        )


@dataclass(frozen=True)
class TaskGateReport:
    """任务级门禁结果."""

    checked: int
    retained: int
    improved: int
    lost_tasks: tuple[str, ...]
    failed_tasks: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """返回门禁是否通过: 成功任务不得丢失已确认证据."""
        return not self.lost_tasks

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的规范字段."""
        return {
            "checked": self.checked,
            "retained": self.retained,
            "improved": self.improved,
            "lost_tasks": list(self.lost_tasks),
            "failed_tasks": list(self.failed_tasks),
            "passed": self.passed,
        }


def task_cases(
    scope: str,
    traces: tuple[dict[str, Any], ...],
    feedback_labels: dict[str, tuple[str, ...]],
    task_outcomes: dict[str, str],
) -> tuple[TaskCase, ...]:
    """把轨迹、已确认证据与任务结果拼成任务级样本.

    只有同时具备任务结果与人工/Agent 确认证据的轨迹才会成为样本,
    避免把"返回过但没被确认有用"的证据当作正标签.
    """
    cases: list[TaskCase] = []
    for trace in traces:
        task_id = str(trace.get("task_id") or "")
        if not task_id:
            continue
        outcome = task_outcomes.get(task_id)
        if outcome not in {"success", "failure"}:
            continue
        relevant = feedback_labels.get(str(trace["trace_id"]), ())
        if not relevant:
            continue
        cases.append(
            TaskCase(
                task_id=task_id,
                scope=scope_key(scope, str(trace.get("repo") or "")),
                query=str(trace["query"]),
                relevant_ids=tuple(relevant),
                outcome=outcome,
                trace_id=str(trace["trace_id"]),
            )
        )
    return tuple(cases)


def run_task_gate(
    memory: CodingMemory,
    baseline: RetrievalPolicy,
    candidate: RetrievalPolicy,
    cases: tuple[TaskCase, ...],
    *,
    top_k: int = DEFAULT_TOP_K,
) -> TaskGateReport:
    """在配对检索下检查候选策略是否保住成功任务用过的证据."""
    if not isinstance(cases, tuple):
        raise OfflineRSIError("task cases must be a tuple")
    if type(top_k) is not int or top_k < 1:
        raise OfflineRSIError("top_k must be a positive integer")
    retained = 0
    improved = 0
    lost: list[str] = []
    failed: list[str] = []
    for case in cases:
        scope = case.scope or case.task_id
        before = memory.search(case.query, scope, top_k, baseline.search)
        after = memory.search(case.query, scope, top_k, candidate.search)
        before_ids = {hit.id for hit in before}
        after_ids = {hit.id for hit in after}
        needed = set(case.relevant_ids)
        if needed <= after_ids:
            retained += 1
            if not needed <= before_ids:
                improved += 1
        elif case.outcome == "success":
            lost.append(case.task_id)
        else:
            failed.append(case.task_id)
    return TaskGateReport(
        checked=len(cases),
        retained=retained,
        improved=improved,
        lost_tasks=tuple(lost),
        failed_tasks=tuple(failed),
    )
