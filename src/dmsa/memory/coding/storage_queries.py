"""Coding memory SQLite neighbor lookup helper."""

from __future__ import annotations

import sqlite3


def neighbors(
    db: sqlite3.Connection, user_id: str, chunk_id: str, window: int
) -> list[sqlite3.Row]:
    """读取同 request/session/message 的有限相邻切片。."""
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
