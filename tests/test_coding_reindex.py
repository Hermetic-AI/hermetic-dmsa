"""向量降级写入与事后补算的回归测试."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from dmsa.memory import (
    DEGRADED_EMBEDDING,
    DEGRADED_VECTOR_INDEX,
    CodingMemory,
    CodingMemoryValidationError,
    SearchPolicy,
    SourceMessage,
)


class FlakyEmbedder:
    """可开关的确定性向量器: 关闭时模拟嵌入服务故障."""

    fingerprint = "flaky-v1"
    dimension = 2

    def __init__(self) -> None:
        self.available = True
        self.calls = 0

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not self.available:
            raise RuntimeError("provider unavailable")
        self.calls += len(texts)
        return [(1.0, float(len(text))) for text in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        return self.embed_batch([text])[0]


class FakeIndex:
    """记录 scope 与 fingerprint 的内存索引."""

    def __init__(self) -> None:
        self.points: dict[str, tuple[str, str, tuple[float, ...]]] = {}
        self.available = True

    def upsert(
        self, user_id: str, fingerprint: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]
    ) -> None:
        if not self.available:
            raise OSError("index unavailable")
        for point_id, vector in zip(ids, vectors, strict=True):
            self.points[point_id] = (user_id, fingerprint, tuple(vector))

    def search(
        self, user_id: str, fingerprint: str, vector: Sequence[float], limit: int
    ) -> list[str]:
        scoped = [
            (point_id, payload)
            for point_id, payload in self.points.items()
            if payload[:2] == (user_id, fingerprint)
        ]
        return [point_id for point_id, _ in sorted(scoped)[:limit]]

    def delete_scope(self, user_id: str) -> None:
        self.points = {key: value for key, value in self.points.items() if value[0] != user_id}

    def close(self) -> None:
        pass


def _vector_count(memory: CodingMemory, scope: str) -> int:
    """统计 scope 内已持久化的向量条数."""
    row = memory.store.db.execute(
        "SELECT COUNT(*) FROM vectors WHERE user_id=?", (scope,)
    ).fetchone()
    return int(row[0])


def test_embedding_failure_still_commits_sparse_memory(tmp_path: Path) -> None:
    """嵌入服务故障时降级为无向量写入, 原文与稀疏索引照常可检索."""
    embedder = FlakyEmbedder()
    embedder.available = False
    memory = CodingMemory(tmp_path / "degraded.sqlite", embedder, vector_index=FakeIndex())
    result = memory.add_guarded("r1", "alice", "s1", [SourceMessage("tool", "cache_refresh hit")])
    assert result.degraded == DEGRADED_EMBEDDING
    assert result.revision == 1
    assert _vector_count(memory, "alice") == 0
    # 嵌入服务恢复后, 降级写入的切片仍然能被稀疏通道检索到.
    embedder.available = True
    assert [hit.id for hit in memory.search("cache_refresh", "alice", 5)]
    memory.close()


def test_vector_index_failure_keeps_vectors_for_republish(tmp_path: Path) -> None:
    """远端索引故障时向量仍写进 SQLite, 供事后原样重发布."""
    embedder = FlakyEmbedder()
    index = FakeIndex()
    index.available = False
    memory = CodingMemory(tmp_path / "index.sqlite", embedder, vector_index=index)
    result = memory.add_guarded("r1", "alice", "s1", [SourceMessage("tool", "evidence body")])
    assert result.degraded == DEGRADED_VECTOR_INDEX
    assert index.points == {}
    assert _vector_count(memory, "alice") == 1
    assert embedder.calls == 1
    memory.close()


def test_reindex_embeds_pending_chunks_and_publishes(tmp_path: Path) -> None:
    """补算把降级写入缺的向量算出来并发布到远端索引."""
    embedder = FlakyEmbedder()
    embedder.available = False
    index = FakeIndex()
    memory = CodingMemory(tmp_path / "reindex.sqlite", embedder, vector_index=index)
    memory.add("r1", "alice", "s1", [SourceMessage("tool", "refresh_cache body")])
    memory.add("r2", "bob", "s1", [SourceMessage("tool", "unrelated scope")])
    embedder.available = True
    report = memory.reindex_vectors("alice")
    assert report.aborted == ""
    assert report.embedded == 1
    assert report.published == 1
    assert report.pending == 0
    assert _vector_count(memory, "alice") == 1
    assert _vector_count(memory, "bob") == 0
    assert len(index.points) == 1
    memory.close()


def test_reindex_republishes_without_reembedding(tmp_path: Path) -> None:
    """清空远端索引后重发布不再调用嵌入模型."""
    embedder = FlakyEmbedder()
    index = FakeIndex()
    memory = CodingMemory(tmp_path / "republish.sqlite", embedder, vector_index=index)
    memory.add("r1", "alice", "s1", [SourceMessage("tool", "first body")])
    memory.add("r2", "alice", "s2", [SourceMessage("tool", "second body")])
    assert len(index.points) == 2
    calls = embedder.calls
    index.points.clear()
    report = memory.reindex_vectors("alice")
    assert report.embedded == 0
    assert report.published == 2
    assert report.pending == 0
    assert embedder.calls == calls
    assert len(index.points) == 2
    memory.close()


def test_reindex_reports_unresolved_upstream(tmp_path: Path) -> None:
    """上游仍故障时补算如实报告缺口, 不抛错也不假装完成."""
    embedder = FlakyEmbedder()
    embedder.available = False
    memory = CodingMemory(tmp_path / "stuck.sqlite", embedder, vector_index=FakeIndex())
    memory.add("r1", "alice", "s1", [SourceMessage("tool", "first body")])
    memory.add("r2", "alice", "s2", [SourceMessage("tool", "second body")])
    report = memory.reindex_vectors("alice", max_chunks=1)
    assert report.aborted == "embedding"
    assert report.embedded == 0
    assert report.pending == 2
    with pytest.raises(CodingMemoryValidationError):
        memory.reindex_vectors("alice", embed_batch=0)
    memory.close()


def test_reindex_without_embedder_is_a_noop(tmp_path: Path) -> None:
    """没有配置嵌入器时补算既不报错也不产生向量."""
    memory = CodingMemory(tmp_path / "plain.sqlite", policy=SearchPolicy())
    memory.add("r1", "alice", "s1", [SourceMessage("tool", "sparse only")])
    report = memory.reindex_vectors("alice")
    assert report.fingerprint is None
    assert report.embedded == 0
    assert report.published == 0
    assert report.pending == 1
    memory.close()


def test_reindex_honours_repo_subscope(tmp_path: Path) -> None:
    """补算按仓库子域隔离, 不越界写其他 scope 的向量."""
    embedder = FlakyEmbedder()
    embedder.available = False
    memory = CodingMemory(tmp_path / "repo.sqlite", embedder, vector_index=FakeIndex())
    memory.add("r1", "alice", "s1", [SourceMessage("tool", "repo body")], repo="cambench")
    memory.add("r2", "alice", "s1", [SourceMessage("tool", "plain body")])
    embedder.available = True
    report = memory.reindex_vectors("alice", repo="cambench")
    assert report.scope == "alice#cambench"
    assert report.embedded == 1
    assert _vector_count(memory, "alice#cambench") == 1
    assert _vector_count(memory, "alice") == 0
    memory.close()
