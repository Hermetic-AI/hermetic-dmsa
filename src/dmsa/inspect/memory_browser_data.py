"""以只读本地 HTTP 页面浏览 CodingMemory SQLite 记忆。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dmsa.memory.coding.experiences import extract_experience_card
from dmsa.memory.coding.extraction import Chunk

MAX_SCAN_ROWS = 20000
PAGE_SIZE = 40
SORTS = {"newest", "oldest", "relevance", "scope"}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """以 SQLite URI read-only 模式打开数据文件。"""
    target = quote(path.expanduser().resolve().as_posix(), safe="/:\\")
    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def read_metadata(path: Path) -> dict[str, Any]:
    """读取计数、范围和检索维度，不返回记忆正文。"""
    with closing(_connect_readonly(path)) as db:
        tables = {
            str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"requests", "messages", "chunks", "vectors"}
        if not required.issubset(tables):
            raise ValueError("database is not a supported DMSA CodingMemory store")
        counts = {
            "requests": int(db.execute("SELECT count(*) FROM requests").fetchone()[0]),
            "messages": int(db.execute("SELECT count(*) FROM messages").fetchone()[0]),
            "chunks": int(db.execute("SELECT count(*) FROM chunks").fetchone()[0]),
            "vectors": int(db.execute("SELECT count(*) FROM vectors").fetchone()[0]),
            "scopes": int(db.execute("SELECT count(DISTINCT user_id) FROM chunks").fetchone()[0]),
        }
        scopes = [
            str(row[0]) for row in db.execute("SELECT DISTINCT user_id FROM chunks ORDER BY 1")
        ]
        roles = [str(row[0]) for row in db.execute("SELECT DISTINCT role FROM chunks ORDER BY 1")]
        outcomes = [
            str(row[0]) for row in db.execute("SELECT DISTINCT outcome FROM chunks ORDER BY 1")
        ]
        fingerprints = [
            {"name": str(row[0]), "dimension": int(row[1]), "count": int(row[2])}
            for row in db.execute(
                "SELECT fingerprint, dimension, count(*) FROM vectors "
                "GROUP BY fingerprint, dimension ORDER BY fingerprint, dimension"
            )
        ]
    return {
        "database": str(path.expanduser().resolve()),
        "backend": "SQLite (DMSA source of truth)",
        "counts": counts,
        "scopes": scopes,
        "roles": roles,
        "outcomes": outcomes,
        "vector_models": fingerprints,
        "vector_note": (
            "Qdrant is an optional derived ANN index; source memory text remains in SQLite. "
            "Noise tier is not a core schema field unless recorded in message content."
        ),
        "read_only": True,
    }


def _filters(query: dict[str, list[str]]) -> tuple[list[str], list[Any]]:
    """构造参数化 scope、来源、标签、时间与文本条件。"""
    clauses: list[str] = []
    values: list[Any] = []
    columns = {
        "scope": "c.user_id",
        "request": "c.request_id",
        "session": "c.session_id",
        "role": "c.role",
        "outcome": "c.outcome",
        "embedding": "v.fingerprint",
        "dimension": "v.dimension",
        "revision": "r.revision",
        "message_index": "m.ordinal",
    }
    for key, column in columns.items():
        value = query.get(key, [""])[0].strip()
        if value:
            clauses.append(f"{column}=?")
            values.append(value)
    for key, column in (("path", "c.paths"), ("symbol", "c.symbols")):
        value = query.get(key, [""])[0].strip()
        if value:
            clauses.append(f"instr(lower({column}), lower(?))>0")
            values.append(value)
    vector = query.get("vector", [""])[0]
    if vector in {"yes", "no"}:
        clauses.append("(v.chunk_id IS NOT NULL) = ?")
        values.append(vector == "yes")
    text = query.get("q", [""])[0].strip()
    if text:
        searchable = (
            "instr(lower(c.text || ' ' || c.paths || ' ' || c.symbols || ' ' || "
            "c.request_id || ' ' || c.session_id || ' ' || c.user_id), lower(?))>0"
        )
        for term in text.split():
            clauses.append(searchable)
            values.append(term)
    for key, op in (("from", ">="), ("to", "<=")):
        value = query.get(key, [""])[0]
        if value:
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise ValueError("date filters must be Unix timestamps") from exc
            clauses.append(f"m.timestamp {op} ?")
            values.append(timestamp)
    for key, column, op in (
        ("char_from", "c.start", ">="),
        ("char_to", "c.end", "<="),
    ):
        value = query.get(key, [""])[0]
        if value:
            try:
                number = int(value)
            except ValueError as exc:
                raise ValueError("character range filters must be integers") from exc
            clauses.append(f"{column} {op} ?")
            values.append(number)
    return clauses, values


def _record(row: sqlite3.Row) -> dict[str, Any]:
    """组合来源、切片、经验槽位和向量元数据。"""
    text = str(row["text"])
    paths = tuple(str(row["paths"]).split())
    symbols = tuple(str(row["symbols"]).split())
    card = extract_experience_card(
        Chunk(
            int(row["message_index"]),
            int(row["start"]),
            int(row["end"]),
            text,
            paths,
            symbols,
            str(row["outcome"]),
        )
    )
    return {
        "id": str(row["id"]),
        "scope": str(row["user_id"]),
        "request_id": str(row["request_id"]),
        "session_id": str(row["session_id"]),
        "message_id": str(row["message_id"]),
        "message_index": int(row["message_index"]),
        "role": str(row["role"]),
        "timestamp": row["timestamp"],
        "revision": row["revision"],
        "start": int(row["start"]),
        "end": int(row["end"]),
        "outcome": str(row["outcome"]),
        "paths": paths,
        "symbols": symbols,
        "text": text,
        "source_content": str(row["source_content"] or ""),
        "kind": card.kind,
        "symptom": card.symptom,
        "root_cause": card.root_cause,
        "action": card.action,
        "failed_attempts": card.failed_attempts,
        "validation": card.validation,
        "preconditions": card.preconditions,
        "evidence": card.evidence,
        "vector_fingerprint": row["fingerprint"],
        "vector_dimension": row["dimension"],
        "has_vector": row["fingerprint"] is not None,
    }


def read_records(path: Path, query: dict[str, list[str]]) -> dict[str, Any]:
    """筛选来源记录，按稳定顺序分页并返回完整 provenance。"""
    clauses, values = _filters(query)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = (
        "SELECT c.*, COALESCE(m.ordinal,c.ordinal) AS message_index, m.timestamp, "
        "m.content AS source_content, r.revision, v.fingerprint, v.dimension "
        "FROM chunks c LEFT JOIN messages m ON m.id=c.message_id "
        "LEFT JOIN requests r ON r.user_id=c.user_id AND r.request_id=c.request_id "
        "LEFT JOIN vectors v ON v.chunk_id=c.id AND v.user_id=c.user_id"
        + where
        + " ORDER BY c.user_id, c.request_id, c.ordinal, c.start LIMIT ?"
    )
    with closing(_connect_readonly(path)) as db:
        rows = db.execute(sql, (*values, MAX_SCAN_ROWS + 1)).fetchall()
    if len(rows) > MAX_SCAN_ROWS:
        raise ValueError("too many records to scan; add a scope or other filter")
    records = [_record(row) for row in rows]
    kind = query.get("kind", [""])[0]
    if kind:
        records = [row for row in records if row["kind"] == kind]
    facet = query.get("facet", [""])[0]
    if facet in {
        "has_symptom",
        "has_cause",
        "has_action",
        "has_failed",
        "has_validation",
        "has_condition",
    }:
        field = {
            "has_symptom": "symptom",
            "has_cause": "root_cause",
            "has_action": "action",
            "has_failed": "failed_attempts",
            "has_validation": "validation",
            "has_condition": "preconditions",
        }[facet]
        records = [row for row in records if row[field]]
    sort = query.get("sort", ["newest"])[0]
    if sort not in SORTS:
        raise ValueError("unsupported sort order")
    if sort == "newest":
        records.sort(key=lambda row: (row["timestamp"] or -1, row["id"]), reverse=True)
    elif sort == "oldest":
        records.sort(key=lambda row: (row["timestamp"] or 2**63, row["id"]))
    elif sort == "scope":
        records.sort(key=lambda row: (row["scope"], row["request_id"], row["start"]))
    elif sort == "relevance":
        terms = query.get("q", [""])[0].lower().split()
        records.sort(
            key=lambda row: sum(term in row["text"].lower() for term in terms), reverse=True
        )
    total = len(records)
    try:
        page = max(1, int(query.get("page", ["1"])[0]))
    except ValueError:
        page = 1
    start = (page - 1) * PAGE_SIZE
    selected = records if query.get("all", [""])[0] == "1" else records[start : start + PAGE_SIZE]
    return {
        "records": selected,
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
    }
