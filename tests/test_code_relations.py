"""工程关系索引与关系扩展召回的测试."""

from __future__ import annotations

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.anchor_store import relation_neighbors

PATCH = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1,2 +1,5 @@\n"
    "+class Handler:\n"
    "+    def handle_request(self, payload):\n"
    "+        return payload\n"
)
RELATED = (
    "回滚记录: 上一次改动同一个文件时忘记清理中间件。\n"
    "```python path=src/app.py\n"
    "ROLLBACK_NOTE = 'missing cleanup'\n"
    "```\n"
)


def _memory(tmp_path) -> CodingMemory:
    """写入一个补丁切片与一个只靠文件关系相连的切片."""
    memory = CodingMemory(tmp_path / "memory.sqlite")
    memory.add("req-1", "proj", "sess-1", [SourceMessage("assistant", PATCH)])
    memory.add("req-2", "proj", "sess-2", [SourceMessage("assistant", RELATED)])
    return memory


def _patch_id(memory: CodingMemory) -> str:
    """返回补丁切片的稳定 ID."""
    hits = memory.search("handle_request", "proj", 5, SearchPolicy(dense_weight=0.0))
    assert hits
    return hits[0].id


def test_relation_index_links_chunks_sharing_a_file(tmp_path):
    memory = _memory(tmp_path)
    try:
        chunk_id = _patch_id(memory)
        neighbors = memory.store.relation_neighbors("proj", chunk_id, 5)
        assert neighbors
        rows = memory.store.rows_by_ids("proj", neighbors)
        assert any("ROLLBACK_NOTE" in str(row["text"]) for row in rows)
    finally:
        memory.close()


def test_relation_expansion_is_off_by_default_and_on_when_requested(tmp_path):
    memory = _memory(tmp_path)
    try:
        off = memory.search(
            "handle_request", "proj", 5, SearchPolicy(dense_weight=0.0, relation_window=0)
        )
        on = memory.search(
            "handle_request", "proj", 5, SearchPolicy(dense_weight=0.0, relation_window=2)
        )
        assert all("ROLLBACK_NOTE" not in hit.content for hit in off)
        assert any("ROLLBACK_NOTE" in hit.content for hit in on)
    finally:
        memory.close()


def test_relation_lookup_is_bounded_and_scoped(tmp_path):
    memory = _memory(tmp_path)
    try:
        memory.add("req-3", "other", "sess-3", [SourceMessage("assistant", RELATED)])
        chunk_id = _patch_id(memory)
        assert relation_neighbors(memory.store.db, "proj", chunk_id, 0) == ()
        linked = relation_neighbors(memory.store.db, "other", chunk_id, 5)
        assert linked == ()
    finally:
        memory.close()


def test_delete_scope_clears_relation_index(tmp_path):
    memory = _memory(tmp_path)
    try:
        chunk_id = _patch_id(memory)
        memory.delete_scope("proj")
        assert memory.store.relation_neighbors("proj", chunk_id, 5) == ()
        assert memory.search("handle_request", "proj", 5, SearchPolicy(relation_window=3)) == []
    finally:
        memory.close()
