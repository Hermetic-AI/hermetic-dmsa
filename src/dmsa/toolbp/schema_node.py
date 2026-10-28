"""ToolSchemaNode / ToolCallRecord（重导出 dmsa.types）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dmsa.types import ToolCallRecord, ToolSchemaNode

__all__ = ["ToolCallRecord", "ToolSchemaNode", "make_call_record", "make_schema_node"]


def make_schema_node(
    *,
    schema_id: str,
    name: str,
    schema_dict: Mapping[str, Any],
    requires_grad: bool = True,
    grad_learning_rate: float = 0.1,
) -> ToolSchemaNode:
    """构造 :class:`ToolSchemaNode`。"""
    return ToolSchemaNode(
        schema_id=schema_id,
        name=name,
        schema_dict=dict(schema_dict),
        version=1,
        requires_grad=requires_grad,
        grad_learning_rate=grad_learning_rate,
    )


def make_call_record(
    *,
    call_id: str,
    tool_name: str,
    args: Mapping[str, Any],
    result: Any = None,
    error: str | None = None,
) -> ToolCallRecord:
    """构造 :class:`ToolCallRecord`。"""
    return ToolCallRecord(
        call_id=call_id,
        tool_name=tool_name,
        args=dict(args),
        result=result,
        error=error,
    )
