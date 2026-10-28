"""冲突检测 + 条件分支生成（Textual Gradient Routing）。

SGA 的核心：当 :class:`FusedGradient` 标记 ``has_conflict=True`` 时，
:class:`ConditionalBranchSynthesizer` 把它转换为"if X then A else B"形式的
条件分支 prompt 片段。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from dmsa.errors import FusionConflictError
from dmsa.gradient.embedding import cosine_distance
from dmsa.types import FusedGradient, GradientSample

__all__ = [
    "ConditionalBranchSynthesizer",
    "ConflictDetector",
    "SynthesizedBranch",
]


class SynthesizedBranch:
    """生成的条件分支。"""

    __slots__ = ("condition", "confidence", "else_text", "then_text")

    def __init__(
        self,
        condition: str,
        then_text: str,
        else_text: str,
        confidence: float,
    ) -> None:
        """初始化条件分支, 记录条件文本、两个分支文本与置信度。"""
        self.condition = condition
        self.then_text = then_text
        self.else_text = else_text
        self.confidence = confidence

    def to_line(self) -> str:
        """渲染为单行文本（写入 FusedGradient.branch_conditions）。"""
        return f"if ({self.condition}) then: {self.then_text} | else: {self.else_text}"

    def __repr__(self) -> str:
        """返回条件分支的调试表示（仅含条件与置信度）。"""
        return f"SynthesizedBranch(condition={self.condition!r}, confidence={self.confidence:.2f})"


class ConflictDetector:
    """检测 :class:`FusedGradient` 之间是否存在真冲突。

    算法：对所有 FusedGradient 两两计算余弦距离；
    若距离 > ``threshold``（默认 0.3），则视为真冲突。
    """

    __slots__ = ("_distance_fn", "_threshold")

    def __init__(
        self,
        *,
        threshold: float = 0.3,
        distance_fn: object = None,
    ) -> None:
        """初始化冲突检测器, 校验阈值区间并绑定距离函数。"""
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {threshold}")
        self._threshold = threshold
        self._distance_fn = distance_fn or cosine_distance

    def detect(
        self,
        fused: list[FusedGradient],
        samples_by_cluster: dict[int, list[GradientSample]],
    ) -> list[tuple[int, int, float]]:
        """检测冲突对。

        Args:
            fused: 融合产物列表。
            samples_by_cluster: 每个簇的样本（用于算簇间质心距离）。

        Returns:
            冲突对列表 ``[(cluster_a_id, cluster_b_id, distance), ...]``。
        """
        conflicts: list[tuple[int, int, float]] = []
        for i in range(len(fused)):
            for j in range(i + 1, len(fused)):
                fi = fused[i]
                fj = fused[j]
                samples_i = samples_by_cluster.get(fi.cluster_id, [])
                samples_j = samples_by_cluster.get(fj.cluster_id, [])
                if not samples_i or not samples_j:
                    continue
                dist = self._avg_cluster_distance(samples_i, samples_j)
                if dist > self._threshold:
                    conflicts.append((fi.cluster_id, fj.cluster_id, dist))
        return conflicts

    def _avg_cluster_distance(
        self,
        a: Iterable[GradientSample],
        b: Iterable[GradientSample],
    ) -> float:
        """计算两簇样本之间的平均余弦距离。"""
        a_list = [s for s in a if s.embedding is not None]
        b_list = [s for s in b if s.embedding is not None]
        if not a_list or not b_list:
            return 0.0
        total = 0.0
        count = 0
        for sa in a_list:
            for sb in b_list:
                ea = sa.embedding
                eb = sb.embedding
                assert ea is not None
                assert eb is not None
                total += self._distance_fn(ea, eb)  # type: ignore[operator]
                count += 1
        return total / count if count else 0.0


class ConditionalBranchSynthesizer:
    """为冲突对合成"if X then A else B"条件分支。

    零依赖实现：用正则从冲突文本里提取 directive 关键词，构造分支。
    """

    __slots__ = ("_branch_template", "_condition_keywords")

    _CONDITION_KEYWORDS = (
        "short",
        "concise",
        "brief",
        "简短",
        "简洁",
        "detailed",
        "verbose",
        "long",
        "详细",
        "详尽",
        "use markdown",
        "use code",
        "用 markdown",
        "用代码",
    )

    def __init__(self, *, branch_template: str = "if ({cond}) then: {then_} else: {else_}") -> None:
        """初始化条件分支合成器, 保存分支模板。"""
        self._branch_template = branch_template

    def synthesize(
        self,
        fused_a: FusedGradient,
        fused_b: FusedGradient,
    ) -> SynthesizedBranch:
        """为冲突对 (a, b) 合成条件分支。

        启发式：
        1. 提取 a / b 的 directive 关键词（negation vs directive）
        2. 找到 condition 关键词（短/长/格式）
        3. 用模板生成 "if (condition) then a_text else b_text"
        """
        a_lower = fused_a.fused_text.lower()
        b_lower = fused_b.fused_text.lower()
        # 检测 condition
        condition = "context"  # default
        for kw in self._CONDITION_KEYWORDS:
            if kw in a_lower or kw in b_lower:
                condition = kw
                break
        # 提取 then/else 文本（取 fused_text 的前 80 字符）
        then_text = _truncate(fused_a.fused_text, 80)
        else_text = _truncate(fused_b.fused_text, 80)
        # confidence: 1 - normalized avg severity variance
        confidence = 0.7  # 启发式生成，置信度保守
        return SynthesizedBranch(
            condition=condition,
            then_text=then_text,
            else_text=else_text,
            confidence=confidence,
        )

    def synthesize_all(
        self,
        conflicts: list[tuple[int, int, float]],
        fused_by_id: dict[int, FusedGradient],
    ) -> dict[int, list[SynthesizedBranch]]:
        """为所有冲突对生成条件分支，按 cluster_id 分组。"""
        branches: dict[int, list[SynthesizedBranch]] = {}
        for cid_a, cid_b, _dist in conflicts:
            a = fused_by_id.get(cid_a)
            b = fused_by_id.get(cid_b)
            if a is None or b is None:
                continue
            branch = self.synthesize(a, b)
            branches.setdefault(cid_a, []).append(branch)
            branches.setdefault(cid_b, []).append(branch)
        return branches


def _truncate(text: str, max_chars: int) -> str:
    """把文本截短到 max_chars（保留首尾）。"""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


# 抑制 unused import warning
_ = FusionConflictError
