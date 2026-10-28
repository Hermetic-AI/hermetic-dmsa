"""留出评测的训练侧逻辑: 语料写入、检索指标、诊断派生与有门禁的候选产生."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from benchmarks.coding.dataset import LEVEL_COUNTS, ndcg
from benchmarks.coding.holdout_tasks import (
    NOISE_LEVEL,
    TOP_K,
    HoldoutTask,
    scope_of,
    select_noise,
)
from dmsa.gradient.policy import FailureDiagnostic, RetrievalPolicy, propose_policy
from dmsa.gradient.replay import gate, run_replay
from dmsa.gradient.replay_models import ReplayCase
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def populate(memory: CodingMemory, tasks: Sequence[HoldoutTask], seed: int) -> dict[str, set[str]]:
    """写入每个任务的独立 scope, 返回 gold 切片 ID."""
    gold: dict[str, set[str]] = {}
    count = LEVEL_COUNTS[NOISE_LEVEL]
    for task in tasks:
        scope = scope_of(task.task_id)
        messages = [SourceMessage("assistant", task.gold)] + [
            SourceMessage("tool", text) for text in select_noise(task, seed, count)
        ]
        memory.add(task.task_id, scope, task.task_id, messages)
        all_ids = tuple(str(row["id"]) for row in memory.store.rows(scope))
        gold[task.task_id] = {
            str(row["id"])
            for row in memory.store.rows_by_ids(scope, all_ids)
            if int(row["message_index"]) == 0
        }
    return gold


def metrics(
    memory: CodingMemory,
    gold: dict[str, set[str]],
    tasks: Sequence[HoldoutTask],
    policy: SearchPolicy,
    top_k: int = TOP_K,
) -> dict[str, Any] | None:
    """统计一个切分的检索指标; 空切分如实返回 None."""
    if not tasks:
        return None
    recalls: list[float] = []
    scores: list[float] = []
    misleading: list[float] = []
    chars: list[int] = []
    for task in tasks:
        hits = memory.search(task.query, scope_of(task.task_id), top_k, policy)
        ranked = [hit.id for hit in hits]
        relevant = gold[task.task_id]
        recalls.append(1.0 if relevant & set(ranked) else 0.0)
        scores.append(ndcg(ranked, relevant))
        misleading.append(1.0 if ranked and not relevant & set(ranked) else 0.0)
        chars.append(sum(len(hit.content) for hit in hits))
    return {
        "task_count": len(tasks),
        "evidence_recall": sum(recalls) / len(recalls),
        "ndcg": sum(scores) / len(scores),
        "misleading_memory_rate": sum(misleading) / len(misleading),
        "evidence_chars": sum(chars) / len(chars),
    }


def derive_diagnostics(
    memory: CodingMemory,
    gold: dict[str, set[str]],
    tasks: Sequence[HoldoutTask],
    policy: SearchPolicy,
    top_k: int = TOP_K,
) -> tuple[FailureDiagnostic, ...]:
    """只用训练切分的已知 gold 标注产生失败诊断, 不窥探其它切分."""
    missed = 0
    misranked = 0
    for task in tasks:
        ranked = [
            hit.id for hit in memory.search(task.query, scope_of(task.task_id), top_k, policy)
        ]
        relevant = gold[task.task_id]
        if not relevant & set(ranked):
            missed += 1
        elif ranked and ranked[0] not in relevant:
            misranked += 1
    diagnostics = []
    if missed:
        diagnostics.append(FailureDiagnostic("exact_miss", missed))
    if misranked:
        diagnostics.append(FailureDiagnostic("identifier_confusion", misranked))
    return tuple(diagnostics)


def train_cases(tasks: Sequence[HoldoutTask], gold: dict[str, set[str]]) -> tuple[ReplayCase, ...]:
    """把训练切分转成回放样本."""
    return tuple(
        ReplayCase(
            case_id=task.task_id,
            user_id=scope_of(task.task_id),
            query=task.query,
            relevant_ids=tuple(sorted(gold[task.task_id])),
            slice=task.family,
        )
        for task in tasks
        if gold[task.task_id]
    )


def evolve(
    memory: CodingMemory, tasks: Sequence[HoldoutTask], gold: dict[str, set[str]]
) -> dict[str, Any]:
    """在训练切分上产生候选并用固定门禁裁决; 不通过则保留基线."""
    baseline = RetrievalPolicy(memory.policy)
    diagnostics = derive_diagnostics(memory, gold, tasks, baseline.search)
    candidates = propose_policy(baseline, diagnostics, development_only=True)
    cases = train_cases(tasks, gold)
    base = asdict(baseline.search)
    if not candidates:
        # 诊断存在但支持度不足(同类失败少于 2 次)时, 确定性提案器不会给出改动
        return {
            "status": "insufficient_support" if diagnostics else "no_signal",
            "diagnostics": [asdict(item) for item in diagnostics],
            "baseline": base,
            "candidate": base,
        }
    if not cases:
        return {
            "status": "no_replay_case",
            "diagnostics": [asdict(item) for item in diagnostics],
            "baseline": base,
            "candidate": base,
        }
    for candidate in candidates[:3]:
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
        if gate(report):
            return {
                "status": "accepted_on_train",
                "diagnostics": [asdict(item) for item in diagnostics],
                "baseline": asdict(baseline.search),
                "candidate": asdict(candidate.search),
                "train_replay": {
                    "mean_recall_before": report.mean_recall_before,
                    "mean_recall_after": report.mean_recall_after,
                    "mean_ndcg_before": report.mean_ndcg_before,
                    "mean_ndcg_after": report.mean_ndcg_after,
                },
            }
    return {
        "status": "rejected_on_train",
        "diagnostics": [asdict(item) for item in diagnostics],
        "baseline": asdict(baseline.search),
        "candidate": asdict(baseline.search),
    }
