"""召回解释: 说明每条证据是被哪些通道、哪些词项召回的.

检索结果本身只给分数, 但排查「为什么这条记忆被捞出来」时真正需要的信息是
命中通道与命中词项. 本模块把这些中间量固化成可序列化的结构, 与多跳检索的
每一轮记录一起返回, 让检索结果可被复核而不是只能被相信.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 单条解释里最多列出的命中词项数, 避免把整段查询倒进结果里.
MAX_MATCHED_TERMS = 12


@dataclass(frozen=True)
class RecallItem:
    """一条召回证据及其来源解释."""

    id: str
    score: float
    channels: tuple[str, ...]
    matched_terms: tuple[str, ...]
    paths: tuple[str, ...]
    symbols: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的单条召回解释."""
        return {
            "id": self.id,
            "score": round(self.score, 6),
            "channels": list(self.channels),
            "matched_terms": list(self.matched_terms),
            "paths": list(self.paths),
            "symbols": list(self.symbols),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RecallRound:
    """一轮迭代召回的记录: 用了什么查询, 补了什么词, 召回多少候选."""

    index: int
    query: str
    added_terms: tuple[str, ...]
    candidates: int

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的轮次记录."""
        return {
            "index": self.index,
            "query": self.query,
            "added_terms": list(self.added_terms),
            "candidates": self.candidates,
        }


@dataclass(frozen=True)
class RecallExplanation:
    """一次检索的完整召回解释."""

    query: str
    hops: int
    rounds: tuple[RecallRound, ...]
    items: tuple[RecallItem, ...]

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的召回解释视图."""
        return {
            "query": self.query,
            "hops": self.hops,
            "rounds": [item.to_dict() for item in self.rounds],
            "items": [item.to_dict() for item in self.items],
        }


def matched_terms(terms: tuple[str, ...], text: str, paths: str, symbols: str) -> tuple[str, ...]:
    """找出查询词项中真正出现在证据正文、路径或符号里的那些."""
    haystack = f"{text} {paths} {symbols}".lower()
    hits = [term for term in terms if term and term.lower() in haystack]
    return tuple(hits[:MAX_MATCHED_TERMS])


def build_reason(channels: tuple[str, ...], terms: tuple[str, ...]) -> str:
    """拼出人读的一句召回说明."""
    if not channels:
        return "未命中任何召回通道"
    channel_text = "、".join(channels)
    if not terms:
        return f"由 {channel_text} 通道召回"
    return f"由 {channel_text} 通道召回, 命中词项: {'、'.join(terms)}"
