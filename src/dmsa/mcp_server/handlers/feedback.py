"""Feedback / 梯度相关工具的 handler。

- dmsa_feedback: 触发反向传播 + SGA 聚合（聚合结果缓存在内存，供 apply 查询）
- dmsa_apply_gradient: 把聚合后的梯度真正写回 MemoryLayer（生成 successor 节点）
"""

from __future__ import annotations

import itertools
from typing import Any

from dmsa.errors import (
    GradientAggregationError,
    MemoryLayerError,
    ToolBPError,
)
from dmsa.gradient import (
    HashEmbedder,
    SGAAggregator,
    StaticFusion,
    make_sample,
)
from dmsa.mcp_server.tools import (
    TOOL_APPLY_GRADIENT,
    TOOL_FEEDBACK,
    register_tool,
)
from dmsa.memory import create_prompt_context

__all__ = ["_handle_apply_gradient", "_handle_feedback", "_register_handlers"]


_aggregator = SGAAggregator(
    embedder=HashEmbedder(dim=64),
    fusion=StaticFusion(),
)


def set_aggregator(aggregator: Any) -> None:
    """替换模块级 aggregator（供 CLI 按配置注入 LLM 后端）。

    Args:
        aggregator: 任何带 ``aggregate(samples, *, feedback_id)`` 方法的对象
            （通常是 :class:`dmsa.gradient.SGAAggregator`）。
    """
    global _aggregator
    _aggregator = aggregator


# 聚合结果缓存：feedback_id -> AGSAggregationResult。
# v0.1 为单进程内存态（server 重启即失效）；每个 MCP server 进程独立，
# 不需要跨进程共享（见 AGENTS.md 路径注意事项）。
_aggregations: dict[str, Any] = {}
_feedback_seq = itertools.count(1)


def _fused_to_dict(fused: Any) -> dict[str, Any]:
    """FusedGradient → dict。"""
    return {
        "cluster_id": fused.cluster_id,
        "fused_text": fused.fused_text,
        "support_size": fused.support_size,
        "confidence": fused.confidence,
        "has_conflict": fused.has_conflict,
        "branch_conditions": list(fused.branch_conditions),
    }


def _cluster_to_dict(cluster: Any) -> dict[str, Any]:
    """ClusterResult → dict。"""
    return {
        "cluster_id": cluster.cluster_id,
        "support_size": len(cluster.support_set),
        "confidence": cluster.confidence,
        "is_outlier": cluster.is_outlier,
    }


def _handle_feedback(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_feedback：触发反向传播 + SGA 聚合。"""
    target_node_id = args.get("target_node_id", "")
    feedback_text = args.get("feedback_text", "")
    if not target_node_id:
        raise MemoryLayerError("target_node_id required", details={"field": "target_node_id"})
    if not feedback_text:
        raise MemoryLayerError("feedback_text required", details={"field": "feedback_text"})

    # 1. 构造 GradientSample
    sample = make_sample(
        feedback_text=feedback_text,
        source=args.get("source", "USER"),
        severity=float(args.get("severity", 1.0)),
        target_node_id=target_node_id,
    )
    # 2. 跑 SGA aggregator（单样本聚类结果就是 1 个 outlier 或 1 个正常簇）
    feedback_id = f"fb-{target_node_id}-{next(_feedback_seq)}"
    result = _aggregator.aggregate([sample], feedback_id=feedback_id)
    # 3. 缓存聚合结果，供 dmsa_apply_gradient 按 feedback_id 找回
    _aggregations[feedback_id] = result
    return {
        "feedback_id": feedback_id,
        "clusters": [_cluster_to_dict(c) for c in result.clusters],
        "fused_gradients": [_fused_to_dict(f) for f in result.fused_gradients],
        "cluster_count": len(result.clusters),
    }


def _handle_apply_gradient(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_apply_gradient：把聚合后的梯度真正写回 MemoryLayer。

    - target_type="prompt"：目标是一个 requires_grad=True 的记忆节点，
      通过 :meth:`MemoryLayer.apply_text_gradient` 生成 successor 节点
      （带 lineage，可 snapshot/rollback）。
    - target_type="schema"：MCP 路径 v0.1 未接入 ToolBPEngine，
      明确报错并指向 ``dmsa.toolbp.ToolBPEngine``。
    """
    feedback_id = args.get("feedback_id", "")
    if not feedback_id:
        raise GradientAggregationError("feedback_id required", details={"field": "feedback_id"})
    result = _aggregations.get(feedback_id)
    if result is None:
        raise GradientAggregationError(
            f"unknown feedback_id: {feedback_id!r}",
            details={
                "feedback_id": feedback_id,
                "hint": "call dmsa_feedback first; aggregations are in-memory only",
            },
        )
    cluster_id = int(args.get("cluster_id", 0))
    fused = next((f for f in result.fused_gradients if f.cluster_id == cluster_id), None)
    if fused is None:
        raise GradientAggregationError(
            f"cluster_id {cluster_id} not in aggregation {feedback_id!r}",
            details={
                "feedback_id": feedback_id,
                "known_cluster_ids": [f.cluster_id for f in result.fused_gradients],
            },
        )

    target_type = args.get("target_type", "prompt")
    target_id = args.get("target_id", "")
    if not target_id:
        raise MemoryLayerError("target_id required", details={"field": "target_id"})

    if target_type == "schema":
        raise ToolBPError(
            "schema target is not wired into the MCP path in v0.1",
            details={
                "target_id": target_id,
                "hint": "use dmsa.toolbp.ToolBPEngine.project() directly",
            },
        )

    # prompt 路径：把融合后的文本梯度写回目标记忆节点
    ctx = create_prompt_context(
        system_prompt="dmsa gradient apply",
        user_prompt=f"apply {feedback_id} cluster={cluster_id} to {target_id}",
    )
    successor = layer.apply_text_gradient(
        node_id=target_id,
        gradient_text=fused.fused_text,
        prompt_context=ctx,
    )
    return {
        "applied": True,
        "target_type": target_type,
        "target_id": target_id,
        "new_node_id": successor.id,
        "new_version": successor.version,
        "cluster_id": cluster_id,
        "feedback_id": feedback_id,
        "branch_conditions": list(fused.branch_conditions),
    }


def _register_handlers() -> None:
    """注册本模块的 handler。"""
    register_tool(TOOL_FEEDBACK, _handle_feedback)
    register_tool(TOOL_APPLY_GRADIENT, _handle_apply_gradient)
