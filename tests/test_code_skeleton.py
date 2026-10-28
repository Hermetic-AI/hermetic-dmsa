"""代码骨架摘要的提取、边界与证据包装测试."""

from __future__ import annotations

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.memory.coding.skeleton import MAX_LINES_PER_BLOCK, extract_skeleton

BLOCK = (
    "根因是缓存没有按仓库分区。\n"
    "```python\n"
    "import sqlite3\n"
    "from pathlib import Path\n"
    "class Cache:\n"
    "    def load(self, key):\n"
    "        return key\n"
    "    async def flush(self, *args, **kwargs):\n"
    "        return None\n"
    "def rebuild(cache, key):\n"
    "    return cache.load(key)\n"
    "```\n"
)


def test_skeleton_lists_imports_classes_and_signatures():
    skeleton = extract_skeleton(BLOCK)
    assert "import sqlite3" in skeleton
    assert "from pathlib import Path" in skeleton
    assert "class Cache" in skeleton
    assert "def load(self, key)" in skeleton
    assert "def flush(self, *args, **kwargs)" in skeleton
    assert "def rebuild(cache, key)" in skeleton


def test_skeleton_only_describes_python_blocks():
    text = "```bash\npytest -q\n```\n```python\ndef run():\n    pass\n```\n"
    skeleton = extract_skeleton(text)
    assert "pytest" not in skeleton
    assert "def run()" in skeleton


def test_skeleton_respects_depth_and_nonpositive_depth():
    assert extract_skeleton(BLOCK, depth=2).count("\n") == 1
    assert extract_skeleton(BLOCK, depth=0) == ""
    assert extract_skeleton(BLOCK, depth=-3) == ""


def test_skeleton_is_empty_without_parsable_code():
    assert extract_skeleton("只是散文, 没有代码块") == ""
    assert extract_skeleton("```python\ndef broken(:\n```") == ""
    assert extract_skeleton("") == ""


def test_skeleton_caps_lines_per_block():
    body = "".join(f"def f{i}():\n    pass\n" for i in range(MAX_LINES_PER_BLOCK * 2))
    skeleton = extract_skeleton(f"```python\n{body}```", depth=MAX_LINES_PER_BLOCK * 2)
    assert len(skeleton.splitlines()) == MAX_LINES_PER_BLOCK


def test_evidence_pack_renders_skeleton_section(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-1", "proj", "sess-1", [SourceMessage("assistant", BLOCK)])
        hits = memory.search("缓存 仓库 分区", "proj", 5, SearchPolicy(dense_weight=0.0))
        assert hits
        assert "[skeleton]" in hits[0].content
        assert "class Cache" in hits[0].content
    finally:
        memory.close()


def test_evidence_pack_omits_skeleton_when_depth_is_zero(tmp_path):
    memory = CodingMemory(tmp_path / "memory.sqlite")
    try:
        memory.add("req-1", "proj", "sess-1", [SourceMessage("assistant", BLOCK)])
        hits = memory.search(
            "缓存 仓库 分区", "proj", 5, SearchPolicy(dense_weight=0.0, skeleton_depth=0)
        )
        assert hits
        assert "[skeleton]" not in hits[0].content
    finally:
        memory.close()
