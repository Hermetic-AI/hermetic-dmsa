"""程序性记忆的持久化与检索: 流程本体、词项索引与触发匹配."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.procedures import Procedure, procedure_terms


def procedure_id(user_id: str, request_id: str, title: str) -> str:
    """产生包含 scope 与请求的稳定流程 ID."""
    raw = "\0".join((user_id, request_id, title))
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ProcedureMatch:
    """一条流程及其触发匹配情况."""

    procedure_id: str
    procedure: Procedure
    matched_terms: tuple[str, ...]
    score: float

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的匹配视图."""
        return {
            "procedure_id": self.procedure_id,
            "matched_terms": list(self.matched_terms),
            "score": self.score,
            **self.procedure.to_dict(),
        }


def save_procedure(
    db: sqlite3.Connection, user_id: str, request_id: str, procedure: Procedure
) -> str:
    """写入一条流程及其词项索引; 同一流程重复写入幂等."""
    identifier = procedure_id(user_id, request_id, procedure.title)
    terms = procedure_terms(procedure)
    with db:
        db.execute(
            "INSERT INTO procedures(id,user_id,request_id,title,steps,preconditions,verification,"
            "trigger_terms,paths,outcome,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, steps=excluded.steps, "
            "preconditions=excluded.preconditions, verification=excluded.verification, "
            "trigger_terms=excluded.trigger_terms, paths=excluded.paths, outcome=excluded.outcome",
            (
                identifier,
                user_id,
                request_id,
                procedure.title,
                json.dumps(list(procedure.steps), ensure_ascii=False),
                json.dumps(list(procedure.preconditions), ensure_ascii=False),
                json.dumps(list(procedure.verification), ensure_ascii=False),
                json.dumps(list(procedure.trigger_terms), ensure_ascii=False),
                json.dumps(list(procedure.paths), ensure_ascii=False),
                procedure.outcome,
                int(time.time()),
            ),
        )
        db.execute(
            "DELETE FROM procedure_terms WHERE user_id=? AND procedure_id=?",
            (user_id, identifier),
        )
        for term in terms:
            db.execute(
                "INSERT OR IGNORE INTO procedure_terms(user_id,procedure_id,term) VALUES(?,?,?)",
                (user_id, identifier, term),
            )
    return identifier


def _row_to_procedure(row: sqlite3.Row) -> Procedure:
    """把数据库行还原成流程对象."""

    def loads(name: str) -> tuple[str, ...]:
        try:
            parsed = json.loads(str(row[name] or "[]"))
        except (TypeError, ValueError):
            return ()
        return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()

    return Procedure(
        title=str(row["title"]),
        steps=loads("steps"),
        preconditions=loads("preconditions"),
        verification=loads("verification"),
        trigger_terms=loads("trigger_terms"),
        paths=loads("paths"),
        outcome=str(row["outcome"]),
    )


def procedures_in_scope(db: sqlite3.Connection, user_id: str) -> list[Procedure]:
    """读取 scope 内全部流程."""
    rows = db.execute(
        "SELECT * FROM procedures WHERE user_id=? ORDER BY created_at, id", (user_id,)
    ).fetchall()
    return [_row_to_procedure(row) for row in rows]


def match_procedures(
    db: sqlite3.Connection, user_id: str, terms: tuple[str, ...], limit: int = 3
) -> list[ProcedureMatch]:
    """按查询词项匹配流程, 返回命中词项数最多的若干条."""
    if not terms:
        return []
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    placeholders = ",".join("?" for _ in terms)
    rows = db.execute(
        "SELECT procedure_id, term, COUNT(*) AS hits FROM procedure_terms "
        f"WHERE user_id=? AND term IN ({placeholders}) GROUP BY procedure_id, term",
        (user_id, *terms),
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(str(row["procedure_id"]), []).append(str(row["term"]))
    if not grouped:
        return []
    identifiers = tuple(grouped)
    id_placeholders = ",".join("?" for _ in identifiers)
    bodies = db.execute(
        f"SELECT * FROM procedures WHERE user_id=? AND id IN ({id_placeholders})",
        (user_id, *identifiers),
    ).fetchall()
    matches = [
        ProcedureMatch(
            procedure_id=str(row["id"]),
            procedure=_row_to_procedure(row),
            matched_terms=tuple(sorted(grouped[str(row["id"])])),
            score=float(len(grouped[str(row["id"])])),
        )
        for row in bodies
    ]
    matches.sort(key=lambda item: (-item.score, item.procedure_id))
    return matches[:limit]
