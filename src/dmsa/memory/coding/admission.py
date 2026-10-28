"""写入门控: 判断一次写入是否值得进入记忆.

门控只在有明确理由时拒绝写入, 并且每次判定都留下可解释的原因:

- ``novel``: 与既有记忆不重复, 正常写入;
- ``idempotent``: 同一请求已写入, 直接返回原 revision;
- ``empty_content``: 没有可用文本, 拒绝;
- ``redundant``: 与既有条目近乎重复, 拒绝, 并指出重复来源;
- ``conflict_candidate``: 近似重复但来自另一位作者, 判定为分歧而非噪音,
  保留写入并登记为待仲裁的写冲突.

比较用词项集合的 Jaccard 相似度: 英文按词切分, 中文按二元字组切分,
避免依赖分词器也避免把整段文本当成一个词项.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

_ASCII_RE = re.compile(r"[A-Za-z0-9_./\\-]+")
_CJK_RE = re.compile(r"[一-鿿]+")

REASON_NOVEL = "novel"
REASON_IDEMPOTENT = "idempotent"
REASON_EMPTY = "empty_content"
REASON_REDUNDANT = "redundant"
# 跨作者的近似重复: 判定为待仲裁的分歧, 不再当作噪音拒绝.
REASON_CONFLICT = "conflict_candidate"


@dataclass(frozen=True)
class AdmissionDecision:
    """一次写入的准入判定结果."""

    admitted: bool
    reason: str
    novelty: float
    duplicate_of: str | None = None
    rival_author: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的判定视图."""
        return {
            "admitted": self.admitted,
            "reason": self.reason,
            "novelty": round(self.novelty, 6),
            "duplicate_of": self.duplicate_of,
            "rival_author": self.rival_author,
        }


@dataclass(frozen=True)
class AdmissionResult:
    """写入结果: scope revision 与准入判定."""

    revision: int
    decision: AdmissionDecision

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的写入结果视图."""
        return {"revision": self.revision, "decision": self.decision.to_dict()}


def tokenize(text: str) -> frozenset[str]:
    """把文本切成可比较的词项: 英文按词, 中文按二元字组."""
    tokens = {item.lower() for item in _ASCII_RE.findall(text)}
    for run in _CJK_RE.findall(text):
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return frozenset(tokens)


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """计算两个词项集合的 Jaccard 相似度."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if not intersection:
        return 0.0
    return intersection / len(left | right)


def recent_texts(
    db: sqlite3.Connection, user_id: str, limit: int = 200
) -> list[tuple[str, str, str]]:
    """读取 scope 内最近的切片正文与作者, 作为重复性比较的参照.

    作者来自 ``write_authors``: 未署名时为空字符串, 表示个人作用域的写入.
    """
    rows = db.execute(
        "SELECT c.id AS id, c.text AS text, COALESCE(a.author, '') AS author "
        "FROM chunks c LEFT JOIN write_authors a "
        "ON a.user_id=c.user_id AND a.chunk_id=c.id "
        "WHERE c.user_id=? ORDER BY c.rowid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [(str(row["id"]), str(row["text"]), str(row["author"])) for row in rows]


def evaluate(
    db: sqlite3.Connection,
    user_id: str,
    texts: tuple[str, ...],
    *,
    threshold: float,
    sample_limit: int = 200,
    author: str = "",
) -> AdmissionDecision:
    """判定一批新内容是否值得写入.

    近似重复的处置取决于作者: 同一位作者的重复写入是噪音, 直接拒绝;
    来自另一位作者的重复写入可能是相反结论, 判定为冲突候选并保留.
    """
    combined = tokenize(" ".join(texts))
    if not combined:
        return AdmissionDecision(False, REASON_EMPTY, 0.0)
    best = 0.0
    best_id: str | None = None
    best_author = ""
    for chunk, text, rival_author in recent_texts(db, user_id, sample_limit):
        score = jaccard(combined, tokenize(text))
        if score > best:
            best = score
            best_id = chunk
            best_author = rival_author
    if best >= threshold:
        if author != best_author:
            return AdmissionDecision(True, REASON_CONFLICT, 1.0 - best, best_id, best_author)
        return AdmissionDecision(False, REASON_REDUNDANT, 1.0 - best, best_id, best_author)
    return AdmissionDecision(True, REASON_NOVEL, 1.0 - best)
