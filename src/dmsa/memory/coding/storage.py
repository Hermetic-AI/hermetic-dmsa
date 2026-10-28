"""Coding memory SQLite 原文、索引与版本存储."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

from dmsa.memory.coding.anchor_store import (
    anchor_candidates,
    anchors_by_ids,
    relation_neighbors,
)
from dmsa.memory.coding.code_anchors import CodeAnchor
from dmsa.memory.coding.extraction import Chunk
from dmsa.memory.coding.ids import chunk_id, message_id
from dmsa.memory.coding.models import CodingMemoryConflictError, CorpusState, SourceMessage
from dmsa.memory.coding.schema import initialize
from dmsa.memory.coding.snapshot import RepoSnapshot
from dmsa.memory.coding.snapshot_store import load_snapshot, save_snapshot
from dmsa.memory.coding.storage_queries import (
    exact_candidates,
    message_prefixes,
    neighbors,
    scope_partitions,
    sparse_candidates,
    substring_candidates,
)
from dmsa.memory.coding.storage_write import publish_request
from dmsa.memory.coding.temporal import AsOf, as_of_filter


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
        *,
        author: str = "",
        rival: tuple[str, float] | None = None,
    ) -> int:
        """事务性发布 request、原文、切片、向量和 revision.

        ``author`` 记录写入者, ``rival`` 是门控判定的对手切片, 两者都只在
        团队场景下生效, 个人作用域写入保持原样.
        """
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
                author=author,
                rival=rival,
            )

    def sparse_candidates(
        self,
        terms: tuple[str, ...],
        user_id: str,
        limit: int,
        as_of: AsOf | None = None,
    ) -> list[sqlite3.Row]:
        """按 scope 在 FTS 虚表检索预展开词项."""
        return sparse_candidates(self.db, terms, user_id, limit, as_of)

    def substring_candidates(
        self,
        terms: tuple[str, ...],
        user_id: str,
        limit: int,
        as_of: AsOf | None = None,
    ) -> list[sqlite3.Row]:
        """按 scope 用 LIKE 召回短于 trigram 窗口的词项."""
        return substring_candidates(self.db, terms, user_id, limit, as_of)

    def exact_candidates(
        self,
        terms: tuple[str, ...],
        user_id: str,
        limit: int,
        as_of: AsOf | None = None,
    ) -> list[sqlite3.Row]:
        """从 scope-filtered SQL 精确索引查路径与符号."""
        return exact_candidates(self.db, terms, user_id, limit, as_of)

    def save_snapshot(self, user_id: str, snapshot: RepoSnapshot) -> None:
        """保存 scope 的仓库符号快照, 同一 root 覆盖旧版本."""
        with self.lock, self.db:
            save_snapshot(self.db, user_id, snapshot)

    def load_snapshot(self, user_id: str) -> RepoSnapshot | None:
        """读取 scope 最近一次仓库快照; 没有快照时返回 None."""
        with self.lock:
            return load_snapshot(self.db, user_id)

    def relation_neighbors(
        self, user_id: str, chunk_id: str, limit: int, as_of: AsOf | None = None
    ) -> tuple[str, ...]:
        """读取与切片共享工程关系目标的其它切片."""
        return relation_neighbors(self.db, user_id, chunk_id, limit, as_of)

    def anchor_candidates(
        self,
        terms: tuple[str, ...],
        user_id: str,
        limit: int,
        as_of: AsOf | None = None,
    ) -> list[sqlite3.Row]:
        """按 scope 召回代码锚点命中的切片."""
        return anchor_candidates(self.db, terms, user_id, limit, as_of)

    def anchors_by_ids(
        self, user_id: str, chunk_ids: Iterable[str]
    ) -> dict[str, tuple[CodeAnchor, ...]]:
        """按切片 ID 读取锚点, 供证据包渲染结构化定位信息."""
        return anchors_by_ids(self.db, user_id, chunk_ids)

    def rows_by_ids(
        self, user_id: str, ids: tuple[str, ...], as_of: AsOf | None = None
    ) -> list[sqlite3.Row]:
        """按同 scope 稳定 ID 读取已召回证据; 可按时序坐标收敛."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        clause, window = as_of_filter("c", as_of)
        return list(
            self.db.execute(
                "SELECT c.*, COALESCE(m.ordinal, c.ordinal) AS message_index FROM chunks c "
                "LEFT JOIN messages m ON m.id=c.message_id "
                f"WHERE c.user_id=? AND c.id IN ({placeholders})" + clause,
                (user_id, *ids, *window),
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

    def message_prefixes(self, user_id: str, message_ids: Sequence[str]) -> dict[str, str]:
        """读取指定来源消息正文的前缀, 用于判断整条消息的形态."""
        return message_prefixes(self.db, user_id, message_ids)

    def revision(self, user_id: str) -> int:
        """读取 scope 当前 revision."""
        row = self.db.execute(
            "SELECT epoch FROM scope_epochs WHERE user_id=?", (user_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def sub_scopes(self, user_id: str) -> tuple[str, ...]:
        """返回该 scope 及其仓库子域清单, 用于按用户完整清理."""
        return scope_partitions(self.db, user_id)

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
            for table in (
                "chunks",
                "vectors",
                "exact_terms",
                "code_anchors",
                "code_relations",
                "repo_snapshots",
                "procedures",
                "procedure_terms",
                "write_authors",
                "write_conflicts",
                "messages",
                "requests",
                "corpus",
            ):
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
