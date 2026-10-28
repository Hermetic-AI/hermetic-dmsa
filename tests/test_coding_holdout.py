"""三分留出评测的切分、指标与对照臂诚实性测试."""

from __future__ import annotations

import pytest

from benchmarks.coding.holdout import run_holdout
from benchmarks.coding.holdout_tasks import SPLITS, assign, corpus, select_noise


def test_corpus_has_unique_ids_and_families():
    tasks = corpus()
    assert len(tasks) >= 4
    assert len({task.task_id for task in tasks}) == len(tasks)
    assert all(task.family and task.gold and task.distractors for task in tasks)


def test_split_assignment_is_deterministic_and_disjoint():
    families = [task.family for task in corpus()]
    first = assign(families, 0)
    assert first == assign(families, 0)
    assert set(first) == set(families)
    assert set(first.values()) <= set(SPLITS)
    # 同族任务必须落在同一切分, 否则近重复任务会跨切分泄漏
    grouped: dict[str, set[str]] = {}
    for task in corpus():
        grouped.setdefault(task.family, set()).add(first[task.family])
    assert all(len(value) == 1 for value in grouped.values())
    # 分层轮转保证任务族足够时三个切分都非空
    assert set(first.values()) == set(SPLITS)


def test_assign_rejects_non_integer_seed():
    with pytest.raises(ValueError, match="seed must be an integer"):
        assign(["family"], "0")


def test_noise_selection_is_stable_and_bounded():
    task = corpus()[0]
    assert select_noise(task, 0, 3) == select_noise(task, 0, 3)
    assert len(select_noise(task, 0, 3)) <= 3
    assert select_noise(task, 0, 0) == ()


def test_run_holdout_reports_real_numbers_and_null_for_empty_splits():
    report = run_holdout(seed=0)
    assert report["official_score"] is None
    assert report["evaluation_type"] == "synthetic_proxy_not_official"
    assert set(report["splits"]) == set(SPLITS)
    for split in SPLITS:
        metrics = report["baseline"][split]
        if not report["splits"][split]:
            assert metrics is None
        else:
            assert metrics["task_count"] == len(report["splits"][split])
            assert 0.0 <= metrics["evidence_recall"] <= 1.0
            assert 0.0 <= metrics["ndcg"] <= 1.0
    assert report["evolution"]["status"] in {
        "no_signal",
        "insufficient_support",
        "no_replay_case",
        "accepted_on_train",
        "rejected_on_train",
    }
    # 训练切分没有产生可用信号时, 候选必须与基线完全一致(不允许暗中调参)
    if report["evolution"]["status"] in {"no_signal", "insufficient_support", "no_replay_case"}:
        assert report["candidate"] == report["baseline"]


def test_mem0_arm_reports_null_with_reason_when_dependency_missing():
    report = run_holdout(seed=0)
    arm = report["mem0"]
    assert arm["available"] is False
    assert isinstance(arm["reason"], str)
    assert arm["reason"]
    assert arm["metrics"] == dict.fromkeys(SPLITS)
