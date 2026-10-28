"""合并条目与跨仓库迁移的持久化: 来源追踪、派生条目写入与读取.

派生条目不是新的会话, 而是既有证据的合并或迁移产物: 它不写经验卡, 不参与
会话级结果传播, 只登记来源 ID, 保证任何一条派生规则都能回溯到原始证据.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.anchor_store import insert_anchors, insert_relations
from dmsa.memory.coding.code_anchors import CodeAnchor
from dmsa.memory.coding.generalize import RuleGroup, SourceSlice
from dmsa.memory.coding.ids import chunk_id, message_id
from dmsa.memory.coding.schema import scope_token
from dmsa.memory.coding.trust_store import ensure_stats

# 派生条目的来源角色; 检索这些角色时排除派生条目, 避免它们被再次合并.
CONSOLIDATED_ROLE = "consolidated"
TRANSFERRED_ROLE = "transferred"
DERIVED_ROLES = (CONSOLIDATED_ROLE, TRANSFERRED_ROLE)


@dataclass(frozen=True)
class ConsolidationRecord:
    """一条合并记录: 派生切片与它的全部来源."""

    id: str
    scope: str
    chunk_id: str
    group_key: str
    rule: str
    source_ids: tuple[str, ...]
    paths: tuple[str, ...]
    symbols: tuple[str, ...]
    transferable: bool
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的合并记录视图."""
        return {
            "id": self.id,
            "scope": self.scope,
            "chunk_id": self.chunk_id,
            "group_key": self.group_key,
            "rule": self.rule,
            "source_ids": list(self.source_ids),
            "paths": list(self.paths),
            "symbols": list(self.symbols),
            "transferable": self.transferable,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class TransferRecord:
    """一条迁移记录: 目标切片与它的来源作用域."""

    id: str
    source_scope: str
    target_scope: str
    chunk_id: str
    source_ids: tuple[str, ...]
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的迁移记录视图."""
        return {
            "id": self.id,
            "source_scope": self.source_scope,
            "target_scope": self.target_scope,
            "chunk_id": self.chunk_id,
            "source_ids": list(self.source_ids),
            "created_at": self.created_at,
        }


def scope_slices(db: sqlite3.Connection, scope: str, limit: int) -> list[SourceSlice]:
    """读取 scope 内的原始切片, 排除合并与迁移产生的派生条目.

    只取最近的有限条: 合并是全量扫描, 必须有明确上限, 否则大库会被拖慢.
    """
    placeholders = ",".join("?" for _ in DERIVED_ROLES)
    rows = db.execute(
        "SELECT id, text, paths, symbols, outcome FROM chunks "
        f"WHERE user_id=? AND role NOT IN ({placeholders}) ORDER BY rowid DESC LIMIT ?",
        (scope, *DERIVED_ROLES, limit),
    ).fetchall()
    return [
        SourceSlice(
            id=str(row["id"]),
            text=str(row["text"]),
            paths=tuple(str(row["paths"]).split()),
            symbols=tuple(str(row["symbols"]).split()),
            outcome=str(row["outcome"]),
        )
        for row in rows
    ]


def publish_consolidated(
    db: sqlite3.Connection,
    scope: str,
    request_id: str,
    session_id: str,
    role: str,
    text: str,
    paths: tuple[str, ...],
    symbols: tuple[str, ...],
    anchors: tuple[CodeAnchor, ...],
    vector: tuple[float, ...] | None,
    fingerprint: str | None,
) -> tuple[str, int] | None:
    """在已开启的事务内发布一条派生条目, 返回切片 ID 与 revision.

    同一 request_id 重复发布时返回 None, 调用方据此把它当作幂等跳过.
    """
    existing = db.execute(
        "SELECT revision FROM requests WHERE user_id=? AND request_id=?", (scope, request_id)
    ).fetchone()
    if existing is not None:
        return None
    row = db.execute("SELECT revision FROM corpus WHERE user_id=?", (scope,)).fetchone()
    epoch = db.execute("SELECT epoch FROM scope_epochs WHERE user_id=?", (scope,)).fetchone()
    revision = max(int(row[0]) if row else 0, int(epoch[0]) if epoch else 0) + 1
    digest = hashlib.sha256("\0".join((scope, request_id, text)).encode()).hexdigest()
    db.execute(
        "INSERT INTO requests VALUES(?,?,?,?,?)", (scope, request_id, session_id, digest, revision)
    )
    moment = int(time.time())
    message_key = message_id(scope, request_id, 0)
    db.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)",
        (message_key, scope, request_id, session_id, 0, role, moment, text),
    )
    chunk_key = chunk_id(message_key, 0, len(text))
    db.execute(
        "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            chunk_key,
            scope,
            request_id,
            session_id,
            message_key,
            0,
            0,
            len(text),
            role,
            "unknown",
            " ".join(paths),
            " ".join(symbols),
            text,
            scope_token(scope),
        ),
    )
    for term in {value.lower() for value in (*paths, *symbols)}:
        db.execute("INSERT INTO exact_terms VALUES(?,?,?)", (scope, chunk_key, term))
    ensure_stats(db, scope, chunk_key, revision=revision, created_at=moment)
    insert_anchors(db, scope, chunk_key, anchors)
    insert_relations(db, scope, chunk_key, anchors)
    if fingerprint is not None and vector is not None:
        db.execute(
            "INSERT INTO vectors VALUES(?,?,?,?,?)",
            (chunk_key, scope, fingerprint, len(vector), json.dumps(vector)),
        )
    db.execute(
        "INSERT INTO corpus VALUES(?,?) ON CONFLICT(user_id) "
        "DO UPDATE SET revision=excluded.revision",
        (scope, revision),
    )
    db.execute(
        "INSERT INTO scope_epochs VALUES(?,?) ON CONFLICT(user_id) "
        "DO UPDATE SET epoch=excluded.epoch",
        (scope, revision),
    )
    db.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='corpus_generation'")
    return chunk_key, revision


def insert_consolidation(
    db: sqlite3.Connection, scope: str, chunk_key: str, group: RuleGroup
) -> str:
    """登记一次合并的来源追踪; 同一规则重复合并时只刷新来源列表."""
    identifier = hashlib.sha256("\0".join((scope, group.rule_key)).encode()).hexdigest()
    db.execute(
        "INSERT INTO memory_consolidations(id,user_id,chunk_id,group_key,rule,source_ids,paths,"
        "symbols,transferable,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET source_ids=excluded.source_ids, chunk_id=excluded.chunk_id",
        (
            identifier,
            scope,
            chunk_key,
            group.rule_key,
            group.rule,
            json.dumps(list(group.source_ids), ensure_ascii=False),
            " ".join(group.paths),
            " ".join(group.symbols),
            1 if group.transferable else 0,
            int(time.time()),
        ),
    )
    return identifier


def insert_transfer(
    db: sqlite3.Connection,
    source_scope: str,
    target_scope: str,
    chunk_key: str,
    group_key: str,
    source_ids: tuple[str, ...],
) -> str:
    """登记一次跨仓库迁移的来源追踪."""
    identifier = hashlib.sha256(
        "\0".join((source_scope, target_scope, group_key)).encode()
    ).hexdigest()
    db.execute(
        "INSERT INTO memory_transfers(id,user_id,source_scope,target_scope,chunk_id,source_ids,"
        "created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
        (
            identifier,
            target_scope,
            source_scope,
            target_scope,
            chunk_key,
            json.dumps(list(source_ids), ensure_ascii=False),
            int(time.time()),
        ),
    )
    return identifier


def _loads(value: object) -> tuple[str, ...]:
    """把 JSON 列表列还原成字符串元组."""
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return ()
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


def list_consolidations(db: sqlite3.Connection, scope: str) -> list[ConsolidationRecord]:
    """列出作用域内的合并记录."""
    rows = db.execute(
        "SELECT * FROM memory_consolidations WHERE user_id=? ORDER BY created_at, id", (scope,)
    ).fetchall()
    return [
        ConsolidationRecord(
            id=str(row["id"]),
            scope=str(row["user_id"]),
            chunk_id=str(row["chunk_id"]),
            group_key=str(row["group_key"]),
            rule=str(row["rule"]),
            source_ids=_loads(row["source_ids"]),
            paths=tuple(str(row["paths"]).split()),
            symbols=tuple(str(row["symbols"]).split()),
            transferable=bool(row["transferable"]),
            created_at=int(row["created_at"]),
        )
        for row in rows
    ]


def list_transfers(db: sqlite3.Connection, scope: str) -> list[TransferRecord]:
    """列出作用域作为目标时的迁移记录."""
    rows = db.execute(
        "SELECT * FROM memory_transfers WHERE target_scope=? ORDER BY created_at, id", (scope,)
    ).fetchall()
    return [
        TransferRecord(
            id=str(row["id"]),
            source_scope=str(row["source_scope"]),
            target_scope=str(row["target_scope"]),
            chunk_id=str(row["chunk_id"]),
            source_ids=_loads(row["source_ids"]),
            created_at=int(row["created_at"]),
        )
        for row in rows
    ]
