"""请求发布: 原文、切片、向量、经验卡与 revision 的事务写入."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from dmsa.memory.coding.episode_store import (
    drop_other_episodes,
    insert_episode,
    propagate_outcome,
    session_messages,
)
from dmsa.memory.coding.episodes import build_episode
from dmsa.memory.coding.extraction import Chunk
from dmsa.memory.coding.ids import chunk_id, message_id
from dmsa.memory.coding.models import (
    CodingMemoryConflictError,
    SourceMessage,
)
from dmsa.memory.coding.schema import scope_token


def existing_revision(
    db: sqlite3.Connection, request_id: str, user_id: str, payload_hash: str
) -> int | None:
    """幂等预检; 内容冲突时显式报错."""
    row = db.execute(
        "SELECT payload_hash, revision FROM requests WHERE user_id=? AND request_id=?",
        (user_id, request_id),
    ).fetchone()
    if row is None:
        return None
    if row["payload_hash"] != payload_hash:
        raise CodingMemoryConflictError("idempotency key payload conflict")
    return int(row["revision"])


def publish_request(
    db: sqlite3.Connection,
    user_id: str,
    request_id: str,
    session_id: str,
    digest: str,
    messages: tuple[SourceMessage, ...],
    chunks: Iterable[Chunk],
    vectors: list[tuple[float, ...]],
    fingerprint: str | None,
) -> int:
    """在已开启的事务内发布 request、原文、切片、向量、经验卡和 revision."""
    old = existing_revision(db, request_id, user_id, digest)
    if old is not None:
        return old
    episode = build_episode(session_messages(db, user_id, session_id, messages))
    row = db.execute("SELECT revision FROM corpus WHERE user_id=?", (user_id,)).fetchone()
    epoch = db.execute("SELECT epoch FROM scope_epochs WHERE user_id=?", (user_id,)).fetchone()
    revision = max(int(row[0]) if row else 0, int(epoch[0]) if epoch else 0) + 1
    db.execute(
        "INSERT INTO requests VALUES(?,?,?,?,?)",
        (user_id, request_id, session_id, digest, revision),
    )
    for ordinal, message in enumerate(messages):
        mid = message_id(user_id, request_id, ordinal)
        db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)",
            (
                mid,
                user_id,
                request_id,
                session_id,
                ordinal,
                message.role,
                message.timestamp,
                message.content,
            ),
        )
    for chunk_index, chunk in enumerate(chunks):
        mid = message_id(user_id, request_id, chunk.message_index)
        cid = chunk_id(mid, chunk.start, chunk.end)
        message = messages[chunk.message_index]
        db.execute(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cid,
                user_id,
                request_id,
                session_id,
                mid,
                chunk_index,
                chunk.start,
                chunk.end,
                message.role,
                chunk.outcome,
                " ".join(chunk.paths),
                " ".join(chunk.symbols),
                chunk.text,
                scope_token(user_id),
            ),
        )
        for term in {value.lower() for value in (*chunk.paths, *chunk.symbols)}:
            db.execute("INSERT INTO exact_terms VALUES(?,?,?)", (user_id, cid, term))
        if fingerprint is not None:
            vector = vectors[chunk_index]
            db.execute(
                "INSERT INTO vectors VALUES(?,?,?,?,?)",
                (cid, user_id, fingerprint, len(vector), json.dumps(vector)),
            )
    db.execute(
        "INSERT INTO corpus VALUES(?,?) ON CONFLICT(user_id) "
        "DO UPDATE SET revision=excluded.revision",
        (user_id, revision),
    )
    db.execute(
        "INSERT INTO scope_epochs VALUES(?,?) ON CONFLICT(user_id) "
        "DO UPDATE SET epoch=excluded.epoch",
        (user_id, revision),
    )
    db.execute(
        "UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='corpus_generation'"
    )
    if episode is not None:
        episode_id = insert_episode(
            db, user_id, request_id, session_id, len(messages), episode
        )
        drop_other_episodes(db, user_id, session_id, episode_id)
        if episode.outcome == "reported_success":
            propagate_outcome(db, user_id, session_id, episode.patch_path)
    return revision
