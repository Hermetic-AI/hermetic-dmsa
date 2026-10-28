"""外层 RSI: 在同等回放预算下比较提案器, 只产出证据, 不自动切换提案器.

内层 RSI (:mod:`dmsa.gradient.integrated`) 回答"这一次该不该激活某个候选策略";
本模块回答一个更外层的问题: 在同一批回放样本与同一预算下, 确定性提案器与模型
辅助提案器谁给出的候选更好. 结论只写入审计事件, 由人工决定是否调整 `models` 开关.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dmsa.gradient.diagnosis import diagnose
from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.model_advisor import ModelAssistedPolicyProposer
from dmsa.gradient.policy import (
    FailureDiagnostic,
    OfflineRSIError,
    RetrievalPolicy,
    propose_policy,
)
from dmsa.gradient.replay import gate, run_replay
from dmsa.gradient.replay_models import ReplayCase
from dmsa.memory import CodingMemory

# 参与比较的提案器; 每个提案器分到相同数量的回放预算.
PROPOSERS = ("deterministic", "model_assisted")
DEFAULT_BUDGET_REPLAYS = 6
TOP_K = 5


@dataclass(frozen=True)
class ProposerOutcome:
    """一个提案器在固定预算内的真实表现."""

    name: str
    status: str
    candidates: int = 0
    replays: int = 0
    accepted: int = 0
    best_mean_recall: float | None = None
    best_mean_ndcg: float | None = None
    error_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的规范字段."""
        return {
            "name": self.name,
            "status": self.status,
            "candidates": self.candidates,
            "replays": self.replays,
            "accepted": self.accepted,
            "best_mean_recall": self.best_mean_recall,
            "best_mean_ndcg": self.best_mean_ndcg,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class MetaEvolutionReport:
    """一次外层比较的完整证据."""

    scope: str
    budget_replays: int
    per_proposer_replays: int
    outcomes: tuple[ProposerOutcome, ...]
    winner: str

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化的规范字段."""
        return {
            "scope": self.scope,
            "budget_replays": self.budget_replays,
            "per_proposer_replays": self.per_proposer_replays,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "winner": self.winner,
        }


def _model_candidates(
    proposer_factory: Callable[[], Any] | None,
    baseline: RetrievalPolicy,
    diagnostics: tuple[FailureDiagnostic, ...],
    observer: Callable[[dict[str, Any]], None] | None,
) -> Callable[[], tuple[RetrievalPolicy, ...]]:
    """返回一个惰性生成模型辅助候选的闭包; 失败时向上抛出由调用方记录."""

    def produce() -> tuple[RetrievalPolicy, ...]:
        factory = proposer_factory or ModelAssistedPolicyProposer.from_environment
        proposer = factory()
        try:
            if observer is not None:
                proposer.observer = observer
            advice = proposer.propose(baseline, diagnostics, development_only=True)
            return tuple(item.policy for item in advice)
        finally:
            proposer.close()

    return produce


def _replay(
    memory: CodingMemory,
    baseline: RetrievalPolicy,
    candidate: RetrievalPolicy,
    cases: tuple[ReplayCase, ...],
) -> tuple[bool, float, float]:
    """执行一次配对回放并返回门禁结论与均值指标."""
    report = run_replay(
        memory,
        baseline,
        candidate,
        cases,
        top_k=TOP_K,
        max_chars=baseline.search.max_chars,
        max_tokens=baseline.search.max_tokens,
        development_only=True,
    )
    return gate(report), report.mean_recall_after, report.mean_ndcg_after


def _evaluate(
    memory: CodingMemory,
    name: str,
    baseline: RetrievalPolicy,
    cases: tuple[ReplayCase, ...],
    budget: int,
    produce: Callable[[], tuple[RetrievalPolicy, ...]],
) -> ProposerOutcome:
    """在不超过 budget 次回放的条件下评估一个提案器."""
    if not cases:
        return ProposerOutcome(name, "no_replay_case")
    try:
        candidates = tuple(produce())[:budget]
    except Exception as exc:
        return ProposerOutcome(name, "unavailable", error_type=type(exc).__name__)
    if not candidates:
        return ProposerOutcome(name, "no_candidate")
    accepted = 0
    best_recall: float | None = None
    best_ndcg: float | None = None
    replays = 0
    for candidate in candidates:
        passed, recall, ndcg = _replay(memory, baseline, candidate, cases)
        replays += 1
        accepted += int(passed)
        best_recall = recall if best_recall is None else max(best_recall, recall)
        best_ndcg = ndcg if best_ndcg is None else max(best_ndcg, ndcg)
    return ProposerOutcome(
        name,
        "accepted" if accepted else "rejected",
        candidates=len(candidates),
        replays=replays,
        accepted=accepted,
        best_mean_recall=best_recall,
        best_mean_ndcg=best_ndcg,
    )


def _winner(outcomes: tuple[ProposerOutcome, ...]) -> str:
    """只在存在通过门禁的候选时给出胜者; 平局保守判给确定性提案器."""
    passing = [item for item in outcomes if item.accepted]
    if not passing:
        return ""

    # 平局显式偏向确定性臂: 相同证据质量下优先无模型成本、无外部依赖的一方.
    def score(item: ProposerOutcome) -> tuple[float, float, int]:
        return (
            item.best_mean_ndcg or 0.0,
            item.best_mean_recall or 0.0,
            1 if item.name == "deterministic" else 0,
        )

    return max(passing, key=score).name


def compare_proposers(
    rsi: IntegratedRSI,
    scope: str,
    *,
    budget_replays: int = DEFAULT_BUDGET_REPLAYS,
    proposer_factory: Callable[[], Any] | None = None,
    observer: Callable[[dict[str, Any]], None] | None = None,
) -> MetaEvolutionReport:
    """用同一批回放样本与同等预算比较两个提案器, 不激活任何策略."""
    if type(budget_replays) is not int or budget_replays < len(PROPOSERS):
        raise OfflineRSIError("budget_replays must give every proposer at least one replay")
    per_proposer = budget_replays // len(PROPOSERS)
    store = RetrievalFeedbackStore(rsi.memory.store.path)
    try:
        baseline = rsi.policy(store, scope)
        diagnostics = tuple(item.to_diagnostic() for item in diagnose(store.diagnosis_rows(scope)))
        cases = store.cases(scope)
        outcomes = (
            _evaluate(
                rsi.memory,
                "deterministic",
                baseline,
                cases,
                per_proposer,
                lambda: propose_policy(baseline, diagnostics, development_only=True),
            ),
            _evaluate(
                rsi.memory,
                "model_assisted",
                baseline,
                cases,
                per_proposer,
                _model_candidates(proposer_factory, baseline, diagnostics, observer),
            ),
        )
        report = MetaEvolutionReport(
            scope, budget_replays, per_proposer, outcomes, _winner(outcomes)
        )
        store.event(scope, "meta_evolution", report.to_dict())
        return report
    finally:
        store.close()
