"""Coding memory SQLite neighbor lookup and FTS query helpers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from dmsa.memory.coding.ids import SCOPE_SEPARATOR
from dmsa.memory.coding.models import CodingMemoryError
from dmsa.memory.coding.schema import scope_token
from dmsa.memory.coding.temporal import AsOf, as_of_filter
from dmsa.memory.coding.tokenize import GRAM


def _like_pattern(term: str) -> str:
    """把词项转成转义过的 LIKE 模式, 使 % 和 _ 按字面匹配."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def prefix_pattern(value: str) -> str:
    """把前缀转成转义过的 LIKE 前缀模式, 用于 scope 子域匹配."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def scope_partitions(db: sqlite3.Connection, user_id: str) -> tuple[str, ...]:
    """列出 scope 本身及其仓库子域, 供按用户完整清理数据.

    清单来自 `scope_epochs`, 已清空的 scope 仍保留 epoch 以维持指纹单调,
    因此可能返回不含数据的 scope; 对它们重复删除只是再次推进 epoch。
    """
    rows = db.execute(
        "SELECT user_id FROM scope_epochs "
        "WHERE user_id=? OR user_id LIKE ? ESCAPE '\\' ORDER BY user_id",
        (user_id, prefix_pattern(f"{user_id}{SCOPE_SEPARATOR}")),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def substring_candidates(
    db: sqlite3.Connection,
    terms: tuple[str, ...],
    user_id: str,
    limit: int,
    as_of: AsOf | None = None,
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
    clause, window = as_of_filter("c", as_of)
    try:
        return list(
            db.execute(
                "SELECT c.*, (" + score + ") AS match_hits FROM chunks c "
                "WHERE c.user_id=? AND (" + " OR ".join(one for _ in short) + ") " + clause + " "
                "ORDER BY match_hits DESC, c.id LIMIT ?",
                (*bound, user_id, *bound, *window, limit),
            )
        )
    except sqlite3.OperationalError as exc:
        raise CodingMemoryError("substring retrieval database operation failed") from exc


def sparse_candidates(
    db: sqlite3.Connection,
    terms: tuple[str, ...],
    user_id: str,
    limit: int,
    as_of: AsOf | None = None,
) -> list[sqlite3.Row]:
    """在 FTS 虚表上按 scope 查询预展开词项并限额.

    路径与符号是比正文更强的定位信号, 因此 BM25 给这两列更高权重, 正文只作兜底。
    """
    if not terms:
        return []
    expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    scope = scope_token(user_id)
    expression = f'scope : "{scope}" AND ({{paths symbols text}} : ({expression}))'
    clause, window = as_of_filter("c", as_of)
    try:
        return list(
            db.execute(
                "SELECT c.* FROM chunk_fts JOIN chunks c ON c.rowid=chunk_fts.rowid "
                "WHERE c.user_id=? AND chunk_fts MATCH ? " + clause + " "
                "ORDER BY bm25(chunk_fts, 6.0, 8.0, 1.0, 0.0), c.id LIMIT ?",
                (user_id, expression, *window, limit),
            )
        )
    except sqlite3.OperationalError as exc:
        raise CodingMemoryError("sparse retrieval database operation failed") from exc


def exact_candidates(
    db: sqlite3.Connection,
    terms: tuple[str, ...],
    user_id: str,
    limit: int,
    as_of: AsOf | None = None,
) -> list[sqlite3.Row]:
    """从 scope-filtered SQL 精确索引查路径与符号."""
    values = tuple(term for term in terms if len(term) > 2)
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    clause, window = as_of_filter("c", as_of)
    try:
        return list(
            db.execute(
                "SELECT c.*, COUNT(*) AS match_hits FROM exact_terms e "
                "JOIN chunks c ON c.id=e.chunk_id "
                "WHERE e.user_id=? AND c.user_id=? AND e.term IN ("
                + placeholders
                + ") "
                + clause
                + " GROUP BY c.id ORDER BY match_hits DESC, c.id LIMIT ?",
                (user_id, user_id, *(term.lower() for term in values), *window, limit),
            )
        )
    except sqlite3.OperationalError as exc:
        raise CodingMemoryError("exact retrieval database operation failed") from exc


def dense_candidates(
    db: sqlite3.Connection,
    user_id: str,
    fingerprint: str,
    limit: int,
    as_of: AsOf | None = None,
) -> tuple[int, list[tuple[sqlite3.Row, tuple[float, ...]]]]:
    """读取有界同 scope 持久向量候选; 计数同样按时序坐标收敛."""
    clause, window = as_of_filter("c", as_of)
    count = int(
        db.execute(
            "SELECT count(*) FROM chunks c WHERE c.user_id=?" + clause, (user_id, *window)
        ).fetchone()[0]
    )
    if count > limit:
        return count, []
    rows = db.execute(
        "SELECT c.*, v.vector FROM chunks c JOIN vectors v ON c.id=v.chunk_id "
        "WHERE c.user_id=? AND v.user_id=? AND v.fingerprint=?" + clause,
        (user_id, user_id, fingerprint, *window),
    )
    return count, [(r, tuple(json.loads(r["vector"]))) for r in rows]


def vectors_by_ids(
    db: sqlite3.Connection, user_id: str, ids: Sequence[str]
) -> dict[str, tuple[float, ...]]:
    """按同 scope 稳定 ID 读取已存向量, 供稠密通道复算相似度."""
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT chunk_id, vector FROM vectors WHERE user_id=? AND chunk_id IN ({placeholders})",
        (user_id, *ids),
    ).fetchall()
    result: dict[str, tuple[float, ...]] = {}
    for row in rows:
        try:
            result[str(row["chunk_id"])] = tuple(json.loads(str(row["vector"])))
        except (TypeError, ValueError):
            continue
    return result


def neighbors(
    db: sqlite3.Connection, user_id: str, chunk_id: str, window: int
) -> list[sqlite3.Row]:
    """读取同 request/session/message 的有限相邻切片."""
    if window <= 0:
        return []
    hit = db.execute(
        "SELECT request_id, session_id, message_id, ordinal FROM chunks WHERE user_id=? AND id=?",
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
