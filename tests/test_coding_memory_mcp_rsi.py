"""stdio MCP 的活动策略、反馈与任务结果工具测试."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dmsa.gradient.integrated import IntegratedRSI
from dmsa.mcp_server.coding_memory import CodingMemoryMCPServer
from dmsa.mcp_server.protocol import parse_request
from dmsa.memory import CodingMemory, SourceMessage


def _server(tmp_path: Path) -> tuple[CodingMemory, CodingMemoryMCPServer, IntegratedRSI]:
    """构造带 RSI 的本地 MCP 服务."""
    path = tmp_path / "coding.sqlite"
    memory = CodingMemory(path)
    rsi = IntegratedRSI(memory)
    server = CodingMemoryMCPServer(memory, "repo-a", path, rsi)
    return memory, server, rsi


def _call(server: CodingMemoryMCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """调用一个 MCP 工具并返回解析后的结果."""
    request = parse_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return json.loads(server._call(request)["result"]["content"][0]["text"])


def test_search_returns_trace_id(tmp_path: Path) -> None:
    memory, server, _ = _server(tmp_path)
    try:
        memory.add("r1", "repo-a", "s1", [SourceMessage("assistant", "缓存 revision 递增")])
        result = _call(server, "dmsa_coding_memory_search", {"query": "缓存"})
        assert result["trace_id"]
        assert result["hits"]
    finally:
        memory.close()


def test_local_stdio_search_keeps_dense_disabled(tmp_path: Path, monkeypatch) -> None:
    memory, server, _ = _server(tmp_path)
    captured: dict[str, Any] = {}
    original = memory.search

    def capture(query, user_id, top_k, policy=None, repo=""):
        captured["policy"] = policy
        captured["repo"] = repo
        return original(query, user_id, top_k, policy, repo=repo)

    try:
        monkeypatch.setattr(memory, "search", capture)
        _call(server, "dmsa_coding_memory_search", {"query": "缓存"})
        assert captured["policy"].dense_weight == 0.0
    finally:
        memory.close()


def test_feedback_stores_label_and_triggers_evolution(tmp_path: Path) -> None:
    memory, server, _ = _server(tmp_path)
    try:
        memory.add("r1", "repo-a", "s1", [SourceMessage("assistant", "缓存 revision 递增")])
        trace = _call(server, "dmsa_coding_memory_search", {"query": "缓存"})["trace_id"]
        result = _call(
            server,
            "dmsa_coding_memory_feedback",
            {"trace_id": trace, "outcome": "failure", "kind": "identifier_confusion"},
        )
        assert result["stored"] is True
        assert result["duplicate"] is False
        assert "evolution" in result
    finally:
        memory.close()


def test_feedback_rejects_unknown_trace(tmp_path: Path) -> None:
    memory, server, _ = _server(tmp_path)
    try:
        request = parse_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "dmsa_coding_memory_feedback",
                    "arguments": {"trace_id": "missing", "outcome": "failure"},
                },
            }
        )
        result = server._call(request)["result"]
        assert result["isError"] is True
    finally:
        memory.close()


def test_mcp_tools_isolate_repo_dimension(tmp_path: Path) -> None:
    memory, server, _ = _server(tmp_path)
    try:
        recorded = _call(
            server,
            "dmsa_coding_memory_record",
            {
                "request_id": "r-repo",
                "session_id": "s-repo",
                "content": (
                    "diff --git a/src/alpha.py b/src/alpha.py\n+def alpha_token():\n+    pass\n"
                ),
                "repo": "alpha",
            },
        )
        assert recorded["stored"] is True
        assert recorded["repo"] == "alpha"
        scoped = _call(
            server, "dmsa_coding_memory_search", {"query": "alpha_token", "repo": "alpha"}
        )
        assert scoped["repo"] == "alpha"
        assert scoped["hits"]
        unscoped = _call(server, "dmsa_coding_memory_search", {"query": "alpha_token"})
        assert unscoped["hits"] == []
    finally:
        memory.close()


def test_mcp_search_rejects_invalid_repo(tmp_path: Path) -> None:
    memory, server, _ = _server(tmp_path)
    try:
        request = parse_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "dmsa_coding_memory_search",
                    "arguments": {"query": "缓存", "repo": 7},
                },
            }
        )
        assert server._call(request)["result"]["isError"] is True
    finally:
        memory.close()


def test_task_result_is_recorded(tmp_path: Path) -> None:
    memory, server, rsi = _server(tmp_path)
    try:
        memory.add("r1", "repo-a", "s1", [SourceMessage("assistant", "缓存 revision 递增")])
        trace = _call(server, "dmsa_coding_memory_search", {"query": "缓存"})["trace_id"]
        result = _call(
            server,
            "dmsa_coding_memory_task_result",
            {
                "task_id": "task-1",
                "trace_id": trace,
                "outcome": "success",
                "tests_passed": 2,
                "files_changed": ["src/cache.py"],
            },
        )
        assert result["stored"] is True
        payload = rsi.task_results("repo-a")
        assert payload["summary"]["success"] == 1
        assert payload["results"][0]["files_changed"] == ["src/cache.py"]
    finally:
        memory.close()


def test_status_reports_whether_rsi_and_active_policy_are_live(tmp_path: Path) -> None:
    memory, server, _ = _server(tmp_path)
    try:
        status = _call(server, "dmsa_coding_memory_status", {})
        assert status["scope"] == "repo-a"
        assert status["rsi"]["enabled"] is True
        assert status["rsi"]["models"] is False
        assert status["rsi"]["active_policy"]["dense_weight"] == memory.policy.dense_weight
    finally:
        memory.close()
