"""Coding memory SQLite neighbor lookup and FTS query helpers."""

from __future__ import annotations

import sqlite3

from dmsa.memory.coding.models import CodingMemoryError
from dmsa.memory.coding.schema import scope_token
from dmsa.memory.coding.tokenize import GRAM


def _like_pattern(term: str) -> str:
    """把词项转成转义过的 LIKE 模式, 使 % 和 _ 按字面匹配."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def substring_candidates(
    db: sqlite3.Connection, terms: tuple[str, ...], user_id: str, limit: int
) -> list[sqlite3.Row]:
    """用有界 LIKE 扫描召回 trigram 虚表无法索引的短词项."""
    short = tuple(term for term in terms if 0 < len(term) < GRAM)
    if not short:
        return []
    one = "(text LIKE ? ESCAPE '\\' OR paths LIKE ? ESCAPE '\\' OR symbols LIKE ? ESCAPE '\\')"
    bound = tuple(
        value
        for term in short
        for value in (_like_pattern(term), _like_pattern(term), _like_pattern(term))
    )
    score = " + ".join(f"(CASE WHEN {one} THEN 1 ELSE 0 END)" for _ in short)
    try:
        return list(
            db.execute(
                "SELECT *, (" + score + ") AS match_hits FROM chunks "
                "WHERE user_id=? AND (" + " OR ".join(one for _ in short) + ") "
                "ORDER BY match_hits DESC, id LIMIT ?",
                (*bound, user_id, *bound, limit),
            )
        )
    except sqlite3.OperationalError as exc:
        raise CodingMemoryError("substring retrieval database operation failed") from exc


def sparse_candidates(
    db: sqlite3.Connection, terms: tuple[str, ...], user_id: str, limit: int
) -> list[sqlite3.Row]:
    """在 FTS 虚表上按 scope 查询预展开词项并限额."""
    if not terms:
        return []
    expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    scope = scope_token(user_id)
    expression = f'scope : "{scope}" AND ({{paths symbols text}} : ({expression}))'
    try:
        return list(
            db.execute(
                "SELECT c.* FROM chunk_fts JOIN chunks c ON c.rowid=chunk_fts.rowid "
                "WHERE c.user_id=? AND chunk_fts MATCH ? "
                "ORDER BY bm25(chunk_fts), c.id LIMIT ?",
                (user_id, expression, limit),
            )
        )
    except sqlite3.OperationalError as exc:
        raise CodingMemoryError("sparse retrieval database operation failed") from exc


def neighbors(
    db: sqlite3.Connection, user_id: str, chunk_id: str, window: int
) -> list[sqlite3.Row]:
    """读取同 request/session/message 的有限相邻切片."""
    if window <= 0:
        return []
    hit = db.execute(
        "SELECT request_id, session_id, message_id, ordinal FROM chunks "
        "WHERE user_id=? AND id=?",
        (user_id, chunk_id),
    ).fetchone()
    if not hit:
        return []
    ordinal = int(hit["ordinal"])
    return list(
        db.execute(
            "SELECT * FROM chunks WHERE user_id=? AND request_id=? "
            "AND session_id=? AND message_id=? AND ordinal BETWEEN ? AND ? "
            "ORDER BY ordinal, id",
            (
                user_id,
                hit["request_id"],
                hit["session_id"],
                hit["message_id"],
                ordinal - window,
                ordinal + window,
            ),
        )
    )
