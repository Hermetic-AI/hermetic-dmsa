"""代码锚点通道消融评测的可复现性测试."""

from __future__ import annotations

from benchmarks.coding.ablation import ARMS, TOP_K, run_ablation


def test_ablation_reports_both_arms():
    result = run_ablation()
    assert set(result["arms"]) == set(ARMS)
    assert result["top_k"] == TOP_K
    for arm in result["arms"].values():
        assert 0.0 <= arm["evidence_recall"] <= 1.0
        assert 0.0 <= arm["ndcg"] <= 1.0
        assert arm["evidence_chars"] > 0


def test_anchor_channel_does_not_reduce_ordering_quality():
    result = run_ablation()
    baseline = result["arms"]["no_anchor"]
    anchored = result["arms"]["with_anchor"]
    assert anchored["ndcg"] >= baseline["ndcg"]
    assert anchored["evidence_recall"] >= baseline["evidence_recall"]
