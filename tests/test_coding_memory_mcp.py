# ruff: noqa: RUF002
"""CodingMemory MCP 工具的协议与持久化验证。"""

from pathlib import Path
from typing import Any

from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import parse_request
from dmsa.memory import CodingMemory


def _call(server: CodingMemoryMCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """调用一个 MCP 工具并返回协议结果。"""
    request = parse_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
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
