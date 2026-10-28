"""证据来源头与历史整文件快照剔除的回归测试."""

from __future__ import annotations

from pathlib import Path

from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage


def test_header_contains_path_and_symbol(tmp_path: Path) -> None:
    """来源头必须带文件归属与符号, Agent 才能定位到要改的位置."""
    memory = CodingMemory(tmp_path / "header.sqlite")
    memory.add(
        "req-1",
        "u",
        "sess-1",
        [
            SourceMessage(
                "assistant",
                "src/app.py 修复缓存刷新: def refresh_cache(store): return store.refresh()",
            )
        ],
    )
    hits = memory.search("refresh_cache src/app.py", "u", 5)
    assert hits
    assert "[path=src/app.py]" in hits[0].content
    assert "[symbol=refresh_cache" in hits[0].content
    memory.close()


def test_header_drops_tool_noise_symbols(tmp_path: Path) -> None:
    """工具调用前缀带来的伪符号不得出现在来源头里."""
    memory = CodingMemory(tmp_path / "noise.sqlite")
    memory.add(
        "req-1",
        "u",
        "sess-1",
        [SourceMessage("assistant", "[tool_result] def real_fn(): return 1")],
    )
    hits = memory.search("real_fn", "u", 5)
    assert hits
    header = hits[0].content.splitlines()[0]
    assert "tool_result" not in header
    assert "[symbol=real_fn]" in header
    memory.close()


def test_stale_write_continuation_chunks_are_dropped(tmp_path: Path) -> None:
    """整文件写入的续接片也必须被剔除, 不能当成现行代码注入."""
    memory = CodingMemory(tmp_path / "stale.sqlite", policy=SearchPolicy(chunk_size=200))
    # 一条足够长的整文件写入: 只有首片带 [tool_use Write] 标记, 续接片看起来像普通代码。
    body = (
        '[tool_use Write] {"file_path": "/tmp/old.py", "content": "\\n'
        + ("def legacy_helper(value):\n    return value + 1\n" * 40)
        + '"}'
    )
    memory.add("req-1", "u", "sess-1", [SourceMessage("assistant", body)])
    rows = memory.store.db.execute(
        "SELECT id FROM chunks WHERE user_id=? ORDER BY start", ("u",)
    ).fetchall()
    assert len(rows) > 2, "该场景需要多条切片才能覆盖续接片"
    hits = memory.search("legacy_helper", "u", 50)
    assert all("legacy_helper" not in hit.content for hit in hits), "旧快照续接片不得被注入"
    memory.close()


def test_normal_message_keeps_all_chunks(tmp_path: Path) -> None:
    """普通长消息的续接片必须保留, 不能被误判成旧快照."""
    memory = CodingMemory(tmp_path / "normal.sqlite", policy=SearchPolicy(chunk_size=200))
    body = "".join(
        f"def helper_{index}(value):\n    return value + {index}\n" for index in range(40)
    )
    memory.add("req-1", "u", "sess-1", [SourceMessage("assistant", body)])
    hits = memory.search("helper_", "u", 50)
    assert len(hits) > 2
    memory.close()
