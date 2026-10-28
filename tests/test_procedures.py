"""程序性记忆的抽取、存储与匹配测试."""

from __future__ import annotations

import pytest

from dmsa.memory import CodingMemory, SourceMessage
from dmsa.memory.coding.models import CodingMemoryValidationError
from dmsa.memory.coding.procedures import build_procedure

PROCEDURE_TEXT = (
    "# 修复缓存竞态\n"
    "前置条件: 服务已开启缓存写入\n"
    "1. 把版本检查移到锁内\n"
    "2. 提交缓存写入后再发布条目\n"
    "3. 校验读到的版本与期望一致\n"
    "验证: pytest tests/test_cache.py 全部通过\n"
)


def _memory(tmp_path) -> CodingMemory:
    """构造空的 coding memory."""
    return CodingMemory(tmp_path / "proc.sqlite")


def test_build_procedure_requires_explicit_ordered_steps() -> None:
    messages = (SourceMessage("assistant", "只有一段说明文字, 没有步骤。"),)
    assert build_procedure(messages) is None


def test_build_procedure_needs_at_least_two_steps() -> None:
    messages = (SourceMessage("assistant", "# 标题\n1. 只有一步\n"),)
    assert build_procedure(messages) is None


def test_build_procedure_extracts_steps_and_conditions() -> None:
    messages = (
        SourceMessage("user", "缓存写入偶发读到旧值, 怎么修?"),
        SourceMessage("assistant", PROCEDURE_TEXT),
    )
    procedure = build_procedure(messages)
    assert procedure is not None
    assert procedure.title == "修复缓存竞态"
    assert procedure.steps == (
        "把版本检查移到锁内",
        "提交缓存写入后再发布条目",
        "校验读到的版本与期望一致",
    )
    assert procedure.preconditions == ("前置条件: 服务已开启缓存写入",)
    assert procedure.verification == ("验证: pytest tests/test_cache.py 全部通过",)
    assert procedure.outcome == "reported_success"
    assert procedure.trigger_terms == ("缓存写入偶发读到旧值, 怎么修?",)


def test_add_and_search_procedure(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        stored = memory.add_procedure(
            "req-1",
            "u",
            "s1",
            [SourceMessage("assistant", PROCEDURE_TEXT)],
        )
        assert stored is not None
        matches = memory.search_procedures("缓存 竞态 版本检查 锁", "u")
        assert matches
        assert matches[0].procedure.title == "修复缓存竞态"
        assert matches[0].matched_terms
        assert matches[0].score > 0
    finally:
        memory.close()


def test_procedure_is_isolated_by_repo(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add_procedure(
            "req-1", "u", "s1", [SourceMessage("assistant", PROCEDURE_TEXT)], repo="alpha"
        )
        assert memory.search_procedures("缓存 竞态", "u", repo="alpha")
        assert memory.search_procedures("缓存 竞态", "u", repo="beta") == []
    finally:
        memory.close()


def test_add_procedure_without_steps_returns_none(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        assert (
            memory.add_procedure("req-2", "u", "s1", [SourceMessage("assistant", "没有步骤的说明")])
            is None
        )
    finally:
        memory.close()


def test_search_procedure_validates_top_k(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.search_procedures("缓存", "u", 0)
    finally:
        memory.close()


def test_delete_scope_removes_procedures(tmp_path) -> None:
    memory = _memory(tmp_path)
    try:
        memory.add_procedure("req-1", "u", "s1", [SourceMessage("assistant", PROCEDURE_TEXT)])
        memory.delete_scope("u")
        assert memory.search_procedures("缓存 竞态", "u") == []
    finally:
        memory.close()
