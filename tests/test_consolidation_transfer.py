"""记忆合并泛化与跨仓库迁移的行为测试."""

from __future__ import annotations

import pytest

from dmsa.memory import (
    CodingMemory,
    CodingMemoryValidationError,
    SearchPolicy,
    SourceMessage,
)
from dmsa.memory.coding.generalize import SourceSlice, generalize, group_rules

POLICY = SearchPolicy(neighbor_window=0)

# 同一条经验换过具体文件: 泛化后应当落到同一条规则上.
RULE_HEAD = "根因: 缓存失效没有递增 revision。修复: 提交后递增 revision。"
RULE_A = RULE_HEAD + "改动在 src/cache.py 的 flush_buffer。"
RULE_B = RULE_HEAD + "改动在 src/buffer.py 的 flush_buffer。"
# 只提到文件名, 路径没登记: 属于仓库专有标识, 不允许迁移.
LEAKY = RULE_HEAD + "改动在 cache.py 的 flush_buffer。"
# 无关经验: 不与上面两条合并.
NOISE = "顺手把日志格式统一成一行输出, 不影响逻辑。"


def _memory(tmp_path) -> CodingMemory:
    """构造含三条证据的 coding memory."""
    return CodingMemory(tmp_path / "consolidation.sqlite", policy=POLICY)


def _write(memory: CodingMemory, request: str, text: str, repo: str = "") -> int:
    """写入一条证据, 返回 scope revision."""
    return memory.add(request, "u", f"s-{request}", [SourceMessage("assistant", text)], repo=repo)


def test_generalization_abstracts_paths_and_symbols() -> None:
    """泛化把具体路径与符号换成占位记号, 两条同规则得到同一个规则键."""
    left = generalize(RULE_A, ("src/cache.py",), ("flush_buffer",))
    right = generalize(RULE_B, ("src/buffer.py",), ("flush_buffer",))
    assert left.text == right.text
    assert left.rule_key == right.rule_key
    assert "<path>" in left.text
    assert "<symbol>" in left.text
    assert "cache.py" not in left.text


def test_generalization_reports_repo_specific_leak() -> None:
    """正文里残留具体文件名时视为泄漏, 规则不可迁移."""
    leaky = generalize(LEAKY, ("src/cache.py",), ("flush_buffer",))
    assert "cache.py" in leaky.leaked
    assert not leaky.transferable
    clean = generalize(RULE_A, ("src/cache.py",), ("flush_buffer",))
    assert clean.transferable


def test_generalization_keeps_short_symbols() -> None:
    """过短的符号视为通用词, 既不抽象也不计泄漏."""
    result = generalize("把 id 写进日志, 便于排查。", (), ("id",))
    assert "<symbol>" not in result.text
    assert result.leaked == ()
    assert result.transferable


def test_group_rules_only_groups_identical_rules() -> None:
    """只有泛化后完全相同的规则才会被归成一组."""
    groups = group_rules(
        [
            SourceSlice("1", RULE_A, ("src/cache.py",), ("flush_buffer",)),
            SourceSlice("2", RULE_B, ("src/buffer.py",), ("flush_buffer",)),
            SourceSlice("3", NOISE),
        ]
    )
    assert len(groups) == 2
    assert groups[0].source_ids == ("1", "2")
    assert groups[1].source_ids == ("3",)


def test_consolidate_merges_duplicate_experience(tmp_path) -> None:
    """合并把重复经验收敛成一条派生条目, 并登记全部来源."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", RULE_A, repo="alpha")
        _write(memory, "r2", RULE_B, repo="alpha")
        _write(memory, "r3", NOISE, repo="alpha")
        report = memory.consolidate("u", "alpha", min_group=2)
        assert len(report.groups) == 1
        assert len(report.written) == 1
        group = report.groups[0]
        assert len(group.source_ids) == 2
        records = memory.consolidation_records("u", "alpha")
        assert len(records) == 1
        assert records[0].chunk_id == report.written[0]
        assert set(records[0].source_ids) == set(group.source_ids)
        assert records[0].rule == group.rule
    finally:
        memory.close()


def test_consolidated_entry_is_searchable_and_keeps_sources(tmp_path) -> None:
    """合并出的条目能被检索到, 原始证据也仍然可查."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", RULE_A, repo="alpha")
        _write(memory, "r2", RULE_B, repo="alpha")
        report = memory.consolidate("u", "alpha", min_group=2)
        hits = memory.search("缓存 revision 修复", "u", 10, repo="alpha")
        assert hits
        assert any(hit.id == report.written[0] for hit in hits)
        assert len(hits) >= 3
    finally:
        memory.close()


def test_consolidate_is_idempotent(tmp_path) -> None:
    """同一条规则重复合并是幂等的, 不会写出第二条派生条目."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", RULE_A, repo="alpha")
        _write(memory, "r2", RULE_B, repo="alpha")
        first = memory.consolidate("u", "alpha", min_group=2)
        second = memory.consolidate("u", "alpha", min_group=2)
        assert len(first.written) == 1
        assert second.written == ()
        assert second.skipped == (first.groups[0].rule_key,)
        assert len(memory.consolidation_records("u", "alpha")) == 1
    finally:
        memory.close()


def test_consolidate_rejects_invalid_arguments(tmp_path) -> None:
    """合并参数越界时必须显式报错."""
    memory = _memory(tmp_path)
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.consolidate("u", "alpha", min_group=1)
        with pytest.raises(CodingMemoryValidationError):
            memory.consolidate("u", "alpha", limit=0)
    finally:
        memory.close()


def test_transfer_moves_generalizable_rule_to_other_repo(tmp_path) -> None:
    """迁移把不含具体标识的规则复制到目标仓库, 并登记来源."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", RULE_A, repo="alpha")
        _write(memory, "r2", RULE_B, repo="alpha")
        report = memory.transfer("u", "alpha", "beta")
        assert len(report.transferred) == 1
        chunk_id = report.transferred[0][1]
        hits = memory.search("缓存 revision 修复", "u", 10, repo="beta")
        assert any(hit.id == chunk_id for hit in hits)
        records = memory.transfer_records("u", "beta")
        assert len(records) == 1
        assert records[0].chunk_id == chunk_id
        assert len(records[0].source_ids) == 2
    finally:
        memory.close()


def test_transfer_drops_repo_specific_anchors(tmp_path) -> None:
    """迁移后的条目不再带源仓库的具体路径与符号."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", RULE_A, repo="alpha")
        report = memory.transfer("u", "alpha", "beta")
        chunk_id = report.transferred[0][1]
        rows = memory.store.db.execute(
            "SELECT paths, symbols FROM chunks WHERE id=?", (chunk_id,)
        ).fetchall()
        assert rows
        assert str(rows[0]["paths"]).strip() == ""
        assert str(rows[0]["symbols"]).strip() == ""
    finally:
        memory.close()


def test_transfer_rejects_rules_with_repo_specific_identifiers(tmp_path) -> None:
    """仍带仓库专有标识的规则不迁移, 报告里给出泄漏项."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", LEAKY, repo="alpha")
        report = memory.transfer("u", "alpha", "beta")
        assert report.transferred == ()
        assert len(report.skipped) == 1
        reason = report.skipped[0][1]
        assert "repo_specific" in reason
        assert "cache.py" in reason
    finally:
        memory.close()


def test_transfer_is_idempotent_and_keeps_source_evidence(tmp_path) -> None:
    """重复迁移不会写出第二条条目, 源仓库证据保持不变."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "r1", RULE_A, repo="alpha")
        first = memory.transfer("u", "alpha", "beta")
        second = memory.transfer("u", "alpha", "beta")
        assert len(first.transferred) == 1
        assert second.transferred == ()
        assert second.skipped[0][1] == "already_transferred"
        assert memory.search("缓存 revision", "u", 10, repo="alpha")
    finally:
        memory.close()


def test_transfer_rejects_same_repository(tmp_path) -> None:
    """源仓库与目标仓库相同时必须显式报错."""
    memory = _memory(tmp_path)
    try:
        with pytest.raises(CodingMemoryValidationError):
            memory.transfer("u", "alpha", "alpha")
    finally:
        memory.close()
