"""L3 工具调用反向传播（Tool-Use BP）。

5 个模块：

- :mod:`dmsa.toolbp.schema_node` — ToolSchemaNode / ToolCallRecord
- :mod:`dmsa.toolbp.ast_projection` — JSON Schema ↔ 树形结构互转
- :mod:`dmsa.toolbp.edit_ops` — 投影编辑指令 (UPDATE_LEAF / UPDATE_REQUIRED)
- :mod:`dmsa.toolbp.critic` — 工具调用反馈生成器
- :mod:`dmsa.toolbp.projector` — :class:`ToolBPEngine` 编排

依赖规则：本层 import L1 + L2 + pmd，不 import L4。
"""

from __future__ import annotations

from dmsa.toolbp.ast_projection import SchemaNode, parse_schema, dump_schema
from dmsa.toolbp.critic import ToolCallCritic
from dmsa.toolbp.edit_ops import (
    EditOp,
    UpdateLeafOp,
    UpdateRequiredOp,
    validate_edit_op,
)
from dmsa.toolbp.projector import ToolBPEngine
from dmsa.toolbp.schema_node import (
    ToolCallRecord,
    ToolSchemaNode,
    make_call_record,
    make_schema_node,
)

__all__ = [
    "SchemaNode",
    "parse_schema",
    "dump_schema",
    "ToolCallCritic",
    "EditOp",
    "UpdateLeafOp",
    "UpdateRequiredOp",
    "validate_edit_op",
    "ToolBPEngine",
    "ToolSchemaNode",
    "ToolCallRecord",
    "make_schema_node",
    "make_call_record",
]
