"""本地记忆浏览器的数据筛选与只读契约。"""

import json
import sqlite3
from http.server import ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest

from dmsa.inspect.memory_browser import MemoryBrowserHandler
from dmsa.inspect.memory_browser_data import read_metadata, read_records
from dmsa.memory import CodingMemory, SourceMessage


def _store(path: Path) -> CodingMemory:
    """生成可重复的浏览器测试数据。"""
    memory = CodingMemory(path)
    memory.add(
        "bug-request",
        "repo-a",
        "session-a",
        [
            SourceMessage(
                "assistant",
                "Bug: cache returns stale data\nRoot cause: revision is not incremented\n"
                "Fix: update revision in CacheStore\npytest: 3 passed",
                timestamp=1_700_000_000,
            )
        ],
    )
    memory.add(
        "feature-request",
        "repo-b",
        "session-b",
        [
            SourceMessage(
                "tool",
                "Feature: add support for cache export\nImplementation: add export_cache",
                timestamp=1_710_000_000,
            )
        ],
    )
    return memory


def test_memory_browser_returns_provenance_and_experience_fields(tmp_path: Path) -> None:
    """浏览结果包含来源元数据与可追溯经验视图。"""
    path = tmp_path / "memory.sqlite"
    memory = _store(path)

    result = read_records(path, {"scope": ["repo-a"]})

    assert result["total"] == 1
    record = result["records"][0]
    assert record["kind"] == "bug_fix"
    assert record["root_cause"] == "revision is not incremented"
    assert record["validation"] == "pytest: 3 passed"
    assert record["source_content"].startswith("Bug: cache")
    assert record["message_id"]
    assert record["start"] == 0
    memory.close()


def test_memory_browser_filters_allows_scope_kind_and_evidence_facets(tmp_path: Path) -> None:
    """scope、类型、路径、时间和经验槽位条件可组合。"""
    path = tmp_path / "memory.sqlite"
    memory = _store(path)

    result = read_records(
        path,
        {
            "scope": ["repo-a"],
            "kind": ["bug_fix"],
            "facet": ["has_validation"],
            "symbol": ["CacheStore"],
            "from": ["1699999999"],
            "to": ["1700000001"],
        },
    )

    assert result["total"] == 1
    assert result["records"][0]["request_id"] == "bug-request"
    memory.close()


def test_memory_browser_pages_large_store_without_full_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过扫描上限的记忆库仍能按时间分页，只有后过滤查询才需要全量扫描。"""
    import dmsa.inspect.memory_browser_data as browser_data

    monkeypatch.setattr(browser_data, "MAX_SCAN_ROWS", 3)
    monkeypatch.setattr(browser_data, "PAGE_SIZE", 2)
    path = tmp_path / "memory.sqlite"
    memory = _store(path)
    for index in range(5):
        memory.add(
            f"bulk-request-{index}",
            "repo-a",
            "session-a",
            [
                SourceMessage(
                    "user",
                    f"Feature: bulk cache warmup case {index}\n"
                    "Fix: iterate segments in stable order",
                    timestamp=1_720_000_000 + index,
                )
            ],
        )
    memory.close()

    first = read_records(path, {"sort": ["newest"], "page": ["1"]})
    second = read_records(path, {"sort": ["newest"], "page": ["2"]})

    assert first["total"] == 7
    assert first["pages"] == 4
    assert [row["request_id"] for row in first["records"]] == [
        "bulk-request-4",
        "bulk-request-3",
    ]
    assert [row["request_id"] for row in second["records"]] == [
        "bulk-request-2",
        "bulk-request-1",
    ]
    assert {row["id"] for row in first["records"]}.isdisjoint(
        {row["id"] for row in second["records"]}
    )

    scoped = read_records(path, {"scope": ["repo-a"], "sort": ["oldest"]})
    assert [row["request_id"] for row in scoped["records"]][:2] == [
        "bug-request",
        "bulk-request-0",
    ]

    with pytest.raises(ValueError, match="add a scope"):
        read_records(path, {"kind": ["feature_enhancement"]})
    # 导出全量（all=1）仍走扫描路径，因此受同一上限保护。
    with pytest.raises(ValueError, match="add a scope"):
        read_records(path, {"all": ["1"]})


def test_memory_browser_reports_storage_and_stays_read_only(tmp_path: Path) -> None:
    """概要包含来源计数，浏览器数据库连接禁止写入。"""
    path = tmp_path / "memory.sqlite"
    memory = _store(path)
    memory.close()

    metadata = read_metadata(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM requests")
    finally:
        connection.close()

    assert metadata["counts"] == {
        "requests": 2,
        "messages": 2,
        "chunks": 2,
        "vectors": 0,
        "scopes": 2,
    }
    assert metadata["read_only"] is True
    assert "Qdrant" in metadata["vector_note"]


def test_memory_browser_serves_page_and_live_metadata(tmp_path: Path) -> None:
    """页面和 JSON 元数据通过本地 HTTP 正常服务。"""
    path = tmp_path / "memory.sqlite"
    memory = _store(path)
    memory.close()
    handler = type(
        "TestMemoryBrowserHandler",
        (MemoryBrowserHandler,),
        {
            "database": path,
            "page": files("dmsa.inspect").joinpath("memory-explorer.html").read_bytes(),
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/meta") as response:
            metadata = json.load(response)
        with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
            page = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert metadata["counts"]["chunks"] == 2
    assert "记忆档案" in page
