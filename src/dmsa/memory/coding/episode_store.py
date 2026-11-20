"""经验卡的会话级合并与入库."""

from __future__ import annotations

import sqlite3

from dmsa.memory.coding.episodes import SUPERSEDED_OUTCOME, EpisodeCard
from dmsa.memory.coding.ids import chunk_id, message_id
from dmsa.memory.coding.models import SourceMessage
from dmsa.memory.coding.schema import scope_token

EPISODE_ROLE = "episode"
# lineage 边类型是封闭集合; 与 PMD EdgeType 的 DERIVED_FROM 语义对齐, 但这里
# 存的是 chunk id 而不是 PMD 节点, 保持原文表为唯一事实来源。
REL_DERIVED_FROM = "DERIVED_FROM"
REL_SUPERSEDED_BY = "SUPERSEDED_BY"


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


def record_derivation(
    db: sqlite3.Connection,
    user_id: str,
    card_id: str,
    source_ids: tuple[str, ...],
    created_at: int,
) -> None:
    """登记经验卡到本次原文切片的派生边.

    只写元数据, 不复制原文; 派生边是后续"验证强度沿来源回传"的依据.
    """
    for source in source_ids:
        if source == card_id:
            continue
        db.execute(
            "INSERT OR IGNORE INTO memory_lineage VALUES(?,?,?,?,?)",
            (user_id, card_id, source, REL_DERIVED_FROM, created_at),
        )


def drop_other_episodes(
    db: sqlite3.Connection,
    user_id: str,
    session_id: str,
    keep_id: str,
    created_at: int = 0,
) -> None:
    """把同一会话更早的经验卡标为 superseded 并登记指向新卡的边.

    不再物理删除: 旧卡仍可由精确词项与 FTS 召回, 检索侧才能识别"最像的那条
    其实已被取代". 默认策略下 superseded 卡仍被装包阶段过滤, 行为与旧版一致.
    """
    rows = db.execute(
        "SELECT id FROM chunks WHERE user_id=? AND session_id=? AND role=? AND id<>?",
        (user_id, session_id, EPISODE_ROLE, keep_id),
    ).fetchall()
    for row in rows:
        old_id = str(row["id"])
        db.execute(
            "UPDATE chunks SET outcome=? WHERE user_id=? AND id=?",
            (SUPERSEDED_OUTCOME, user_id, old_id),
        )
        db.execute(
            "INSERT OR IGNORE INTO memory_lineage VALUES(?,?,?,?,?)",
            (user_id, old_id, keep_id, REL_SUPERSEDED_BY, created_at),
        )
