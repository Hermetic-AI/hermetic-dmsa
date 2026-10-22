"""离线 RSI 策略与配对回放。"""

from dataclasses import replace

import pytest

from dmsa.gradient import (
    FailureDiagnostic,
    OfflineRSIError,
    PolicyRegistry,
    ReplayCase,
    RetrievalPolicy,
    gate,
    propose_policy,
    run_replay,
)
from dmsa.gradient.replay_models import CaseResult, ReplayReport
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def fixture_memory(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    memory.add(
        "r1",
        "u",
        "s1",
        [
            SourceMessage("assistant", "alpha anchor"),
            SourceMessage("assistant", "independent target evidence"),
        ],
    )
    memory.add("r2", "u", "s2", [SourceMessage("assistant", "beta noisy unrelated")])
    return memory


def test_replay_promotes_and_reloads_then_rolls_back(tmp_path):
    memory = fixture_memory(tmp_path)
    base = RetrievalPolicy(SearchPolicy(candidate_limit=1))
    candidate = RetrievalPolicy(SearchPolicy(candidate_limit=10))
    relevant = memory.search("alpha target", "u", 2, candidate.search)[1].id
    cases = (ReplayCase("c1", "u", "alpha target", (relevant,), "slice-a"),)
    report = run_replay(
        memory,
        base,
        candidate,
        cases,
        top_k=2,
        max_chars=12000,
        max_tokens=12000,
        development_only=True,
    )
    registry = PolicyRegistry(tmp_path / "registry.sqlite")
    version = registry.register(candidate, report, allow_bootstrap=True)
    assert gate(report)
    assert registry.active_version == version
    registry.close()
    reloaded = PolicyRegistry(tmp_path / "registry.sqlite")
    assert reloaded.get(version) == candidate
    reloaded.rollback(version)
    assert reloaded.history[-1] == ("rollback", version)
    reloaded.close()
    memory.close()


def test_replay_requires_dev_flag_and_rejects_no_change(tmp_path):
    memory = fixture_memory(tmp_path)
    policy = RetrievalPolicy()
    case = ReplayCase("c", "u", "alpha", ("missing-id",), "all")
    with pytest.raises(OfflineRSIError):
        run_replay(memory, policy, policy, (case,), top_k=1, max_chars=12000, max_tokens=12000)
    report = run_replay(
        memory,
        policy,
        policy,
        (case,),
        top_k=1,
        max_chars=12000,
        max_tokens=12000,
        development_only=True,
    )
    assert not gate(report)
    registry = PolicyRegistry(tmp_path / "registry.sqlite")
    registry.register(policy, report)
    assert registry.active_version is None
    registry.close()
    memory.close()


def test_budget_and_report_binding(tmp_path):
    memory = fixture_memory(tmp_path)
    policy = RetrievalPolicy()
    with pytest.raises(OfflineRSIError):
        run_replay(
            memory,
            policy,
            policy.changed(neighbor_window=2),
            (ReplayCase("c", "u", "alpha", (), "s"),),
            top_k=1,
            max_chars=1,
            max_tokens=12000,
            development_only=True,
        )
    registry = PolicyRegistry(tmp_path / "registry.sqlite")
    with pytest.raises(OfflineRSIError):
        registry.rollback("absent")
    registry.close()
    memory.close()


def test_promotion_rejects_stale_baseline_and_duplicate_cases(tmp_path):
    memory = fixture_memory(tmp_path)
    baseline = RetrievalPolicy(SearchPolicy(candidate_limit=1))
    candidate = RetrievalPolicy(SearchPolicy(candidate_limit=10))
    relevant = memory.search("alpha target", "u", 2, candidate.search)[1].id
    case = ReplayCase("case", "u", "alpha target", (relevant,), "slice")
    with pytest.raises(OfflineRSIError):
        run_replay(
            memory,
            baseline,
            candidate,
            (case, case),
            top_k=2,
            max_chars=12000,
            max_tokens=12000,
            development_only=True,
        )
    report = run_replay(
        memory,
        baseline,
        candidate,
        (case,),
        top_k=2,
        max_chars=12000,
        max_tokens=12000,
        development_only=True,
    )
    registry = PolicyRegistry(tmp_path / "registry.sqlite")
    registry.register(candidate, report, allow_bootstrap=True)
    assert registry.active_version is not None
    with pytest.raises(OfflineRSIError):
        registry.register(candidate, report)
    registry.close()
    memory.close()


def test_proposer_requires_support_and_lowers_redundancy_threshold():
    policy = RetrievalPolicy()
    assert propose_policy(
        policy, (FailureDiagnostic("exact_miss", 1),), development_only=True
    ) == ()
    proposals = propose_policy(
        policy,
        (FailureDiagnostic("redundancy", 2, success_counterexamples=1),),
        development_only=True,
    )
    assert proposals[0].search.redundancy_threshold < policy.search.redundancy_threshold


def test_proposer_rejects_counterexample_diagnostics():
    with pytest.raises(OfflineRSIError):
        propose_policy(RetrievalPolicy(), (FailureDiagnostic("bad", -1),), development_only=True)
    assert propose_policy(
        RetrievalPolicy(), (FailureDiagnostic("exact_miss", 2, success_counterexamples=3),),
        development_only=True,
    ) == ()


def test_fabricated_report_cannot_register(tmp_path):
    report = ReplayReport("b", "c", "corpus", "cases", 1, 10, 10,
                          (CaseResult("c", ("x",), ("x",), "s"),),
                          (CaseResult("c", ("x",), ("x",), "s"),))
    registry = PolicyRegistry(tmp_path / "registry.sqlite")
    with pytest.raises(OfflineRSIError):
        registry.register(RetrievalPolicy(), report)
    registry.close()


def test_tampered_report_is_rejected(tmp_path):
    memory = fixture_memory(tmp_path)
    baseline = RetrievalPolicy(SearchPolicy(candidate_limit=1))
    candidate = RetrievalPolicy(SearchPolicy(candidate_limit=10))
    relevant = memory.search("alpha target", "u", 2, candidate.search)[1].id
    report = run_replay(memory, baseline, candidate,
                        (ReplayCase("c", "u", "alpha target", (relevant,), "s"),),
                        top_k=2, max_chars=12000, max_tokens=12000, development_only=True)
    tampered = replace(report, corpus_hash="changed")
    registry = PolicyRegistry(tmp_path / "registry.sqlite")
    with pytest.raises(OfflineRSIError):
        registry.promote_replay(candidate, tampered)
    registry.close()
    memory.close()


def test_gate_rejects_mismatched_slices_and_nan_budget():
    result = CaseResult("c", ("x",), ("x",), "s")
    report = ReplayReport("b", "c", "corpus", "cases", 1, 10, 10, (result,),
                          (replace(result, slice="other"),))
    assert not gate(report)
    assert not gate(replace(report, max_chars=True))


def test_replay_detects_corpus_mutation(tmp_path, monkeypatch):
    memory = fixture_memory(tmp_path)
    original_search = memory.search
    mutated = False

    def mutating_search(query, user_id, top_k, policy=None):
        nonlocal mutated
        hits = original_search(query, user_id, top_k, policy)
        if not mutated:
            mutated = True
            memory.add("mutation", user_id, "new-session", [SourceMessage("assistant", "changed")])
        return hits

    monkeypatch.setattr(memory, "search", mutating_search)
    with pytest.raises(OfflineRSIError, match="corpus changed"):
        run_replay(memory, RetrievalPolicy(), RetrievalPolicy(),
                   (ReplayCase("c", "u", "alpha", ("missing",), "s"),),
                   top_k=1, max_chars=12000, max_tokens=12000, development_only=True)
    memory.close()


def test_replay_rejects_non_integer_budget(tmp_path):
    memory = fixture_memory(tmp_path)
    case = ReplayCase("c", "u", "alpha", ("missing",), "s")
    with pytest.raises(OfflineRSIError, match="positive integers"):
        run_replay(memory, RetrievalPolicy(), RetrievalPolicy(), (case,),
                   top_k=1, max_chars=True, max_tokens=12000, development_only=True)
    memory.close()
