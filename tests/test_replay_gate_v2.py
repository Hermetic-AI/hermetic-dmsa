"""多目标回放门禁与新增诊断种类的测试."""

from __future__ import annotations

from dataclasses import replace

from dmsa.gradient.policy import FailureDiagnostic, RetrievalPolicy, propose_policy
from dmsa.gradient.replay_gate import gate
from dmsa.gradient.replay_models import CaseResult, ReplayReport


def _report(before: CaseResult, after: CaseResult) -> ReplayReport:
    """构造绑定固定哈希的配对报告."""
    return ReplayReport("b", "c", "corpus", "cases", 5, 1000, 1000, (before,), (after,))


def test_case_result_ndcg_reflects_rank():
    top = CaseResult("c", ("y",), ("y",), "s")
    deep = CaseResult("c", ("x", "y"), ("y",), "s")
    assert top.ndcg == 1.0
    assert 0 < deep.ndcg < 1.0


def test_gate_accepts_recall_improvement():
    before = CaseResult("c", ("x",), ("y",), "s", 10, 5)
    after = CaseResult("c", ("y",), ("y",), "s", 12, 6)
    assert gate(_report(before, after))


def test_gate_rejects_cost_without_benefit():
    before = CaseResult("c", ("x",), ("y",), "s", 10, 5)
    after = CaseResult("c", ("x",), ("y",), "s", 30, 20)
    assert not gate(_report(before, after))


def test_gate_rejects_ranking_regression():
    before = CaseResult("c", ("y",), ("y",), "s", 10, 5)
    after = CaseResult("c", ("x", "y"), ("y",), "s", 20, 10)
    assert not gate(_report(before, after))


def test_gate_rejects_invalid_report_and_requires_improvement():
    before = CaseResult("c", ("x",), ("y",), "s", 10, 5)
    report = _report(before, before)
    assert not gate(report)
    assert not gate(replace(report, gate_version="offline-rsi-gate-v1"))


def test_proposer_supports_anchor_and_stale_diagnostics():
    policy = RetrievalPolicy()
    anchor = propose_policy(
        policy,
        (FailureDiagnostic("identifier_confusion", 2),),
        development_only=True,
    )
    assert anchor[0].search.anchor_weight > policy.search.anchor_weight
    stale = propose_policy(policy, (FailureDiagnostic("stale_evidence", 3),), development_only=True)
    assert stale[0].search.experience_weight < policy.search.experience_weight


def test_new_diagnostics_require_support_and_respect_counterexamples():
    policy = RetrievalPolicy()
    assert (
        propose_policy(
            policy, (FailureDiagnostic("identifier_confusion", 1),), development_only=True
        )
        == ()
    )
    assert (
        propose_policy(
            policy,
            (FailureDiagnostic("stale_evidence", 2, success_counterexamples=3),),
            development_only=True,
        )
        == ()
    )


def test_proposer_shrinks_chunk_when_evidence_is_coarse():
    policy = RetrievalPolicy()
    proposals = propose_policy(
        policy, (FailureDiagnostic("coarse_chunk", 2),), development_only=True
    )
    assert proposals[0].search.chunk_size == policy.search.chunk_size - 400
    floor = RetrievalPolicy(replace(policy.search, chunk_size=400))
    clamped = propose_policy(floor, (FailureDiagnostic("coarse_chunk", 2),), development_only=True)
    assert clamped[0].search.chunk_size == 400


def test_proposer_opens_relation_window_and_deepens_skeleton():
    policy = RetrievalPolicy()
    relation = propose_policy(
        policy, (FailureDiagnostic("missing_relation", 2),), development_only=True
    )
    assert relation[0].search.relation_window == 1
    structure = propose_policy(
        policy, (FailureDiagnostic("missing_structure", 2),), development_only=True
    )
    assert structure[0].search.skeleton_depth == policy.search.skeleton_depth + 2


def test_proposer_clamps_relation_and_skeleton_at_upper_bounds():
    relation = RetrievalPolicy(replace(RetrievalPolicy().search, relation_window=20))
    assert (
        propose_policy(
            relation, (FailureDiagnostic("missing_relation", 4),), development_only=True
        )[0].search.relation_window
        == 20
    )
    skeleton = RetrievalPolicy(replace(RetrievalPolicy().search, skeleton_depth=40))
    assert (
        propose_policy(
            skeleton, (FailureDiagnostic("missing_structure", 4),), development_only=True
        )[0].search.skeleton_depth
        == 40
    )


def test_proposer_ignores_unregistered_diagnostic_kinds():
    policy = RetrievalPolicy()
    assert (
        propose_policy(policy, (FailureDiagnostic("not_a_kind", 5),), development_only=True) == ()
    )
