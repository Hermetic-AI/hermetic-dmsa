"""推理结论前缀的排序优先级回归测试."""

from __future__ import annotations

from pathlib import Path

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def test_thinking_conclusion_is_not_penalized(tmp_path: Path) -> None:
    """[thinking] 推理结论是散文, 但不能因此被降权到无信息填充之后."""
    memory = CodingMemory(tmp_path / "thinking.sqlite")
    memory.add(
        "r-think",
        "u",
        "s-think",
        [
            SourceMessage(
                "assistant",
                "[thinking] cache_refresh 的根因是装饰器没有覆盖 __setitem__, 列检查没被触发。",
            )
        ],
    )
    memory.add(
        "r-filler",
        "u",
        "s-filler",
        [SourceMessage("assistant", "cache_refresh alpha beta gamma delta")],
    )
    hits = memory.search("cache_refresh", "u", 5, SearchPolicy(adjustment_weight=1))
    think = next(hit for hit in hits if "根因是装饰器" in hit.content)
    filler = next(hit for hit in hits if "alpha beta" in hit.content)
    assert think.score > filler.score, "推理结论必须排在无信息填充之前"
    memory.close()


def test_thinking_bonus_stays_bounded(tmp_path: Path) -> None:
    """推理结论有加分, 但加分幅度有界, 不会把同分的代码证据压下去太多."""
    memory = CodingMemory(tmp_path / "thinking2.sqlite")
    memory.add(
        "r-think",
        "u",
        "s-think",
        [SourceMessage("assistant", "[thinking] 这段结论描述了 cache_refresh 的根因")],
    )
    memory.add(
        "r-code",
        "u",
        "s-code",
        [
            SourceMessage(
                "assistant",
                "cache_refresh def refresh_cache(store):\n    return store.refresh()",
            )
        ],
    )
    hits = memory.search("cache_refresh", "u", 5, SearchPolicy(adjustment_weight=1))
    think = next(hit for hit in hits if "这段结论" in hit.content)
    code = next(hit for hit in hits if "refresh_cache" in hit.content)
    assert think.score > code.score, "命中查询词的结论应排在同分的代码证据之前"
    assert think.score - code.score < 0.03, "结论加分必须有界, 不能整体压过通道排名"
    memory.close()
