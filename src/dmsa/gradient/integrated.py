# ruff: noqa: D415, RUF002
"""记忆检索、反馈、模型提案、回放和策略激活的统一协调器。"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from typing import Any

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.model_advisor import ModelAssistedPolicyProposer
from dmsa.gradient.policy import OfflineRSIError, RetrievalPolicy, propose_policy
from dmsa.gradient.replay import gate, run_replay
from dmsa.gradient.replay_models import digest
from dmsa.memory import CodingMemory


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

    def search(self, query: str, scope: str, top_k: int) -> dict[str, Any]:
        """检索采用活动策略，返回可提交反馈的轨迹 ID。"""
        store = RetrievalFeedbackStore(self.memory.store.path)
        try:
            policy = self.policy(store, scope)
            hits = self.memory.search(query, scope, top_k, policy.search)
            trace = store.trace(
                scope, query, top_k, [h.id for h in hits], digest(asdict(policy.search))
            )
            return {"trace_id": trace, "data": [asdict(h) for h in hits]}
        finally:
            store.close()

    def evolve(self, scope: str) -> dict[str, Any]:
        """按真实反馈生成候选，经固定预算回放后原子激活。"""
        with self.lock:
            store = RetrievalFeedbackStore(self.memory.store.path)
            proposer = None
            try:
                baseline = self.policy(store, scope)
                diagnostics, cases = store.diagnostics(scope), store.cases(scope)
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
                    if accepted:
                        self._activate(store, scope, baseline, candidate, report.corpus_hash)
                        return {"status": "activated", "version": digest(asdict(candidate.search))}
                return {"status": "rejected"}
            except Exception as exc:
                store.event(scope, "evolution_failed", {"error_type": type(exc).__name__})
                raise OfflineRSIError("integrated RSI evolution failed") from exc
            finally:
                if proposer is not None:
                    proposer.close()
                store.close()

    def _activate(
        self,
        store: RetrievalFeedbackStore,
        scope: str,
        baseline: RetrievalPolicy,
        candidate: RetrievalPolicy,
        corpus_hash: str,
    ) -> None:
        """用写锁复核基线，避免旧回放覆盖并发新策略。"""
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            if self.policy(store, scope) != baseline:
                raise OfflineRSIError("stale baseline")
            if self.memory.corpus_state((scope,)).fingerprint != corpus_hash:
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
