"""Tool / Version 工具的 handler。

- dmsa_tool_call: 记录工具调用结果到 DAG（不实际执行工具）
- dmsa_snapshot: 打点版本
"""

from __future__ import annotations

from typing import Any

from dmsa.errors import MemoryLayerError
from dmsa.mcp_server.tools import (
    TOOL_SNAPSHOT,
    TOOL_TOOL_CALL,
    register_tool,
)
from dmsa.toolbp import (
    ToolCallCritic,
    make_call_record,
)
from dmsa.types import ToolCallRecord

__all__ = ["_handle_snapshot", "_handle_tool_call", "_register_handlers"]

_critic = ToolCallCritic()


def _handle_tool_call(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_tool_call：包装 Agent 的 tool call 并写入 DAG。

    v0.1 简化：记录 ToolCallRecord + 生成 critique feedback 附加到 tool_name 节点。
    完整实现：把 ToolCallRecord 当作一个 memory node 写入，parent 是 tool schema node。
    """
    tool_name = args.get("tool_name", "")
    call_args = args.get("args", {})
    if not tool_name:
        raise MemoryLayerError("tool_name required", details={"field": "tool_name"})

    record: ToolCallRecord = make_call_record(
        call_id=f"call-{tool_name}",
        tool_name=tool_name,
        args=call_args,
        result=args.get("result"),
        error=args.get("error"),
    )
    # 1. 生成 critique
    signal = _critic.critique(record)
    # 2. 返回信号（Agent 可选后续调用 dmsa_feedback 应用）
    return {
        "record": {
            "call_id": record.call_id,
            "tool_name": record.tool_name,
            "args": record.args,
            "has_error": record.error is not None,
        },
        "critique": {
            "feedback_id": signal.feedback_id,
            "category": signal.extra_context.get("category"),
            "severity": signal.severity,
            "feedback_text": signal.feedback_text,
        },
    }


def _handle_snapshot(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_snapshot。"""
    snap = layer.snapshot(
        description=args.get("description", ""),
        tag=args.get("tag"),
    )
    return {
        "version_id": snap.version_id,
        "created_at": snap.created_at,
        "description": snap.description,
        "tag": snap.tag,
        "node_count": snap.node_count,
        "edge_count": snap.edge_count,
    }


def _register_handlers() -> None:
    """注册本模块的 handler。"""
    register_tool(TOOL_TOOL_CALL, _handle_tool_call)
    register_tool(TOOL_SNAPSHOT, _handle_snapshot)
