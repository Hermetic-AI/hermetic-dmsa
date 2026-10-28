"""记忆条目统计的读写: 曝光、确认、反驳、验证与淘汰.

统计只在被观测时增长, 因此未出现过的条目保持中性; 淘汰只打标记不删原文,
被淘汰的条目不再进入检索结果, 但仍可按 ID 追溯.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable

from dmsa.memory.coding.schema import STATS_SCHEMA
from dmsa.memory.coding.trust import MemoryStat, compute_trust


def ensure_stats_table(db: sqlite3.Connection) -> None:
    """确保条目统计表存在.

    RSI 侧可能先于 CodingMemory 建库, 记账前补一次表结构即可,
    不触碰其它表, 也不会覆盖既有统计.
    """
    db.executescript(STATS_SCHEMA)
    db.commit()


def now_seconds() -> int:
    """返回当前 Unix 秒."""
    return int(time.time())


def ensure_stats(
    db: sqlite3.Connection, user_id: str, chunk_id: str, *, revision: int, created_at: int
) -> None:
    """为新写入的切片建立中性统计行; 已存在时不覆盖既有观测."""
    db.execute(
        "INSERT INTO memory_stats(chunk_id, user_id, first_revision, created_at, updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(user_id, chunk_id) DO NOTHING",
        (chunk_id, user_id, revision, created_at, created_at),
    )


def record_exposure(
    db: sqlite3.Connection, user_id: str, chunk_ids: Iterable[str], *, now: int | None = None
) -> int:
    """记录条目被检索返回的曝光次数并刷新最后使用时间."""
    moment = now_seconds() if now is None else now
    count = 0
    for chunk in chunk_ids:
        db.execute(
            "INSERT INTO memory_stats(chunk_id, user_id, retrieved, created_at, updated_at, "
            "last_used) VALUES(?,?,1,?,?,?) ON CONFLICT(user_id, chunk_id) DO UPDATE SET "
            "retrieved=retrieved+1, last_used=excluded.last_used, updated_at=excluded.updated_at",
            (chunk, user_id, moment, moment, moment),
        )
        count += 1
    return count


def apply_verdict(
    db: sqlite3.Connection,
    user_id: str,
    chunk_ids: Iterable[str],
    *,
    confirmed: bool = False,
    contradicted: bool = False,
    verified: bool = False,
    now: int | None = None,
) -> int:
    """按反馈或任务结果更新条目的确认、反驳与验证计数."""
    moment = now_seconds() if now is None else now
    count = 0
    for chunk in chunk_ids:
        db.execute(
            "INSERT INTO memory_stats(chunk_id, user_id, created_at, updated_at, "
            "confirmed, contradicted, verified) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, chunk_id) DO UPDATE SET "
            "confirmed=confirmed+excluded.confirmed, "
            "contradicted=contradicted+excluded.contradicted, "
            "verified=verified+excluded.verified, updated_at=excluded.updated_at",
            (
                chunk,
                user_id,
                moment,
                moment,
                1 if confirmed else 0,
                1 if contradicted else 0,
                1 if verified else 0,
            ),
        )
        count += 1
    return count


def _row_to_stat(row: sqlite3.Row) -> MemoryStat:
    """把数据库行转成统计快照."""
    return MemoryStat(
        chunk_id=str(row["chunk_id"]),
        user_id=str(row["user_id"]),
        retrieved=int(row["retrieved"]),
        confirmed=int(row["confirmed"]),
        contradicted=int(row["contradicted"]),
        verified=int(row["verified"]),
        evicted=bool(row["evicted"]),
        first_revision=int(row["first_revision"]),
        created_at=int(row["created_at"]),
        last_used=None if row["last_used"] is None else int(row["last_used"]),
        updated_at=int(row["updated_at"]),
    )


def stats_by_ids(
    db: sqlite3.Connection, user_id: str, chunk_ids: Iterable[str]
) -> dict[str, MemoryStat]:
    """按切片 ID 读取统计快照."""
    ids = tuple(chunk_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT * FROM memory_stats WHERE user_id=? AND chunk_id IN ({placeholders})",
        (user_id, *ids),
    ).fetchall()
    return {str(row["chunk_id"]): _row_to_stat(row) for row in rows}


def trust_map(
    db: sqlite3.Connection, user_id: str, chunk_ids: Iterable[str], *, now: int | None = None
) -> dict[str, float]:
    """返回切片 ID 到当前信任度的映射."""
    moment = now_seconds() if now is None else now
    stats = stats_by_ids(db, user_id, chunk_ids)
    return {chunk: compute_trust(stat, now=moment) for chunk, stat in stats.items()}


def evicted_ids(db: sqlite3.Connection, user_id: str, chunk_ids: Iterable[str]) -> set[str]:
    """返回给定切片中已被淘汰的 ID."""
    ids = tuple(chunk_ids)
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT chunk_id FROM memory_stats WHERE user_id=? AND evicted=1 "
        f"AND chunk_id IN ({placeholders})",
        (user_id, *ids),
    ).fetchall()
    return {str(row["chunk_id"]) for row in rows}


def scope_stats(db: sqlite3.Connection, user_id: str) -> list[MemoryStat]:
    """读取 scope 内全部条目统计."""
    rows = db.execute(
        "SELECT * FROM memory_stats WHERE user_id=? ORDER BY chunk_id", (user_id,)
    ).fetchall()
    return [_row_to_stat(row) for row in rows]


def evict_below(
    db: sqlite3.Connection,
    user_id: str,
    *,
    floor: float,
    min_age_seconds: int,
    now: int | None = None,
) -> tuple[str, ...]:
    """把信任度低于阈值且长期未命中的条目标记为淘汰; 返回被淘汰的 ID."""
    moment = now_seconds() if now is None else now
    evicted: list[str] = []
    for stat in scope_stats(db, user_id):
        if stat.evicted:
            continue
        anchor = stat.last_used if stat.last_used is not None else stat.created_at
        if moment - anchor < min_age_seconds:
            continue
        if compute_trust(stat, now=moment) >= floor:
            continue
        db.execute(
            "UPDATE memory_stats SET evicted=1, updated_at=? WHERE user_id=? AND chunk_id=?",
            (moment, user_id, stat.chunk_id),
        )
        evicted.append(stat.chunk_id)
    return tuple(evicted)


def unevict(db: sqlite3.Connection, user_id: str, chunk_ids: Iterable[str]) -> int:
    """取消淘汰标记, 让条目重新参与检索."""
    count = 0
    for chunk in chunk_ids:
        db.execute(
            "UPDATE memory_stats SET evicted=0, updated_at=? WHERE user_id=? AND chunk_id=?",
            (now_seconds(), user_id, chunk),
        )
        count += 1
    return count
