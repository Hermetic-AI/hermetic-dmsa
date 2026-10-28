"""团队共享作用域与写冲突仲裁.

个人作用域里, 与既有条目近乎重复的写入是噪音, 门控直接拒绝. 团队作用域里
同一个问题会被不同成员各写一遍, 双方可能给出相反结论, 这样的近似重复不是
噪音而是待仲裁的分歧: 写入被保留并登记为冲突候选, 由仲裁决定哪一方是权威版本.

仲裁结论同时是信任证据: 被保留的一方记「已确认」, 被否定的一方记「已反驳」,
因此冲突解决会立刻反馈到检索排序, 而不是停在一条待办列表里.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.admission import jaccard, tokenize
from dmsa.memory.coding.ids import scope_key
from dmsa.memory.coding.models import (
    CodingMemoryValidationError,
    require_identifier,
)
from dmsa.memory.coding.trust_store import apply_verdict, ensure_stats_table, now_seconds

# 团队作用域前缀: 与个人作用域区分, 团队证据不会被按个人清理误删.
TEAM_PREFIX = "team:"

# 仲裁处置方式: 保留新写入、保留既有写入, 或两者并存且不做信任惩罚.
RESOLUTION_KEEP_NEW = "keep_new"
RESOLUTION_KEEP_RIVAL = "keep_rival"
RESOLUTION_KEEP_BOTH = "keep_both"
RESOLUTIONS: tuple[str, ...] = (
    RESOLUTION_KEEP_NEW,
    RESOLUTION_KEEP_RIVAL,
    RESOLUTION_KEEP_BOTH,
)

# 冲突状态: 待仲裁与已仲裁.
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"


def team_scope(team_id: str, repo: str = "") -> str:
    """返回团队共享作用域键: 成员共享同一证据空间, 仓库仍互相隔离.

    团队作用域带 ``team:`` 前缀, 因此按个人清理数据时不会误删团队证据,
    团队内部的仓库子域依旧按仓库隔离.
    """
    require_identifier(team_id, "team_id")
    return scope_key(f"{TEAM_PREFIX}{team_id}", repo)


@dataclass(frozen=True)
class WriteConflict:
    """一次跨作者写冲突的登记记录."""

    id: str
    scope: str
    chunk_id: str
    rival_chunk_id: str
    author: str
    rival_author: str
    similarity: float
    status: str
    resolution: str
    created_at: int
    resolved_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的冲突视图."""
        return {
            "id": self.id,
            "scope": self.scope,
            "chunk_id": self.chunk_id,
            "rival_chunk_id": self.rival_chunk_id,
            "author": self.author,
            "rival_author": self.rival_author,
            "similarity": round(self.similarity, 6),
            "status": self.status,
            "resolution": self.resolution,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


@dataclass(frozen=True)
class ConflictResolution:
    """一次仲裁的结论: 冲突状态与落到条目上的信任增量."""

    conflict: WriteConflict
    applied: bool
    confirmed: tuple[str, ...] = ()
    contradicted: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的仲裁结论视图."""
        return {
            "conflict": self.conflict.to_dict(),
            "applied": self.applied,
            "confirmed": list(self.confirmed),
            "contradicted": list(self.contradicted),
        }


def conflict_id(scope: str, chunk_id: str, rival_chunk_id: str) -> str:
    """产生跨作者写冲突的稳定 ID."""
    return hashlib.sha256("\0".join((scope, chunk_id, rival_chunk_id)).encode()).hexdigest()


def record_author(db: sqlite3.Connection, scope: str, chunk_id: str, author: str) -> None:
    """登记切片作者; 未署名时不留记录, 视为个人作用域写入."""
    if not author:
        return
    db.execute(
        "INSERT INTO write_authors(user_id, chunk_id, author) VALUES(?,?,?) "
        "ON CONFLICT(user_id, chunk_id) DO UPDATE SET author=excluded.author",
        (scope, chunk_id, author),
    )


def author_of(db: sqlite3.Connection, scope: str, chunk_id: str) -> str:
    """读取切片作者; 未登记时返回空字符串."""
    row = db.execute(
        "SELECT author FROM write_authors WHERE user_id=? AND chunk_id=?", (scope, chunk_id)
    ).fetchone()
    return "" if row is None else str(row["author"])


def _row_to_conflict(row: sqlite3.Row) -> WriteConflict:
    """把数据库行转成冲突快照."""
    return WriteConflict(
        id=str(row["id"]),
        scope=str(row["user_id"]),
        chunk_id=str(row["chunk_id"]),
        rival_chunk_id=str(row["rival_chunk_id"]),
        author=str(row["author"]),
        rival_author=str(row["rival_author"]),
        similarity=float(row["similarity"]),
        status=str(row["status"]),
        resolution=str(row["resolution"]),
        created_at=int(row["created_at"]),
        resolved_at=None if row["resolved_at"] is None else int(row["resolved_at"]),
    )


def register_conflict(
    db: sqlite3.Connection,
    scope: str,
    author: str,
    new_chunks: tuple[tuple[str, str], ...],
    rival_chunk_id: str,
    similarity: float,
    *,
    now: int | None = None,
) -> WriteConflict | None:
    """登记一次跨作者写冲突; 对手切片不存在时返回 None.

    ``new_chunks`` 是本次写入的 (切片 ID, 正文) 列表; 登记时选出与对手切片
    最接近的一条作为冲突代表, 避免把整次写入都算成同一条冲突.
    """
    rival = db.execute(
        "SELECT text FROM chunks WHERE user_id=? AND id=?", (scope, rival_chunk_id)
    ).fetchone()
    if rival is None:
        return None
    rival_tokens = tokenize(str(rival["text"]))
    best_id: str | None = None
    best_score = 0.0
    for chunk_id, text in new_chunks:
        score = jaccard(tokenize(text), rival_tokens)
        if score > best_score:
            best_id, best_score = chunk_id, score
    if best_id is None:
        return None
    moment = now_seconds() if now is None else now
    identifier = conflict_id(scope, best_id, rival_chunk_id)
    db.execute(
        "INSERT INTO write_conflicts(id, user_id, chunk_id, rival_chunk_id, author, "
        "rival_author, similarity, status, resolution, created_at, resolved_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
        (
            identifier,
            scope,
            best_id,
            rival_chunk_id,
            author,
            author_of(db, scope, rival_chunk_id),
            similarity,
            STATUS_OPEN,
            "",
            moment,
            None,
        ),
    )
    row = db.execute("SELECT * FROM write_conflicts WHERE id=?", (identifier,)).fetchone()
    return None if row is None else _row_to_conflict(row)


def list_conflicts(
    db: sqlite3.Connection, scope: str, *, status: str = "", limit: int = 20
) -> list[WriteConflict]:
    """列出作用域内的写冲突; status 为空时返回全部状态."""
    sql = "SELECT * FROM write_conflicts WHERE user_id=?"
    params: list[Any] = [scope]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC, id LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [_row_to_conflict(row) for row in rows]


def find_conflict(db: sqlite3.Connection, scope: str, identifier: str) -> WriteConflict | None:
    """按作用域读取一条写冲突; 不存在时返回 None."""
    row = db.execute(
        "SELECT * FROM write_conflicts WHERE user_id=? AND id=?", (scope, identifier)
    ).fetchone()
    return None if row is None else _row_to_conflict(row)


def resolve_conflict(
    db: sqlite3.Connection,
    scope: str,
    identifier: str,
    resolution: str,
    *,
    now: int | None = None,
) -> ConflictResolution:
    """按仲裁结论关闭一条冲突, 并把结论写回双方条目的信任计数.

    重复仲裁同一条冲突不会重复记账, 直接返回已关闭的结论.
    """
    if resolution not in RESOLUTIONS:
        raise CodingMemoryValidationError("resolution must be a registered conflict resolution")
    conflict = find_conflict(db, scope, identifier)
    if conflict is None:
        raise CodingMemoryValidationError("unknown conflict id")
    if conflict.status == STATUS_RESOLVED:
        return ConflictResolution(conflict=conflict, applied=False)
    confirmed: tuple[str, ...] = ()
    contradicted: tuple[str, ...] = ()
    if resolution == RESOLUTION_KEEP_NEW:
        confirmed, contradicted = (conflict.chunk_id,), (conflict.rival_chunk_id,)
    elif resolution == RESOLUTION_KEEP_RIVAL:
        confirmed, contradicted = (conflict.rival_chunk_id,), (conflict.chunk_id,)
    moment = now_seconds() if now is None else now
    ensure_stats_table(db)
    if confirmed:
        apply_verdict(db, scope, confirmed, confirmed=True)
    if contradicted:
        apply_verdict(db, scope, contradicted, contradicted=True)
    db.execute(
        "UPDATE write_conflicts SET status=?, resolution=?, resolved_at=? WHERE id=?",
        (STATUS_RESOLVED, resolution, moment, conflict.id),
    )
    updated = find_conflict(db, scope, identifier)
    resolved = conflict if updated is None else updated
    return ConflictResolution(
        conflict=resolved,
        applied=True,
        confirmed=confirmed,
        contradicted=contradicted,
    )
