"""公共数据类（PD3：frozen dataclass + 完整 type hints）。

所有跨模块共享的数据结构都在这里。子类 dataclass + slots 保证不可变，
便于 hash + 比较 + 多线程安全。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# 梯度聚合相关
# ============================================================================


@dataclass(frozen=True, slots=True)
class GradientSample:
    """单条文本梯度样本。

    Attributes:
        sample_id: 唯一 ID（ULID 推荐）。
        feedback_text: 自然语言反馈（30-200 token 典型）。
        source: ``"USER"`` / ``"LLM_AS_JUDGE"`` / ``"AUTOMATED_TEST"`` / ``"HEURISTIC"``。
        severity: 严重程度 ``[0, 1]``；缩放后续文本梯度。
        embedding: 嵌入向量（``HashEmbedder`` 算好后注入；未嵌入前为 None）。
        target_node_id: 这条反馈针对的记忆节点 ULID。
        created_at: Unix 时间戳。
        metadata: 附加上下文（如 few-shot 来源、heuristic 名称等）。
    """

    sample_id: str
    feedback_text: str
    source: str
    severity: float
    embedding: tuple[float, ...] | None = None
    target_node_id: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClusterResult:
    """HDBSCAN / fallback 单簇结果。

    Attributes:
        cluster_id: 簇 ID；``-1`` 表示 outlier。
        support_set: 簇内样本的 tuple（顺序稳定，便于测试）。
        centroid: 簇质心向量（按样本平均），outlier 时为 None。
        confidence: 簇内样本一致性 ``[0, 1]``；1.0 = 完全一致。
        is_outlier: 是否是 outlier 簇。
    """

    cluster_id: int
    support_set: tuple[GradientSample, ...]
    centroid: tuple[float, ...] | None
    confidence: float
    is_outlier: bool = False


@dataclass(frozen=True, slots=True)
class FusedGradient:
    """SGA 融合产物。

    Attributes:
        cluster_id: 所属簇 ID。
        fused_text: 融合后的文本梯度（默认 :class:`StaticFusion` 生成）。
        support_size: 参与融合的样本数。
        confidence: 置信度 ``[0, 1]``。
        has_conflict: 是否检测到真冲突（应走 routing）。
        branch_conditions: 条件分支（冲突时为 ``["if X then A", "else B"]`` 形式）。
    """

    cluster_id: int
    fused_text: str
    support_size: int
    confidence: float
    has_conflict: bool = False
    branch_conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FeedbackSignal:
    """从工具调用 / 评测结果生成的反馈信号。

    Attributes:
        feedback_id: 唯一 ID。
        target_node_id: 这条反馈针对的记忆节点。
        feedback_text: 反馈内容。
        severity: ``[0, 1]``。
        source: ``"USER"`` / ``"LLM_AS_JUDGE"`` / ``"AUTOMATED_TEST"`` / ``"HEURISTIC"``。
        extra_context: 附加上下文（tool_name、args、error 等）。
    """

    feedback_id: str
    target_node_id: str
    feedback_text: str
    severity: float
    source: str
    extra_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AGSAggregationResult:
    """SGA 聚合结果。

    Attributes:
        feedback_id: 对应 :class:`FeedbackSignal.feedback_id`。
        clusters: 所有聚类结果（含 outlier）。
        fused_gradients: 融合产物 tuple（与 clusters 一一对应，cluster_id 对齐）。
        applied_to: 已应用到的节点 ID（apply_gradient 后填）。
    """

    feedback_id: str
    clusters: tuple[ClusterResult, ...]
    fused_gradients: tuple[FusedGradient, ...]
    applied_to: tuple[str, ...] = ()


# ============================================================================
# 工具反向传播相关
# ============================================================================


@dataclass(frozen=True, slots=True)
class ToolSchemaNode:
    """工具 JSON Schema 作为可微 Variable。

    Attributes:
        schema_id: 唯一 ID。
        name: 工具名（与 OpenAI function name 对齐）。
        schema_dict: 不可变的 JSON Schema 字典视图（实际是 ``Mapping[str, Any]``）。
        version: 版本号（编辑后 +1）。
        requires_grad: 是否参与反向传播。
        grad_learning_rate: ``[0, 1]``。
    """

    schema_id: str
    name: str
    schema_dict: Mapping[str, Any]
    version: int = 1
    requires_grad: bool = True
    grad_learning_rate: float = 0.1


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """工具调用结果记录。

    Attributes:
        call_id: 唯一 ID。
        tool_name: 调用的工具名（对应 ToolSchemaNode.name）。
        args: 实际传入的参数。
        result: 调用返回值（成功时）。
        error: 错误信息（失败时）。
        timestamp: Unix 时间戳。
    """

    call_id: str
    tool_name: str
    args: Mapping[str, Any]
    result: Any = None
    error: str | None = None
    timestamp: float = field(default_factory=time.time)


# ============================================================================
# 记忆层配置（共享）
# ============================================================================


@dataclass(frozen=True, slots=True)
class MemoryLayerConfig:
    """MemoryLayer 运行时配置（区别于全局 :class:`DMSAConfig`）。"""

    storage_path: str = ":memory:"
    auto_snapshot: bool = False
    """是否在每次 feedback 后自动打 snapshot（默认 False，节省 IO）。"""

    snapshot_interval_seconds: float = 300.0
    """自动 snapshot 间隔（仅 auto_snapshot=True 生效）。"""


__all__ = [
    "AGSAggregationResult",
    "ClusterResult",
    "FeedbackSignal",
    "FusedGradient",
    "GradientSample",
    "MemoryLayerConfig",
    "ToolCallRecord",
    "ToolSchemaNode",
]
