"""代码锚点的持久化与 scope 内查询."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from dmsa.memory.coding.code_anchors import CodeAnchor, anchor_terms
from dmsa.memory.coding.temporal import AsOf, as_of_filter

# 单个切片最多写入的锚点词项数, 防止长日志放大索引体积.
MAX_TERMS_PER_CHUNK = 24


def insert_anchors(
    db: sqlite3.Connection, user_id: str, chunk_id: str, anchors: tuple[CodeAnchor, ...]
) -> None:
    """在既有事务内写入切片的锚点词项."""
    rows: list[tuple[str, ...]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for anchor in anchors:
        for term in anchor_terms((anchor,)):
            key = (term, anchor.path, anchor.symbol, anchor.kind)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                (
                    user_id,
                    chunk_id,
                    term,
                    anchor.path,
                    anchor.symbol,
                    f"{anchor.kind}:{anchor.confidence:.2f}",
                )
            )
            if len(rows) >= MAX_TERMS_PER_CHUNK:
                break
        if len(rows) >= MAX_TERMS_PER_CHUNK:
            break
    for row in rows:
        db.execute("INSERT INTO code_anchors VALUES(?,?,?,?,?,?)", row)


def anchor_candidates(
    db: sqlite3.Connection,
    terms: tuple[str, ...],
    user_id: str,
    limit: int,
    as_of: AsOf | None = None,
) -> list[sqlite3.Row]:
    """按 scope 召回锚点词项命中的切片, 命中数越多越靠前."""
    values = tuple(dict.fromkeys(term.lower() for term in terms if len(term) > 2))
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    clause, window = as_of_filter("c", as_of)
    return list(
        db.execute(
            "SELECT c.*, COUNT(DISTINCT a.term) AS anchor_hits "
            "FROM code_anchors a JOIN chunks c ON c.id=a.chunk_id "
            "WHERE a.user_id=? AND c.user_id=? AND a.term IN ("
            + placeholders
            + ") "
            + clause
            + " GROUP BY c.id ORDER BY anchor_hits DESC, c.id LIMIT ?",
            (user_id, user_id, *values, *window, limit),
        )
    )


def insert_relations(
    db: sqlite3.Connection, user_id: str, chunk_id: str, anchors: tuple[CodeAnchor, ...]
) -> None:
    """在既有事务内写入工程关系: 定义符号、涉及文件与验证测试."""
    rows: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for anchor in anchors:
        pairs: tuple[tuple[str, str], ...] = ()
        if anchor.kind == "defines" and anchor.symbol:
            pairs = (("defines_symbol", anchor.symbol),)
        elif anchor.kind == "test" and anchor.symbol:
            pairs = (("validates_test", anchor.symbol),)
        elif anchor.path:
            pairs = (("touches_file", anchor.path),)
        for rel_type, target in pairs:
            key = (rel_type, target)
            if key in seen:
                continue
            seen.add(key)
            rows.append((user_id, chunk_id, rel_type, target))
    for row in rows:
        db.execute("INSERT INTO code_relations VALUES(?,?,?,?)", row)


def relation_neighbors(
    db: sqlite3.Connection,
    user_id: str,
    chunk_id: str,
    limit: int,
    as_of: AsOf | None = None,
) -> tuple[str, ...]:
    """返回与给定切片共享工程关系目标的其它切片 ID.

    关系邻居可能来自更晚的写入, 因此按时序坐标过滤后才会被扩展进证据包.
    """
    if limit <= 0:
        return ()
    clause, window = as_of_filter("c", as_of)
    rows = db.execute(
        "SELECT DISTINCT r2.chunk_id FROM code_relations r1 "
        "JOIN code_relations r2 ON r2.target=r1.target AND r2.user_id=r1.user_id "
        "JOIN chunks c ON c.id=r2.chunk_id "
        "WHERE r1.user_id=? AND r1.chunk_id=? AND r2.chunk_id<>? " + clause + " "
        "ORDER BY r2.chunk_id LIMIT ?",
        (user_id, chunk_id, chunk_id, *window, limit),
    ).fetchall()
    return tuple(str(row["chunk_id"]) for row in rows)


def anchors_by_ids(
    db: sqlite3.Connection, user_id: str, chunk_ids: Iterable[str]
) -> dict[str, tuple[CodeAnchor, ...]]:
    """按切片 ID 读取锚点, 供证据包渲染结构化定位信息."""
    ids = tuple(dict.fromkeys(chunk_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        "SELECT chunk_id, path, symbol, kind FROM code_anchors "
        f"WHERE user_id=? AND chunk_id IN ({placeholders})",
        (user_id, *ids),
    ).fetchall()
    grouped: dict[str, list[CodeAnchor]] = {}
    for row in rows:
        kind, _, confidence = str(row["kind"]).partition(":")
        grouped.setdefault(str(row["chunk_id"]), []).append(
            CodeAnchor(
                str(row["path"]),
                str(row["symbol"]),
                kind,
                float(confidence) if confidence else 0.0,
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}
