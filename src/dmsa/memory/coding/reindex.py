"""向量补算与重发布: 让降级写入最终恢复稠密通道.

写入路径在嵌入服务或向量索引故障时会降级为无向量落库, 原文与稀疏索引不受
影响. 本模块提供事后修复: 待补算的切片用 ``chunks LEFT JOIN vectors`` 直接
算出, 不需要额外的队列表; 已经写进 SQLite 的向量可以原样重发布到远端索引,
不需要重新调用嵌入模型.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from dmsa.memory.coding.models import CodingMemoryError, CodingMemoryValidationError
from dmsa.memory.coding.retrieval import CodingRetriever
from dmsa.memory.coding.storage import CodingStore
from dmsa.memory.coding.vector_codec import decode_vector, encode_vector, text_hash
from dmsa.memory.coding.vector_index import VectorIndex
from dmsa.observability import observe_count


@dataclass(frozen=True)
class ReindexReport:
    """一次补算的规模统计与中止原因."""

    scope: str
    fingerprint: str | None
    embedded: int
    published: int
    pending: int
    aborted: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的补算视图."""
        return {
            "scope": self.scope,
            "fingerprint": self.fingerprint,
            "embedded": self.embedded,
            "published": self.published,
            "pending": self.pending,
            "aborted": self.aborted,
        }


def pending_chunks(
    db: sqlite3.Connection, user_id: str, fingerprint: str | None, limit: int
) -> list[tuple[str, str]]:
    """读取指定模型下还缺向量的切片, 返回 (chunk_id, text)."""
    if fingerprint is None:
        rows = db.execute(
            "SELECT c.id AS id, c.text AS text FROM chunks c "
            "LEFT JOIN vectors v ON v.chunk_id=c.id "
            "WHERE c.user_id=? AND v.chunk_id IS NULL ORDER BY c.rowid LIMIT ?",
            (user_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT c.id AS id, c.text AS text FROM chunks c "
            "LEFT JOIN vectors v ON v.chunk_id=c.id AND v.fingerprint=? "
            "WHERE c.user_id=? AND v.chunk_id IS NULL ORDER BY c.rowid LIMIT ?",
            (fingerprint, user_id, limit),
        ).fetchall()
    return [(str(row["id"]), str(row["text"])) for row in rows]


def count_pending(db: sqlite3.Connection, user_id: str, fingerprint: str | None) -> int:
    """统计指定模型下还缺向量的切片数."""
    if fingerprint is None:
        row = db.execute(
            "SELECT COUNT(*) FROM chunks c LEFT JOIN vectors v ON v.chunk_id=c.id "
            "WHERE c.user_id=? AND v.chunk_id IS NULL",
            (user_id,),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COUNT(*) FROM chunks c "
            "LEFT JOIN vectors v ON v.chunk_id=c.id AND v.fingerprint=? "
            "WHERE c.user_id=? AND v.chunk_id IS NULL",
            (fingerprint, user_id),
        ).fetchone()
    return int(row[0])


def insert_vectors(
    db: sqlite3.Connection,
    user_id: str,
    fingerprint: str,
    pairs: list[tuple[str, str, tuple[float, ...]]],
) -> None:
    """把 (chunk_id, text, vector) 写入向量表; 同一切片按模型覆盖."""
    db.executemany(
        "INSERT OR REPLACE INTO vectors"
        "(chunk_id, user_id, fingerprint, dimension, vector, text_hash) VALUES(?,?,?,?,?,?)",
        [
            (chunk_id, user_id, fingerprint, len(vector), encode_vector(vector), text_hash(text))
            for chunk_id, text, vector in pairs
        ],
    )


def stored_vectors(
    db: sqlite3.Connection, user_id: str, fingerprint: str, offset: int, limit: int
) -> list[tuple[str, tuple[float, ...]]]:
    """分页读取已持久化的向量, 供原样重发布到远端索引."""
    rows = db.execute(
        "SELECT chunk_id, vector FROM vectors WHERE user_id=? AND fingerprint=? "
        "ORDER BY chunk_id LIMIT ? OFFSET ?",
        (user_id, fingerprint, limit, offset),
    ).fetchall()
    return [(str(row["chunk_id"]), decode_vector(row["vector"])) for row in rows]


def reindex(
    store: CodingStore,
    retriever: CodingRetriever,
    user_id: str,
    *,
    max_chunks: int = 0,
    embed_batch: int = 64,
    publish_batch: int = 256,
) -> ReindexReport:
    """补齐 scope 内缺失的向量并把全部向量重发布到远端索引.

    ``max_chunks`` 为 0 表示补到没有缺口为止; 大于 0 时只处理前若干条, 便于
    分批控制嵌入调用量. 补算与重发布都是幂等的, 中断后重跑不会产生重复数据.
    上游故障不会抛错, 而是记在报告的 ``aborted`` 字段里, 已完成的进度保留.
    """
    if embed_batch < 1 or publish_batch < 1 or max_chunks < 0:
        raise CodingMemoryValidationError("reindex batch sizes must be positive")
    fingerprint = retriever.fingerprint
    embedded = 0
    aborted = ""
    if fingerprint is not None:
        embedded, aborted = _embed_pending(
            store, retriever, user_id, fingerprint, max_chunks, embed_batch
        )
    index = retriever.vector_index
    published = 0
    if fingerprint is not None and index is not None and not aborted:
        published, aborted = _republish(store, index, user_id, fingerprint, publish_batch)
    with store.lock:
        remaining = count_pending(store.db, user_id, fingerprint)
    return ReindexReport(user_id, fingerprint, embedded, published, remaining, aborted)


def _embed_pending(
    store: CodingStore,
    retriever: CodingRetriever,
    user_id: str,
    fingerprint: str,
    max_chunks: int,
    embed_batch: int,
) -> tuple[int, str]:
    """分批补算缺失向量; 嵌入服务故障时停止并返回原因."""
    embedded = 0
    while max_chunks == 0 or embedded < max_chunks:
        budget = embed_batch if max_chunks == 0 else min(embed_batch, max_chunks - embedded)
        with store.lock:
            batch = pending_chunks(store.db, user_id, fingerprint, budget)
        if not batch:
            break
        try:
            vectors = retriever.embed_new([text for _, text in batch])
        except CodingMemoryError:
            observe_count("dmsa.reindex.embedding_failed", len(batch))
            return embedded, "embedding"
        pairs = [
            (chunk_id, text, vector)
            for (chunk_id, text), vector in zip(batch, vectors, strict=True)
        ]
        with store.lock, store.db:
            insert_vectors(store.db, user_id, fingerprint, pairs)
        embedded += len(pairs)
        observe_count("dmsa.reindex.embedded", len(pairs))
    return embedded, ""


def _republish(
    store: CodingStore,
    index: VectorIndex,
    user_id: str,
    fingerprint: str,
    publish_batch: int,
) -> tuple[int, str]:
    """把 SQLite 里的向量原样写回远端索引; 索引故障时停止并返回原因."""
    published = 0
    while True:
        with store.lock:
            batch = stored_vectors(store.db, user_id, fingerprint, published, publish_batch)
        if not batch:
            break
        try:
            index.upsert(
                user_id, fingerprint, [item[0] for item in batch], [item[1] for item in batch]
            )
        except Exception:
            observe_count("dmsa.reindex.publish_failed", len(batch))
            return published, "vector_index"
        published += len(batch)
        observe_count("dmsa.reindex.published", len(batch))
    return published, ""
