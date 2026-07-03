"""L4 MCP server。

通过 stdio JSON-RPC 2.0 把 hermetic-dmsa 能力暴露给任何 MCP 兼容的 Agent
（Claude Code / OpenCode / Hermes / 自研）。

模块：

- :mod:`dmsa.mcp_server.server` — 异步 server 主体
- :mod:`dmsa.mcp_server.protocol` — JSON-RPC 消息解析 + 校验
- :mod:`dmsa.mcp_server.tools` — 7 个工具实现（dmsa_add_memory / dmsa_feedback / ...）
- :mod:`dmsa.mcp_server.cli` — ``dmsa-mcp-server`` CLI 入口

依赖规则：本层 import L1 + L2 + L3 + pmd，是代码层最顶部。
"""

from __future__ import annotations

from dmsa.mcp_server.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcErrorCode,
)
from dmsa.mcp_server.server import MCPServer
from dmsa.mcp_server.tools import (
    DMSA_TOOLS,
    TOOL_ADD_MEMORY,
    TOOL_APPLY_GRADIENT,
    TOOL_FEEDBACK,
    TOOL_QUERY_LINEAGE,
    TOOL_SEARCH,
    TOOL_SNAPSHOT,
    TOOL_TOOL_CALL,
)

__all__ = [
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcErrorCode",
    "MCPServer",
    "DMSA_TOOLS",
    "TOOL_ADD_MEMORY",
    "TOOL_APPLY_GRADIENT",
    "TOOL_FEEDBACK",
    "TOOL_QUERY_LINEAGE",
    "TOOL_SEARCH",
    "TOOL_SNAPSHOT",
    "TOOL_TOOL_CALL",
]
