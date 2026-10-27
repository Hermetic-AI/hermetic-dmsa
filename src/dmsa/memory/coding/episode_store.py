"""经验卡的会话级合并与入库."""

from __future__ import annotations

import sqlite3

from dmsa.memory.coding.episodes import EpisodeCard
from dmsa.memory.coding.ids import chunk_id, message_id
from dmsa.memory.coding.models import SourceMessage
from dmsa.memory.coding.schema import scope_token

EPISODE_ROLE = "episode"


def session_messages(
    db: sqlite3.Connection,
    user_id: str,
    session_id: str,
    new_messages: tuple[SourceMessage, ...],
) -> tuple[SourceMessage, ...]:
    """按写入顺序合并既有会话消息与本次新消息."""
    rows = list(
        db.execute(
            "SELECT m.role, m.content, m.timestamp FROM messages m "
            "JOIN requests r ON r.user_id=m.user_id AND r.request_id=m.request_id "
            "WHERE m.user_id=? AND m.session_id=? ORDER BY r.revision, m.ordinal",
            (user_id, session_id),
        )
    )
    merged = [SourceMessage(row["role"], row["content"], row["timestamp"]) for row in rows]
    merged.extend(new_messages)
    return tuple(merged)


def insert_episode(
    db: sqlite3.Connection,
    user_id: str,
    request_id: str,
    session_id: str,
    ordinal: int,
    card: EpisodeCard,
) -> str:
    """把经验卡写成独立证据切片并登记精确词项."""
    message_key = message_id(user_id, request_id, ordinal)
    chunk_key = chunk_id(message_key, 0, len(card.text))
    db.execute(
        "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            chunk_key,
            user_id,
            request_id,
            session_id,
            message_key,
            ordinal,
            0,
            len(card.text),
            EPISODE_ROLE,
            card.outcome,
            " ".join(card.paths),
            " ".join(card.symbols),
            card.text,
            scope_token(user_id),
        ),
    )
    for term in {value.lower() for value in (*card.paths, *card.symbols)}:
        db.execute("INSERT INTO exact_terms VALUES(?,?,?)", (user_id, chunk_key, term))
    return chunk_key


def propagate_outcome(
    db: sqlite3.Connection, user_id: str, session_id: str, patch_path: str
) -> None:
    """会话末次验证通过时, 把同会话该补丁文件的切片标记为已验证."""
    if not patch_path:
        return
    db.execute(
        "UPDATE chunks SET outcome='reported_success' WHERE user_id=? AND session_id=? "
        "AND outcome='unknown' AND role<>? AND (paths LIKE ? OR text LIKE ?)",
        (
            user_id,
            session_id,
            EPISODE_ROLE,
            f"%{patch_path}%",
            f"%path={patch_path}%",
        ),
    )


def drop_other_episodes(
    db: sqlite3.Connection, user_id: str, session_id: str, keep_id: str
) -> None:
    """删除同一会话里更早的经验卡, 只保留最新一张."""
    db.execute(
        "DELETE FROM exact_terms WHERE chunk_id IN ("
        "SELECT id FROM chunks WHERE user_id=? AND session_id=? AND role=? AND id<>?)",
        (user_id, session_id, EPISODE_ROLE, keep_id),
    )
    db.execute(
        "DELETE FROM chunks WHERE user_id=? AND session_id=? AND role=? AND id<>?",
        (user_id, session_id, EPISODE_ROLE, keep_id),
    )
