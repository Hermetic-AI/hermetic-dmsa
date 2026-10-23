# ruff: noqa: D415, RUF001, RUF002
"""将 CodingMemory 通过 stdio MCP 暴露给 Codex。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from dmsa.mcp_server.protocol import (
    JsonRpcErrorCode,
    JsonRpcRequest,
    make_error_response,
    make_response,
    parse_request,
)
from dmsa.memory import CodingMemory, SearchPolicy, SourceMessage
from dmsa.observability import get_logger

TOOLS: list[dict[str, Any]] = [
    {
        "name": "dmsa_coding_memory_search",
        "description": "检索本项目过去的缺陷修复、设计决策和验证经验；编码前调用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前任务、报错、文件或符号"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dmsa_coding_memory_record",
        "description": "在任务验证后沉淀可复用的代码经验，需明确区分事实、决策和验证结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "稳定幂等键，如任务/提交标识"},
                "session_id": {"type": "string", "description": "任务或会话标识"},
                "content": {"type": "string", "description": "问题、根因、改动、测试及结果"},
            },
            "required": ["request_id", "session_id", "content"],
        },
    },
    {
        "name": "dmsa_coding_memory_status",
        "description": "检查当前 CodingMemory 数据库和项目隔离范围。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
_logger = get_logger("dmsa.mcp_server.coding_memory")


class CodingMemoryMCPServer:
    """使用 DMSA CodingMemory 实例响应 MCP 工具请求。"""

    def __init__(self, memory: CodingMemory, scope: str, path: Path) -> None:
        """保存已打开的记忆库及只读连接信息。"""
        self.memory = memory
        self.scope = scope
        self.path = path

    async def handle(self, request: JsonRpcRequest) -> dict[str, Any] | None:
        """分发 MCP 请求；通知消息不返回 JSON-RPC 响应。"""
        if request.method == "initialize":
            return make_response(
                {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "dmsa-coding-memory", "version": "0.1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                },
                request.id,
            )
        if request.method == "notifications/initialized":
            return None
        if request.method == "ping":
            return make_response({}, request.id)
        if request.method == "tools/list":
            return make_response({"tools": TOOLS}, request.id)
        if request.method == "tools/call":
            return self._call(request)
        return make_error_response(
            JsonRpcErrorCode.METHOD_NOT_FOUND.value,
            f"method not found: {request.method!r}",
            request_id=request.id,
        )

    def _call(self, request: JsonRpcRequest) -> dict[str, Any]:
        """校验参数并调用 CodingMemory 公共 API。"""
        params = request.params
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return self._tool_error(request, "arguments must be an object")
        try:
            if name == "dmsa_coding_memory_status":
                result: Any = {
                    "backend": "CodingMemory",
                    "database": str(self.path),
                    "scope": self.scope,
                    "available": self.path.exists() or str(self.path) == ":memory:",
                }
            elif name == "dmsa_coding_memory_search":
                query = arguments.get("query")
                top_k = arguments.get("top_k", 5)
                if not isinstance(query, str) or type(top_k) is not int:
                    return self._tool_error(request, "query must be text and top_k an integer")
                hits = self.memory.search(
                    query, self.scope, top_k, SearchPolicy(dense_weight=0.0)
                )
                result = {
                    "scope": self.scope,
                    "hits": [
                        {"id": hit.id, "content": hit.content, "score": hit.score}
                        for hit in hits
                    ],
                }
            elif name == "dmsa_coding_memory_record":
                request_id = arguments.get("request_id")
                session_id = arguments.get("session_id")
                content = arguments.get("content")
                if not isinstance(request_id, str) or not isinstance(session_id, str):
                    return self._tool_error(request, "request_id and session_id must be text")
                if not isinstance(content, str):
                    return self._tool_error(request, "content must be text")
                revision = self.memory.add(
                    request_id,
                    self.scope,
                    session_id,
                    [SourceMessage("assistant", content)],
                )
                result = {"scope": self.scope, "revision": revision, "stored": True}
            else:
                return self._tool_error(request, f"unknown tool: {name!r}")
            content_result = {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ]
            }
            return make_response(content_result, request.id)
        except Exception as exc:
            _logger.error(
                "coding memory MCP operation failed",
                error_type=type(exc).__name__,
            )
            return self._tool_error(request, "coding memory operation failed")

    @staticmethod
    def _tool_error(request: JsonRpcRequest, message: str) -> dict[str, Any]:
        """返回 MCP 工具级错误。"""
        result = {"isError": True, "content": [{"type": "text", "text": message}]}
        return make_response(result, request.id)

    async def run_stdio(self, stdin: TextIO, stdout: TextIO) -> None:
        """跨 Windows 与 POSIX 读取行式 stdio JSON-RPC。"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def pump() -> None:
            try:
                for line in stdin:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=pump, daemon=True, name="dmsa-coding-mcp-stdin").start()
        while (line := await queue.get()) is not None:
            try:
                response = await self.handle(parse_request(line))
            except Exception as exc:
                response = make_error_response(
                    JsonRpcErrorCode.INVALID_REQUEST.value, str(exc)
                )
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def main() -> int:
    """以 MCP stdio 模式运行项目级 CodingMemory。"""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw_path = os.environ.get("DMSA_CODING_MEMORY_PATH", "~/.dmsa/coding-memory.sqlite")
    path = Path(raw_path).expanduser()
    scope = os.environ.get("DMSA_CODING_MEMORY_SCOPE", Path.cwd().name)
    from dmsa.mcp_server.remote_memory import configured_remote

    remote = configured_remote(scope)
    if remote is not None:
        try:
            asyncio.run(remote.run_stdio(sys.stdin, sys.stdout))
        finally:
            remote.close()
        return 0
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    memory = CodingMemory(path)
    server = CodingMemoryMCPServer(memory, scope, path)
    try:
        asyncio.run(server.run_stdio(sys.stdin, sys.stdout))
    except KeyboardInterrupt:
        return 0
    finally:
        memory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
