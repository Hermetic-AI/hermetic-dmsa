"""团队作用域与跨作者写冲突仲裁的行为测试."""

from __future__ import annotations

import pytest

from dmsa.memory import CodingMemory, CodingMemoryValidationError, SearchPolicy, SourceMessage

# 门控阈值调到 0.5, 让"近似重复"在测试里稳定可复现.
POLICY = SearchPolicy(admission_similarity=0.5, neighbor_window=0)

ALICE_TEXT = "def handler(): return 1  # 用缓存兜底"
BOB_TEXT = "def handler(): return 2  # 用缓存兜底"


def _memory(tmp_path) -> CodingMemory:
    """构造一个团队场景专用的 coding memory."""
    return CodingMemory(tmp_path / "team.sqlite", policy=POLICY)


def _write(memory: CodingMemory, scope: str, request: str, author: str, text: str, repo: str = ""):
    """写入一条署名证据, 返回准入判定."""
    return memory.add_guarded(
        request,
        scope,
        f"s-{request}",
        [SourceMessage("assistant", text)],
        repo=repo,
        author=author,
    )


def _stats(memory: CodingMemory, scope: str) -> dict[str, dict[str, int]]:
    """读取 scope 内条目统计, 便于断言信任计数."""
    rows = memory.store.db.execute(
        "SELECT chunk_id, confirmed, contradicted FROM memory_stats WHERE user_id=?", (scope,)
    ).fetchall()
    return {
        str(row["chunk_id"]): {
            "confirmed": int(row["confirmed"]),
            "contradicted": int(row["contradicted"]),
        }
        for row in rows
    }


def test_team_scope_key_uses_prefix_and_repo_subdomain() -> None:
    """团队作用域带前缀, 仓库仍是隔离子域."""
    assert CodingMemory.team_scope("alpha") == "team:alpha"
    assert CodingMemory.team_scope("alpha", "repo-a") == "team:alpha#repo-a"
    assert CodingMemory.team_scope("alpha") != CodingMemory.team_scope("beta")


def test_team_scope_rejects_separator_in_identifier() -> None:
    """作用域分隔符出现在标识里会让子域归属产生歧义, 必须拒绝."""
    with pytest.raises(CodingMemoryValidationError):
        CodingMemory.team_scope("alpha#beta")


def test_same_author_duplicate_stays_redundant(tmp_path) -> None:
    """同一位作者的重复写入仍是噪音, 门控直接拒绝."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        result = _write(memory, scope, "r2", "alice", BOB_TEXT)
        assert result.decision.admitted is False
        assert result.decision.reason == "redundant"
        assert memory.conflicts(scope) == []
    finally:
        memory.close()


def test_cross_author_duplicate_is_admitted_as_conflict(tmp_path) -> None:
    """跨作者的近似重复是分歧, 写入保留并登记待仲裁冲突."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        result = _write(memory, scope, "r2", "bob", BOB_TEXT)
        assert result.decision.admitted is True
        assert result.decision.reason == "conflict_candidate"
        assert result.decision.rival_author == "alice"
        conflicts = memory.conflicts(scope)
        assert len(conflicts) == 1
        conflict = conflicts[0]
        assert conflict.author == "bob"
        assert conflict.rival_author == "alice"
        assert conflict.status == "open"
        assert conflict.similarity > 0
    finally:
        memory.close()


def test_personal_scope_keeps_previous_redundant_behavior(tmp_path) -> None:
    """个人作用域没有作者概念, 近似重复仍然被拒绝."""
    memory = _memory(tmp_path)
    try:
        _write(memory, "u", "r1", "", ALICE_TEXT)
        result = _write(memory, "u", "r2", "", BOB_TEXT)
        assert result.decision.admitted is False
        assert result.decision.reason == "redundant"
    finally:
        memory.close()


def test_conflicts_are_isolated_per_repo(tmp_path) -> None:
    """同一团队的不同仓库互不看见对方的冲突."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT, repo="repo-a")
        _write(memory, scope, "r2", "bob", BOB_TEXT, repo="repo-a")
        assert len(memory.conflicts(scope, "repo-a")) == 1
        assert memory.conflicts(scope, "repo-b") == []
    finally:
        memory.close()


def test_resolve_keep_new_marks_new_chunk_confirmed(tmp_path) -> None:
    """保留新写入: 新条目记已确认, 对手条目记已反驳."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        _write(memory, scope, "r2", "bob", BOB_TEXT)
        conflict = memory.conflicts(scope)[0]
        resolution = memory.resolve_conflict(scope, conflict.id, "keep_new")
        assert resolution.applied is True
        assert resolution.confirmed == (conflict.chunk_id,)
        assert resolution.contradicted == (conflict.rival_chunk_id,)
        stats = _stats(memory, scope)
        assert stats[conflict.chunk_id]["confirmed"] == 1
        assert stats[conflict.rival_chunk_id]["contradicted"] == 1
    finally:
        memory.close()


def test_resolve_keep_rival_reverses_trust_counters(tmp_path) -> None:
    """保留既有写入: 信任增量落到对手条目上."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        _write(memory, scope, "r2", "bob", BOB_TEXT)
        conflict = memory.conflicts(scope)[0]
        resolution = memory.resolve_conflict(scope, conflict.id, "keep_rival")
        assert resolution.confirmed == (conflict.rival_chunk_id,)
        assert resolution.contradicted == (conflict.chunk_id,)
        stats = _stats(memory, scope)
        assert stats[conflict.rival_chunk_id]["confirmed"] == 1
        assert stats[conflict.chunk_id]["contradicted"] == 1
    finally:
        memory.close()


def test_resolve_keep_both_leaves_trust_untouched(tmp_path) -> None:
    """两者并存: 不做信任惩罚, 冲突照常关闭."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        _write(memory, scope, "r2", "bob", BOB_TEXT)
        conflict = memory.conflicts(scope)[0]
        resolution = memory.resolve_conflict(scope, conflict.id, "keep_both")
        assert resolution.applied is True
        assert resolution.confirmed == ()
        assert resolution.contradicted == ()
        assert all(
            value == 0 for counts in _stats(memory, scope).values() for value in counts.values()
        )
        assert memory.conflicts(scope, status="resolved")[0].resolution == "keep_both"
    finally:
        memory.close()


def test_resolving_twice_does_not_double_count(tmp_path) -> None:
    """重复仲裁同一条冲突不重复记账."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        _write(memory, scope, "r2", "bob", BOB_TEXT)
        conflict = memory.conflicts(scope)[0]
        memory.resolve_conflict(scope, conflict.id, "keep_new")
        again = memory.resolve_conflict(scope, conflict.id, "keep_new")
        assert again.applied is False
        stats = _stats(memory, scope)
        assert stats[conflict.chunk_id]["confirmed"] == 1
        assert stats[conflict.rival_chunk_id]["contradicted"] == 1
    finally:
        memory.close()


def test_unknown_conflict_id_is_rejected(tmp_path) -> None:
    """仲裁不存在的冲突必须报错, 而不是静默成功."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        with pytest.raises(CodingMemoryValidationError):
            memory.resolve_conflict(scope, "does-not-exist", "keep_new")
    finally:
        memory.close()


def test_invalid_resolution_is_rejected(tmp_path) -> None:
    """未登记的处置方式不属于仲裁语义, 必须拒绝."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        _write(memory, scope, "r2", "bob", BOB_TEXT)
        conflict = memory.conflicts(scope)[0]
        with pytest.raises(CodingMemoryValidationError):
            memory.resolve_conflict(scope, conflict.id, "delete_both")
    finally:
        memory.close()


def test_delete_scope_clears_conflicts_and_authors(tmp_path) -> None:
    """清理作用域时冲突与作者归属一并清掉, 不留悬空记录."""
    memory = _memory(tmp_path)
    try:
        scope = CodingMemory.team_scope("alpha")
        _write(memory, scope, "r1", "alice", ALICE_TEXT)
        _write(memory, scope, "r2", "bob", BOB_TEXT)
        assert len(memory.conflicts(scope)) == 1
        memory.delete_scope(scope)
        assert memory.conflicts(scope) == []
        rows = memory.store.db.execute(
            "SELECT count(*) FROM write_authors WHERE user_id=?", (scope,)
        ).fetchone()
        assert int(rows[0]) == 0
    finally:
        memory.close()
