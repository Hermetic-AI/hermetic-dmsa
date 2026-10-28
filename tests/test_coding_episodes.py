"""session 级经验卡的构建、入库与检索行为."""

from __future__ import annotations

from dmsa.memory import CodingMemory, SourceMessage
from dmsa.memory.coding.episodes import EPISODE_PREFIX, build_episode
from dmsa.memory.coding.extraction import extract

_SESSION = (
    SourceMessage("user", "ops_0_clamp.py 的 clamp_0 对负数返回错误，需要修复。"),
    SourceMessage("assistant", "复现失败用例后确认是下界缺失导致。"),
    SourceMessage(
        "assistant",
        "尝试过的错误补丁：\n```python path=ops_0_clamp.py\ndef clamp_0(value, lower, upper):\n"
        "    return min(upper, value)\n```",
    ),
    SourceMessage(
        "assistant",
        "最终修复如下：\n```python path=ops_0_clamp.py\ndef clamp_0(value, lower, upper):\n"
        "    return max(lower, min(upper, value))\n```\n验证：pytest 全部通过。",
    ),
)


def test_build_episode_extracts_symptom_cause_fix_and_validation() -> None:
    """经验卡要给出症状、根因、最终补丁和验证结论."""
    card = build_episode(_SESSION)
    assert card is not None
    assert "clamp_0" in card.symptom
    assert card.patch_path == "ops_0_clamp.py"
    assert "max(lower, min(upper, value))" in card.patch_body
    assert "pytest" in card.validation
    assert card.outcome == "reported_success"
    assert card.failed_attempts >= 1
    assert card.text.startswith(EPISODE_PREFIX)
    assert "ops_0_clamp.py" in card.paths
    assert "clamp_0" in card.symbols


def test_build_episode_returns_none_without_reusable_content() -> None:
    """只有寒暄没有补丁或根因时不生成经验卡."""
    assert build_episode((SourceMessage("user", "你好"),)) is None
    assert build_episode(()) is None


def test_add_stores_one_episode_card_per_session(tmp_path) -> None:
    """同一会话多次写入只保留最新一张经验卡."""
    memory = CodingMemory(tmp_path / "episode.db")
    memory.add("r1", "u", "s", list(_SESSION))
    memory.add("r2", "u", "s", [SourceMessage("assistant", "补充：还需要覆盖上界用例。")])
    rows = memory.store.rows("u")
    cards = [row for row in rows if row["role"] == "episode"]
    assert len(cards) == 1
    assert "max(lower, min(upper, value))" in str(cards[0]["text"])
    memory.close()


def test_episode_card_is_returned_for_related_query(tmp_path) -> None:
    """经验卡是可检索的独立证据, 能被同任务查询召回."""
    memory = CodingMemory(tmp_path / "episode.db")
    memory.add("r1", "u", "s", list(_SESSION))
    hits = memory.search("ops_0_clamp.py clamp_0 负数 返回错误", "u", 5)
    assert hits
    assert any(EPISODE_PREFIX in hit.content for hit in hits)
    memory.close()


def test_episode_card_does_not_replace_raw_chunks(tmp_path) -> None:
    """经验卡是新增证据, 原始切片仍保留以便追溯."""
    memory = CodingMemory(tmp_path / "episode.db")
    memory.add("r1", "u", "s", list(_SESSION))
    rows = memory.store.rows("u")
    raw = [row for row in rows if row["role"] != "episode"]
    assert len(raw) == len(extract(tuple(_SESSION)))
    memory.close()
