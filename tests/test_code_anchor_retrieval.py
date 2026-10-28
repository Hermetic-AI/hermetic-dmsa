"""代码锚点通道的召回、证据增强与 scope 隔离测试."""

from __future__ import annotations

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage

PATCH = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -10,6 +10,9 @@\n"
    "+class Handler:\n"
    "+    def handle_request(self, payload):\n"
    "+        return payload\n"
)
NOISE = "handle_request keeps failing in the staging environment since last release."


def _memory(tmp_path, *, scope: str = "proj", body: str = PATCH) -> CodingMemory:
    memory = CodingMemory(tmp_path / "memory.sqlite")
    memory.add(
        "req-1",
        scope,
        "sess-1",
        [SourceMessage("assistant", body), SourceMessage("assistant", NOISE)],
    )
    return memory


def test_anchor_channel_promotes_structured_evidence(tmp_path):
    memory = _memory(tmp_path)
    try:
        hits = memory.search(
            "handle_request", "proj", 5, SearchPolicy(anchor_weight=5.0, dense_weight=0.0)
        )
        assert hits
        assert "src/app.py" in hits[0].content
        assert "[anchors]" in hits[0].content
        assert "Handler.handle_request" in hits[0].content
    finally:
        memory.close()


def test_anchor_channel_is_bounded_by_weight(tmp_path):
    memory = _memory(tmp_path)
    try:
        without = memory.search("handle_request", "proj", 5, SearchPolicy(anchor_weight=0.0))
        with_anchor = memory.search(
            "handle_request", "proj", 5, SearchPolicy(anchor_weight=5.0, dense_weight=0.0)
        )
        assert without
        assert with_anchor
        assert "src/app.py" in with_anchor[0].content
    finally:
        memory.close()


def test_anchor_channel_respects_scope_isolation(tmp_path):
    memory = _memory(tmp_path)
    try:
        memory.add(
            "req-2",
            "other",
            "sess-2",
            [SourceMessage("assistant", "diff --git a/src/other.py b/src/other.py\n")],
        )
        hits = memory.search("other.py", "proj", 5, SearchPolicy(anchor_weight=5.0))
        assert all("other.py" not in hit.content for hit in hits)
    finally:
        memory.close()


def test_delete_scope_removes_anchor_index(tmp_path):
    memory = _memory(tmp_path)
    try:
        memory.delete_scope("proj")
        hits = memory.search("handle_request", "proj", 5, SearchPolicy(anchor_weight=5.0))
        assert hits == []
    finally:
        memory.close()
