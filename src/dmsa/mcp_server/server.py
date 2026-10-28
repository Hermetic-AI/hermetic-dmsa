"""MCP server 主入口。

依赖 stdlib asyncio（无外部依赖）。通过 stdio JSON-RPC 2.0 与 Agent 通信。

支持的 method：
- ``initialize`` — 返回 server info
- ``tools/list`` — 列出所有 7 个工具的 schema
- ``tools/call`` — 调用工具，返回结果
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any, TextIO

from dmsa.errors import DMSAError
from dmsa.mcp_server.protocol import (
    JsonRpcErrorCode,
    JsonRpcRequest,
    make_error_response,
    make_response,
    parse_request,
)
from dmsa.mcp_server.tools import DMSA_TOOLS, TOOL_SCHEMAS
from dmsa.observability import get_logger

__all__ = ["MCPServer"]


_logger = get_logger("dmsa.mcp_server.server")


# MCP server info
SERVER_INFO: dict[str, Any] = {
    "name": "hermetic-dmsa",
    "version": "0.1.0",
}

SERVER_CAPABILITIES: dict[str, Any] = {
    "tools": {"listChanged": False},
}


class MCPServer:
    """MCP server 主体。

    Args:
        memory_layer: :class:`dmsa.memory.MemoryLayer` 实例（被工具调用）。
        tool_schemas: 自定义工具 schema（可选，默认用内置 7 个）。
        stdin: 输入流（默认 :data:`sys.stdin`）。
        stdout: 输出流（默认 :data:`sys.stdout`）。
    """

    def __init__(
        self,
        *,
        memory_layer: Any,
        tool_schemas: dict[str, dict[str, Any]] | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        """初始化 MCP server, 绑定内存层与 IO 流并注册默认工具处理器。"""
        self._memory = memory_layer
        self._tool_schemas = tool_schemas or TOOL_SCHEMAS
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._ensure_embedder()
        self._register_default_handlers()

    def _ensure_embedder(self) -> None:
        """Server 路径开箱可用：没注入 embedder 时挂零依赖的 HashEmbedder。

        dmsa_search 需要 embedder；与"默认零 LLM 依赖可跑"的设计一致，
        缺省用 :class:`dmsa.gradient.HashEmbedder`（确定性哈希嵌入）。
        """
        if getattr(self._memory, "embedder", None) is not None:
            return
        if not hasattr(self._memory, "attach_embedder"):
            return  # 非 MemoryLayer 的自定义 layer，由调用方自行负责
        from dmsa.gradient import HashEmbedder

        self._memory.attach_embedder(HashEmbedder(dim=64))
        _logger.info("default HashEmbedder attached", dim=64)

    def _register_default_handlers(self) -> None:
        """注册 7 个工具的默认实现。"""
        from dmsa.mcp_server.handlers import register_default_handlers

        register_default_handlers(self)

    async def handle_request(self, request: JsonRpcRequest) -> dict[str, Any]:
        """处理单个 JSON-RPC 请求，返回响应 dict。"""
        if request.method == "initialize":
            return make_response(
                {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": SERVER_INFO,
                    "capabilities": SERVER_CAPABILITIES,
                },
                request.id,
            )
        if request.method == "tools/list":
            tools = list(self._tool_schemas.values())
            return make_response({"tools": tools}, request.id)
        if request.method == "tools/call":
            return await self._handle_tools_call(request)
        if request.method == "ping":
            return make_response({}, request.id)
        return make_error_response(
            JsonRpcErrorCode.METHOD_NOT_FOUND.value,
            f"method not found: {request.method!r}",
            request_id=request.id,
        )

    async def _handle_tools_call(self, request: JsonRpcRequest) -> dict[str, Any]:
        """处理 tools/call。"""
        params = request.params
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name not in DMSA_TOOLS:
            return make_error_response(
                JsonRpcErrorCode.TOOL_NOT_FOUND.value,
                f"unknown tool: {tool_name!r}",
                request_id=request.id,
                error_data={"known_tools": list(self._tool_schemas.keys())},
            )
        handler = DMSA_TOOLS[tool_name]
        try:
            result = handler(arguments, self._memory)
            if asyncio.iscoroutine(result):
                result = await result
            return make_response(result, request.id)
        except DMSAError as exc:
            return make_error_response(
                JsonRpcErrorCode.TOOL_EXECUTION_ERROR.value,
                exc.message,
                request_id=request.id,
                error_data=exc.to_dict(),
            )
        except Exception as exc:  # pragma: no cover - unexpected
            return make_error_response(
                JsonRpcErrorCode.INTERNAL_ERROR.value,
                f"internal error: {exc}",
                request_id=request.id,
                error_data={"type": type(exc).__name__},
            )

    async def run_stdio(self) -> None:
        """从 stdin 读 JSON-RPC 请求，输出到 stdout。

        用后台守护线程做阻塞式读行，再通过 :class:`asyncio.Queue` 桥接到
        async 侧。Windows 的 ProactorEventLoop 不支持用
        ``connect_read_pipe`` 接管 stdin 管道（会直接崩溃），线程方案在
        Windows / Linux / macOS 上行为一致。
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _pump_stdin() -> None:
            try:
                for line in self._stdin:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
            except Exception as exc:  # pragma: no cover - 依赖平台 IO
                _logger.error("stdin pump failed", error=str(exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_pump_stdin, daemon=True, name="dmsa-stdin-pump").start()

        while True:
            line = await queue.get()
            if line is None:  # EOF：stdin 关闭，干净退出
                break
            line = line.rstrip()
            if not line:
                continue
            try:
                request = parse_request(line)
                response = await self.handle_request(request)
            except DMSAError as exc:
                response = make_error_response(
                    JsonRpcErrorCode.INVALID_REQUEST.value,
                    exc.message,
                    error_data=exc.to_dict(),
                )
            except Exception as exc:  # pragma: no cover - unexpected
                response = make_error_response(
                    JsonRpcErrorCode.INTERNAL_ERROR.value,
                    f"internal error: {exc}",
                )
            self._stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            self._stdout.flush()

    def process_request_sync(self, payload: str | dict[str, Any]) -> dict[str, Any]:
        """同步处理单个请求（用于测试）。"""
        request = parse_request(payload)
        return asyncio.run(self.handle_request(request))
