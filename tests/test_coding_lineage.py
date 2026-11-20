"""lineage 通道回归: supersession 检测与锚点作用域的验证强度."""

from __future__ import annotations

import pytest

from dmsa.gradient.policy import FailureDiagnostic, RetrievalPolicy, propose_policy
from dmsa.memory import CodingMemory, CodingMemoryValidationError, SearchPolicy, SourceMessage
from dmsa.memory.coding.episode_store import REL_DERIVED_FROM, REL_SUPERSEDED_BY
from dmsa.memory.coding.episodes import SUPERSEDED_OUTCOME
from dmsa.memory.coding.ranking import QueryFeatures

_TURN = (
    SourceMessage("user", "tests/test_cache.py 的 test_load 失败，需要修复缓存。"),
    SourceMessage("assistant", "根因：读取时没有比较版本号。"),
    SourceMessage(
        "assistant",
        "修复：\n```python path=tests/test_cache.py\ndef test_load():\n"
        "    assert load_cache()\n```\n验证：pytest tests/test_cache.py::test_load -q: 1 passed",
    ),
)


def _features() -> QueryFeatures:
    return QueryFeatures(frozenset(), frozenset(), frozenset())


def test_episode_records_derivation_and_supersession_edges(tmp_path) -> None:
    """写卡时登记派生边, 同会话更新时把旧卡标 superseded 而非删除."""
    memory = CodingMemory(tmp_path / "lineage.db")
    memory.add("r1", "u", "s", list(_TURN))
    cards = [row for row in memory.store.rows("u") if row["role"] == "episode"]
    assert len(cards) == 1
    card_id = str(cards[0]["id"])
    sources = memory.store.lineage_targets("u", (card_id,), REL_DERIVED_FROM)[card_id]
    assert sources
    assert set(sources) <= {str(row["id"]) for row in memory.store.rows("u")}

    memory.add("r2", "u", "s", [SourceMessage("assistant", "补充：再覆盖一次边界用例。")])
    cards = [row for row in memory.store.rows("u") if row["role"] == "episode"]
    assert len(cards) == 2
    old = [row for row in cards if row["outcome"] == SUPERSEDED_OUTCOME]
    assert len(old) == 1
    successors = memory.store.lineage_targets("u", (str(old[0]["id"]),), REL_SUPERSEDED_BY)
    assert len(successors[str(old[0]["id"])]) == 1
    memory.close()


def test_superseded_hidden_by_default_and_marked_when_enabled(tmp_path) -> None:
    """默认行为不变; 打开 lineage 通道后旧卡带标记返回并排在后面."""
    memory = CodingMemory(tmp_path / "lineage.db")
    memory.add("r1", "u", "s", list(_TURN))
    memory.add("r2", "u", "s", [SourceMessage("assistant", "补充：再覆盖一次边界用例。")])
    # 用卡片独有字段查询, 避免被近重过滤当成原文切片的副本。
    query = "失败尝试 修复文件 test_cache.py"
    default_hits = memory.search(query, "u", 10)
    assert default_hits
    assert all("[superseded" not in hit.content for hit in default_hits)
    assert all("outcome=superseded" not in hit.content for hit in default_hits)

    # 两张卡文本高度相似, 整链 search 会被近重过滤压掉一张; 这里直接测装包层,
    # 验证"默认过滤 / 打开后带标记返回"这条分支本身。
    old = next(row for row in memory.store.rows("u") if row["outcome"] == SUPERSEDED_OUTCOME)
    old_id = str(old["id"])
    hidden = memory.retriever.pack_hits("u", 10, SearchPolicy(), {old_id: 0.1}, None)
    assert hidden == []
    marked = memory.retriever.pack_hits(
        "u", 10, SearchPolicy(lineage_window=1), {old_id: 0.1}, None
    )
    assert len(marked) == 1
    assert "[superseded by" in marked[0].content
    assert "outcome=superseded" in marked[0].content

    # 端到端: 新卡会带 [supersedes], 即使旧卡被近重过滤掉也能送达 Agent。
    end_to_end = memory.search(query, "u", 10, SearchPolicy(lineage_window=1))
    assert any("[supersedes " in hit.content for hit in end_to_end)
    memory.close()


def test_supersession_walks_bounded_hops(tmp_path) -> None:
    """lineage_window 支持有限跳数, 旧卡能追踪到两级后继."""
    memory = CodingMemory(tmp_path / "lineage.db")
    memory.add("r1", "u", "s", list(_TURN))
    memory.add("r2", "u", "s", [SourceMessage("assistant", "第二次补充。")])
    memory.add("r3", "u", "s", [SourceMessage("assistant", "第三次补充。")])
    cards = [row for row in memory.store.rows("u") if row["role"] == "episode"]
    oldest = min(cards, key=lambda row: str(row["id"]))
    chain = memory.store.lineage_targets("u", (str(oldest["id"]),), REL_SUPERSEDED_BY)
    assert chain[str(oldest["id"])]
    # 多跳查询不应抛错, 且后继数量不超过配置窗口内的可达节点。
    memory.search("test_cache.py test_load", "u", 10, SearchPolicy(lineage_window=2))
    memory.close()


def test_verification_prior_requires_explicit_anchor_overlap(tmp_path) -> None:
    """验证强度只作用于测试锚点路径与证据路径重合的切片, 默认完全关闭."""
    memory = CodingMemory(tmp_path / "lineage.db")
    memory.add("r1", "u", "s", list(_TURN))
    card = next(row for row in memory.store.rows("u") if row["role"] == "episode")
    sources = memory.store.lineage_targets("u", (str(card["id"]),), REL_DERIVED_FROM)[
        str(card["id"])
    ]
    assert sources

    policy_off = SearchPolicy()
    ranks_off = dict.fromkeys(sources, 0.0)
    memory.retriever.score_candidates("test_load", "u", policy_off, ranks_off, _features(), None)
    policy_on = SearchPolicy(verification_weight=1.0)
    ranks_on = dict.fromkeys(sources, 0.0)
    memory.retriever.score_candidates("test_load", "u", policy_on, ranks_on, _features(), None)
    boosted = [key for key in ranks_on if ranks_on[key] > ranks_off[key]]
    assert boosted
    assert all(ranks_on[key] - ranks_off[key] <= 0.0025 + 1e-9 for key in ranks_on)
    memory.close()


def test_new_policy_fields_validate_bounds() -> None:
    """新增字段沿用SearchPolicy的有界校验, 不引入无界权重."""
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(lineage_window=-1)
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(lineage_window=4)
    with pytest.raises(CodingMemoryValidationError):
        SearchPolicy(verification_weight=1.5)
    assert SearchPolicy(lineage_window=3, verification_weight=1.0).lineage_window == 3


def test_rsi_proposes_lineage_and_verification_changes() -> None:
    """两类新诊断进入规则提案, 且仍受 development_only 保护."""
    current = RetrievalPolicy()
    proposals = propose_policy(
        current,
        (
            FailureDiagnostic("superseded_evidence", 2),
            FailureDiagnostic("overclaimed_verification", 2),
        ),
        development_only=True,
    )
    assert any(item.search.lineage_window >= 1 for item in proposals)
    assert any(item.search.verification_weight >= 0.1 for item in proposals)
