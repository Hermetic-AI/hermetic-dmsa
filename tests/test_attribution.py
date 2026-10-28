"""条目级因果归因的行为测试."""

from __future__ import annotations

import sqlite3

import pytest

from dmsa.gradient.attribution import (
    Attribution,
    attribute_feedback,
    attribute_task,
    attribute_trace_feedback,
    persist_attribution,
)
from dmsa.gradient.feedback import RetrievalFeedbackStore
from dmsa.gradient.integrated import IntegratedRSI
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def _memory(tmp_path):
    """构造含两条经验的 coding memory."""
    memory = CodingMemory(tmp_path / "attr.sqlite", policy=SearchPolicy(neighbor_window=0))
    memory.add("r1", "u", "s1", [SourceMessage("assistant", "def handler(): return 1")])
    memory.add("r2", "u", "s2", [SourceMessage("assistant", "def other(): return 2")])
    return memory


def _stats(memory: CodingMemory) -> dict[str, dict]:
    """读取 scope 内条目统计."""
    return {item["chunk_id"]: item for item in memory.governance.trust_report("u")}


def test_feedback_confirms_named_evidence_only() -> None:
    attribution = attribute_feedback("u#", ("a", "b"), ("a",), "success", "unknown")
    assert attribution.confirmed == ("a",)
    assert attribution.contradicted == ()
    assert not attribution.empty


def test_stale_evidence_failure_contradicts_unnamed_recall() -> None:
    attribution = attribute_feedback("u#", ("a", "b"), ("a",), "failure", "stale_evidence")
    assert attribution.confirmed == ("a",)
    assert attribution.contradicted == ("b",)


@pytest.mark.parametrize("kind", ["exact_miss", "missing_context", "missing_relation", "unknown"])
def test_insufficient_evidence_failure_does_not_contradict(kind: str) -> None:
    """证据不足类失败不构成对召回条目的反驳."""
    attribution = attribute_feedback("u#", ("a", "b"), (), "failure", kind)
    assert attribution.contradicted == ()
    assert attribution.confirmed == ()


def test_task_success_verifies_and_task_failure_is_silent() -> None:
    verified = attribute_task("u#", ("a", "b"), "success")
    assert verified.verified == ("a", "b")
    silent = attribute_task("u#", ("a", "b"), "failure")
    assert silent.empty


def test_persist_attribution_writes_counters_and_skips_empty() -> None:
    db = sqlite3.connect(":memory:")
    try:
        empty = Attribution(partition="u#")
        assert empty.empty
        assert persist_attribution(db, empty) == 0
        applied = persist_attribution(
            db, Attribution(partition="u#", confirmed=("a",), contradicted=("b",))
        )
        assert applied == 2
        rows = {
            str(row[0]): (int(row[1]), int(row[2]))
            for row in db.execute(
                "SELECT chunk_id, confirmed, contradicted FROM memory_stats WHERE user_id='u#'"
            )
        }
        assert rows == {"a": (1, 0), "b": (0, 1)}
    finally:
        db.close()


def test_feedback_updates_item_statistics(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        payload = rsi.search("handler", "u", 5)
        trace_id = payload["trace_id"]
        hits = [hit["id"] for hit in payload["data"]]
        assert hits
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            saved, summary = store.feedback_with_attribution(
                "u", trace_id, "success", "unknown", [hits[0]]
            )
        finally:
            store.close()
        assert saved is True
        assert summary["status"] == "applied"
        assert summary["confirmed"] == [hits[0]]
        stats = _stats(memory)
        assert stats[hits[0]]["confirmed"] == 1
        assert stats[hits[0]]["contradicted"] == 0
    finally:
        memory.close()


def test_duplicate_feedback_does_not_double_count(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        payload = rsi.search("handler", "u", 5)
        trace_id = payload["trace_id"]
        hits = [hit["id"] for hit in payload["data"]]
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            store.feedback_with_attribution("u", trace_id, "success", "unknown", [hits[0]])
            saved, summary = store.feedback_with_attribution(
                "u", trace_id, "success", "unknown", [hits[0]]
            )
        finally:
            store.close()
        assert saved is False
        assert summary["status"] == "duplicate"
        assert _stats(memory)[hits[0]]["confirmed"] == 1
    finally:
        memory.close()


def test_task_success_marks_recalled_items_verified(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        payload = rsi.search("handler", "u", 5, task_id="task-1")
        hits = [hit["id"] for hit in payload["data"]]
        result = rsi.record_task_result("u", "task-1", payload["trace_id"], "success")
        assert result["attribution"]["status"] == "applied"
        assert _stats(memory)[hits[0]]["verified"] == 1
    finally:
        memory.close()


def test_task_failure_does_not_blame_recalled_items(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        rsi = IntegratedRSI(memory)
        payload = rsi.search("handler", "u", 5, task_id="task-2")
        hits = [hit["id"] for hit in payload["data"]]
        rsi.record_task_result("u", "task-2", payload["trace_id"], "failure")
        stats = _stats(memory)
        assert stats[hits[0]]["verified"] == 0
        assert stats[hits[0]]["contradicted"] == 0
    finally:
        memory.close()


def test_attribution_without_trace_reports_no_recall(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        store = RetrievalFeedbackStore(memory.store.path)
        try:
            summary = attribute_trace_feedback(
                store.db, "u", "missing-trace", "success", "unknown", []
            )
        finally:
            store.close()
        assert summary["status"] == "no_recall"
    finally:
        memory.close()
