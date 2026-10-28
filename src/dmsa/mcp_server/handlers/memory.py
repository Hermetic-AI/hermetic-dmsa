"""Memory 工具的 handler：dmsa_add_memory / dmsa_query_lineage / dmsa_search。

依赖：dmsa.memory + dmsa.mcp_server.tools。
"""

from __future__ import annotations

from typing import Any

from dmsa.errors import MemoryLayerError
from dmsa.mcp_server.tools import register_tool
from dmsa.memory import (
    LineageDirection,
    LineageQuery,
    create_prompt_context,
)

__all__ = ["_handle_add_memory", "_handle_query_lineage", "_handle_search"]


def _node_to_dict(node: Any) -> dict[str, Any]:
    """把 pmd.MemoryNode 序列化为 dict。"""
    return {
        "id": node.id,
        "version": node.version,
        "content": node.content,
        "content_type": (
            node.content_type.value
            if hasattr(node.content_type, "value")
            else str(node.content_type)
        ),
        "creator_prompt_id": node.creator_prompt_id,
        "creator_agent_id": node.creator_agent_id,
        "requires_grad": node.requires_grad,
        "grad_learning_rate": node.grad_learning_rate,
        "importance": node.importance,
        "tags": list(node.tags),
        "status": node.status.value if hasattr(node.status, "value") else str(node.status),
        "lineage_depth": node.lineage_depth,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }


def _handle_add_memory(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_add_memory。"""
    content = args.get("content", "")
    if not content:
        raise MemoryLayerError("content must be non-empty", details={"field": "content"})
    # 兜底：如果 caller 没给 system_prompt / user_prompt，生成一个占位
    system_prompt = args.get("system_prompt") or "hermetic-dmsa default agent"
    user_prompt = args.get("user_prompt") or f"add memory: {content[:50]}"
    ctx = create_prompt_context(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_name=args.get("model_name", ""),
    )
    node = layer.add_memory(
        content=content,
        prompt_context=ctx,
        parents=args.get("parents", []),
        tags=args.get("tags", []),
        importance=float(args.get("importance", 0.5)),
        requires_grad=bool(args.get("requires_grad", False)),
        grad_learning_rate=float(args.get("grad_learning_rate", 0.0)),
    )
    return {"node": _node_to_dict(node)}


def _handle_query_lineage(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_query_lineage。"""
    node_id = args.get("node_id", "")
    if not node_id:
        raise MemoryLayerError("node_id must be non-empty", details={"field": "node_id"})
    direction_str = args.get("direction", "BOTH")
    direction = LineageDirection(direction_str)
    query = LineageQuery(
        root_id=node_id,
        depth=int(args.get("depth", 2)),
        direction=direction,
    )
    results = layer.query_lineage(query)
    return {"nodes": [_node_to_dict(n) for n in results], "count": len(results)}


def _handle_search(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_search。"""
    query = args.get("query", "")
    if not query:
        raise MemoryLayerError("query must be non-empty", details={"field": "query"})
    results = layer.lineage_aware_search(
        query,
        k=int(args.get("k", 10)),
        depth=int(args.get("depth", 2)),
        alpha=float(args.get("alpha", 0.6)),
        beta=float(args.get("beta", 0.3)),
        gamma=float(args.get("gamma", 0.1)),
    )
    return {"nodes": [_node_to_dict(n) for n in results], "count": len(results)}


# 抑制 unused import warning
_ = register_tool
