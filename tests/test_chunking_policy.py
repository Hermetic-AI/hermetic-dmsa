"""切片窗口策略的生效范围与边界校验测试."""

from __future__ import annotations

import pytest

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.extraction import extract
from dmsa.memory.coding.models import CodingMemoryValidationError

BODY = "\n".join(f"line {index}: 修复 revision_cache 的过期判断" for index in range(20))


def test_smaller_chunk_size_produces_more_chunks():
    coarse = extract((SourceMessage("assistant", BODY),), 1800, 160)
    fine = extract((SourceMessage("assistant", BODY),), 300, 40)
    assert len(coarse) == 1
    assert len(fine) > len(coarse)
    assert all(len(chunk.text) <= 300 for chunk in fine)


def test_overlap_repeats_boundary_text_and_zero_overlap_does_not():
    overlapped = extract((SourceMessage("assistant", BODY),), 300, 120)
    disjoint = extract((SourceMessage("assistant", BODY),), 300, 0)
    assert overlapped[1].start < overlapped[0].end
    assert disjoint[1].start == disjoint[0].end
    # 偏移必须仍然覆盖原文, 保证来源可追溯
    assert disjoint[-1].end == len(BODY)


def test_extract_rejects_invalid_windows():
    messages = (SourceMessage("assistant", BODY),)
    with pytest.raises(CodingMemoryValidationError):
        extract(messages, 199, 0)
    with pytest.raises(CodingMemoryValidationError):
        extract(messages, 300, 300)
    with pytest.raises(CodingMemoryValidationError):
        extract(messages, True, 0)


def test_each_chunk_carries_its_own_code_anchors():
    text = (
        "diff --git a/src/early.py b/src/early.py\n+++ b/src/early.py\n+def early():\n    pass\n"
        + "填充文本。" * 200
        + "\ndiff --git a/src/late.py b/src/late.py\n+++ b/src/late.py\n+def late():\n    pass\n"
    )
    chunks = extract((SourceMessage("assistant", text),), 400, 0)
    symbols = [{anchor.symbol for anchor in chunk.anchors} for chunk in chunks]
    assert any("early" in item for item in symbols)
    assert any("late" in item for item in symbols)


def test_engine_persists_policy_chunk_window(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add(
            "req-fine",
            "fine",
            "sess",
            [SourceMessage("assistant", BODY)],
            SearchPolicy(chunk_size=300, chunk_overlap=40),
        )
        memory.add("req-coarse", "coarse", "sess", [SourceMessage("assistant", BODY)])
        fine = memory.store.db.execute(
            "SELECT COUNT(*) FROM chunks WHERE user_id='fine'"
        ).fetchone()[0]
        coarse = memory.store.db.execute(
            "SELECT COUNT(*) FROM chunks WHERE user_id='coarse'"
        ).fetchone()[0]
        assert fine > coarse >= 1
    finally:
        memory.close()


def test_search_policy_rejects_out_of_range_windows():
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(chunk_size=100)
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(chunk_size=300, chunk_overlap=400)
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(chunk_overlap=-1)
