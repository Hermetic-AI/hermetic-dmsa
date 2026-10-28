"""端到端有效性度量的行为测试."""

from __future__ import annotations

import pytest

from dmsa.gradient.effectiveness import measure_effectiveness
from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.gradient.policy import OfflineRSIError
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def _memory(tmp_path):
    """构造含两条经验的 coding memory."""
    memory = CodingMemory(tmp_path / "eff.sqlite", policy=SearchPolicy(neighbor_window=0))
    memory.add("r1", "u", "s1", [SourceMessage("assistant", "def handler(): return 1")])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", "def other(): return 2")])
    return memory


def test_measure_rejects_empty_scope(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        with pytest.raises(OfflineRSIError):
            measure_effectiveness(memory.store.path, " ")
    finally:
        memory.close()


def test_empty_scope_reports_null_metrics(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        report = measure_effectiveness(memory.store.path, "empty")
        assert report.label_recall is None
        assert report.hit_precision is None
        assert report.task_total == 0
        assert report.delivered_pass_rate is None
        assert report.undelivered_pass_rate is None
    finally:
        memory.close()


def test_label_recall_measures_confirmed_evidence_in_hits(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        payload = rsi.search("handler", "u", 5)
        hits = [hit["id"] for hit in payload["data"]]
        assert hits
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            store.feedback_with_attribution(
                "u", payload["trace_id"], "success", "unknown", hits[:1]
            )
        finally:
            store.close()
        report = measure_effectiveness(memory.store.path, "u")
        assert report.labeled_traces == 1
        assert report.label_recall == pytest.approx(1.0)
        assert report.hit_precision is not None
    finally:
        memory.close()


def test_task_results_split_into_delivered_and_undelivered(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        first = rsi.search("handler", "u", 5, task_id="t-1")
        hits = [hit["id"] for hit in first["data"]]
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            store.feedback_with_attribution("u", first["trace_id"], "success", "unknown", hits[:1])
        finally:
            store.close()
        rsi.record_task_result("u", "t-1", first["trace_id"], "success")
        second = rsi.search("other", "u", 5, task_id="t-2")
        rsi.record_task_result("u", "t-2", second["trace_id"], "failure")
        report = measure_effectiveness(memory.store.path, "u")
        assert report.task_total == 2
        assert report.delivered_total == 1
        assert report.delivered_success == 1
        assert report.delivered_pass_rate == pytest.approx(1.0)
        assert report.unlabeled_total == 1
        assert report.undelivered_total == 0
    finally:
        memory.close()


def test_report_states_observational_limitation(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        report = measure_effectiveness(memory.store.path, "u")
        assert "非受控 A/B" in report.observation
        assert "观察性对比" in report.to_dict()["observation"]
    finally:
        memory.close()
