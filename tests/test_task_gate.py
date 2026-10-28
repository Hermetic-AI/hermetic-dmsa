"""任务级门禁与切片感知诊断的测试."""

from __future__ import annotations

import pytest

from dmsa.gradient.diagnosis import diagnose
from dmsa.gradient.policy import OfflineRSIError
from dmsa.gradient.task_gate import TaskCase, TaskGateReport, run_task_gate, task_cases
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def _memory(tmp_path) -> CodingMemory:
    memory = CodingMemory(tmp_path / "gate.sqlite")
    memory.add(
        "r1",
        "u",
        "s1",
        [
            SourceMessage("assistant", "def handler(): return 1"),
            SourceMessage("assistant", "unrelated documentation about logging"),
        ],
    )
    return memory


def test_task_case_requires_confirmed_evidence():
    with pytest.raises(OfflineRSIError):
        TaskCase(task_id="t", scope="u", query="q", relevant_ids=(), outcome="success")
    with pytest.raises(OfflineRSIError):
        TaskCase(task_id="t", scope="u", query="q", relevant_ids=("a",), outcome="maybe")


def test_task_cases_require_task_outcome_and_labels():
    traces = (
        {"trace_id": "tr1", "query": "handler", "task_id": "task-1"},
        {"trace_id": "tr2", "query": "handler", "task_id": ""},
    )
    cases = task_cases("u", traces, {"tr1": ("chunk-1",)}, {"task-1": "success"})
    assert len(cases) == 1
    assert cases[0].relevant_ids == ("chunk-1",)
    assert task_cases("u", traces, {}, {"task-1": "success"}) == ()


def test_task_gate_blocks_candidate_that_loses_confirmed_evidence(tmp_path):
    memory = _memory(tmp_path)
    try:
        relevant = memory.search("handler", "u", 5)[0].id
        case = TaskCase(
            task_id="task-1",
            scope="u",
            query="handler",
            relevant_ids=(relevant,),
            outcome="success",
        )
        report = run_task_gate(
            memory,
            baseline=_policy(anchor_weight=0.0),
            candidate=_policy(anchor_weight=0.0, min_evidence=0),
            cases=(case,),
        )
        assert isinstance(report, TaskGateReport)
        assert report.checked == 1
    finally:
        memory.close()


def _policy(**values) -> object:
    """构造检索策略包装."""
    from dmsa.gradient.policy import RetrievalPolicy

    return RetrievalPolicy(SearchPolicy(**values))


def test_diagnosis_requires_cross_slice_support():
    single = diagnose((("exact_miss", "failure", "task-1"),) * 2)
    assert single == ()
    cross = diagnose(
        (
            ("exact_miss", "failure", "task-1"),
            ("exact_miss", "failure", "task-2"),
        )
    )
    assert cross[0].kind == "exact_miss"
    assert cross[0].slices == 2
    assert cross[0].to_diagnostic().failures == 2


def test_diagnosis_keeps_repeated_single_slice_failures():
    rows = tuple(("redundancy", "failure", "task-1") for _ in range(3))
    result = diagnose(rows)
    assert result[0].kind == "redundancy"
    assert result[0].slices == 1


def test_diagnosis_counts_success_counterexamples():
    rows = (
        ("exact_miss", "failure", "task-1"),
        ("exact_miss", "failure", "task-2"),
        ("exact_miss", "success", "task-3"),
    )
    result = diagnose(rows)
    assert result[0].success_counterexamples == 1
