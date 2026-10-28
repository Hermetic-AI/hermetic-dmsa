"""文本梯度融合（默认 StaticFusion + FusionClient Protocol）。

融合 = 把一个簇内多条反馈合并成单一文本梯度。

默认实现 :class:`StaticFusion` 不调用 LLM，把所有 feedback_text
首尾拼接成统一格式（带 [cluster=N] 前缀和 [severity=...] 标签）。
生产环境可注入 :class:`LLMFusion`（用户自行实现）。
"""

from __future__ import annotations

import statistics
from typing import Protocol

from dmsa.errors import FusionConflictError
from dmsa.types import FusedGradient, GradientSample

__all__ = ["FusionClient", "RuleBasedFusion", "StaticFusion"]


class FusionClient(Protocol):
    """融合客户端接口。"""

    name: str

    def fuse(
        self,
        cluster_id: int,
        samples: list[GradientSample],
    ) -> FusedGradient:
        """把一个簇内样本融合为单一文本梯度。"""
        ...


class StaticFusion:
    """零依赖静态融合器：拼接所有 feedback_text，不调用 LLM。"""

    name = "static"

    def fuse(
        self,
        cluster_id: int,
        samples: list[GradientSample],
    ) -> FusedGradient:
        """简单拼接。"""
        if not samples:
            raise FusionConflictError(
                "cannot fuse empty sample list",
                details={"cluster_id": cluster_id},
            )
        # 严重度取平均
        sev = statistics.mean(s.severity for s in samples)
        # confidence: 取样本一致度（这里用 1.0 - 严重度方差 简化版）
        if len(samples) == 1:
            confidence = 1.0
        else:
            sev_var = statistics.variance(s.severity for s in samples)
            confidence = max(0.0, 1.0 - sev_var)
        # 拼接文本
        lines = [f"[cluster={cluster_id} size={len(samples)} avg_severity={sev:.2f}]"]
        for i, s in enumerate(samples, 1):
            lines.append(f"{i}. [{s.source}] {s.feedback_text}")
        fused_text = "\n".join(lines)
        return FusedGradient(
            cluster_id=cluster_id,
            fused_text=fused_text,
            support_size=len(samples),
            confidence=confidence,
            has_conflict=False,
            branch_conditions=(),
        )


class RuleBasedFusion:
    """基于规则 + 关键词的融合（启发式检测冲突关键词如"不要"/"避免"）。

    比 StaticFusion 多一步：检测簇内是否有强否定词对，从而标记 has_conflict。
    """

    name = "rule-based"

    NEGATION_KEYWORDS = (
        "不要",
        "避免",
        "禁止",
        "no",
        "not",
        "never",
        "avoid",
        "don't",
    )
    DIRECTIVE_KEYWORDS = (
        "应该",
        "必须",
        "应当",
        "should",
        "must",
        "always",
    )

    def __init__(self, *, conflict_marker: str = "[CONFLICT_DETECTED]") -> None:
        """初始化融合器, conflict_marker 用于标记冲突片段。"""
        self.conflict_marker = conflict_marker

    def fuse(
        self,
        cluster_id: int,
        samples: list[GradientSample],
    ) -> FusedGradient:
        """规则融合。"""
        if not samples:
            raise FusionConflictError(
                "cannot fuse empty sample list",
                details={"cluster_id": cluster_id},
            )
        sev = statistics.mean(s.severity for s in samples)
        confidence = (
            1.0
            if len(samples) == 1
            else max(0.0, 1.0 - statistics.variance(s.severity for s in samples))
        )
        # 冲突检测：簇内既有 NEGATION 又有 DIRECTIVE
        has_neg = any(
            any(kw in s.feedback_text.lower() for kw in self.NEGATION_KEYWORDS) for s in samples
        )
        has_dir = any(
            any(kw in s.feedback_text.lower() for kw in self.DIRECTIVE_KEYWORDS) for s in samples
        )
        has_conflict = has_neg and has_dir
        # 拼接
        header = (
            f"[cluster={cluster_id} size={len(samples)} avg_severity={sev:.2f} "
            f"conflict={'YES' if has_conflict else 'no'}]"
        )
        if has_conflict:
            header = self.conflict_marker + " " + header
        lines = [header]
        for i, s in enumerate(samples, 1):
            lines.append(f"{i}. [{s.source} sev={s.severity:.2f}] {s.feedback_text}")
        return FusedGradient(
            cluster_id=cluster_id,
            fused_text="\n".join(lines),
            support_size=len(samples),
            confidence=confidence,
            has_conflict=has_conflict,
            branch_conditions=(),
        )
