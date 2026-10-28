"""MCP 工具实现（7 个工具暴露给 Agent）。

每个工具：
- 接受 JSON 字典参数
- 返回 JSON 字典结果
- 抛 :class:`DMSAError` 子类会被转成 JSON-RPC 错误响应
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dmsa.errors import DMSAError, ToolNotFoundError

__all__ = [
    "DMSA_TOOLS",
    "TOOL_ADD_MEMORY",
    "TOOL_APPLY_GRADIENT",
    "TOOL_FEEDBACK",
    "TOOL_QUERY_LINEAGE",
    "TOOL_SEARCH",
    "TOOL_SNAPSHOT",
    "TOOL_TOOL_CALL",
]


# ============================================================================
# 工具元数据
# ============================================================================

TOOL_ADD_MEMORY = "dmsa_add_memory"
TOOL_QUERY_LINEAGE = "dmsa_query_lineage"
TOOL_FEEDBACK = "dmsa_feedback"
TOOL_APPLY_GRADIENT = "dmsa_apply_gradient"
TOOL_TOOL_CALL = "dmsa_tool_call"
TOOL_SNAPSHOT = "dmsa_snapshot"
TOOL_SEARCH = "dmsa_search"


# 工具 schema（OpenAI function calling 格式；MCP tools/list 也用这个）
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    TOOL_ADD_MEMORY: {
        "name": TOOL_ADD_MEMORY,
        "description": "写入一条带 creator_prompt_id 的 memory node。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "记忆内容"},
                "system_prompt": {"type": "string", "description": "创建此次写入的 system prompt"},
                "user_prompt": {"type": "string", "description": "创建此次写入的 user prompt"},
                "model_name": {"type": "string", "default": ""},
                "parents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "父节点 ULID 列表",
                    "default": [],
                },
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
                "importance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "requires_grad": {"type": "boolean", "default": False},
                "grad_learning_rate": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.0,
                },
            },
            "required": ["content"],
        },
    },
    TOOL_QUERY_LINEAGE: {
        "name": TOOL_QUERY_LINEAGE,
        "description": "查询某个节点的祖先/后代链。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer", "default": 2, "minimum": -1},
                "direction": {"type": "string", "enum": ["UP", "DOWN", "BOTH"], "default": "BOTH"},
            },
            "required": ["node_id"],
        },
    },
    TOOL_FEEDBACK: {
        "name": TOOL_FEEDBACK,
        "description": "触发反向传播 + SGA 聚合，返回聚合结果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_node_id": {"type": "string"},
                "feedback_text": {"type": "string", "maxLength": 100000},
                "source": {
                    "type": "string",
                    "enum": ["USER", "LLM_AS_JUDGE", "AUTOMATED_TEST", "HEURISTIC"],
                    "default": "USER",
                },
                "severity": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0},
            },
            "required": ["target_node_id", "feedback_text"],
        },
    },
    TOOL_APPLY_GRADIENT: {
        "name": TOOL_APPLY_GRADIENT,
        "description": "应用聚合后的梯度到 prompt 或 schema。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feedback_id": {"type": "string"},
                "cluster_id": {"type": "integer"},
                "target_type": {
                    "type": "string",
                    "enum": ["prompt", "schema"],
                    "default": "prompt",
                },
                "target_id": {"type": "string"},
            },
            "required": ["feedback_id", "cluster_id"],
        },
    },
    TOOL_TOOL_CALL: {
        "name": TOOL_TOOL_CALL,
        "description": "包装 Agent 的 tool call 并写入 DAG。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "args": {"type": "object"},
                "result": {},
                "error": {"type": "string"},
            },
            "required": ["tool_name", "args"],
        },
    },
    TOOL_SNAPSHOT: {
        "name": TOOL_SNAPSHOT,
        "description": "打点版本。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "default": ""},
                "tag": {"type": "string"},
            },
        },
    },
    TOOL_SEARCH: {
        "name": TOOL_SEARCH,
        "description": "lineage-aware 检索。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 10, "minimum": 1},
                "depth": {"type": "integer", "default": 2, "minimum": 0},
                "alpha": {"type": "number", "default": 0.6, "minimum": 0, "maximum": 1},
                "beta": {"type": "number", "default": 0.3, "minimum": 0, "maximum": 1},
                "gamma": {"type": "number", "default": 0.1, "minimum": 0, "maximum": 1},
            },
            "required": ["query"],
        },
    },
}


# 工具实现：接受 (args: dict, context: ToolContext) -> dict
# 由 MCPServer 注册
ToolHandler = Callable[[dict[str, Any], Any], dict[str, Any]]


# 占位 dict；实际 handlers 由 MCPServer 在初始化时注入（避免循环 import）
DMSA_TOOLS: dict[str, ToolHandler] = {}


def register_tool(name: str, handler: ToolHandler) -> None:
    """注册工具 handler（供 :class:`MCPServer` 调用）。"""
    if name not in TOOL_SCHEMAS:
        raise ToolNotFoundError(
            f"unknown tool: {name}",
            details={"tool_name": name, "known_tools": list(TOOL_SCHEMAS.keys())},
        )
    DMSA_TOOLS[name] = handler


# 抑制 unused import warning
_ = DMSAError
