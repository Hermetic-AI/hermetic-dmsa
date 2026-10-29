"""内容寻址的向量复用回归测试."""

from __future__ import annotations

from pathlib import Path

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.vector_codec import text_hash


class _CountingEmbedder:
    """记录被调用次数的 deterministic dense backend。"""

    fingerprint = "unit-v1"
    dimension = 2

    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []

    def embed_batch(self, texts):
        """记录本次调用并返回固定向量。"""
        self.calls += len(texts)
        self.texts.extend(texts)
        return [[1.0, 0.5] for _ in texts]


def test_same_content_in_two_scopes_embeds_once(tmp_path: Path) -> None:
    """跨作用域写入同一段正文时, 嵌入模型只被调用一次."""
    embedder = _CountingEmbedder()
    memory = CodingMemory(tmp_path / "reuse.sqlite", embedder=embedder)
    body = "cache_refresh def refresh_cache(store): return store.refresh()"
    memory.add("r1", "scope-a", "s1", [SourceMessage("assistant", body)])
    memory.add("r2", "scope-b", "s1", [SourceMessage("assistant", body)])
    assert embedder.calls == 1, "相同正文不应重复调用嵌入模型"
    memory.close()


def test_reused_vector_is_identical(tmp_path: Path) -> None:
    """复用得到的向量必须与直接计算完全一致."""
    embedder = _CountingEmbedder()
    memory = CodingMemory(tmp_path / "same.sqlite", embedder=embedder)
    body = "cache_refresh handler def refresh(): return 1"
    memory.add("r1", "scope-a", "s1", [SourceMessage("assistant", body)])
    memory.add("r2", "scope-b", "s1", [SourceMessage("assistant", body)])
    rows_a = memory.store.db.execute(
        "SELECT vector FROM vectors WHERE user_id=?", ("scope-a",)
    ).fetchall()
    rows_b = memory.store.db.execute(
        "SELECT vector FROM vectors WHERE user_id=?", ("scope-b",)
    ).fetchall()
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert bytes(rows_a[0][0]) == bytes(rows_b[0][0])
    memory.close()


def test_different_content_still_embeds(tmp_path: Path) -> None:
    """正文不同的写入仍然各自调用嵌入模型."""
    embedder = _CountingEmbedder()
    memory = CodingMemory(tmp_path / "diff.sqlite", embedder=embedder)
    memory.add("r1", "scope-a", "s1", [SourceMessage("assistant", "first evidence alpha")])
    memory.add("r2", "scope-b", "s1", [SourceMessage("assistant", "second evidence beta")])
    assert embedder.calls == 2
    memory.close()


def test_duplicate_chunks_in_one_request_embed_once(tmp_path: Path) -> None:
    """同一次写入里的重复正文只算一次向量."""
    embedder = _CountingEmbedder()
    memory = CodingMemory(
        tmp_path / "batch.sqlite", embedder=embedder, policy=SearchPolicy(chunk_size=200)
    )
    body = "cache_refresh def refresh(): return 1\n" * 30
    memory.add("r1", "scope-a", "s1", [SourceMessage("assistant", body)])
    assert embedder.calls < 30, "批内重复正文不应逐条调用嵌入模型"
    memory.close()


def test_vectors_carry_content_hash(tmp_path: Path) -> None:
    """写入的向量行必须带上正文哈希, 供后续写入复用."""
    embedder = _CountingEmbedder()
    memory = CodingMemory(tmp_path / "hash.sqlite", embedder=embedder)
    body = "cache_refresh handler def refresh(): return 1"
    memory.add("r1", "scope-a", "s1", [SourceMessage("assistant", body)])
    row = memory.store.db.execute(
        "SELECT text_hash FROM vectors WHERE user_id=?", ("scope-a",)
    ).fetchone()
    assert row is not None
    assert str(row[0]) == text_hash(body)
    memory.close()


def test_existing_database_gains_hash_column(tmp_path: Path) -> None:
    """既有库打开时补上 text_hash 列, 历史数据保持可读."""
    path = tmp_path / "legacy.sqlite"
    first = CodingMemory(path, embedder=_CountingEmbedder())
    first.add("r1", "scope-a", "s1", [SourceMessage("assistant", "legacy evidence alpha")])
    first.close()
    second = CodingMemory(path, embedder=_CountingEmbedder())
    columns = {str(row[1]) for row in second.store.db.execute("PRAGMA table_info(vectors)")}
    assert "text_hash" in columns
    second.add(
        "r2",
        "scope-b",
        "s1",
        [SourceMessage("assistant", "legacy evidence alpha")],
    )
    rows = second.store.db.execute(
        "SELECT text_hash FROM vectors WHERE user_id=?", ("scope-b",)
    ).fetchall()
    assert rows
    assert rows[0][0]
    second.close()
