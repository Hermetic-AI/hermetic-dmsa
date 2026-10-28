"""CodingMemory MCP 工具的协议与持久化验证。"""

import json
from pathlib import Path
from typing import Any

from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import parse_request
from dmsa.memory import CodingMemory, SearchPolicy


def _call(server: CodingMemoryMCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """调用一个 MCP 工具并返回协议结果。"""
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    response = server._call(request)
    return response["result"]


def test_coding_memory_mcp_lists_tools_and_reports_status(tmp_path: Path) -> None:
    """工具发现与状态检查能识别 CodingMemory 后端。"""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)

    result = _call(server, "dmsa_coding_memory_status", {})

    assert "CodingMemory" in result["content"][0]["text"]
    assert "repo-a" in result["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_records_then_retrieves_experience(tmp_path: Path) -> None:
    """MCP 写入经验后可在同一 scope 检索到。"""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)
    recorded = _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-cache-1",
            "session_id": "session-1",
            "content": (
                "根因: 缓存失效未更新 revision。修复: 提交后递增 revision。"
                "验证: pytest tests/test_cache.py 通过。"
            ),
        },
    )

    searched = _call(
        server,
        "dmsa_coding_memory_search",
        {"query": "缓存失效 revision pytest", "top_k": 3},
    )

    assert '"stored": true' in recorded["content"][0]["text"]
    assert "缓存失效" in searched["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_rejects_invalid_tool_arguments(tmp_path: Path) -> None:
    """参数错误以 MCP 工具错误返回，不会污染记忆库。"""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)

    result = _call(server, "dmsa_coding_memory_search", {"query": "x", "top_k": True})

    assert result["isError"] is True
    memory.close()


def test_coding_memory_mcp_records_and_matches_procedure(tmp_path: Path) -> None:
    """程序性记忆可经 MCP 沉淀并按触发语境匹配回来。"""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)
    recorded = _call(
        server,
        "dmsa_coding_memory_procedure",
        {
            "action": "record",
            "request_id": "proc-1",
            "session_id": "session-1",
            "content": (
                "# 修复缓存竞态\n"
                "1. 把版本检查移到锁内\n"
                "2. 提交缓存写入后再发布条目\n"
                "3. 校验读到的版本与期望一致\n"
            ),
        },
    )
    matched = _call(
        server,
        "dmsa_coding_memory_procedure",
        {"action": "search", "query": "缓存 竞态 版本检查"},
    )

    assert '"stored": true' in recorded["content"][0]["text"]
    assert "修复缓存竞态" in matched["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_reports_effectiveness_without_samples(tmp_path: Path) -> None:
    """没有真实样本时有效性报告如实留空, 不伪造数字。"""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)

    result = _call(server, "dmsa_coding_memory_effectiveness", {})

    assert '"label_recall": null' in result["content"][0]["text"]
    assert '"task_total": 0' in result["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_lists_write_conflicts(tmp_path: Path) -> None:
    """跨作者写入后, MCP 能列出待仲裁的写冲突."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path, policy=SearchPolicy(admission_similarity=0.5, neighbor_window=0))
    server = CodingMemoryMCPServer(memory, "team:alpha", path)
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-1",
            "session_id": "session-1",
            "author": "alice",
            "content": "def handler(): return 1  # 用缓存兜底",
        },
    )
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-2",
            "session_id": "session-2",
            "author": "bob",
            "content": "def handler(): return 2  # 用缓存兜底",
        },
    )

    listed = _call(server, "dmsa_coding_memory_conflicts", {"action": "list"})

    assert '"author": "bob"' in listed["content"][0]["text"]
    assert '"rival_author": "alice"' in listed["content"][0]["text"]
    assert '"status": "open"' in listed["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_resolves_write_conflict(tmp_path: Path) -> None:
    """仲裁结论经 MCP 回写, 冲突状态变为已解决."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path, policy=SearchPolicy(admission_similarity=0.5, neighbor_window=0))
    server = CodingMemoryMCPServer(memory, "team:alpha", path)
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-1",
            "session_id": "session-1",
            "author": "alice",
            "content": "def handler(): return 1  # 用缓存兜底",
        },
    )
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-2",
            "session_id": "session-2",
            "author": "bob",
            "content": "def handler(): return 2  # 用缓存兜底",
        },
    )
    listed = json.loads(_call(server, "dmsa_coding_memory_conflicts", {})["content"][0]["text"])

    resolved = _call(
        server,
        "dmsa_coding_memory_conflicts",
        {
            "action": "resolve",
            "conflict_id": listed["conflicts"][0]["id"],
            "resolution": "keep_new",
        },
    )

    assert '"applied": true' in resolved["content"][0]["text"]
    assert '"status": "resolved"' in resolved["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_rejects_invalid_conflict_arguments(tmp_path: Path) -> None:
    """未登记的处置方式与缺失的冲突 ID 都以工具错误返回."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "team:alpha", path)

    bad_resolution = _call(
        server,
        "dmsa_coding_memory_conflicts",
        {"action": "resolve", "conflict_id": "x", "resolution": "delete_both"},
    )
    missing_id = _call(
        server,
        "dmsa_coding_memory_conflicts",
        {"action": "resolve", "resolution": "keep_new"},
    )

    assert bad_resolution["isError"] is True
    assert missing_id["isError"] is True
    memory.close()


def test_coding_memory_mcp_returns_recall_explanation(tmp_path: Path) -> None:
    """召回解释工具返回命中通道、命中词项与迭代轮次."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-1",
            "session_id": "session-1",
            "content": "根因: 缓存失效没有递增 revision。修复: 提交后递增 revision。",
        },
    )

    result = _call(server, "dmsa_coding_memory_recall", {"query": "缓存 revision 修复", "hops": 2})

    assert '"channels"' in result["content"][0]["text"]
    assert '"matched_terms"' in result["content"][0]["text"]
    assert '"rounds"' in result["content"][0]["text"]
    assert '"hops": 1' in result["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_rejects_invalid_recall_arguments(tmp_path: Path) -> None:
    """越界的跳数与 top_k 以工具错误返回."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)

    bad_hops = _call(server, "dmsa_coding_memory_recall", {"query": "缓存", "hops": 9})
    bad_top_k = _call(server, "dmsa_coding_memory_recall", {"query": "缓存", "top_k": True})

    assert bad_hops["isError"] is True
    assert bad_top_k["isError"] is True
    memory.close()


def test_coding_memory_mcp_hides_internal_exception_details(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """内部异常消息不能将来源内容或本机路径回显给 MCP 客户端。"""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)

    def fail_search(*_: Any, **__: Any) -> list[Any]:
        """模拟可能带有敏感值的底层错误。"""
        raise RuntimeError("/private/worktree source=secret-token")

    monkeypatch.setattr(memory, "search", fail_search)
    result = _call(server, "dmsa_coding_memory_search", {"query": "cache"})
    message = result["content"][0]["text"]

    assert result["isError"] is True
    assert message == "coding memory operation failed"
    assert "private" not in message
    assert "secret-token" not in message
    memory.close()


def test_coding_memory_mcp_consolidates_and_transfers_experience(tmp_path: Path) -> None:
    """合并与迁移工具回报派生条目与来源, 目标仓库能检索到迁移来的规则."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)
    head = "根因: 缓存失效没有递增 revision。修复: 提交后递增 revision。"
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-1",
            "session_id": "session-1",
            "content": head + "改动在 src/cache.py 的 flush_buffer。",
            "repo": "alpha",
        },
    )
    _call(
        server,
        "dmsa_coding_memory_record",
        {
            "request_id": "fix-2",
            "session_id": "session-2",
            "content": head + "改动在 src/buffer.py 的 flush_buffer。",
            "repo": "alpha",
        },
    )

    consolidated = _call(
        server, "dmsa_coding_memory_consolidate", {"repo": "alpha", "min_group": 2}
    )
    transferred = _call(
        server,
        "dmsa_coding_memory_transfer",
        {"source_repo": "alpha", "target_repo": "beta"},
    )
    searched = _call(
        server,
        "dmsa_coding_memory_search",
        {"query": "缓存 revision 修复", "repo": "beta"},
    )

    assert '"written"' in consolidated["content"][0]["text"]
    assert '"sources"' in consolidated["content"][0]["text"]
    assert '"transferred"' in transferred["content"][0]["text"]
    assert "缓存" in searched["content"][0]["text"]
    memory.close()


def test_coding_memory_mcp_rejects_invalid_consolidation_arguments(tmp_path: Path) -> None:
    """合并与迁移的越界参数以工具错误返回."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, "repo-a", path)

    bad_group = _call(server, "dmsa_coding_memory_consolidate", {"min_group": 1})
    same_repo = _call(
        server,
        "dmsa_coding_memory_transfer",
        {"source_repo": "alpha", "target_repo": "alpha"},
    )
    missing_target = _call(server, "dmsa_coding_memory_transfer", {"source_repo": "alpha"})

    assert bad_group["isError"] is True
    assert same_repo["isError"] is True
    assert missing_target["isError"] is True
    memory.close()
