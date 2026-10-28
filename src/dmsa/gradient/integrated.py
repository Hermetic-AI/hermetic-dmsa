"""记忆检索、反馈、模型提案、回放和策略激活的统一协调器。"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, replace
from typing import Any

from dmsa.gradient.attribution import attribute_trace_task
from dmsa.gradient.diagnosis import diagnose
from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.model_advisor import ModelAssistedPolicyProposer
from dmsa.gradient.policy import OfflineRSIError, RetrievalPolicy, propose_policy
from dmsa.gradient.replay import gate, run_replay
from dmsa.gradient.replay_models import digest
from dmsa.gradient.task_gate import TaskGateReport, run_task_gate, task_cases
from dmsa.gradient.task_results import TaskResult, TaskResultStore
from dmsa.memory import CodingMemory, scope_key


class IntegratedRSI:
    """按 scope 隔离活动策略，只激活经过真实回放门禁的候选。"""

    def __init__(self, memory: CodingMemory, *, models: bool = False) -> None:
        """绑定记忆与显式模型开关。"""
        self.memory = memory
        if memory.store.path == ":memory:":
            raise OfflineRSIError("integrated RSI requires persistent SQLite storage")
        self.models = models
        self.lock = threading.RLock()

    def policy(self, store: RetrievalFeedbackStore, scope: str) -> RetrievalPolicy:
        """读取持久化活动策略，首次使用默认策略。"""
        row = store.db.execute(
            "SELECT v.policy FROM rsi_active a JOIN rsi_versions v "
            "ON a.scope=v.scope AND a.version=v.version WHERE a.scope=?",
            (scope,),
        ).fetchone()
        return (
            RetrievalPolicy.from_dict(json.loads(row[0]))
            if row
            else RetrievalPolicy(self.memory.policy)
        )

    def search(
        self,
        query: str,
        scope: str,
        top_k: int,
        task_id: str | None = None,
        *,
        dense: bool = True,
        repo: str = "",
    ) -> dict[str, Any]:
        """检索采用活动策略，返回可提交反馈的轨迹 ID。"""
        store = RetrievalFeedbackStore(self.memory.store.path)
        try:
            policy = self.policy(store, scope)
            effective = policy.search if dense else replace(policy.search, dense_weight=0.0)
            hits = self.memory.search(query, scope, top_k, effective, repo=repo)
            corpus_hash = self.memory.corpus_state((scope_key(scope, repo),)).fingerprint
            trace = store.trace(
                scope,
                query,
                top_k,
                [h.id for h in hits],
                digest(asdict(policy.search)),
                task_id=task_id,
                corpus_hash=corpus_hash,
                repo=repo,
            )
            return {"trace_id": trace, "repo": repo, "data": [asdict(h) for h in hits]}
        finally:
            store.close()

    def record_task_result(
        self,
        scope: str,
        task_id: str,
        trace_id: str,
        outcome: str,
        *,
        tests_passed: int = 0,
        files_changed: tuple[str, ...] = (),
        tokens: int = 0,
        latency_ms: int = 0,
        detail: str = "",
    ) -> dict[str, Any]:
        """记录真实任务结果; 与检索反馈分开存放, 不参与根因推断."""
        store = TaskResultStore(self.memory.store.path)
        try:
            result = TaskResult(
                task_id=task_id,
                scope=scope,
                trace_id=trace_id,
                outcome=outcome,
                tests_passed=tests_passed,
                files_changed=files_changed,
                tokens=tokens,
                latency_ms=latency_ms,
                detail=detail,
            )
            stored = store.record(result)
            attribution = attribute_trace_task(store.db, scope, trace_id, outcome) if stored else {}
            return {
                "stored": stored,
                "scope": scope,
                "task_id": task_id,
                "attribution": attribution,
            }
        finally:
            store.close()

    def task_results(self, scope: str) -> dict[str, Any]:
        """读取 scope 内已记录的任务结果与成功失败计数."""
        store = TaskResultStore(self.memory.store.path)
        try:
            results = store.list(scope)
            return {
                "results": [item.to_dict() for item in results],
                "summary": store.summary(scope),
            }
        finally:
            store.close()

    def evolve(self, scope: str) -> dict[str, Any]:
        """按真实反馈生成候选，经固定预算回放后原子激活。"""
        with self.lock:
            store = RetrievalFeedbackStore(self.memory.store.path)
            proposer = None
            try:
                baseline = self.policy(store, scope)
                diagnoses = diagnose(store.diagnosis_rows(scope))
                diagnostics = tuple(item.to_diagnostic() for item in diagnoses)
                cases = store.cases(scope)
                candidates = propose_policy(baseline, diagnostics, development_only=True)
                if not cases or not candidates:
                    result: dict[str, Any] = {"status": "insufficient_feedback"}
                    store.event(scope, "waiting_for_labels", result)
                    return result
                store.event(scope, "evolution_started", {"models": self.models})
                if self.models:
                    try:
                        proposer = ModelAssistedPolicyProposer.from_environment()
                        proposer.observer = lambda payload: store.event(
                            scope, "model_call", payload
                        )
                        advice = proposer.propose(baseline, diagnostics, development_only=True)
                        candidates = tuple(item.policy for item in advice)
                        store.event(
                            scope,
                            "model_proposal",
                            {
                                "decision_model": proposer.decision_model,
                                "text_model": proposer.text_model,
                                "proposals": [
                                    {"policy": asdict(a.policy.search), "rationale": a.rationale}
                                    for a in advice
                                ],
                            },
                        )
                    except OfflineRSIError as exc:
                        store.event(
                            scope,
                            "model_fallback",
                            {"error_type": type(exc).__name__, "mode": "deterministic_candidates"},
                        )
                for candidate in candidates[:3]:
                    report = run_replay(
                        self.memory,
                        baseline,
                        candidate,
                        cases,
                        top_k=5,
                        max_chars=baseline.search.max_chars,
                        max_tokens=baseline.search.max_tokens,
                        development_only=True,
                    )
                    accepted = gate(report)
                    store.event(
                        scope,
                        "replay_passed" if accepted else "replay_rejected",
                        {"report": report.to_dict(), "hash": report.report_hash},
                    )
                    if not accepted:
                        continue
                    task_report = self._task_gate(store, scope, baseline, candidate)
                    store.event(
                        scope,
                        "task_gate_passed" if task_report.passed else "task_gate_rejected",
                        task_report.to_dict(),
                    )
                    if not task_report.passed:
                        continue
                    self._activate(
                        store,
                        scope,
                        baseline,
                        candidate,
                        report.corpus_hash,
                        partitions=tuple(sorted({case.user_id for case in cases})),
                    )
                    return {"status": "activated", "version": digest(asdict(candidate.search))}
                return {"status": "rejected"}
            except Exception as exc:
                store.event(scope, "evolution_failed", {"error_type": type(exc).__name__})
                raise OfflineRSIError("integrated RSI evolution failed") from exc
            finally:
                if proposer is not None:
                    proposer.close()
                store.close()

    def _task_gate(
        self,
        store: RetrievalFeedbackStore,
        scope: str,
        baseline: RetrievalPolicy,
        candidate: RetrievalPolicy,
    ) -> TaskGateReport:
        """用已记录的任务结果与已确认证据复核候选策略."""
        traces = store.traces(scope)
        labels = store.labels(scope)
        outcomes = {item.task_id: item.outcome for item in self._task_results(scope)}
        cases = task_cases(scope, traces, labels, outcomes)
        if not cases:
            return TaskGateReport(0, 0, 0, (), ())
        return run_task_gate(self.memory, baseline, candidate, cases)

    def _task_results(self, scope: str) -> tuple[TaskResult, ...]:
        """读取 scope 内已记录的任务结果."""
        store = TaskResultStore(self.memory.store.path)
        try:
            return store.list(scope)
        finally:
            store.close()

    def _activate(
        self,
        store: RetrievalFeedbackStore,
        scope: str,
        baseline: RetrievalPolicy,
        candidate: RetrievalPolicy,
        corpus_hash: str,
        *,
        partitions: tuple[str, ...] = (),
    ) -> None:
        """用写锁复核基线，避免旧回放覆盖并发新策略。

        `partitions` 是回放样本覆盖的完整分区键（可能含仓库子域）；缺省时退回
        scope 本身，与单仓库场景保持一致。
        """
        observed = partitions or (scope,)
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            if self.policy(store, scope) != baseline:
                raise OfflineRSIError("stale baseline")
            if self.memory.corpus_state(observed).fingerprint != corpus_hash:
                raise OfflineRSIError("corpus changed before activation")
            for policy in (baseline, candidate):
                body = asdict(policy.search)
                store.db.execute(
                    "INSERT OR IGNORE INTO rsi_versions VALUES(?,?,?)",
                    (scope, digest(body), json.dumps(body)),
                )
            store.db.execute(
                "INSERT OR REPLACE INTO rsi_active VALUES(?,?)",
                (scope, digest(asdict(candidate.search))),
            )
        store.event(scope, "activated", {"version": digest(asdict(candidate.search))})

    def rollback(self, scope: str, version: str) -> None:
        """仅允许回滚至当前 scope 已存储的基线或通过策略。"""
        with self.lock:
            store = RetrievalFeedbackStore(self.memory.store.path)
            try:
                with store.db:
                    if not store.db.execute(
                        "SELECT 1 FROM rsi_versions WHERE scope=? AND version=?", (scope, version)
                    ).fetchone():
                        raise OfflineRSIError("unknown scoped version")
                    store.db.execute(
                        "INSERT OR REPLACE INTO rsi_active VALUES(?,?)", (scope, version)
                    )
                store.event(scope, "rollback", {"version": version})
            finally:
                store.close()
