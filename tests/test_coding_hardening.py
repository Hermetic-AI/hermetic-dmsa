"""代码记忆索引、预算与原子可见性回归测试."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from dmsa.memory import (
    CodingMemory,
    CodingMemoryError,
    CodingMemoryValidationError,
    SearchPolicy,
    SourceMessage,
    estimate_tokens,
)


def test_scope_metadata_has_indexed_lookup_and_scope_fts(tmp_path):
    memory = CodingMemory(tmp_path / "memory.db")
    memory.add("r", "alice", "s", [SourceMessage("user", "src/cache.py CacheLoader")])
    for index in range(20):
        memory.add(str(index), "bob", "s", [SourceMessage("user", "src/cache.py CacheLoader")])
    assert len(memory.search("CacheLoader", "alice", 5, SearchPolicy(candidate_limit=1))) == 1
    plans = memory.store.db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM chunks WHERE user_id=? AND id=?", ("alice", "id")
    ).fetchall()
    assert any("INDEX" in row[3] and "SCAN" not in row[3] for row in plans)
    counts = memory.store.db.execute(
        "EXPLAIN QUERY PLAN SELECT count(*) FROM chunks WHERE user_id=?", ("alice",)
    ).fetchall()
    assert any("SEARCH" in row[3] and "COVERING INDEX" in row[3] for row in counts)
    memory.delete_scope("alice")
    assert memory.search("CacheLoader", "alice", 5) == []
    assert memory.search("CacheLoader", "bob", 5)
    memory.close()


def test_legacy_fts_database_migrates_without_losing_ids(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE VIRTUAL TABLE chunks USING fts5(id UNINDEXED,user_id UNINDEXED,"
        "request_id UNINDEXED,session_id UNINDEXED,message_id UNINDEXED,ordinal UNINDEXED,"
        "start UNINDEXED,end UNINDEXED,role UNINDEXED,outcome UNINDEXED,paths,symbols,text)"
    )
    db.execute(
        "INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("stable", "用户", "r", "s", "m", 0, 0, 17, "user", "unknown", "", "", "cache regression"),
    )
    db.commit()
    db.close()
    memory = CodingMemory(path)
    assert memory.search("cache", "用户", 1)[0].id == "stable"
    memory.close()
    reopened = CodingMemory(path)
    assert reopened.search("cache", "用户", 1)[0].id == "stable"
    reopened.close()


@pytest.mark.parametrize("text", ["中文代码" * 100, "identifier" * 150])
def test_budget_counts_cjk_and_long_code_including_source_header(tmp_path, text):
    memory = CodingMemory(tmp_path / "budget.db")
    memory.add("r", "u", "s", [SourceMessage("user", "needle " + text)])
    full = memory.search("needle", "u", 1)[0]
    size = estimate_tokens(full.content)
    assert size > 0
    assert size < len(full.content.encode("utf-8"))
    partial = memory.search("needle", "u", 1, SearchPolicy(max_tokens=size - 1))
    assert len(partial) == 1
    assert estimate_tokens(partial[0].content) < size
    assert partial[0].content.endswith("[truncated]")
    assert memory.search("needle", "u", 1, SearchPolicy(max_tokens=size)) == [full]
    memory.close()


def test_partial_write_is_not_visible_to_search_or_retry(tmp_path):
    memory = CodingMemory(tmp_path / "atomic.db")
    inserted, release, read_started = threading.Event(), threading.Event(), threading.Event()

    def pause():
        inserted.set()
        assert release.wait(5)
        return 0

    memory.store.db.create_function("pause", 0, pause)
    memory.store.db.execute(
        "CREATE TRIGGER pause_write AFTER INSERT ON chunks BEGIN SELECT pause(); END"
    )
    source = [SourceMessage("user", "atomic evidence")]

    def read():
        read_started.set()
        return memory.search("atomic", "u", 1)

    with ThreadPoolExecutor(max_workers=3) as pool:
        writer = pool.submit(memory.add, "r", "u", "s", source)
        assert inserted.wait(5)
        reader = pool.submit(read)
        retry = pool.submit(memory.add, "r", "u", "s", source)
        try:
            assert read_started.wait(5)
            assert not reader.done()
            assert not retry.done()
        finally:
            release.set()
        assert writer.result(timeout=5) == retry.result(timeout=5) == 1
        assert len(reader.result(timeout=5)) == 1
    memory.close()


def test_provider_and_database_errors_are_typed(tmp_path):
    class BrokenEmbedder:
        dimension = 2
        fingerprint = "broken"

        def embed_batch(self, texts):
            raise RuntimeError("provider secret")

    memory = CodingMemory(tmp_path / "errors.db", embedder=BrokenEmbedder())
    with pytest.raises(CodingMemoryError, match="embedding provider operation failed"):
        memory.add("r", "u", "s", [SourceMessage("user", "evidence")])
    assert memory.corpus_state(["u"]).revision == 0
    with pytest.raises(CodingMemoryValidationError):
        memory.delete_scope("")
    memory.close()
    with pytest.raises(CodingMemoryError):
        memory.search("evidence", "u", 1)


def test_budget_empty_and_invalid_text():
    assert estimate_tokens("") == 0
    assert estimate_tokens(" ") == 1
    with pytest.raises(CodingMemoryValidationError):
        estimate_tokens("\ud800")
