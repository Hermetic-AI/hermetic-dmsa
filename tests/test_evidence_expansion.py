"""扩展证据折扣在负分场景下的排序回归测试."""

from __future__ import annotations

from pathlib import Path

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.evidence_pack import EXPANSION_DISCOUNT, _discount


def test_discount_keeps_positive_base_ratio() -> None:
    """父分为正时保持原有折扣比例."""
    assert _discount(0.016, EXPANSION_DISCOUNT) == 0.016 * EXPANSION_DISCOUNT


def test_discount_stays_below_negative_base() -> None:
    """父分为负时折扣不能把扩展分数抬到父命中之前."""
    base = -0.003607
    assert _discount(base, EXPANSION_DISCOUNT) < base


def test_expanded_neighbor_never_outranks_parent(tmp_path: Path) -> None:
    """父命中为负时, 同一条消息的邻居切片必须排在父命中之后."""
    memory = CodingMemory(tmp_path / "expand.sqlite", policy=SearchPolicy())
    scope = "expand-scope"
    # 纯填充正文没有任何代码或修复类词汇, 证据质量调分会给出负分。
    filler = "alpha beta gamma delta " * 120
    memory.add(
        "req-1",
        scope,
        "sess-1",
        [SourceMessage(role="assistant", content="cache " + filler + "omega psi " * 120)],
    )
    rows = memory.store.db.execute(
        "SELECT id, start FROM chunks WHERE user_id=? ORDER BY start", (scope,)
    ).fetchall()
    assert len(rows) >= 2
    parent_id = str(rows[0][0])
    hits = memory.search("cache", scope, 5)
    assert hits, "查询必须返回候选"
    assert hits[0].id == parent_id, "命中词项的切片必须排在扩展邻居之前"
    assert hits[0].score < 0, "该场景用于锁定负分分支"
    if len(hits) > 1:
        assert hits[0].score > hits[1].score
    memory.close()
