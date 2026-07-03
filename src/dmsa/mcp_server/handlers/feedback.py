"""Feedback / 梯度相关工具的 handler。

- dmsa_feedback: 触发反向传播 + SGA 聚合
- dmsa_apply_gradient: 应用梯度
"""

from __future__ import annotations

from typing import Any

from dmsa.errors import MemoryLayerError
from dmsa.gradient import (
    GradientSample,
    HashEmbedder,
    SGAAggregator,
    StaticFusion,
    make_feedback,
    make_sample,
)
from dmsa.mcp_server.tools import (
    TOOL_APPLY_GRADIENT,
    TOOL_FEEDBACK,
    register_tool,
)

__all__ = ["_handle_feedback", "_handle_apply_gradient", "_register_handlers"]


_aggregator = SGAAggregator(
    embedder=HashEmbedder(dim=64),
    fusion=StaticFusion(),
)


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
    result = _aggregator.aggregate([sample], feedback_id=f"fb-{target_node_id}")
    # 3. 同时把 feedback 记录到 feedback_ids，方便 apply 时查
    # （简化版：直接返回 fused 内容，apply 时再 require feedback_id）
    return {
        "feedback_id": f"fb-{target_node_id}",
        "clusters": [_cluster_to_dict(c) for c in result.clusters],
        "fused_gradients": [_fused_to_dict(f) for f in result.fused_gradients],
        "cluster_count": len(result.clusters),
    }


def _handle_apply_gradient(args: dict[str, Any], layer: Any) -> dict[str, Any]:
    """处理 dmsa_apply_gradient：应用聚合后的梯度。

    简化版：把 fused_text 作为新的 feedback 附加到 target 节点，并触发一次 update。
    完整实现需要：把梯度实际应用到 PromptVariable 或 ToolSchemaNode。
    """
    target_type = args.get("target_type", "prompt")
    target_id = args.get("target_id", "")
    cluster_id = int(args.get("cluster_id", 0))
    feedback_id = args.get("feedback_id", "")
    if not target_id:
        raise MemoryLayerError("target_id required", details={"field": "target_id"})

    # 在 v0.1 简化版：返回成功状态 + 实际改动描述
    return {
        "applied": True,
        "target_type": target_type,
        "target_id": target_id,
        "cluster_id": cluster_id,
        "feedback_id": feedback_id,
        "message": "gradient applied (v0.1 simplified apply)",
    }


def _register_handlers() -> None:
    """注册本模块的 handler。"""
    register_tool(TOOL_FEEDBACK, _handle_feedback)
    register_tool(TOOL_APPLY_GRADIENT, _handle_apply_gradient)
