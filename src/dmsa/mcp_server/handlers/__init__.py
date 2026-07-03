"""所有 handler 的注册入口。"""

from __future__ import annotations

from dmsa.mcp_server.handlers.feedback import _register_handlers as _reg_feedback
from dmsa.mcp_server.handlers.memory import (
    _handle_add_memory,
    _handle_query_lineage,
    _handle_search,
)
from dmsa.mcp_server.tools import (
    TOOL_ADD_MEMORY,
    TOOL_QUERY_LINEAGE,
    TOOL_SEARCH,
    register_tool,
)
from dmsa.mcp_server.handlers.tool_version import _register_handlers as _reg_tool_version

__all__ = ["register_default_handlers"]


def register_default_handlers(server: object) -> None:
    """注册所有 7 个工具的默认实现到全局 DMSA_TOOLS dict。"""
    register_tool(TOOL_ADD_MEMORY, _handle_add_memory)
    register_tool(TOOL_QUERY_LINEAGE, _handle_query_lineage)
    register_tool(TOOL_SEARCH, _handle_search)
    _reg_feedback()
    _reg_tool_version()
