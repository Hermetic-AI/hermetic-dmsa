"""Coding memory SQLite 原文、索引与版本存储."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from dmsa.memory.coding.extraction import Chunk
from dmsa.memory.coding.ids import chunk_id, message_id
from dmsa.memory.coding.models import (
    CodingMemoryConflictError,
    CorpusState,
    SourceMessage,
)
from dmsa.memory.coding.schema import initialize
from dmsa.memory.coding.storage_queries import neighbors, sparse_candidates, substring_candidates
from dmsa.memory.coding.storage_write import publish_request


class CodingStore:
    """管理 WAL 数据库及原子请求发布."""

    def __init__(self, path: str | Path) -> None:
        """打开或创建数据库."""
        self.path = str(path)
        if self.path != ":memory:":
            expanded = Path(self.path).expanduser()
            expanded.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(expanded)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=30000")
            self.db.execute("PRAGMA journal_mode=WAL")
            initialize(self.db)
        except Exception:
            self.db.close()
            raise

    @staticmethod
    def payload_hash(session_id: str, messages: tuple[SourceMessage, ...]) -> str:
        """哈希 session 与有序原始字段."""
        raw = json.dumps([session_id, [m.__dict__ for m in messages]], ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def existing_request(self, request_id: str, user_id: str, payload_hash: str) -> int | None:
        """幂等预检以便已提交重试跳过 embedding."""
        row = self.db.execute(
            "SELECT payload_hash, revision FROM requests WHERE user_id=? AND request_id=?",
            (user_id, request_id),
        ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != payload_hash:
            raise CodingMemoryConflictError("idempotency key payload conflict")
        return int(row["revision"])

    @staticmethod
    def message_id(user_id: str, request_id: str, ordinal: int) -> str:
        """产生包含 scope 的稳定来源 ID."""
        return message_id(user_id, request_id, ordinal)

    @staticmethod
    def chunk_id(message_id: str, start: int, end: int) -> str:
        """产生来源切片稳定 ID."""
        return chunk_id(message_id, start, end)

    def add(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        messages: tuple[SourceMessage, ...],
        chunks: Iterable[Chunk],
        vectors: list[tuple[float, ...]],
        fingerprint: str | None,
    ) -> int:
        """事务性发布 request、原文、切片、向量和 revision."""
        digest = self.payload_hash(session_id, messages)
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return publish_request(
                self.db,
                user_id,
                request_id,
                session_id,
                digest,
                messages,
                chunks,
                vectors,
                fingerprint,
            )

    def sparse_candidates(
        self, terms: tuple[str, ...], user_id: str, limit: int
    ) -> list[sqlite3.Row]:
        """按 scope 在 FTS 虚表检索预展开词项."""
        return sparse_candidates(self.db, terms, user_id, limit)

    def substring_candidates(
        self, terms: tuple[str, ...], user_id: str, limit: int
    ) -> list[sqlite3.Row]:
        """按 scope 用 LIKE 召回短于 trigram 窗口的词项."""
        return substring_candidates(self.db, terms, user_id, limit)

    def exact_candidates(
        self, terms: tuple[str, ...], user_id: str, limit: int
    ) -> list[sqlite3.Row]:
        """从 scope-filtered SQL 精确索引查路径与符号."""
        values = tuple(term for term in terms if len(term) > 2)
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return list(
            self.db.execute(
                "SELECT c.*, COUNT(*) AS match_hits FROM exact_terms e "
                "JOIN chunks c ON c.id=e.chunk_id "
                "WHERE e.user_id=? AND c.user_id=? AND e.term IN (" + placeholders + ") "
                "GROUP BY c.id ORDER BY match_hits DESC, c.id LIMIT ?",
                (user_id, user_id, *(term.lower() for term in values), limit),
            )
        )

    def dense_candidates(
        self, user_id: str, fingerprint: str, limit: int
    ) -> tuple[int, list[tuple[sqlite3.Row, tuple[float, ...]]]]:
        """读取有界同 scope 持久向量候选."""
        count = int(
            self.db.execute("SELECT count(*) FROM chunks WHERE user_id=?", (user_id,)).fetchone()[0]
        )
        if count > limit:
            return count, []
        rows = self.db.execute(
            "SELECT c.*, v.vector FROM chunks c JOIN vectors v ON c.id=v.chunk_id "
            "WHERE c.user_id=? AND v.user_id=? AND v.fingerprint=?",
            (user_id, user_id, fingerprint),
        )
        return count, [(r, tuple(json.loads(r["vector"]))) for r in rows]

    def rows_by_ids(self, user_id: str, ids: tuple[str, ...]) -> list[sqlite3.Row]:
        """按同 scope 稳定 ID 读取已召回证据."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return list(
            self.db.execute(
                "SELECT c.*, COALESCE(m.ordinal, c.ordinal) AS message_index FROM chunks c "
                "LEFT JOIN messages m ON m.id=c.message_id "
                f"WHERE c.user_id=? AND c.id IN ({placeholders})",
                (user_id, *ids),
            )
        )

    def rows(self, user_id: str) -> list[sqlite3.Row]:
        """读取指定 scope 的切片, 供诊断与测试使用."""
        return list(
            self.db.execute(
                "SELECT * FROM chunks WHERE user_id=? ORDER BY request_id, ordinal, start",
                (user_id,),
            )
        )

    def neighbors(self, user_id: str, chunk_id: str, window: int) -> list[sqlite3.Row]:
        """读取同 request/session/message 的有限相邻切片."""
        return neighbors(self.db, user_id, chunk_id, window)

    def revision(self, user_id: str) -> int:
        """读取 scope 当前 revision."""
        row = self.db.execute(
            "SELECT epoch FROM scope_epochs WHERE user_id=?", (user_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def corpus_state(self, user_ids: Iterable[str]) -> CorpusState:
        """用一个只读事务读取指定 scope revisions 快照."""
        scopes = tuple(sorted(set(user_ids)))
        with self.lock:
            self.db.execute("BEGIN")
            try:
                corpus_id = str(
                    self.db.execute("SELECT value FROM metadata WHERE key='corpus_id'").fetchone()[
                        0
                    ]
                )
                generation = int(
                    self.db.execute(
                        "SELECT value FROM metadata WHERE key='corpus_generation'"
                    ).fetchone()[0]
                )
                values = []
                for user_id in scopes:
                    row = self.db.execute(
                        "SELECT epoch FROM scope_epochs WHERE user_id=?", (user_id,)
                    ).fetchone()
                    values.append((user_id, int(row[0]) if row else 0))
                self.db.commit()
            except sqlite3.Error:
                self.db.rollback()
                raise
        payload = json.dumps([corpus_id, generation, values], separators=(",", ":")).encode()
        fingerprint = hashlib.sha256(payload).hexdigest()
        return CorpusState(sum(rev for _, rev in values), fingerprint)

    def delete_scope(self, user_id: str) -> None:
        """事务性删除 scope 原文、向量、索引和 revision."""
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.revision(user_id)
            epoch = current + 1
            for table in ("chunks", "vectors", "exact_terms", "messages", "requests", "corpus"):
                self.db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            self.db.execute(
                "INSERT INTO scope_epochs VALUES(?,?) ON CONFLICT(user_id) "
                "DO UPDATE SET epoch=excluded.epoch",
                (user_id, epoch),
            )
            self.db.execute(
                "UPDATE metadata SET value=CAST(value AS INTEGER)+1 WHERE key='corpus_generation'"
            )

    def close(self) -> None:
        """关闭连接."""
        self.db.close()
