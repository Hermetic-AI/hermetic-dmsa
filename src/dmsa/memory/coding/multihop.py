"""多跳检索的查询扩展: 用首轮命中的定位信号继续找相关证据.

单跳检索只看用户原话, 当原话里的词与真实代码标识符对不上时就会漏召回.
多跳的做法是拿首轮最强的几条命中的路径与符号当扩展词, 再用扩展后的查询
再召回一轮: 命中过同一文件或同一函数的其他证据因此有机会被捞出来.

扩展是有界的: 跳数、扩展词数量与每轮候选额度都固定, 扩展词只来自已有证据的
路径和符号, 不引入外部模型, 因此结果完全可复现.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# 每轮用多少条命中来推导扩展词.
SEED_HITS = 3
# 单轮最多补多少个扩展词.
MAX_EXPANDED_TERMS = 8


@dataclass(frozen=True)
class Expansion:
    """一轮扩展的结果: 补进去的词与拼好的新查询."""

    terms: tuple[str, ...]
    query: str

    @property
    def changed(self) -> bool:
        """判断本轮是否真的补进了新词."""
        return bool(self.terms)


def seed_terms(rows: list[sqlite3.Row]) -> tuple[str, ...]:
    """从首轮命中里取出路径与符号作为扩展词来源.

    路径与符号是比正文更强的定位信号: 它们精确指向文件与函数, 扩展后能召回
    同一处代码的其它证据, 而正文里的散文词往往过于泛化.
    """
    terms: list[str] = []
    for row in rows:
        for column in ("paths", "symbols"):
            for value in str(row[column] or "").split():
                if value and value not in terms:
                    terms.append(value)
    return tuple(terms[:MAX_EXPANDED_TERMS])


def expand(query: str, terms: tuple[str, ...], existing: tuple[str, ...]) -> Expansion:
    """把扩展词拼成新查询, 跳过原查询里已经出现的词."""
    known = {value.lower() for value in existing}
    fresh = tuple(term for term in terms if term.lower() not in known)
    if not fresh:
        return Expansion((), query)
    return Expansion(fresh, f"{query} {' '.join(fresh)}")
