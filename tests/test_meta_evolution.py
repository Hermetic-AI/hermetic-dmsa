"""外层 RSI 提案器比较的行为与边界测试."""

from __future__ import annotations

import pytest

from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.meta_evolution import PROPOSERS, compare_proposers
from dmsa.gradient.model_advisor import PolicyAdvice
from dmsa.gradient.policy import OfflineRSIError, RetrievalPolicy
from dmsa.inspect.rsi_data import read_rsi
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def build(tmp_path):
    """构造带两条 missing_context 失败反馈的最小记忆与 RSI."""
    memory = CodingMemory(tmp_path / "meta.sqlite", policy=SearchPolicy(neighbor_window=0))
    memory.add(
        "r", "u", "s", [SourceMessage("assistant", "anchor " + "a " * 1000 + "target evidence")]
    )
    rsi = IntegratedRSI(memory)
    relevant = memory.search("target evidence", "u", 5)[0].id
    store = RetrievalFeedbackStore(memory.store.path)
    for _ in range(2):
        store.feedback(
            "u", rsi.search("anchor", "u", 5)["trace_id"], "failure", "missing_context", [relevant]
        )
    store.close()
    return memory, rsi


class FakeProposer:
    """只提供候选策略, 不接触真实模型的替身提案器."""

    decision_model = "test-decision"
    text_model = "test-text"

    def __init__(self, policies):
        """记录固定候选并统计生命周期调用."""
        self.policies = tuple(policies)
        self.observer = None
        self.closed = False

    def propose(self, baseline, diagnostics, **kwargs):
        """把固定候选包装成 PolicyAdvice."""
        return tuple(
            PolicyAdvice(index, policy, "test rationale")
            for index, policy in enumerate(self.policies)
        )

    def close(self):
        """标记关闭以便断言资源释放."""
        self.closed = True


def test_both_arms_are_evaluated_and_only_audit_event_is_written(tmp_path):
    memory, rsi = build(tmp_path)
    baseline = RetrievalPolicy(memory.policy)
    created: list[FakeProposer] = []

    def factory():
        proposer = FakeProposer([baseline.changed(neighbor_window=1)])
        created.append(proposer)
        return proposer

    report = compare_proposers(rsi, "u", budget_replays=4, proposer_factory=factory)
    outcomes = {item.name: item for item in report.outcomes}
    assert set(outcomes) == set(PROPOSERS)
    assert report.per_proposer_replays == 2
    for outcome in outcomes.values():
        assert outcome.status in {"accepted", "rejected"}
        assert outcome.candidates == 1
        assert outcome.replays == 1
        assert outcome.best_mean_ndcg is not None
    assert created
    assert created[0].closed is True
    # 外层比较只留证据, 绝不激活策略.
    assert read_rsi(tmp_path / "meta.sqlite")["active"] == []
    events = read_rsi(tmp_path / "meta.sqlite")["events"]
    assert [e["kind"] for e in events].count("meta_evolution") == 1
    assert "activated" not in {e["kind"] for e in events}
    memory.close()


def test_equal_budget_caps_replays_per_proposer(tmp_path):
    memory, rsi = build(tmp_path)
    baseline = RetrievalPolicy(memory.policy)
    many = [baseline.changed(neighbor_window=value) for value in (1, 2, 3, 4, 5, 6, 7, 8)]
    report = compare_proposers(
        rsi, "u", budget_replays=4, proposer_factory=lambda: FakeProposer(many)
    )
    model_arm = next(item for item in report.outcomes if item.name == "model_assisted")
    assert model_arm.candidates == 2
    assert model_arm.replays == 2
    assert all(item.replays <= report.per_proposer_replays for item in report.outcomes)
    memory.close()


def test_model_outage_is_recorded_without_breaking_the_comparison(tmp_path):
    memory, rsi = build(tmp_path)

    def unavailable():
        raise OfflineRSIError("provider unavailable")

    report = compare_proposers(rsi, "u", budget_replays=4, proposer_factory=unavailable)
    outcomes = {item.name: item for item in report.outcomes}
    assert outcomes["model_assisted"].status == "unavailable"
    assert outcomes["model_assisted"].error_type == "OfflineRSIError"
    assert outcomes["model_assisted"].replays == 0
    assert outcomes["deterministic"].status in {"accepted", "rejected", "no_candidate"}
    assert read_rsi(tmp_path / "meta.sqlite")["active"] == []
    memory.close()


def test_winner_requires_a_gate_passing_candidate(tmp_path):
    memory, rsi = build(tmp_path)
    baseline = RetrievalPolicy(memory.policy)
    report = compare_proposers(
        rsi, "u", budget_replays=4, proposer_factory=lambda: FakeProposer([baseline])
    )
    # 候选与基线完全相同 -> 无改善, 门禁拒绝, 不允许宣称胜者.
    model_arm = next(item for item in report.outcomes if item.name == "model_assisted")
    assert model_arm.status == "rejected"
    assert model_arm.accepted == 0
    if not any(item.accepted for item in report.outcomes):
        assert report.winner == ""
    memory.close()


def test_tie_is_awarded_to_the_deterministic_arm(tmp_path):
    memory, rsi = build(tmp_path)
    baseline = RetrievalPolicy(memory.policy)
    report = compare_proposers(
        rsi,
        "u",
        budget_replays=4,
        proposer_factory=lambda: FakeProposer([baseline.changed(neighbor_window=1)]),
    )
    outcomes = {item.name: item for item in report.outcomes}
    assert outcomes["deterministic"].best_mean_ndcg == outcomes["model_assisted"].best_mean_ndcg
    assert outcomes["deterministic"].accepted
    assert outcomes["model_assisted"].accepted
    # 证据质量相同时保守选择无模型成本的一方.
    assert report.winner == "deterministic"
    memory.close()


def test_without_feedback_no_arm_runs_a_replay(tmp_path):
    memory = CodingMemory(tmp_path / "empty.sqlite", policy=SearchPolicy(neighbor_window=0))
    memory.add("r", "u", "s", [SourceMessage("assistant", "anchor evidence")])
    rsi = IntegratedRSI(memory)
    report = compare_proposers(
        rsi, "u", budget_replays=4, proposer_factory=lambda: FakeProposer([])
    )
    assert {item.status for item in report.outcomes} == {"no_replay_case"}
    assert report.winner == ""
    assert all(item.replays == 0 for item in report.outcomes)
    memory.close()


def test_budget_must_cover_every_proposer(tmp_path):
    memory, rsi = build(tmp_path)
    with pytest.raises(OfflineRSIError):
        compare_proposers(rsi, "u", budget_replays=1)
    with pytest.raises(OfflineRSIError):
        compare_proposers(rsi, "u", budget_replays=2.0)
    memory.close()
